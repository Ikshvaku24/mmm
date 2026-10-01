"""The web app's ONLY door into codebase 1 - the backend.

Codebase 1 is NOT copied into this app. It is loaded live from wherever it
lives, so the app always runs the same code as the Databricks job and as
anyone running the codebase by hand from the workspace. Where it looks, in
order:

  1. CODEBASE1_DIR             a local folder (development)
  2. CODEBASE1_WORKSPACE_PATH  a Databricks workspace folder
  3. the folder of the notebook the model job (MDR_JOB_ID) runs. demo.ipynb
     lives in codebase 1, so the Jobs API says where the backend is: nothing
     to configure, and the app can never point at a different copy than the
     job does
  4. ../codebase1_hierarchical_mmm next to this app (the repository layout)

A workspace copy is downloaded through the Workspace API and re-checked every
REFRESH_SECONDS. When anything in it changed it is downloaded again and every
later call uses the new code - re-uploading codebase 1 to the workspace is all
it takes; the app does not need redeploying. The config editor and the prior
table are built from the backend's own schema, so a key added to codebase 1
appears in the app by itself.

Every call here
  * runs under ONE lock. Codebase 1 captures warnings and printing for the
    whole process, and Streamlit runs each browser session as a thread;
  * returns an Outcome(ok, value, errors, warnings, log);
  * catches SystemExit as well as exceptions. The mapping and share readers
    stop with SystemExit, which would otherwise end the Streamlit script run.

Nothing here imports Streamlit, so the tests can import it.
"""
from __future__ import annotations

import contextlib
import copy
import csv
import difflib
import hashlib
import importlib
import io
import os
import posixpath
import re
import shutil
import sys
import tempfile
import threading
import time
import warnings
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pandas as pd
import requests
import yaml

# the first version with settings.app_access (app_access.yaml - who may do what)
# and partial run configs laid over the team's config.yaml; 2026.09.29.2
# brought the per-run folders
MIN_CODEBASE = "2026.09.30.1"
REFRESH_SECONDS = 300
WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIBLING = os.path.normpath(os.path.join(WEB_DIR, "..", "codebase1_hierarchical_mmm"))

# what the app needs from the backend folder (app_access.yaml: who may do what -
# optional, and part of the fingerprint, so editing it reaches the app like any
# other re-upload)
_NEEDED_FILES = ("config.yaml", "app_access.yaml")
_NEEDED_DIRS = ("mmm", "samples")
_NEEDED_DOCS = ("CONFIG_GUIDE.md", "FEATURE_PRIOR_GUIDE.md")

_LOCK = threading.RLock()
_STATE = {"dir": None, "source": "", "where": "", "fingerprint": None,
          "checked": 0.0}


@dataclass
class Outcome:
    ok: bool = True
    value: object = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)     # [(severity, text)]
    log: str = ""


# --------------------------------------------------------------------------- #
# Databricks REST (the same host/token the app already uses for the Jobs API)
# --------------------------------------------------------------------------- #
def _host() -> str:
    h = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    if not h:
        return ""
    return h if h.startswith("http") else f"https://{h}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ.get('DATABRICKS_TOKEN', '')}"}


def _api_path(p: str) -> str:
    """The Workspace API wants /Users/..., not /Workspace/Users/..."""
    p = "/" + str(p).strip().strip("/")
    return p[len("/Workspace"):] if p.startswith("/Workspace/") else p


def _job_codebase_path(job_id: str) -> str | None:
    """The workspace folder of the notebook the model job runs."""
    r = requests.get(f"{_host()}/api/2.1/jobs/get", headers=_headers(),
                     params={"job_id": job_id}, timeout=30)
    r.raise_for_status()
    settings = r.json().get("settings", {})
    if settings.get("git_source"):
        return None                      # a Git-sourced job has no workspace copy
    for task in settings.get("tasks", []):
        nb = task.get("notebook_task") or {}
        if nb.get("notebook_path") and nb.get("source", "WORKSPACE") != "GIT":
            return posixpath.dirname(_api_path(nb["notebook_path"]))
    return None


def _ws_list(path: str) -> list:
    r = requests.get(f"{_host()}/api/2.0/workspace/list", headers=_headers(),
                     params={"path": _api_path(path)}, timeout=30)
    r.raise_for_status()
    return r.json().get("objects", []) or []


