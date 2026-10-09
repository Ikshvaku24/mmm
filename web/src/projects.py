"""BMC, run-group and run folders - where every run's inputs and outputs live
in ADLS.

Each run the app starts gets its own folder, under its BMC and its RUN GROUP -
the modelling period and type, e.g. "2025Q1-2025Q4 Secondary":

    Secondary Modelling/<BMC>/<run group>/<run name>/
        run_request.json       written when the run is submitted: who, when, the
                               note, the job run id, which run it reused and
                               what changed
        note.txt               the modeller's note, when there is one
        Config/config.yaml     the settings (only those the person may change)
        Data/<datacube>        the input datacube
        Prior/<prior>.csv      the feature-prior file
        Mapping/  Share/       only when the run had them
        Outputs/               written by the job (codebase 1's mmm/app_job.py)

A new run always starts directly under its group. When someone marks the run
the group's results were REPORTED from (`mark_reported`), that run's folder
moves into <run group>/Results Reported/ and every other run of the group
into <run group>/Archived/; <run group>/reporting.json records who marked
which run when. A run is therefore found by its BMC, group and name - never by
where it sits now (`run_place` looks that up), so a moved run keeps its
results, its cached files and its widgets.

A finished run can be RENAMED (`rename_run`: its folder, where it sits; the
old name is kept in its run_request.json, and in the group's renames.json so
the Jobs API's record of the run - which keeps the name it ran under - still
finds it) and its NOTE edited (`update_note`: run_request.json keeps every
version, note.txt the latest). A run can be DELETED (`delete_run`: its whole
folder, for good - never while it runs, never the reported run); the group's
deleted.json records who and when. A name a run had - renamed away or deleted
- is never used again (`retired_names`): the Jobs API keeps the name a run
ran under, and a new run under that name would make the old record open the
wrong run.

So a run's zip holds exactly what went in and what came out, and a new run
can start from any earlier run's inputs. Runs from before the run groups
(Secondary Modelling/<BMC>/<run name>/) and from before the run folders (the
shared Data/ Prior/ Config/ ... folders and Outputs/<run_id>) can still be
opened and reused.

The folder names and the name rules come from the backend
(`codebase.layout()`, i.e. `mmm/app_job.py`), so the app and the job always
agree on a path. Nothing here uses Streamlit.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd

from src import codebase, perf
from src.files import (ADLS_ROOT, delete_dir, delete_file, download_from_adls,
                       is_not_found, list_dir, move_dir, path_exists, read_json,
                       upload_to_adls, write_json)

FILE_KINDS = ("config_file", "data_file", "prior_file", "mapping_file", "share_file")
KIND_LABELS = {"config_file": "settings", "data_file": "datacube",
               "prior_file": "prior file", "mapping_file": "mapping file",
               "share_file": "share file"}
RUN_INFO = "run_info.json"
NOTE_FILE = "note.txt"
REPORTING_FILE = "reporting.json"
RENAMES_FILE = "renames.json"
DELETED_FILE = "deleted.json"
NOTE_MAX = 2000                   # characters - a note, not a document

# used only until the backend answers (the same values as mmm/app_job.py)
_FALLBACK = {
    "folders": {"data_file": "Data", "prior_file": "Prior", "config_file": "Config",
                "mapping_file": "Mapping", "share_file": "Share"},
    "output_folder": "Outputs",
    "shared_folders": ["Data", "Prior", "Config", "Mapping", "Share", "Outputs"],
    "run_request": "run_request.json",
    "name_pattern": r"[A-Za-z0-9](?:[A-Za-z0-9 _.\-]{0,78}[A-Za-z0-9_\-])?",
    "group_pattern": r"(\d{4})Q([1-4])-(\d{4})Q([1-4]) (\S.*)",
    "reported_folder": "Results Reported",
    "archived_folder": "Archived",
}
_LAYOUT = {"value": None, "at": 0.0}
_LAYOUT_SECONDS = 300

# Shared by every user (perf.SharedCache): the BMC folders (a minute), each
# BMC's run list (30 s; "Refresh list", a new run and a reporting mark clear
# it for everyone), where each group's runs sit (30 s, same), the run
# requests (a minute) and the files of FINISHED runs, which never change again.
BMC_LIST_SECONDS = 60
RUN_LIST_SECONDS = 30
_BMCS = perf.SharedCache("bmc_list", ttl=BMC_LIST_SECONDS, maxsize=4)
_RUN_LISTS = perf.SharedCache("bmc_runs", ttl=RUN_LIST_SECONDS, maxsize=64)
_GROUPS = perf.SharedCache("group_places", ttl=RUN_LIST_SECONDS, maxsize=512)
_REQUESTS = perf.SharedCache("run_requests", ttl=60, maxsize=512)
_RENAMES = perf.SharedCache("run_renames", ttl=RUN_LIST_SECONDS, maxsize=256)
_DELETED = perf.SharedCache("run_deletions", ttl=RUN_LIST_SECONDS, maxsize=256)
_FINISHED = {}            # "<bmc>/<group>/<run>" -> {"request", "info"} of a finished run
_FINISHED_MAX = 5000

# one lock per run group: a reporting mark and a run being saved into the same
# group never interleave (both run in this app process)
_GROUP_LOCKS = {}
_GROUP_GUARD = threading.Lock()

STATUS_LABELS = {"success": "✅ Success", "failed": "❌ Failed",
                 "not started": "❌ Not started", "submitted": "🔄 Submitted",
                 "submitting": "⏳ Submitting", "unknown": "–"}
REPORTED_BADGE = "⭐ Reported"
ARCHIVED_BADGE = "Archived"
SUBMITTING_STALE_MS = 10 * 60 * 1000   # a "submitting" run older than this did not start


# --------------------------------------------------------------------------- #
# layout and names
# --------------------------------------------------------------------------- #
def layout() -> dict:
    """Folder names and the name rules, from the backend (re-read every few
    minutes, like the backend itself)."""
    now = time.time()
    if _LAYOUT["value"] is None or now - _LAYOUT["at"] > _LAYOUT_SECONDS:
        out = codebase.layout()
        if out.ok and out.value:
            _LAYOUT.update(value=dict(_FALLBACK, **out.value), at=now)
        elif _LAYOUT["value"] is None:
            return dict(_FALLBACK)
    return _LAYOUT["value"]


def reported_folder() -> str:
    return layout().get("reported_folder") or _FALLBACK["reported_folder"]


def archived_folder() -> str:
    return layout().get("archived_folder") or _FALLBACK["archived_folder"]


def name_problem(name, what="run name") -> str | None:
    """Why `name` cannot be a folder name - None when it can. The same rule
    as the job's `app_job.name_problem` (the pattern is the job's)."""
    n = str(name or "")
    if not n.strip():
        return f"the {what} is empty"
    if n != n.strip():
        return f"the {what} '{n}' starts or ends with a space"
    if not re.fullmatch(layout().get("name_pattern") or _FALLBACK["name_pattern"], n):
        return (f"the {what} '{n}' may use letters, digits, spaces, _ - and ., must "
                "start with a letter or digit, must not end with a space or a dot, "
                "and must be at most 80 characters")
    return None


def bmc_problem(name) -> str | None:
    """name_problem for a BMC name, which also must not be a shared folder."""
    problem = name_problem(name, "BMC name")
    if problem:
        return problem
    shared = layout().get("shared_folders") or _FALLBACK["shared_folders"]
    if str(name).lower() in {s.lower() for s in shared}:
        return (f"'{name}' is one of the shared folders ({', '.join(shared)}) "
                "- pick another BMC name")
    return None


def run_name_problem(name) -> str | None:
    """name_problem for a run name, which also must not be a group's folder."""
    problem = name_problem(name, "run name")
    if problem:
        return problem
    folders = (reported_folder(), archived_folder())
    if str(name).lower() in {f.lower() for f in folders}:
        return (f"'{name}' is the name of a run group's folder ({', '.join(folders)}) "
                "- pick another run name")
    return None


