"""BMC and run folders - where every run's inputs and outputs live in ADLS.

Each run the app starts gets its own folder, one per BMC and run name:

    Secondary Modelling/<BMC>/<run name>/
        run_request.json       written when the run is submitted: who, when, the
                               job run id, which run it reused and what changed
        Config/config.yaml     the settings, every key
        Data/<datacube>        the input datacube
        Prior/<prior>.csv      the feature-prior file
        Mapping/  Share/       only when the run had them
        Outputs/               written by the job (codebase 1's mmm/app_job.py)

So a run's zip holds exactly what went in and what came out, and a new run
can start from any earlier run's inputs. Runs from before the run folders
(the shared Data/ Prior/ Config/ ... folders and Outputs/<run_id>) can still
be opened and reused through their job parameters.

The folder names and the name rule come from the backend (`codebase.layout()`,
i.e. `mmm/app_job.py`), so the app and the job always agree on a path.
Nothing here uses Streamlit.
"""
from __future__ import annotations

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd

from src import codebase
from src.files import (ADLS_ROOT, download_from_adls, is_not_found, list_dir,
                       path_exists, read_json, upload_to_adls, write_json)

FILE_KINDS = ("config_file", "data_file", "prior_file", "mapping_file", "share_file")
KIND_LABELS = {"config_file": "settings", "data_file": "datacube",
               "prior_file": "prior file", "mapping_file": "mapping file",
               "share_file": "share file"}
RUN_INFO = "run_info.json"

# used only until the backend answers (the same values as mmm/app_job.py)
_FALLBACK = {
    "folders": {"data_file": "Data", "prior_file": "Prior", "config_file": "Config",
                "mapping_file": "Mapping", "share_file": "Share"},
    "output_folder": "Outputs",
    "shared_folders": ["Data", "Prior", "Config", "Mapping", "Share", "Outputs"],
    "run_request": "run_request.json",
    "name_pattern": r"[A-Za-z0-9](?:[A-Za-z0-9 _.\-]{0,78}[A-Za-z0-9_\-])?",
}
_LAYOUT = {"value": None, "at": 0.0}
_LAYOUT_SECONDS = 300

STATUS_LABELS = {"success": "✅ Success", "failed": "❌ Failed",
                 "not started": "❌ Not started", "submitted": "🔄 Submitted",
                 "submitting": "⏳ Submitting", "unknown": "–"}


# --------------------------------------------------------------------------- #
# layout and names
# --------------------------------------------------------------------------- #
def layout() -> dict:
    """Folder names and the name rule, from the backend (re-read every few
    minutes, like the backend itself)."""
    now = time.time()
    if _LAYOUT["value"] is None or now - _LAYOUT["at"] > _LAYOUT_SECONDS:
        out = codebase.layout()
        if out.ok and out.value:
            _LAYOUT.update(value=out.value, at=now)
        elif _LAYOUT["value"] is None:
            return dict(_FALLBACK)
    return _LAYOUT["value"]


def name_problem(name, what="run name") -> str | None:
    """Why `name` cannot be a run folder name - None when it can. The same
    rule as the job's `app_job.name_problem` (the pattern is the job's)."""
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