def _ws_listing(root: str) -> list[dict]:
    """Every file the app needs under `root`, with what identifies a version."""
    root = _api_path(root)
    top = {posixpath.basename(o["path"]): o for o in _ws_list(root)}
    if top.get("mmm", {}).get("object_type") != "DIRECTORY":
        raise RuntimeError(f"no mmm/ package in the workspace folder {root}")
    files = []

    def add(o):
        rel = posixpath.relpath(o["path"], root)
        files.append({"rel": rel, "path": o["path"],
                      "stamp": (o.get("modified_at"), o.get("size"),
                                o.get("object_id"))})

    def walk(path):
        for o in _ws_list(path):
            name = posixpath.basename(o["path"])
            if name == "__pycache__":
                continue
            if o.get("object_type") == "DIRECTORY":
                walk(o["path"])
            elif o.get("object_type") == "FILE":
                add(o)

    for name in _NEEDED_FILES:
        if top.get(name, {}).get("object_type") == "FILE":
            add(top[name])
    for name in _NEEDED_DIRS:
        if top.get(name, {}).get("object_type") == "DIRECTORY":
            walk(top[name]["path"])
    if top.get("docs", {}).get("object_type") == "DIRECTORY":
        for o in _ws_list(top["docs"]["path"]):
            if posixpath.basename(o["path"]) in _NEEDED_DOCS:
                add(o)
    return sorted(files, key=lambda f: f["rel"])


def _ws_download(path: str) -> bytes:
    for fmt in ("AUTO", "SOURCE"):
        r = requests.get(f"{_host()}/api/2.0/workspace/export", headers=_headers(),
                         params={"path": path, "format": fmt,
                                 "direct_download": "true"}, timeout=60)
        if r.status_code == 400 and fmt == "AUTO":
            continue
        r.raise_for_status()
        if "application/json" in r.headers.get("Content-Type", ""):
            import base64
            return base64.b64decode(r.json().get("content", ""))
        return r.content
    raise RuntimeError(f"could not export {path}")


def _download_all(listing: list[dict], folder: str) -> None:
    part = folder + ".partial"
    shutil.rmtree(part, ignore_errors=True)
    os.makedirs(part, exist_ok=True)

    def one(f):
        target = os.path.join(part, *f["rel"].split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(_ws_download(f["path"]))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(one, listing))
    shutil.rmtree(folder, ignore_errors=True)
    os.replace(part, folder)
    open(os.path.join(folder, ".complete"), "w").close()


# --------------------------------------------------------------------------- #
# finding, fingerprinting and (re)loading the backend
# --------------------------------------------------------------------------- #
def _resolve_source() -> tuple[str, str]:
    d = os.environ.get("CODEBASE1_DIR", "").strip()
    if d:
        return "local", os.path.abspath(d)
    w = os.environ.get("CODEBASE1_WORKSPACE_PATH", "").strip()
    if w:
        return "workspace", _api_path(w)
    job = os.environ.get("MDR_JOB_ID", "").strip()
    if job and _host() and os.environ.get("DATABRICKS_TOKEN"):
        p = _job_codebase_path(job)
        if p:
            return "workspace", p
    if os.path.isdir(os.path.join(SIBLING, "mmm")):
        return "local", SIBLING
    raise RuntimeError(
        "codebase 1 not found. Set CODEBASE1_WORKSPACE_PATH in app.yml to the "
        "workspace folder that holds mmm/ and demo.ipynb (or CODEBASE1_DIR to "
        "a local folder).")


def _local_fingerprint(folder: str) -> str:
    h = hashlib.sha1()
    for sub in ("mmm", "samples"):
        for root, dirs, files in os.walk(os.path.join(folder, sub)):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for f in sorted(files):
                p = os.path.join(root, f)
                st_ = os.stat(p)
                h.update(f"{os.path.relpath(p, folder)}|{st_.st_mtime_ns}|"
                         f"{st_.st_size}".encode())
    for name in _NEEDED_FILES:
        p = os.path.join(folder, name)
        if os.path.exists(p):
            h.update(f"{name}|{os.stat(p).st_mtime_ns}".encode())
    return h.hexdigest()


def _version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


def _activate(folder: str) -> None:
    """Import mmm from `folder`, dropping any previously imported copy."""
    for name in [n for n in sys.modules if n == "mmm" or n.startswith("mmm.")]:
        del sys.modules[name]
    old = _STATE["dir"]
    if old and old in sys.path:
        sys.path.remove(old)
    sys.path.insert(0, folder)
    importlib.invalidate_caches()
    _STATE["dir"] = None
    mmm = importlib.import_module("mmm")
    if _version_tuple(mmm.__version__) < _version_tuple(MIN_CODEBASE):
        raise RuntimeError(
            f"codebase 1 at {_STATE['where'] or folder} is version "
            f"{mmm.__version__}; this app needs {MIN_CODEBASE} or later. "
            "Re-upload the whole codebase1_hierarchical_mmm folder.")
    _STATE["dir"] = folder