def parse_group(name) -> dict | None:
    """{start_year, start_quarter, end_year, end_quarter, modelling_type} of a
    run group name ("2025Q1-2025Q4 Secondary"), or None when it is not one."""
    m = re.fullmatch(layout().get("group_pattern") or _FALLBACK["group_pattern"],
                     str(name or ""))
    if not m:
        return None
    sy, sq, ey, eq, kind = m.groups()
    return {"start_year": int(sy), "start_quarter": int(sq), "end_year": int(ey),
            "end_quarter": int(eq), "modelling_type": kind}


def group_problem(name) -> str | None:
    """Why `name` cannot be a run group folder - None when it can (the job's
    `app_job.group_problem`)."""
    problem = name_problem(name, "run group")
    if problem:
        return problem
    parts = parse_group(name)
    if parts is None:
        return (f"the run group '{name}' must read like '2025Q1-2025Q4 Secondary': the "
                "first and the last quarter modelled, a space, the modelling type")
    if (parts["end_year"], parts["end_quarter"]) < (parts["start_year"],
                                                    parts["start_quarter"]):
        return f"the modelling period of '{name}' ends before it starts"
    return None


def group_name(start_year, start_quarter, end_year, end_quarter, modelling_type) -> str:
    """'2025Q1-2025Q4 Secondary' - the folder of a modelling period and type.
    Raises ValueError for a period that runs backwards or a bad type name."""
    name = (f"{int(start_year):04d}Q{int(start_quarter)}-{int(end_year):04d}"
            f"Q{int(end_quarter)} {str(modelling_type or '').strip()}")
    problem = group_problem(name)
    if problem:
        raise ValueError(problem)
    return name


