"""Run codebase 1 for the BRIDGE web app - everything behind `demo.ipynb`.

The Databricks job the web app triggers runs `demo.ipynb` (this codebase's
root folder). The notebook only reads its widgets and calls `run_app_job`;
the logic lives here so it can be tested on a laptop without Databricks.

Where the files are
-------------------
Every run has its own folder, holding its inputs and its outputs together,
under its BMC and its RUN GROUP - the modelling period and the modelling type,
e.g. "2025Q1-2025Q4 Secondary" (`group_name`):

    <base_path>/<bmc_name>/<run_group>/<run_name>/
        run_request.json               written by the web app: who, when, why (note),
                                       reused from
        Config/  Data/  Prior/         the run's inputs (Mapping/ and Share/ when used)
        Outputs/                       written here: the stage folders, run_info.json,
                                       job_log.txt, app_config.yaml

A new run always starts directly under its group. When a modeller marks the
run the results were REPORTED from, the web app moves that run folder into
`<run_group>/Results Reported/` and the group's other runs into
`<run_group>/Archived/` - only while none of the group's runs is running, so a
job never loses its folder. The job itself never moves anything.

Runs made before the run groups existed sit directly under the BMC
(`<base_path>/<bmc_name>/<run_name>/`); a blank `run_group` still runs there.

`base_path` is the mount the original notebook used, the web app's ADLS root
`Secondary Modelling` as the cluster sees it:

    /dbfs/mnt/testuat/Secondary Modelling

A Unity Catalog volume path (`/Volumes/<catalog>/<schema>/<volume>/...`)
works the same way - pass it as the `base_path` job parameter.

With `bmc_name` and `run_name` both blank, the oldest, shared layout is used:
inputs from `<base_path>/<Folder>/<name>`, outputs to
`<base_path>/Outputs/<run_id>` (runs made before the run folders existed, and
hand runs).

What it changes, and what it never changes
------------------------------------------
The job runs the config the app uploaded, with ONLY the keys in
`JOB_OWNED_KEYS` replaced - the input paths, where outputs go and the run
name. Everything the modeller chose stays exactly as uploaded. It writes that
config to the run's own folder; it never edits this folder's `config.yaml`,
so running the codebase by hand from the workspace is unaffected by app runs.
(The old notebook wrote back into config.yaml, which also meant two runs at
once overwrote each other's settings.)

Outputs are written to local disk first (HDF5 `trace.nc` and fast PNG writes
are fragile on FUSE storage) and then copied to the run folder's `Outputs/`
- also when the run fails, so the warnings of a failed run can be read.

Running by hand
---------------
With every widget blank, `demo.ipynb` runs this folder's `config.yaml` as it
stands, like `run_real_data.py`, and still publishes to `Outputs/manual_<time>`.
"""
from __future__ import annotations

__codebase__ = "2026.10.07.1"   # must equal mmm.__version__

import contextlib
import copy
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback

import yaml

DEFAULT_BASE_PATH = "/dbfs/mnt/testuat/Secondary Modelling"
LOG_FILE = "job_log.txt"        # everything the run printed, published with the outputs
LIVE_LOG_SECONDS = 30           # while a run runs, its log so far is copied to Outputs/
RUN_REQUEST = "run_request.json"  # written into the run folder by the web app

# job parameter -> folder under the run folder (old layout: under base_path)
FOLDERS = {
    "data_file": "Data",
    "prior_file": "Prior",
    "config_file": "Config",
    "mapping_file": "Mapping",
    "share_file": "Share",
}
OUTPUT_FOLDER = "Outputs"
# the old layout's shared folders directly under base_path - not BMC names
SHARED_FOLDERS = tuple(FOLDERS.values()) + (OUTPUT_FOLDER,)

# the job parameters demo.ipynb defines as widgets, all blank by default
PARAMS = ("config_file", "data_file", "prior_file", "mapping_file",
          "share_file", "run_id", "base_path", "bmc_name", "run_name", "run_group")