def safe_file_name(name, default="file") -> str:
    """A file name that is safe as the last part of an ADLS path."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return re.sub(r"[^A-Za-z0-9._ \-()]", "_", base) or default


def default_run_name(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("run_%Y%m%d-%H%M")


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


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def bmc_dir(bmc) -> str:
    return f"{ADLS_ROOT}/{bmc}"


def run_dir(bmc, run) -> str:
    return f"{ADLS_ROOT}/{bmc}/{run}"


def input_dir(bmc, run, kind) -> str:
    return f"{run_dir(bmc, run)}/{layout()['folders'][kind]}"


def request_path(bmc, run) -> str:
    return f"{run_dir(bmc, run)}/{layout()['run_request']}"


def shared_input_path(kind, name) -> str:
    """The old layout: a file in the shared Data/ Prior/ ... folder."""
    return f"{ADLS_ROOT}/{layout()['folders'][kind]}/{name}"


# --------------------------------------------------------------------------- #
# run references: a run is found by its folder (bmc + run) and/or its job run
# --------------------------------------------------------------------------- #
def make_ref(job_run_id=None, bmc=None, run=None) -> dict:
    return {"job_run_id": str(job_run_id) if job_run_id not in (None, "") else None,
            "bmc": bmc or None, "run": run or None}


def has_folder(ref) -> bool:
    return bool((ref or {}).get("bmc") and (ref or {}).get("run"))


def ref_label(ref) -> str:
    return f"{ref['bmc']} / {ref['run']}" if has_folder(ref) else f"run {ref.get('job_run_id')}"


def ref_key(ref) -> str:
    """A widget-key-safe id: the job run id for an old-layout run, else the
    folder (sanitised, plus a short hash so 'a b' and 'a_b' never collide)."""
    if not has_folder(ref):
        return str(ref.get("job_run_id"))
    label = f"{ref['bmc']}/{ref['run']}"
    stem = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")[:40]
    return f"{stem}_{hashlib.sha1(label.encode()).hexdigest()[:6]}"


def outputs_dir(ref) -> str:
    out = layout()["output_folder"]
    if has_folder(ref):
        return f"{run_dir(ref['bmc'], ref['run'])}/{out}"
    return f"{ADLS_ROOT}/{out}/{ref.get('job_run_id')}"


# --------------------------------------------------------------------------- #
# listing
# --------------------------------------------------------------------------- #
def list_bmcs() -> list:
    """The BMC folders under the root (the shared folders are not BMCs)."""
    return [name for name, is_dir in list_dir(ADLS_ROOT)
            if is_dir and bmc_problem(name) is None]


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


def _source_label(bmc, source) -> str:
    if not source:
        return ""
    if source.get("bmc") and source.get("run"):
        return source["run"] if source["bmc"] == bmc else f"{source['bmc']} / {source['run']}"
    return f"run {source.get('job_run_id')}"


def _row(bmc, name, request, info) -> dict:
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
    return {"run": name, "state": state, "status": STATUS_LABELS[state],
            "submitted_ms": submitted, "submitted_by": request.get("submitted_by", ""),
            "source": _source_label(bmc, request.get("source_run")),
            "changed": changed,
            "job_run_id": str(request.get("job_run_id") or info.get("run_id") or "") or None,
            "error": info.get("error") or request.get("job_error") or "",
            "request": request, "info": info}


def list_runs(bmc, cache=None, job_state=None, workers=8) -> tuple[list, list]:
    """The BMC's runs, newest first, and any read errors.

    Each run's run_request.json and Outputs/run_info.json are read in
    parallel. `cache` (a dict the caller keeps) holds finished runs, whose
    files no longer change. `job_state(job_run_id)`, when given, turns
    "submitted" into the live Jobs API state (a label, or None)."""
    cache = cache if cache is not None else {}
    names = [n for n, is_dir in list_dir(bmc_dir(bmc)) if is_dir]
    errors = []
    out_folder = layout()["output_folder"]

    def load(name):
        key = f"{bmc}/{name}"
        hit = cache.get(key)
        if hit and hit.get("info"):
            return name, hit["request"], hit["info"]
        request, e1 = _read(request_path(bmc, name))
        info, e2 = _read(f"{run_dir(bmc, name)}/{out_folder}/{RUN_INFO}")
        errors.extend(e for e in (e1, e2) if e)
        cache[key] = {"request": request, "info": info}
        return name, request, info

    with ThreadPoolExecutor(max_workers=workers) as pool:
        loaded = list(pool.map(load, names))
    rows = [_row(bmc, n, r, i) for n, r, i in loaded]
    pending = [r for r in rows if r["state"] == "submitted" and r["job_run_id"]]
    if job_state and pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            states = list(pool.map(lambda r: job_state(r["job_run_id"]), pending))
        for row, label in zip(pending, states):
            if label:
                row["status"] = label
    rows.sort(key=lambda r: (r["submitted_ms"] or 0, r["run"]), reverse=True)
    return rows, errors


def run_exists(bmc, run) -> bool:
    return path_exists(run_dir(bmc, run))


def read_request(bmc, run) -> dict:
    return read_json(request_path(bmc, run)) or {}


# --------------------------------------------------------------------------- #
# a run's inputs
# --------------------------------------------------------------------------- #
def run_inputs(ref, request=None, job_params=None) -> dict:
    """{kind: ADLS path} of a run's input files.

    Per-run layout: the names run_request.json lists (a folder made by hand,
    without one: the file in each input folder). Old layout: the names in the
    run's job parameters, in the shared folders."""
    if has_folder(ref):
        bmc, run = ref["bmc"], ref["run"]
        if request is None:
            request = read_request(bmc, run)
        listed = request.get("files") if isinstance(request.get("files"), dict) else None
        out = {}
        for kind in FILE_KINDS:
            folder = input_dir(bmc, run, kind)
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
# saving a run
# --------------------------------------------------------------------------- #
def save_inputs(bmc, run, files: dict) -> dict:
    """Upload {kind: (name, bytes)} into the run folder -> {kind: name}."""
    names = {}
    for kind, (name, data) in files.items():
        name = safe_file_name(name)
        upload_to_adls(data, name, input_dir(bmc, run, kind))
        names[kind] = name
    return names


def new_request(bmc, run, names: dict, user="", source=None, changed=None,
                job_id="", app_codebase="") -> dict:
    now = datetime.now(timezone.utc)
    return {"bmc_name": bmc, "run_name": run,
            "submitted_by": user or "",
            "submitted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "submitted_ms": int(now.timestamp() * 1000),
            "files": {kind: names.get(kind, "") for kind in FILE_KINDS},
            "source_run": source or None, "changed": changed,
            "job_id": str(job_id or ""), "job_run_id": None, "job_error": None,
            "state": "submitting", "app_codebase": app_codebase}


def write_request(bmc, run, request: dict) -> None:
    write_json(request, layout()["run_request"], run_dir(bmc, run))


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


def config_diff(before: dict, after: dict, skip=()) -> list:
    """'section.key: old -> new' for each setting that differs (the keys in
    `skip` - the job-owned ones - are ignored)."""
    out = []
    before, after = before or {}, after or {}
    for sec in dict.fromkeys(list(before) + list(after)):
        a, b = before.get(sec) or {}, after.get(sec) or {}
        for key in dict.fromkeys(list(a) + list(b)):
            if f"{sec}.{key}" in skip:
                continue
            if not _same(a.get(key), b.get(key)):
                out.append(f"{sec}.{key}: {_fmt(a.get(key))} → {_fmt(b.get(key))}")
    return out