def _ensure_loaded(force: bool = False) -> None:
    with _LOCK:
        now = time.time()
        if (_STATE["dir"] and not force
                and now - _STATE["checked"] < REFRESH_SECONDS):
            return
        kind, where = _resolve_source()
        if kind == "local":
            folder, fp = where, _local_fingerprint(where)
        else:
            listing = _ws_listing(where)
            fp = hashlib.sha1(repr([(f["rel"], f["stamp"]) for f in listing])
                              .encode()).hexdigest()
            if force:
                fp = f"{fp}-{int(now)}"
            folder = os.path.join(tempfile.gettempdir(), "bridge_codebase1", fp[:16])
            if not os.path.exists(os.path.join(folder, ".complete")):
                _download_all(listing, folder)
        _STATE["checked"] = now
        if fp != _STATE["fingerprint"] or folder != _STATE["dir"] or force:
            _STATE.update(source=kind, where=where)
            _activate(folder)
            _STATE["fingerprint"] = fp


def _m(name: str):
    return importlib.import_module(name)


def _classify(texts) -> list:
    seen, out = set(), []
    try:
        classify = _m("mmm.checks.warnings_report").classify
    except Exception:  # noqa: BLE001
        classify = None
    for t in texts:
        t = " ".join(str(t).split())
        if not t or t in seen:
            continue
        seen.add(t)
        sev = "review"
        if classify is not None:
            try:
                sev = classify(t).get("severity", "review")
            except Exception:  # noqa: BLE001
                pass
        out.append((sev, t))
    order = {"high": 0, "medium": 1, "review": 2}
    return sorted(out, key=lambda x: order.get(x[0], 3))


def _guarded(fn, *args, **kwargs) -> Outcome:
    out, buf = Outcome(), io.StringIO()
    with _LOCK:
        try:
            _ensure_loaded()
        except Exception as e:  # noqa: BLE001
            return Outcome(ok=False, errors=[f"codebase 1 could not be loaded: {e}"])
        with warnings.catch_warnings(record=True) as caught, \
                contextlib.redirect_stdout(buf):
            warnings.simplefilter("always")
            try:
                out.value = fn(*args, **kwargs)
            except SystemExit as e:
                out.ok = False
                out.errors.append(str(e.code if e.code is not None else e))
            except ValueError as e:
                out.ok = False
                out.errors.append(str(e))
            except Exception as e:  # noqa: BLE001
                out.ok = False
                out.errors.append(f"{type(e).__name__}: {e}")
        out.warnings = _classify(w.message for w in caught)
    out.log = buf.getvalue()
    if isinstance(out.value, _Checked):             # checks with many findings
        out.errors += out.value.errors
        out.warnings += [("medium", w) for w in out.value.warnings]
        out.ok = out.ok and not out.value.errors
        out.value = out.value.value
    return out


@dataclass
class _Checked:
    value: object = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def status(force: bool = False) -> dict:
    """Which backend the app is using. Never raises."""
    try:
        with _LOCK:
            _ensure_loaded(force=force)
            mmm = _m("mmm")
            return {"ok": True, "version": mmm.__version__,
                    "source": _STATE["source"], "where": _STATE["where"],
                    "fingerprint": _STATE["fingerprint"],
                    "out_of_sync": mmm.check_sync(), "min_version": MIN_CODEBASE}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "min_version": MIN_CODEBASE}


def backend_dir() -> str | None:
    return _STATE["dir"]


# --------------------------------------------------------------------------- #
# config.yaml
# --------------------------------------------------------------------------- #
def schema() -> Outcome:
    """The backend's config schema, which keys/folders the job owns, and who
    may do what in the app (`access`, from app_access.yaml)."""
    def impl():
        st_, aj = _m("mmm.core.settings"), _m("mmm.app_job")
        access_file = os.path.join(_STATE["dir"], st_.ACCESS_FILE)
        try:
            access = dict(st_.app_access(access_file), error="")
        except ValueError as e:
            # a file that cannot be read fixes every setting and grants nobody
            # full access - it never opens anything
            access = {"full_access": [], "config_full_access": [], "editable": [],
                      "show_fixed": True, "unknown": [], "source": access_file,
                      "error": str(e)}
        return {"rows": st_.config_schema(), "job_owned": list(aj.JOB_OWNED_KEYS),
                "folders": dict(aj.FOLDERS), "output_folder": aj.OUTPUT_FOLDER,
                "sections": ["data"] + list(st_.SECTIONS),
                "blurbs": dict(st_.SECTION_BLURB), "access": access}
    return _guarded(impl)