# a run group's two folders for the runs the web app has sorted (see above) -
# never a run name
REPORTED_FOLDER = "Results Reported"
ARCHIVED_FOLDER = "Archived"
GROUP_FOLDERS = (REPORTED_FOLDER, ARCHIVED_FOLDER)

# the standard names, one CSV each in this folder - the web app offers only these
BMC_NAMES_FILE = "bmc_names.csv"
MODELLING_TYPES_FILE = "modelling_types.csv"

# A BMC or run name is ONE folder level: letters, digits, spaces, _ - and .,
# starting with a letter or digit and not ending in a space or a dot (ADLS
# trims those). No slash and no "..", so a name can never point outside
# base_path. The web app checks names with this same rule.
NAME_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9 _.\-]{0,78}[A-Za-z0-9_\-])?"
_NAME_RE = re.compile(NAME_PATTERN)

# the ONLY config keys the job sets; a UI should show them read-only
JOB_OWNED_KEYS = ("data.input_path", "data.feature_priors", "data.mapping_file",
                  "data.share_file", "data.pre_model_dir", "run.output_dir",
                  "run.run_name")

# where each file parameter lands in the config
_PARAM_KEY = {"data_file": "input_path", "prior_file": "feature_priors",
              "mapping_file": "mapping_file", "share_file": "share_file"}
_PATH_KEYS = ("input_path", "feature_priors", "mapping_file", "share_file",
              "pre_model_dir")


def normalise_params(params: dict | None) -> dict:
    """Every known parameter as a stripped string ('' when absent)."""
    params = params or {}
    unknown = sorted(set(params) - set(PARAMS))
    if unknown:
        print(f"[app_job] ignoring unknown parameter(s): {unknown}")
    return {k: str(params.get(k) or "").strip() for k in PARAMS}


def input_path(base_path: str, param: str, name: str) -> str | None:
    """`<base_path>/<Folder>/<name>` for a file parameter, None when blank.
    Pass the run folder as `base_path` for a run in the per-run layout."""
    return os.path.join(base_path, FOLDERS[param], name) if name else None


def name_problem(name: str, what: str = "name") -> str | None:
    """Why `name` cannot be a BMC or run folder name - None when it can."""
    n = str(name or "")
    if not n.strip():
        return f"the {what} is empty"
    if n != n.strip():
        return f"the {what} '{n}' starts or ends with a space"
    if not _NAME_RE.fullmatch(n):
        return (f"the {what} '{n}' may use letters, digits, spaces, _ - and ., must "
                "start with a letter or digit, must not end with a space or a dot, "
                "and must be at most 80 characters")
    return None


def bmc_problem(name: str) -> str | None:
    """name_problem for a BMC name, which also must not be a shared folder."""
    problem = name_problem(name, "BMC name")
    if problem:
        return problem
    if str(name).lower() in {f.lower() for f in SHARED_FOLDERS}:
        return (f"'{name}' is one of the shared folders ({', '.join(SHARED_FOLDERS)}) "
                "- pick another BMC name")
    return None


def run_name_problem(name: str) -> str | None:
    """name_problem for a run name, which also must not be a group folder."""
    problem = name_problem(name, "run name")
    if problem:
        return problem
    if str(name).lower() in {f.lower() for f in GROUP_FOLDERS}:
        return (f"'{name}' is the name of a run group's folder "
                f"({', '.join(GROUP_FOLDERS)}) - pick another run name")
    return None


# "2025Q1-2025Q4 Secondary": the first and last quarter modelled, and the type
_GROUP_RE = re.compile(r"(\d{4})Q([1-4])-(\d{4})Q([1-4]) (\S.*)")