def quarter_of(day) -> tuple[int, int]:
    """(year, quarter) of a date."""
    d = pd.Timestamp(day)
    return int(d.year), int((d.month - 1) // 3 + 1)


def safe_file_name(name, default="file") -> str:
    """A file name that is safe as the last part of an ADLS path."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return re.sub(r"[^A-Za-z0-9._ \-()]", "_", base) or default


def default_run_name(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("run_%Y%m%d-%H%M")


def is_auto_run_name(name) -> bool:
    """A name default_run_name made (run_20261007-1430, or _2, _3 after it)."""
    return bool(re.fullmatch(r"run_\d{8}-\d{4}(?:_\d+)?", str(name or "")))


def next_free_name(base: str, taken) -> str:
    """The next name after `base` that is not in `taken`: run -> run_2,
    run_7 -> run_8 (then run_9, ... while those are taken)."""
    taken = set(taken or ())
    match = re.fullmatch(r"(.*?)_(\d+)", str(base))
    stem, n = (match.group(1), int(match.group(2)) + 1) if match and match.group(1) \
        else (str(base), 2)
    stem = stem[:70]
    while f"{stem}_{n}" in taken:
        n += 1
    return f"{stem}_{n}"


def free_run_name(base: str, taken) -> str:
    """`base` itself when it is free, otherwise the next free name after it."""
    taken = set(taken or ())
    return base if base not in taken else next_free_name(base, taken)


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def bmc_dir(bmc) -> str:
    return f"{ADLS_ROOT}/{bmc}"


def group_dir(bmc, group) -> str:
    return f"{ADLS_ROOT}/{bmc}/{group}"


def run_dir(bmc, run, group="", place="") -> str:
    """<root>/<bmc>[/<group>[/<place>]]/<run> - `place` is "" (the group
    itself), "Results Reported" or "Archived"."""
    parts = [ADLS_ROOT, bmc] + [p for p in (group or "", place if group else "") if p] + [run]
    return "/".join(parts)


def input_dir(bmc, run, kind, group="", place="") -> str:
    return f"{run_dir(bmc, run, group, place)}/{layout()['folders'][kind]}"


def request_path(bmc, run, group="", place="") -> str:
    return f"{run_dir(bmc, run, group, place)}/{layout()['run_request']}"


def shared_input_path(kind, name) -> str:
    """The old layout: a file in the shared Data/ Prior/ ... folder."""
    return f"{ADLS_ROOT}/{layout()['folders'][kind]}/{name}"


# --------------------------------------------------------------------------- #
# run references: a run is found by its folder (bmc + group + run) and/or its
# job run - never by where in its group it sits right now
# --------------------------------------------------------------------------- #
def make_ref(job_run_id=None, bmc=None, run=None, group=None) -> dict:
    return {"job_run_id": str(job_run_id) if job_run_id not in (None, "") else None,
            "bmc": bmc or None, "run": run or None, "group": group or None}


def has_folder(ref) -> bool:
    return bool((ref or {}).get("bmc") and (ref or {}).get("run"))


def ref_group(ref) -> str:
    return str((ref or {}).get("group") or "")


def ref_label(ref) -> str:
    if not has_folder(ref):
        return f"run {ref.get('job_run_id')}"
    return " / ".join(p for p in (ref["bmc"], ref_group(ref), ref["run"]) if p)


def ref_key(ref) -> str:
    """A widget-key-safe id: the job run id for an old-layout run, else the
    folder (sanitised, plus a short hash so 'a b' and 'a_b' never collide).
    Never the place in the group, so a moved run keeps its key."""
    if not has_folder(ref):
        return str(ref.get("job_run_id"))
    label = "/".join(p for p in (ref["bmc"], ref_group(ref), ref["run"]) if p)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")[:40]
    return f"{stem}_{hashlib.sha1(label.encode()).hexdigest()[:6]}"


def ref_dir(ref) -> str:
    """Where the run's folder is NOW - a run of a group may have been moved
    into Results Reported/ or Archived/ since it ran."""
    group = ref_group(ref)
    place = run_place(ref["bmc"], group, ref["run"]) if group else ""
    return run_dir(ref["bmc"], ref["run"], group, place)


def outputs_dir(ref) -> str:
    out = layout()["output_folder"]
    if has_folder(ref):
        return f"{ref_dir(ref)}/{out}"
    return f"{ADLS_ROOT}/{out}/{ref.get('job_run_id')}"


# --------------------------------------------------------------------------- #
# run groups: where each run of a group sits
# --------------------------------------------------------------------------- #
def _list_group(bmc, group) -> dict:
    """{"root": [...], "reported": [...], "archived": [...]} - the run folders
    of one group by where they sit (read now)."""
    rep, arc = reported_folder(), archived_folder()
    out = {"root": [], "reported": [], "archived": []}
    for name, is_dir in list_dir(group_dir(bmc, group)):
        if not is_dir:
            continue
        if name == rep:
            out["reported"] = [n for n, d in list_dir(f"{group_dir(bmc, group)}/{rep}") if d]
        elif name == arc:
            out["archived"] = [n for n, d in list_dir(f"{group_dir(bmc, group)}/{arc}") if d]
        else:
            out["root"].append(name)
    return out


def group_runs(bmc, group, force=False) -> dict:
    """_list_group, read at most every RUN_LIST_SECONDS for everyone."""
    key = f"{bmc}/{group}"
    if force:
        _GROUPS.invalidate(key)
    value, _hit = _GROUPS.get_or_compute(key, lambda: _list_group(bmc, group))
    return value


def run_place(bmc, group, run) -> str:
    """"" (the group itself), "Results Reported" or "Archived" - where the
    run's folder is now. A run not listed yet (just started) is in the group."""
    places = group_runs(bmc, group)
    if run in places["reported"]:
        return reported_folder()
    if run in places["archived"]:
        return archived_folder()
    return ""


def group_lock(bmc, group) -> threading.Lock:
    with _GROUP_GUARD:
        return _GROUP_LOCKS.setdefault(f"{bmc}/{group}", threading.Lock())


def taken_run_names(bmc, group="") -> set:
    """Every run name in use in the group (wherever it sits) - or, without a
    group, every folder directly under the BMC."""
    if group:
        places = _list_group(bmc, group)
        return set(places["root"]) | set(places["reported"]) | set(places["archived"])
    return {n for n, is_dir in list_dir(bmc_dir(bmc)) if is_dir}


# --------------------------------------------------------------------------- #
# listing
# --------------------------------------------------------------------------- #
def list_bmcs() -> list:
    """The BMC folders under the root (the shared folders are not BMCs)."""
    return [name for name, is_dir in list_dir(ADLS_ROOT)
            if is_dir and bmc_problem(name) is None]


def bmc_children(bmc) -> tuple[list, list]:
    """(run groups, runs from before the groups) - the BMC's folders."""
    groups, old = [], []
    for name, is_dir in list_dir(bmc_dir(bmc)):
        if is_dir:
            (groups if parse_group(name) else old).append(name)
    return groups, old


def _read(path):
    """(dict or None, error or None) - a missing file is not an error."""
    try:
        return read_json(path), None
    except Exception as e:  # noqa: BLE001 - shown, never fatal
        return None, f"{path}: {e}"


def _state(request, info) -> str:
    if info:
        return "success" if info.get("status") == "success" else "failed"
    if request.get("job_error"):
        return "not started"
    if request.get("job_run_id"):
        return "submitted"
    return "submitting" if request else "unknown"


def _source_label(bmc, group, source) -> str:
    if not source:
        return ""
    if source.get("bmc") and source.get("run"):
        if source["bmc"] == bmc and str(source.get("group") or "") == str(group or ""):
            return source["run"]
        return " / ".join(p for p in (source["bmc"], source.get("group"), source["run"]) if p)
    return f"run {source.get('job_run_id')}"


def _row(bmc, name, request, info, group="", place="", reporting=None) -> dict:
    request, info = request or {}, info or {}
    state = _state(request, info)
    submitted = request.get("submitted_ms")
    if not submitted and info.get("started"):
        try:
            submitted = int(datetime.strptime(info["started"], "%Y-%m-%d %H:%M:%S")
                            .replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            submitted = None
    changed = request.get("changed") or []
    if not changed and request.get("source_run") and request.get("changed") is not None:
        changed = ["nothing"]            # rerun on purpose with identical inputs
    reported = bool(group) and place == reported_folder()
    mark = (reporting or {}) if reported and (reporting or {}).get("reported_run") == name else {}
    return {"run": name, "group": group or "", "place": place or "",
            "reported": reported, "archived": bool(group) and place == archived_folder(),
            "reported_by": mark.get("by", ""), "reported_at": mark.get("at", ""),
            "state": state, "status": STATUS_LABELS[state],
            "submitted_ms": submitted, "submitted_by": request.get("submitted_by", ""),
            "note": str(request.get("note") or ""),
            "source": _source_label(bmc, group, request.get("source_run")),
            "changed": changed,
            "job_run_id": str(request.get("job_run_id") or info.get("run_id") or "") or None,
            "error": info.get("error") or request.get("job_error") or "",
            "seconds": info.get("seconds"),
            "request": request, "info": info}


def list_runs(bmc, cache=None, job_state=None, workers=8) -> tuple[list, list]:
    """The BMC's runs - every group's and those from before the groups -
    newest first, and any read errors.

    Each run's run_request.json and Outputs/run_info.json are read in
    parallel. `cache` (a dict the caller keeps) holds finished runs, whose
    files no longer change. `job_state(job_run_id)`, when given, turns
    "submitted" into the live Jobs API state (a label, or None). Every row
    says its group and where in it the run sits (`place`, `reported`)."""
    cache = cache if cache is not None else _FINISHED
    groups, old = bmc_children(bmc)
    errors = []
    out_folder = layout()["output_folder"]
    rep, arc = reported_folder(), archived_folder()

    def group_listing(group):
        try:
            places = _list_group(bmc, group)
        except Exception as e:  # noqa: BLE001 - one bad group does not hide the rest
            errors.append(f"{group_dir(bmc, group)}: {e}")
            return group, {"root": [], "reported": [], "archived": []}, None
        _GROUPS.put(f"{bmc}/{group}", places)       # the panels' place look-ups agree
        reporting, problem = _read(f"{group_dir(bmc, group)}/{REPORTING_FILE}")
        if problem:
            errors.append(problem)
        return group, places, reporting

    with ThreadPoolExecutor(max_workers=workers) as pool:
        listed = list(pool.map(group_listing, groups))
    entries = [("", name, "") for name in old]
    reportings = {}
    for group, places, reporting in listed:
        reportings[group] = reporting
        entries += ([(group, n, "") for n in places["root"]]
                    + [(group, n, rep) for n in places["reported"]]
                    + [(group, n, arc) for n in places["archived"]])

    def load(entry):
        group, name, place = entry
        key = "/".join(p for p in (bmc, group, name) if p)
        hit = cache.get(key)
        if hit and hit.get("info"):
            return entry, hit["request"], hit["info"]
        request, e1 = _read(request_path(bmc, name, group, place))
        info, e2 = _read(f"{run_dir(bmc, name, group, place)}/{out_folder}/{RUN_INFO}")
        errors.extend(e for e in (e1, e2) if e)
        if info or cache is not _FINISHED:      # the shared store keeps finished runs only
            if cache is _FINISHED and len(cache) >= _FINISHED_MAX:
                cache.clear()
            cache[key] = {"request": request, "info": info}
        return entry, request, info

    with ThreadPoolExecutor(max_workers=workers) as pool:
        loaded = list(pool.map(load, entries))
    rows = [_row(bmc, name, r, i, group, place, reportings.get(group))
            for (group, name, place), r, i in loaded]
    pending = [r for r in rows if r["state"] == "submitted" and r["job_run_id"]]
    if job_state and pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            states = list(pool.map(lambda r: job_state(r["job_run_id"]), pending))
        for row, label in zip(pending, states):
            if label:
                row["status"] = label
    rows.sort(key=lambda r: (r["submitted_ms"] or 0, r["run"]), reverse=True)
    return rows, errors


def list_bmcs_shared() -> list:
    """list_bmcs(), listed at most once a minute for everyone."""
    names, _hit = _BMCS.get_or_compute("bmcs", list_bmcs)
    return names


def list_runs_shared(bmc, job_state=None, force=False) -> tuple[list, list]:
    """list_runs(bmc), read at most every RUN_LIST_SECONDS for everyone.
    `force` (Refresh list) reads it again now - for everyone."""
    if force:
        _RUN_LISTS.invalidate(str(bmc))
    value, _hit = _RUN_LISTS.get_or_compute(
        str(bmc), lambda: list_runs(bmc, job_state=job_state),
        cache_if=lambda v: not v[1])            # a read error is tried again
    return value


def forget_runs(bmc=None):
    """Make the next run list (one BMC, or all and the BMC folders) fresh -
    and where each of its groups' runs sits."""
    if bmc is None:
        _RUN_LISTS.invalidate()
        _BMCS.invalidate()
        _GROUPS.invalidate()
        _RENAMES.invalidate()
        _DELETED.invalidate()
    else:
        _RUN_LISTS.invalidate(str(bmc))
        _GROUPS.invalidate(prefix=f"{bmc}/")
        _RENAMES.invalidate(prefix=f"{bmc}/")
        _DELETED.invalidate(prefix=f"{bmc}/")


def run_exists(bmc, run, group="") -> bool:
    """Is the name taken - in the group wherever its runs sit, or (no group)
    directly under the BMC?"""
    if group:
        return run in taken_run_names(bmc, group)
    return path_exists(run_dir(bmc, run))


def read_request(bmc, run, group="") -> dict:
    """The run's run_request.json ({} when there is none)."""
    place = run_place(bmc, group, run) if group else ""
    return read_json(request_path(bmc, run, group, place)) or {}


def request_of(ref) -> dict:
    """read_request for a ref, shared by everyone for a minute ({} for a run
    without a folder, or when it cannot be read)."""
    if not has_folder(ref):
        return {}
    try:
        value, _hit = _REQUESTS.get_or_compute(
            ref_key(ref), lambda: read_request(ref["bmc"], ref["run"], ref_group(ref)))
    except Exception:  # noqa: BLE001 - the panel works without it
        return {}
    return value or {}


# --------------------------------------------------------------------------- #
# a run's inputs
# --------------------------------------------------------------------------- #
def run_inputs(ref, request=None, job_params=None) -> dict:
    """{kind: ADLS path} of a run's input files.

    Per-run layout: the names run_request.json lists (a folder made by hand,
    without one: the file in each input folder). Old layout: the names in the
    run's job parameters, in the shared folders."""
    if has_folder(ref):
        if request is None:
            request = read_request(ref["bmc"], ref["run"], ref_group(ref))
        root = ref_dir(ref)
        listed = request.get("files") if isinstance(request.get("files"), dict) else None
        out = {}
        for kind in FILE_KINDS:
            folder = f"{root}/{layout()['folders'][kind]}"
            if listed is not None:
                name = listed.get(kind) or ""
            else:
                files = [n for n, is_dir in list_dir(folder) if not is_dir]
                name = files[-1] if files else ""
            if name:
                out[kind] = f"{folder}/{name}"
        return out
    out = {}
    for kind in FILE_KINDS:
        name = str((job_params or {}).get(kind) or "").strip()
        if name:
            out[kind] = shared_input_path(kind, name)
    return out


def fetch_files(paths: dict, workers=5) -> tuple[dict, dict]:
    """Download {kind: path} in parallel -> ({kind: (name, bytes)}, {kind: error})."""
    def get(item):
        kind, path = item
        try:
            return kind, (path.rsplit("/", 1)[-1], download_from_adls(path)), None
        except Exception as e:  # noqa: BLE001 - reported per file
            return kind, None, ("not found: " + path) if is_not_found(e) else f"{path}: {e}"

    got, errors = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for kind, value, error in pool.map(get, list(paths.items())):
            if error:
                errors[kind] = error
            else:
                got[kind] = value
    return got, errors


# --------------------------------------------------------------------------- #
# saving a run - always directly under its group
# --------------------------------------------------------------------------- #
def save_inputs(bmc, run, files: dict, group="") -> dict:
    """Upload {kind: (name, bytes)} into the run folder -> {kind: name}."""
    names = {}
    for kind, (name, data) in files.items():
        name = safe_file_name(name)
        upload_to_adls(data, name, input_dir(bmc, run, kind, group))
        names[kind] = name
    return names


def clean_note(text) -> str:
    """A note as it is stored: trimmed, at most NOTE_MAX characters."""
    return str(text or "").strip()[:NOTE_MAX]


def new_request(bmc, run, names: dict, user="", source=None, changed=None,
                job_id="", app_codebase="", group="", note="") -> dict:
    now = datetime.now(timezone.utc)
    return {"bmc_name": bmc, "run_group": group or "", "run_name": run,
            "submitted_by": user or "",
            "submitted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "submitted_ms": int(now.timestamp() * 1000),
            "note": clean_note(note),
            "files": {kind: names.get(kind, "") for kind in FILE_KINDS},
            "source_run": source or None, "changed": changed,
            "job_id": str(job_id or ""), "job_run_id": None, "job_error": None,
            "state": "submitting", "app_codebase": app_codebase}


def write_request(bmc, run, request: dict, group="") -> None:
    write_json(request, layout()["run_request"], run_dir(bmc, run, group))


def write_note(bmc, run, note, group="") -> None:
    """note.txt in the run folder - the note readable in ADLS without the app."""
    text = clean_note(note)
    if text:
        upload_to_adls((text + "\n").encode("utf-8"), NOTE_FILE, run_dir(bmc, run, group))


# --------------------------------------------------------------------------- #
# the run a group's results were reported from
# --------------------------------------------------------------------------- #
def is_running(row, job_done=None, now_ms=None) -> bool:
    """Could this run still be writing into its folder? `job_done(job_run_id)`
    is True when the Jobs API has the run finished (or no longer knows it),
    False while it runs, None when it could not be asked - which counts as
    running: a folder is never moved on a guess."""
    if row["state"] == "submitting":
        now_ms = now_ms if now_ms is not None else time.time() * 1000
        return now_ms - (row.get("submitted_ms") or 0) < SUBMITTING_STALE_MS
    if row["state"] != "submitted":
        return False
    if job_done is None or not row.get("job_run_id"):
        return True
    return job_done(row["job_run_id"]) is not True


def mark_reported(bmc, group, run, user="", running=()) -> dict:
    """Make `run` the run the group's results were reported from.

    Its folder moves into <group>/Results Reported/ and every other run of the
    group into <group>/Archived/ (runs already there stay), and
    <group>/reporting.json records it. `running` names the group's runs still
    running - then nothing moves (the job would lose its folder). Repeating
    a mark that was interrupted finishes it. Returns {"moved": [(run, from,
    to)], "previous": the run reported before, or None}; raises ValueError
    when the mark cannot be made."""
    rep, arc = reported_folder(), archived_folder()
    with group_lock(bmc, group):
        places = _list_group(bmc, group)
        where = {n: "" for n in places["root"]}
        where.update({n: arc for n in places["archived"]})
        where.update({n: rep for n in places["reported"]})
        if run not in where:
            raise ValueError(f"'{run}' is not a run of {bmc} / {group}")
        busy = sorted(set(running or ()) & set(where))
        if busy:
            raise ValueError("wait until every run of this period and modelling type has "
                             f"finished - still running: {', '.join(busy)}")
        previous = [n for n in places["reported"] if n != run]
        moves = [(n, p, arc) for n, p in where.items() if n != run and p != arc]
        if where[run] != rep:
            moves.append((run, where[run], rep))       # last: the others make room first
        moved = []
        try:
            for name, src, dst in moves:
                move_dir(run_dir(bmc, name, group, src), run_dir(bmc, name, group, dst))
                moved.append((name, src, dst))
        except Exception as e:
            raise RuntimeError(
                f"moving '{name}' into {dst} failed: {e}"
                + (f" ({len(moved)} other run(s) were moved already - mark it again to "
                   "finish)" if moved else "")) from e
        finally:
            forget_runs(bmc)
            _REQUESTS.invalidate()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record, _problem = _read(f"{group_dir(bmc, group)}/{REPORTING_FILE}")
        history = list((record or {}).get("history") or [])
        history.append({"run": run, "by": user or "", "at": now,
                        "previous": previous[0] if previous else None})
        write_json({"reported_run": run, "by": user or "", "at": now, "history": history},
                   REPORTING_FILE, group_dir(bmc, group))
        forget_runs(bmc)
    return {"moved": moved, "previous": previous[0] if previous else None}


# --------------------------------------------------------------------------- #
# after a run: its name and its note
# --------------------------------------------------------------------------- #
def _names_home(bmc, group="") -> str:
    """Where a group's renames.json lives (without a group: the BMC's)."""
    return group_dir(bmc, group) if group else bmc_dir(bmc)


def renames(bmc, group="") -> dict:
    """{old name: name now} of the group's renamed runs (shared, 30 s)."""
    def load():
        data, _problem = _read(f"{_names_home(bmc, group)}/{RENAMES_FILE}")
        return dict((data or {}).get("renamed") or {})
    value, _hit = _RENAMES.get_or_compute(f"{bmc}/{group}", load)
    return value


def deleted_runs(bmc, group="") -> dict:
    """{name: {by, at, job_run_id, ...}} of the group's deleted runs (shared,
    30 s) - deleted.json, next to renames.json."""
    def load():
        data, _problem = _read(f"{_names_home(bmc, group)}/{DELETED_FILE}")
        return dict((data or {}).get("deleted") or {})
    value, _hit = _DELETED.get_or_compute(f"{bmc}/{group}", load)
    return value


def retired_names(bmc, group="") -> set:
    """The names no new run of the group may take: those of renamed and of
    deleted runs. The Jobs API keeps the name a run ran under - a new run
    with that name would make the old record open the wrong run."""
    return set(renames(bmc, group)) | set(deleted_runs(bmc, group))


def unavailable_names(bmc, group="") -> set:
    """Every name a new (or renamed) run of the group cannot have: the runs
    there now and the retired names."""
    return taken_run_names(bmc, group) | retired_names(bmc, group)


def deleted_record(ref) -> dict | None:
    """Who deleted the ref's run and when - None when it was not deleted."""
    if not has_folder(ref):
        return None
    try:
        return deleted_runs(ref["bmc"], ref_group(ref)).get(ref["run"])
    except Exception:  # noqa: BLE001 - an unreadable deleted.json: not deleted
        return None


def current_run_name(bmc, group, run) -> str:
    """The name a run has NOW. The Jobs API keeps the name a run ran under;
    renames.json says what it became."""
    return renames(bmc, group).get(run, run)


def resolve_ref(ref) -> dict:
    """The ref with the run's current name (a renamed run's old ref still
    opens it)."""
    if not has_folder(ref):
        return ref
    try:
        name = current_run_name(ref["bmc"], ref_group(ref), ref["run"])
    except Exception:  # noqa: BLE001 - an unreadable renames.json: keep the name
        return ref
    return ref if name == ref["run"] else dict(ref, run=name)


def _place_now(bmc, group, run) -> str:
    """run_place, read now (not the shared 30 s copy)."""
    if not group:
        return ""
    places = _list_group(bmc, group)
    if run in places["reported"]:
        return reported_folder()
    if run in places["archived"]:
        return archived_folder()
    return ""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rename_run(bmc, group, run, new, user="", running=False) -> str:
    """Rename a run that is not running: its folder (wherever it sits in its
    group), `run_name` in its run_request.json (the old name and who renamed
    it are kept under `renamed_from`), reporting.json when it is the
    group's reported run, and renames.json (old -> new) for the Jobs API's
    record of the run. Returns the new name; raises ValueError when it cannot
    be done (running, a bad name, a name in use - now or by a run renamed
    before, so an old link can never open the wrong run)."""
    new = str(new or "").strip()
    group = group or ""
    if running:
        raise ValueError("the run is still running - rename it once it has finished")
    if new == run:
        raise ValueError("that is already its name")
    problem = run_name_problem(new)
    if problem:
        raise ValueError(problem)
    if not group and parse_group(new):
        raise ValueError(f"'{new}' reads like a period folder - pick another run name")
    where = f"{bmc} / {group}" if group else bmc
    with group_lock(bmc, group):
        taken = taken_run_names(bmc, group)
        if run not in taken:
            raise ValueError(f"'{run}' is not a run of {where}")
        if new in taken:
            raise ValueError(f"'{new}' already exists in {where} - choose another name")
        home = _names_home(bmc, group)
        record, _problem = _read(f"{home}/{RENAMES_FILE}")
        record = record or {}
        renamed = dict(record.get("renamed") or {})
        if new in renamed:
            raise ValueError(f"'{new}' was the name of another run before (now "
                             f"'{renamed[new]}') - choose another name")
        gone, _problem = _read(f"{home}/{DELETED_FILE}")
        if new in ((gone or {}).get("deleted") or {}):
            raise ValueError(f"'{new}' was the name of a deleted run - choose another name")
        place = _place_now(bmc, group, run)
        try:
            move_dir(run_dir(bmc, run, group, place), run_dir(bmc, new, group, place))
        finally:
            forget_runs(bmc)
            _REQUESTS.invalidate()
        now = _stamp()
        request, _problem = _read(request_path(bmc, new, group, place))
        request = request or {}
        request["run_name"] = new
        request["renamed_from"] = list(request.get("renamed_from") or []) + [
            {"name": run, "by": user or "", "at": now}]
        write_json(request, layout()["run_request"], run_dir(bmc, new, group, place))
        if group:
            rep_path = f"{group_dir(bmc, group)}/{REPORTING_FILE}"
            reporting, _problem = _read(rep_path)
            if reporting and reporting.get("reported_run") == run:
                reporting["reported_run"] = new
                reporting["history"] = list(reporting.get("history") or []) + [
                    {"run": new, "renamed_from": run, "by": user or "", "at": now}]
                write_json(reporting, REPORTING_FILE, group_dir(bmc, group))
        renamed = {k: (new if v == run else v) for k, v in renamed.items()}
        renamed[run] = new
        write_json({"renamed": renamed,
                    "history": list(record.get("history") or []) + [
                        {"from": run, "to": new, "by": user or "", "at": now}]},
                   RENAMES_FILE, home)
        forget_runs(bmc)
        _REQUESTS.invalidate()
    return new


def delete_run(bmc, group, run, user="", running=False) -> dict:
    """Delete a run for good: its folder - inputs, Outputs/, note - wherever
    it sits in its group. Refused while the run may still write into it, and
    for the group's reported run (mark another run as reported first). The
    group's deleted.json (the BMC's, without a group) records who deleted it
    and when, and its name is never used again (`retired_names`), so the
    Jobs API's record of the run can only say "deleted". Returns the record;
    raises ValueError when it cannot be done."""
    group = group or ""
    if running:
        raise ValueError("the run is still running - cancel it, or delete it once it "
                         "has finished")
    where = f"{bmc} / {group}" if group else bmc
    with group_lock(bmc, group):
        if run not in taken_run_names(bmc, group):
            raise ValueError(f"'{run}' is not a run of {where}")
        place = _place_now(bmc, group, run)
        if place == reported_folder():
            raise ValueError(f"'{run}' is the run the results of {group} were reported "
                             "from - mark another run as reported first")
        request, _problem = _read(request_path(bmc, run, group, place))
        request = request or {}
        try:
            delete_dir(run_dir(bmc, run, group, place))
        finally:
            forget_runs(bmc)
            _REQUESTS.invalidate()
        entry = {"by": user or "", "at": _stamp(), "place": place or "",
                 "job_run_id": str(request.get("job_run_id") or "") or None,
                 "submitted_by": request.get("submitted_by", ""),
                 "note": str(request.get("note") or "")}
        home = _names_home(bmc, group)
        record, _problem = _read(f"{home}/{DELETED_FILE}")
        record = record or {}
        deleted = dict(record.get("deleted") or {})
        deleted[run] = entry
        write_json({"deleted": deleted,
                    "history": list(record.get("history") or []) + [dict(entry, run=run)]},
                   DELETED_FILE, home)
        forget_runs(bmc)
    return entry


def update_note(bmc, group, run, note, user="") -> dict:
    """Set a run's note - at any time, also while it runs (it touches neither
    the inputs nor Outputs/). run_request.json keeps every version under
    `note_history` (the note it was started with first); note.txt holds the
    latest (it is deleted for an empty note). Returns the request."""
    text = clean_note(note)
    group = group or ""
    with group_lock(bmc, group):
        place = _place_now(bmc, group, run)
        folder = run_dir(bmc, run, group, place)
        if not path_exists(folder):
            raise ValueError(f"the run folder {folder} does not exist")
        request, _problem = _read(f"{folder}/{layout()['run_request']}")
        request = request or {}
        history = list(request.get("note_history") or [])
        if not history and request.get("note"):
            history.append({"note": request["note"], "by": request.get("submitted_by", ""),
                            "at": request.get("submitted_at", "")})
        history.append({"note": text, "by": user or "", "at": _stamp()})
        request["note"] = text
        request["note_history"] = history
        write_json(request, layout()["run_request"], folder)
        if text:
            upload_to_adls((text + "\n").encode("utf-8"), NOTE_FILE, folder)
        else:
            delete_file(f"{folder}/{NOTE_FILE}")
        forget_runs(bmc)
        _REQUESTS.invalidate()
    return request


# --------------------------------------------------------------------------- #
# what changed since the run the inputs came from
# --------------------------------------------------------------------------- #
def sha(data) -> str:
    return hashlib.sha1(data).hexdigest() if data is not None else ""


def _cell(v) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, bool):
        return str(v)
    try:
        return format(float(v), ".10g")
    except (TypeError, ValueError):
        return str(v).strip()


def prior_signature(table) -> str:
    """The prior table's CONTENT - blind to 1 vs 1.0, blank vs None, the
    column order and the editor's Remove / Serial No columns."""
    if table is None:
        return ""
    t = codebase.clean_prior_table(table)
    parts = [f"{c}=" + "|".join(_cell(v) for v in t[c]) for c in sorted(t.columns)]
    return sha("\n".join(parts).encode("utf-8"))


def _same(a, b) -> bool:
    if a in (None, "") and b in (None, ""):
        return True                      # a text box shows null as ""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    try:
        return a == b
    except Exception:  # noqa: BLE001
        return False


def _fmt(v) -> str:
    if v is None:
        return "null"
    return str(v).lower() if isinstance(v, bool) else str(v)


def config_changes(before: dict, after: dict, skip=()) -> list:
    """[{'setting', 'before', 'after'}] for each setting that differs (the keys
    in `skip` - the job-owned ones - are ignored), values as readable text."""
    out = []
    before, after = before or {}, after or {}
    for sec in dict.fromkeys(list(before) + list(after)):
        a, b = before.get(sec) or {}, after.get(sec) or {}
        for key in dict.fromkeys(list(a) + list(b)):
            if f"{sec}.{key}" in skip:
                continue
            if not _same(a.get(key), b.get(key)):
                out.append({"setting": f"{sec}.{key}", "before": _fmt(a.get(key)),
                            "after": _fmt(b.get(key))})
    return out


def config_diff(before: dict, after: dict, skip=()) -> list:
    """'section.key: old -> new' for each setting that differs - the text form
    of config_changes, as run_request.json records it."""
    return [f"{c['setting']}: {c['before']} → {c['after']}"
            for c in config_changes(before, after, skip)]