def layout() -> Outcome:
    """Where a run's files live - the job's own folder names and name rule,
    so the app and the job can never disagree about a path."""
    def impl():
        aj = _m("mmm.app_job")
        return {"folders": dict(aj.FOLDERS), "output_folder": aj.OUTPUT_FOLDER,
                "shared_folders": list(aj.SHARED_FOLDERS),
                "run_request": aj.RUN_REQUEST, "name_pattern": aj.NAME_PATTERN}
    return _guarded(impl)


def _full_config(raw: dict) -> dict:
    """Every key filled: what the job would actually run for `raw`."""
    st_ = _m("mmm.core.settings")
    s = st_.settings_from_dict(copy.deepcopy(raw or {}), base_dir=_STATE["dir"],
                               features=[])
    full = s.as_dict()
    data = dict(st_.DEFAULT_DATA)
    data.update((raw or {}).get("data") or {})
    full["data"] = data
    return full


def base_config() -> Outcome:
    """The backend folder's config.yaml, every key filled."""
    def impl():
        with open(os.path.join(_STATE["dir"], "config.yaml"), encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return _full_config(raw)
    return _guarded(impl)


def default_config() -> Outcome:
    """The codebase defaults, every key filled (the fallback base)."""
    def impl():
        st_ = _m("mmm.core.settings")
        return {sec: st_.section_defaults(sec) for sec in ["data"] + list(st_.SECTIONS)}
    return _guarded(impl)


def validate_config(cfg: dict) -> Outcome:
    """Load `cfg` exactly as the job will; errors stop a run, warnings inform."""
    def impl():
        st_ = _m("mmm.core.settings")
        return st_.settings_from_dict(copy.deepcopy(cfg), base_dir=tempfile.gettempdir(),
                                      features=[]).as_dict()
    return _guarded(impl)


def units_problems(cfg: dict) -> Outcome:
    """Why generated priors would be in the wrong units under `cfg` ([] = fine)."""
    def impl():
        st_, pb = _m("mmm.core.settings"), _m("mmm.data.prior_builder")
        s = st_.settings_from_dict(copy.deepcopy(cfg), base_dir=tempfile.gettempdir(),
                                   features=[])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return pb.check_units(s.run, s.data.get("dv_aggregation") or "mean")
    return _guarded(impl)


def config_yaml(cfg: dict, only=None) -> Outcome:
    """The annotated YAML the job will run: every key (deviations marked) - or,
    with `only` ("section.key" names), just those; the job lays such a file
    over the team's config.yaml."""
    return _guarded(lambda: _m("mmm.core.settings").settings_text(
        copy.deepcopy(cfg), only=only))


def parse_config_yaml(text: str, base: dict | None = None) -> Outcome:
    """An uploaded config.yaml -> the full config dict, validated. With `base`
    (the team's config) the file is laid over it, the way the job does, so a
    file holding only some settings keeps the team's values for the rest."""
    def impl():
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ValueError("the file must be a mapping of sections "
                             "(data:, model:, run:, ...)")
        if base:
            raw = _m("mmm.app_job").merge_config(base, raw)
        return _full_config(raw)
    return _guarded(impl)


# --------------------------------------------------------------------------- #
# the datacube
# --------------------------------------------------------------------------- #
def _safe_name(name: str) -> str:
    base = os.path.basename(str(name or "file"))
    return re.sub(r"[^A-Za-z0-9._ -]", "_", base) or "file"


def _write_tmp(file_bytes: bytes, name: str, folder: str | None = None) -> str:
    folder = folder or tempfile.mkdtemp(prefix="bridge_")
    path = os.path.join(folder, _safe_name(name))
    with open(path, "wb") as fh:
        fh.write(file_bytes)
    return path


def _cols(cfg: dict) -> tuple[str, str, str]:
    run = (cfg or {}).get("run") or {}
    return (run.get("date_col") or "date", run.get("region_col") or "region",
            run.get("dv_col") or "dv")


def read_datacube(file_bytes: bytes, file_name: str, cfg: dict) -> Outcome:
    """The panel exactly as the job reads it (settings.load_panel)."""
    def impl():
        st_ = _m("mmm.core.settings")
        path = _write_tmp(file_bytes, file_name)
        c = copy.deepcopy(cfg or {})
        data = dict(c.get("data") or {})
        data.update(input_path=path, feature_priors=None, mapping_file=None,
                    share_file=None, pre_model_dir=None)
        c["data"] = data
        s = st_.settings_from_dict(c, base_dir=os.path.dirname(path), features=[])
        return st_.load_panel(s)
    return _guarded(impl)


def datacube_features(df: pd.DataFrame, cfg: dict) -> list:
    dc, rc, yc = _cols(cfg)
    return [str(c) for c in df.columns if c not in (dc, rc, yc)]


def datacube_regions(df: pd.DataFrame, cfg: dict) -> list:
    rc = _cols(cfg)[1]
    return sorted(df[rc].astype(str).unique()) if rc in df.columns else []


def check_datacube(df: pd.DataFrame, cfg: dict) -> Outcome:
    """What would STOP the run, found before upload - errors only.

    Per-variable notes (constant columns, dust, uneven periods) are left to
    the run itself: codebase 1 writes them to 00_warnings/ with the EDA, where
    they are grouped and explained, instead of a wall of names here.
    Nothing is renamed or changed: the file that is uploaded is the file that
    was checked (the old check lower-cased the first three columns in place).
    """
    def impl():
        dc, rc, yc = _cols(cfg)
        errors, warns = [], []
        cols = [str(c) for c in df.columns]
        lower = {c.lower(): c for c in cols}
        for label, key, name in (("date", "date_col", dc), ("region", "region_col", rc),
                                 ("KPI", "dv_col", yc)):
            if name not in cols:
                hint = lower.get(str(name).lower())
                if hint:
                    errors.append(
                        f"The {label} column is set to '{name}' but the file has "
                        f"'{hint}'. Rename it in the file, or set run.{key} to "
                        f"'{hint}' in Model settings.")
                else:
                    errors.append(
                        f"No '{name}' column (the {label} column, run.{key}). "
                        f"Columns in the file: {cols[:15]}"
                        f"{' ...' if len(cols) > 15 else ''}")
        if errors:
            return _Checked(None, errors, warns)

        feats = datacube_features(df, cfg)
        non_num = [c for c in feats if not pd.api.types.is_numeric_dtype(df[c])]
        if non_num:
            errors.append(f"Non-numeric feature columns (the model needs numbers): "
                          f"{non_num[:10]}")
        if not pd.api.types.is_numeric_dtype(df[yc]):
            errors.append(f"The KPI column '{yc}' is not numeric.")
        nan_cols = [c for c in [yc] + feats if df[c].isna().any()]
        if nan_cols:
            errors.append(f"Missing values in: {nan_cols[:10]}"
                          f"{' ...' if len(nan_cols) > 10 else ''}")
        dates = pd.to_datetime(df[dc], errors="coerce")
        if dates.isna().any():
            errors.append(f"{int(dates.isna().sum())} rows have a date that cannot "
                          f"be read in '{dc}' (set data.date_format if the format "
                          "is unusual).")
        dups = int(df.assign(_d=dates).duplicated([rc, "_d"]).sum())
        if dups:
            errors.append(f"{dups} duplicate region x date rows.")

        summary = {"rows": int(len(df)), "regions": datacube_regions(df, cfg),
                   "features": feats, "n_periods": int(dates.nunique()),
                   "date_min": str(dates.min().date()) if dates.notna().any() else "",
                   "date_max": str(dates.max().date()) if dates.notna().any() else ""}
        if not errors:
            try:
                st_, dp = _m("mmm.core.settings"), _m("mmm.data.data_prep")
                s = st_.settings_from_dict(copy.deepcopy(cfg),
                                           base_dir=tempfile.gettempdir(), features=[])
                _mask, holdout, plan = dp.split_train(dates, s.run)
                summary["plan"] = plan.describe()
                summary["holdout"] = int(holdout)
            except Exception:  # noqa: BLE001 - the summary is a courtesy; the
                pass           # settings block reports a bad config itself
        return _Checked(summary, errors, warns)
    return _guarded(impl)


# --------------------------------------------------------------------------- #
# the feature-prior table
# --------------------------------------------------------------------------- #
PRIOR_HELP = {
    "variable": "Must match a datacube column exactly.",
    "region": "Blank = the feature's row. A region name = an extra row that "
              "overrides that region's prior (hierarchical: mean only).",
    "pooling": "hierarchical (pool regions) | independent (own prior per "
               "region) | global (one coefficient for all). Blank = hierarchical.",
    "sign_constraint": "positive | negative | free. Blank = free.",
    "global_prior_mean": "The coefficient's prior median (see prior_mean_basis). "
                         "A magnitude (> 0) for signed features.",
    "global_prior_sd": "The prior width, read by prior_sd_basis: relative 0.2 = "
                       "+/-20%; 0.02 pins it, 0.5 lets the data speak.",
    "regional_sd_prior": "How far regions may differ (same basis as the sd).",
    "baseline": "1 = fold into the baseline instead of reporting as incremental.",
    "pillar": "Reporting group (TV & DTV, Online Media, Trade, ...).",
    "contribution_reference": "auto | zero | mean | min | a number. Reporting only.",
    "center_mode": "none | mean. Consider mean for always-on level variables "
                   "(TDP, price, ACV).",
    "scale_mode": "none | sd | mean | mean_positive | max. Blank = none.",
    "prior_sd_basis": "log | relative | absolute - how the sd columns are read.",
    "prior_mean_basis": "median | mean - what global_prior_mean is.",
    "hierarchical": "DEPRECATED 1/0 - use pooling.",
}


def prior_columns() -> Outcome:
    """Column order, kind, allowed values and help for the prior editor."""
    def impl():
        c1, pb = _m("mmm.core.config"), _m("mmm.data.prior_builder")
        options = {"pooling": list(c1.VALID_POOLING),
                   "sign_constraint": list(c1.VALID_SIGNS),
                   "center_mode": list(c1.VALID_CENTER),
                   "scale_mode": list(c1.VALID_SCALE),
                   "prior_sd_basis": list(c1.VALID_SD_BASIS),
                   "prior_mean_basis": list(c1.VALID_MEAN_BASIS)}
        numbers = ("global_prior_mean", "global_prior_sd", "regional_sd_prior")
        kinds = {}
        for col in pb.PRIOR_COLUMNS:
            kinds[col] = ("choice" if col in options else "number" if col in numbers
                          else "flag" if col == "baseline"
                          else "region" if col == "region" else "text")
        return {"columns": list(pb.PRIOR_COLUMNS), "kinds": kinds,
                "options": options, "help": dict(PRIOR_HELP)}
    return _guarded(impl)


def read_prior_file(file_bytes: bytes, file_name: str) -> Outcome:
    """A prior file as a table: CSV (any common delimiter, BOM-safe) or xlsx."""
    def impl():
        name = str(file_name).lower()
        if name.endswith((".xlsx", ".xls", ".xlsm")):
            df = pd.read_excel(io.BytesIO(file_bytes))
        else:
            text = file_bytes.decode("utf-8-sig", errors="replace")
            first = text.splitlines()[0] if text.strip() else ""
            try:
                sep = csv.Sniffer().sniff(first, delimiters=",;\t").delimiter
            except csv.Error:
                sep = ","
            df = pd.read_csv(io.StringIO(text), sep=sep)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how="all")
        if "variable" not in df.columns:
            raise ValueError(f"{file_name}: no 'variable' column - found "
                             f"{list(df.columns)[:12]}")
        return df.reset_index(drop=True)
    return _guarded(impl)