def group_name(start_year: int, start_quarter: int, end_year: int,
               end_quarter: int, modelling_type: str) -> str:
    """The run group folder for a modelling period and type, e.g.
    group_name(2025, 1, 2025, 4, "Secondary") -> "2025Q1-2025Q4 Secondary".
    Raises ValueError when the period runs backwards or the name is not a
    folder name (see group_problem)."""
    name = (f"{int(start_year):04d}Q{int(start_quarter)}-"
            f"{int(end_year):04d}Q{int(end_quarter)} {str(modelling_type or '').strip()}")
    problem = group_problem(name)
    if problem:
        raise ValueError(problem)
    return name


def parse_group(name: str) -> dict | None:
    """The parts of a run group name - {start_year, start_quarter, end_year,
    end_quarter, modelling_type} - or None when `name` is not one (a run
    folder of the older layout, for instance)."""
    m = _GROUP_RE.fullmatch(str(name or ""))
    if not m:
        return None
    sy, sq, ey, eq, kind = m.groups()
    return {"start_year": int(sy), "start_quarter": int(sq), "end_year": int(ey),
            "end_quarter": int(eq), "modelling_type": kind}


def group_problem(name: str) -> str | None:
    """Why `name` cannot be a run group folder - None when it can. A group is
    "<start>-<end> <modelling type>", quarters as 2025Q1, end not before start."""
    problem = name_problem(name, "run group")
    if problem:
        return problem
    parts = parse_group(name)
    if parts is None:
        return (f"the run group '{name}' must read like '2025Q1-2025Q4 Secondary': the "
                "first and the last quarter modelled, a space, the modelling type")
    if (parts["end_year"], parts["end_quarter"]) < (parts["start_year"],
                                                    parts["start_quarter"]):
        return f"the run group '{name}' ends before it starts"
    return None


def run_folder(base_path: str, bmc_name: str, run_name: str,
               run_group: str = "") -> str | None:
    """`<base_path>/<bmc_name>/<run_group>/<run_name>` - or, with no group,
    `<base_path>/<bmc_name>/<run_name>` (runs made before the groups) - or
    None for the old shared layout (everything blank). Raises ValueError for a
    BMC without a run name (or the reverse), a group without both, or a bad
    name."""
    bmc, run = str(bmc_name or "").strip(), str(run_name or "").strip()
    group = str(run_group or "").strip()
    if not bmc and not run:
        if group:
            raise ValueError(f"run_group={group!r} needs bmc_name and run_name")
        return None
    if not (bmc and run):
        raise ValueError("bmc_name and run_name go together - set both or neither "
                         f"(got bmc_name={bmc!r}, run_name={run!r})")
    for problem in (bmc_problem(bmc), run_name_problem(run),
                    group_problem(group) if group else None):
        if problem:
            raise ValueError(problem)
    return os.path.join(base_path, bmc, group, run) if group else os.path.join(
        base_path, bmc, run)


def read_names(path: str, column: str) -> list:
    """The standard names in a one-column CSV (`bmc_names.csv`,
    `modelling_types.csv`): the `column` column (or the first one; a file
    without the header line is read as names only), in file order - blanks,
    repeats and lines starting with # dropped. Raises ValueError for a name
    that cannot be a folder name, so a bad list is caught where it is
    written."""
    import csv
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.reader(fh) if r and r[0].strip()
                and not r[0].strip().startswith("#")]
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    has_header = column in header or header[0] in (column.replace("_", " "), "name")
    col = header.index(column) if column in header else 0
    names, seen, problems = [], set(), []
    for r in (rows[1:] if has_header else rows):
        name = r[col].strip() if col < len(r) else ""
        if not name or name.lower() in seen:
            continue
        problem = (bmc_problem(name) if column == "bmc_name"
                   else name_problem(name, column.replace("_", " ")))
        if problem:
            problems.append(problem)
            continue
        seen.add(name.lower())
        names.append(name)
    if problems:
        raise ValueError(f"{os.path.basename(path)}: " + "; ".join(problems))
    return names