def clean_prior_table(df: pd.DataFrame) -> pd.DataFrame:
    """The table as the loader should see it: UI-only columns dropped, text
    trimmed, empty strings blank. Pure pandas - safe without the backend."""
    t = df.copy()
    t = t.drop(columns=[c for c in ("Remove", "Serial No") if c in t.columns])
    t.columns = [str(c).strip() for c in t.columns]
    def tidy(v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    for c in t.columns:
        if t[c].dtype == object or str(t[c].dtype) in ("string", "str"):
            t[c] = t[c].map(tidy).astype(object)
    if "variable" in t.columns:
        t = t[t["variable"].notna()]
    return t.reset_index(drop=True)


def prior_csv_bytes(df: pd.DataFrame) -> bytes:
    return clean_prior_table(df).to_csv(index=False).encode("utf-8")


def validate_prior_table(df: pd.DataFrame, datacube_df: pd.DataFrame | None,
                         cfg: dict) -> Outcome:
    """The loader's verdict on the table, plus the name and region checks the
    run would make - so a bad prior never reaches the cluster."""
    def impl():
        c1, pb = _m("mmm.core.config"), _m("mmm.data.prior_builder")
        table = clean_prior_table(df)
        errors, warns = [], []
        extra = [c for c in table.columns
                 if c not in pb.PRIOR_COLUMNS and c not in ("hierarchical", "center")]
        if extra:
            warns.append(f"Columns the model ignores: {extra}")
        path = os.path.join(tempfile.mkdtemp(prefix="bridge_prior_"), "priors.csv")
        table.to_csv(path, index=False)
        specs = c1.load_feature_config(path)
        names = [s.name for s in specs]
        if datacube_df is not None:
            feats = datacube_features(datacube_df, cfg)
            missing = [n for n in names if n not in feats]
            if missing:
                lines = []
                for m in missing[:20]:
                    near = difflib.get_close_matches(m, feats, n=2, cutoff=0.6)
                    lines.append(m + (f"  (did you mean {' / '.join(near)}?)" if near else ""))
                errors.append(f"{len(missing)} variable(s) are not columns of the "
                              "datacube:\n  " + "\n  ".join(lines))
            unused = [f for f in feats if f not in names]
            if unused:
                warns.append(f"{len(unused)} datacube column(s) are not in the prior "
                             f"file and will not be modelled: {unused[:12]}"
                             f"{' ...' if len(unused) > 12 else ''}")
            c1.validate_region_priors(specs, datacube_regions(datacube_df, cfg))
        n_region = int(table["region"].notna().sum()) if "region" in table else 0
        return _Checked({"features": len(specs), "region_rows": n_region,
                         "csv": table.to_csv(index=False).encode("utf-8")},
                        errors, warns)
    return _guarded(impl)


# --------------------------------------------------------------------------- #
# mapping and share files
# --------------------------------------------------------------------------- #
def _known(datacube_df, cfg, prior_variables):
    mp = _m("mmm.data.mapping")
    if prior_variables:
        return list(prior_variables), mp.PRIOR_FILE
    return (datacube_features(datacube_df, cfg) if datacube_df is not None else None,
            mp.DATACUBE)


def validate_mapping(file_bytes: bytes, file_name: str, datacube_df, cfg: dict,
                     prior_variables=None) -> Outcome:
    """The codebase's own reader: names, regions, one contribution per cell.

    Names are checked against the datacube - or, once a prior file is chosen,
    against its variables, which is the rule the run itself applies."""
    def impl():
        mp = _m("mmm.data.mapping")
        path = _write_tmp(file_bytes, file_name)
        known, against = _known(datacube_df, cfg, prior_variables)
        table = mp.load_mapping_table(path, known, against)
        if datacube_df is not None:
            table = mp.align_regions(table, datacube_regions(datacube_df, cfg),
                                     _safe_name(file_name))
        return {"table": table, "has_contribution": bool(mp.has_contribution(table)),
                "links": int(len(table)),
                "vendor_variables": int(table["vendor_variable"].nunique())}
    return _guarded(impl)


def validate_share(file_bytes: bytes, file_name: str, datacube_df, cfg: dict,
                   prior_variables=None) -> Outcome:
    def impl():
        pb = _m("mmm.data.prior_builder")
        path = _write_tmp(file_bytes, file_name)
        known, against = _known(datacube_df, cfg, prior_variables)
        table = pb.load_share_file(path, known, against)
        sections = sorted(table["section"].dropna().unique()) if "section" in table else []
        return {"table": table, "rows": int(len(table)), "sections": sections}
    return _guarded(impl)


def sample_file(kind: str) -> Outcome:
    """The backend's own sample: kind = 'mapping' | 'share'."""
    def impl():
        name = {"mapping": "mapping_sample.csv", "share": "share_sample.csv"}[kind]
        with open(os.path.join(_STATE["dir"], "samples", name), "rb") as fh:
            return fh.read()
    return _guarded(impl)


def template_file(kind: str, datacube_df: pd.DataFrame, cfg: dict) -> Outcome:
    """The sample's columns, one row per datacube variable, the rest blank.

    Mapping: fill vendor_variable (and contribution) for the rows you have and
    leave the others - rows with a blank vendor_variable are skipped. Share:
    fill section/pillar/shares for what you cover and DELETE the other rows."""
    def impl():
        name = {"mapping": "mapping_sample.csv", "share": "share_sample.csv"}[kind]
        cols = list(pd.read_csv(os.path.join(_STATE["dir"], "samples", name),
                                nrows=0).columns)
        feats = datacube_features(datacube_df, cfg)
        key = "our_variable" if kind == "mapping" else "variable"
        t = pd.DataFrame({c: ([*feats] if c == key else [""] * len(feats)) for c in cols})
        return t.to_csv(index=False).encode("utf-8")
    return _guarded(impl)


# --------------------------------------------------------------------------- #
# generating the prior file - codebase 1's pre-model step, nothing of our own
# --------------------------------------------------------------------------- #
def generate_priors(datacube: tuple, cfg: dict, mapping: tuple | None = None,
                    share: tuple | None = None,
                    restrict_to: pd.DataFrame | None = None) -> Outcome:
    """Run `prior_builder.build_priors` on the uploaded files.

    `datacube`, `mapping`, `share` are (bytes, file_name). `restrict_to` is a
    prior table whose variables become the list (the rule "the prior file may
    carry more variables than the mapping/share files, never fewer"); without
    it the datacube's columns are the list. Returns the case, every file it
    wrote as bytes, the warnings index and the printed log."""
    def impl():
        pb = _m("mmm.data.prior_builder")
        tmp = tempfile.mkdtemp(prefix="bridge_gen_")
        out = os.path.join(tmp, "pre_model_outputs")
        c = copy.deepcopy(cfg or {})
        data = dict(c.get("data") or {})
        data["input_path"] = _write_tmp(datacube[0], datacube[1], tmp)
        data["mapping_file"] = _write_tmp(mapping[0], mapping[1], tmp) if mapping else None
        data["share_file"] = _write_tmp(share[0], share[1], tmp) if share else None
        data["pre_model_dir"] = out
        data["feature_priors"] = None
        if restrict_to is not None and len(restrict_to):
            fp = os.path.join(tmp, "restrict_to.csv")
            clean_prior_table(restrict_to).to_csv(fp, index=False)
            data["feature_priors"] = fp
        c["data"] = data
        cfg_path = os.path.join(tmp, "config.yaml")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(c, fh, sort_keys=False)
        res = pb.build_priors(cfg_path)
        files = {}
        for key in ("feature_priors_national", "feature_priors_regional",
                    "prior_calculation"):
            p = res.get(key)
            if p and os.path.exists(p):
                with open(p, "rb") as fh:
                    files[os.path.basename(p)] = fh.read()
        wdir = res.get("warnings_dir") or os.path.join(out, "00_warnings")
        index_md, rows, docs = "", [], {}
        if os.path.isdir(wdir):
            idx = os.path.join(wdir, "00_INDEX.md")
            if os.path.exists(idx):
                index_md = open(idx, encoding="utf-8").read()
            table = os.path.join(wdir, "all_warnings.csv")
            if os.path.exists(table):
                rows = pd.read_csv(table).fillna("").to_dict("records")
            for f in sorted(os.listdir(wdir)):
                if f.endswith(".md") and f != "00_INDEX.md":
                    docs[f[:-3]] = open(os.path.join(wdir, f), encoding="utf-8").read()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(os.listdir(wdir)):
                    z.write(os.path.join(wdir, f), f"00_warnings/{f}")
            files["00_warnings.zip"] = buf.getvalue()
        shutil.rmtree(tmp, ignore_errors=True)
        return {"case": res.get("case"), "basis": res.get("basis"),
                "case_text": pb.CASE_TEXT.get(res.get("case"), ""),
                "files": files, "index_md": index_md,
                "warnings": rows, "warning_docs": docs}
    return _guarded(impl)


def expected_case(mapping_table=None, share_table=None) -> Outcome:
    """Which of codebase 1's four cases (a-d) the uploaded files lead to - its
    own `decide_case`, so the preview can never disagree with the builder."""
    def impl():
        pb, mp = _m("mmm.data.prior_builder"), _m("mmm.data.mapping")
        mapping = mapping_table if mapping_table is not None \
            else mp.load_mapping_table(None)
        shares = share_table if share_table is not None else pd.DataFrame()
        case = pb.decide_case(mapping, shares)
        return {"case": case, "text": pb.CASE_TEXT[case]}
    return _guarded(impl)


def doc_text(name: str) -> str:
    """A backend guide (CONFIG_GUIDE.md / FEATURE_PRIOR_GUIDE.md), '' if absent."""
    folder = _STATE["dir"]
    if not folder:
        return ""
    for p in (os.path.join(folder, "docs", name), os.path.join(folder, name)):
        if os.path.exists(p):
            return open(p, encoding="utf-8").read()
    return ""