def effective_config(raw: dict | None, params: dict, base_path: str,
                     local_root: str, config_dir: str) -> dict:
    """The config the job runs: `raw` with only the job-owned keys replaced.

    A file parameter that is set points its key at `<run folder>/<Folder>/`
    (old layout: `<base_path>/<Folder>/`); a blank one keeps the config's own
    value, resolved against `config_dir` (the uploaded file is moved to the
    run folder, so a relative path would otherwise resolve against the wrong
    place).
    """
    params = normalise_params(params)
    run_id = params["run_id"]
    if not run_id:
        raise ValueError("effective_config needs params['run_id']")
    root = run_folder(base_path, params["bmc_name"], params["run_name"],
                      params["run_group"]) or base_path
    cfg = copy.deepcopy(raw or {})
    if not isinstance(cfg, dict):
        raise ValueError("the config must be a mapping of sections")
    data = dict(cfg.get("data") or {})
    run = dict(cfg.get("run") or {})

    for key in _PATH_KEYS:
        v = data.get(key)
        if v and not os.path.isabs(str(v)):
            data[key] = os.path.normpath(os.path.join(config_dir, str(v)))
    for param, key in _PARAM_KEY.items():
        if params[param]:
            data[key] = input_path(root, param, params[param])

    run["output_dir"] = local_root
    run["run_name"] = run_id
    data["pre_model_dir"] = os.path.join(local_root, run_id, "00_pre_model")
    cfg["data"], cfg["run"] = data, run
    return cfg


def local_output_root() -> str:
    """/local_disk0 on classic clusters, the temp folder elsewhere (serverless)."""
    for cand in ("/local_disk0/mmm_outputs",
                 os.path.join(tempfile.gettempdir(), "mmm_outputs")):
        try:
            os.makedirs(cand, exist_ok=True)
            probe = os.path.join(cand, ".write_probe")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            return cand
        except OSError:
            continue
    return tempfile.mkdtemp(prefix="mmm_outputs_")


def copy_tree(src: str, dst: str) -> int:
    """Copy a folder file by file - no chmod/utime, which FUSE storage refuses
    (shutil.copytree would copy everything and then fail on copystat)."""
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for f in files:
            shutil.copyfile(os.path.join(root, f), os.path.join(target, f))
            n += 1
    return n


def _tail(text: str, n: int = 40) -> str:
    return "\n".join(str(text).rstrip().splitlines()[-n:])


def merge_config(base: dict | None, over: dict | None) -> dict:
    """`over` laid on top of `base`, section by section - a run's config.yaml
    (only the settings its author may change, see settings.app_access) over
    the team's config.yaml, which supplies everything else."""
    out = copy.deepcopy(base or {})
    for section, block in (over or {}).items():
        if isinstance(block, dict) and isinstance(out.get(section), dict):
            out[section] = {**out[section], **copy.deepcopy(block)}
        else:
            out[section] = copy.deepcopy(block)
    return out


def _live_log(src: str, dst_dir: str, stop: threading.Event, every: float) -> None:
    """Copy the log so far to Outputs/ now and every `every` s until `stop`,
    so the web app can show a run's progress while it runs."""
    while True:
        try:
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copyfile(src, os.path.join(dst_dir, LOG_FILE))
        except Exception:  # noqa: BLE001 - a live copy must never stop a run
            pass
        if stop.wait(every):
            return


def _read_request(root: str | None) -> dict:
    """The web app's run_request.json in the run folder ({} when absent)."""
    if not root:
        return {}
    try:
        with open(os.path.join(root, RUN_REQUEST), encoding="utf-8") as fh:
            request = json.load(fh)
        return request if isinstance(request, dict) else {}
    except (OSError, ValueError):
        return {}


class _LogTee:
    """Write to the notebook AND to the job log.

    A sampler progress bar rewrites one line with carriage returns thousands
    of times; the file keeps only each line's final state, so the log stays
    readable (and small) while the notebook shows the live bar."""

    def __init__(self, stream, fh):
        self._stream, self._fh, self._buf = stream, fh, ""

    def write(self, text):
        n = self._stream.write(text)
        self._buf += str(text)
        wrote = False
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._fh.write(line.rsplit("\r", 1)[-1] + "\n")
            wrote = True
        if wrote:
            self._fh.flush()           # the live copy reads the file as it grows
        return n

    def flush(self):
        self._stream.flush()
        self._fh.flush()

    def close_line(self):
        if self._buf:
            self._fh.write(self._buf.rsplit("\r", 1)[-1] + "\n")
            self._buf = ""

    def __getattr__(self, name):            # isatty, encoding, fileno, ...
        return getattr(self._stream, name)


@contextlib.contextmanager
def tee_log(path: str):
    """Copy stdout, stderr and log records into `path` while the block runs."""
    fh = open(path, "a", encoding="utf-8")
    out, err = _LogTee(sys.stdout, fh), _LogTee(sys.stderr, fh)
    handler = logging.StreamHandler(fh)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    loggers = [logging.getLogger()] + [logging.getLogger(n) for n in ("pymc", "pytensor")
                                       if not logging.getLogger(n).propagate]
    old = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    for lg in loggers:
        lg.addHandler(handler)
    try:
        yield path
    finally:
        for lg in loggers:
            lg.removeHandler(handler)
        out.close_line()
        err.close_line()
        sys.stdout, sys.stderr = old
        fh.close()


def run_app_job(params: dict | None = None, *, base_path: str | None = None,
                config_dir: str | None = None, local_root: str | None = None,
                publish: bool = True, runner=None, copier=None,
                live_log_every: float | None = None) -> dict:
    """Run one model for the web app and publish its output folder.

    `params` are the job parameters (see PARAMS). `runner` defaults to
    `run_from_yaml` and `copier` to `copy_tree`; both are arguments so a test
    can run the whole path without PyMC. Returns the `run_info` dict (also
    written to `<run>/run_info.json`). Everything the run prints goes to the
    notebook AND to `<run>/job_log.txt`, which the web app shows. A failed run
    - including one whose config cannot be read - is published first and then
    RE-RAISED, so the Databricks job fails with the real error.

    With `bmc_name` and `run_name` set, the inputs come from, and the outputs
    go to, `<base_path>/<bmc_name>/<run_group>/<run_name>/` (see the module
    docstring; without a `run_group`, `<base_path>/<bmc_name>/<run_name>/`).
    A bad name is an error of the run like any other - recorded and published
    to the old shared `Outputs/<run_id>`, never to a path built from it.

    The uploaded config.yaml is laid over the codebase folder's config.yaml
    (`merge_config`): the web app saves only the settings its user may change.
    While the run runs, its log so far is copied to the output folder every
    `live_log_every` seconds (LIVE_LOG_SECONDS; 0 = off).
    """
    import mmm

    t0 = time.time()
    p = normalise_params(params)
    run_id = p["run_id"] or time.strftime("manual_%Y%m%d-%H%M%S")
    p["run_id"] = run_id
    base_path = p["base_path"] or base_path or DEFAULT_BASE_PATH
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_dir = config_dir or here
    local_root = local_root or local_output_root()
    copier = copier or copy_tree

    try:
        root = run_folder(base_path, p["bmc_name"], p["run_name"], p["run_group"])
        folder_error = None
    except ValueError as e:
        root, folder_error = None, e
    if p["config_file"]:
        cfg_src = input_path(root or base_path, "config_file", p["config_file"])
        cfg_dir = os.path.dirname(cfg_src)
    else:
        cfg_src = os.path.join(config_dir, "config.yaml")
        cfg_dir = config_dir
    run_dir = os.path.join(local_root, run_id)
    os.makedirs(run_dir, exist_ok=True)
    published = (os.path.join(root, OUTPUT_FOLDER) if root
                 else os.path.join(base_path, OUTPUT_FOLDER, run_id))
    info = {"status": "running", "run_id": run_id,
            "bmc_name": p["bmc_name"], "run_group": p["run_group"],
            "run_name": p["run_name"], "run_folder": root,
            "codebase": mmm.__version__, "config_source": cfg_src,
            "params": p, "local_dir": run_dir,
            "output_dir": published if publish else run_dir,
            "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    request = _read_request(root)
    if request.get("submitted_by"):
        info["submitted_by"] = request["submitted_by"]
    if request.get("note"):
        info["note"] = str(request["note"])
    error = None
    log_path = os.path.join(run_dir, LOG_FILE)
    every = LIVE_LOG_SECONDS if live_log_every is None else live_log_every
    stop_live, live = threading.Event(), None
    with tee_log(log_path):
        if publish and every:
            live = threading.Thread(target=_live_log, daemon=True,
                                    args=(log_path, published, stop_live, every))
            live.start()
        try:
            print(f"[app_job] codebase {mmm.__version__}  run_id={run_id}  "
                  f"started {info['started']}")
            if folder_error is not None:
                raise folder_error
            if root:
                where = " / ".join(x for x in (p["bmc_name"], p["run_group"], p["run_name"])
                                   if x)
                print(f"[app_job] run      {where}  ->  {root}"
                      + (f"  (submitted by {request['submitted_by']})"
                         if request.get("submitted_by") else ""))
                if request.get("note"):
                    print(f"[app_job] note     {request['note']}")
            print(f"[app_job] config   {cfg_src}")
            with open(cfg_src, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            if p["config_file"]:
                team = os.path.join(config_dir, "config.yaml")
                if os.path.exists(team):
                    with open(team, encoding="utf-8") as fh:
                        raw = merge_config(yaml.safe_load(fh) or {}, raw)
                    print(f"[app_job] settings {p['config_file']} on top of the team's {team}")
                # the app names every input it sends: a mapping / share file in the
                # team's config.yaml must not slip into a run that has none
                raw["data"] = dict(raw.get("data") or {})
                for param in ("mapping_file", "share_file"):
                    if not p[param]:
                        raw["data"][param] = None
            cfg = effective_config(raw, p, base_path, local_root, cfg_dir)
            cfg_path = os.path.join(run_dir, "app_config.yaml")
            with open(cfg_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(cfg, fh, sort_keys=False, default_flow_style=False)
            for k in JOB_OWNED_KEYS:
                sec, key = k.split(".")
                print(f"[app_job]   {k:22s} = {cfg[sec].get(key)}")
            if runner is None:
                from mmm.core.settings import run_from_yaml as runner
            result = runner(cfg_path)
            info["status"] = "success"
            if isinstance(result, dict) and result.get("output_dir"):
                info["model_output_dir"] = str(result["output_dir"])
        except Exception as e:  # noqa: BLE001 - recorded, published, re-raised
            error = e
            info["status"] = "failed"
            info["error"] = f"{type(e).__name__}: {e}"
            info["traceback"] = _tail(traceback.format_exc())
            print(f"[app_job] FAILED: {info['error']}\n{info['traceback']}")
        finally:
            info["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
            info["seconds"] = round(time.time() - t0, 1)
            print(f"[app_job] {info['status']} after {info['seconds']} s")
    if live is not None:
        stop_live.set()
        live.join(timeout=60)
    try:
        with open(os.path.join(run_dir, "run_info.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(info, fh, indent=2, default=str)
    finally:
        if publish:
            try:
                if root and os.path.isdir(published) and [
                        f for f in os.listdir(published) if f != LOG_FILE]:
                    print(f"[app_job] note: {published} already had files (this run "
                          "folder was run before) - they are overwritten")
                n = copier(run_dir, published)
                print(f"[app_job] published {n} files -> {published}")
            except Exception as e:  # noqa: BLE001 - keep the local copy
                info["publish_error"] = f"{type(e).__name__}: {e}"
                info["output_dir"] = run_dir
                with open(os.path.join(run_dir, "run_info.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump(info, fh, indent=2, default=str)
                print(f"[app_job] !!! could not publish to {published}: {e}\n"
                      f"[app_job] the outputs are still in {run_dir}")
    if error is not None:
        raise error
    return info
