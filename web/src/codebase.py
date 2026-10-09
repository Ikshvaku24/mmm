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
REFRESH_SECONDS - on a background thread once the app has started it
(`start_background_refresh`), so no user's click waits for the Workspace API -
and whenever someone opens or reloads the page (`check_now`, at most once per
RECHECK_SECONDS for everyone together). When anything in the copy changed it
is downloaded again and every later call uses the new code - re-uploading
codebase 1 to the workspace is all it takes; the app does not need
redeploying. The config editor and the prior table are
built from the backend's own schema, so a key added to codebase 1 appears in
the app by itself.

Every call here
  * returns an Outcome(ok, value, errors, warnings, log), and catches
    SystemExit as well as exceptions (the mapping and share readers stop with
    SystemExit, which would otherwise end the Streamlit script run);
  * is cached for EVERY user by its content and the backend copy (perf.py):
    the same datacube, prior table or config is checked once, not once per
    session or per refresh;
  * runs either in a WORKER PROCESS - the heavy ones (_HEAVY: reading and
    checking the datacube, checking the prior/mapping/share files, generating
    priors) - or in this process under ONE lock (the quick ones). Codebase 1
    captures warnings and printing for the whole process and Streamlit runs
    each session as a thread, so in-process calls must queue; a worker is its
    own process, so heavy calls run side by side and never hold the lock that
    everyone else's clicks wait on. Workers are BRIDGE_WORKERS (2) plain
    `python -m src.worker_main` processes fed over a pipe (0 turns them off) -
    deliberately not multiprocessing, whose "spawn" re-runs __main__, which in
    a Streamlit app is the page script itself.

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

from src import perf

# the first version whose app_access.yaml takes `all` under a level (everyone
# signed in has that level, never a higher one) and in the lists of levels;
# 2026.10.09.2 brought modelling_types.yaml (each type's name and the settings
# it sets - applied by the app AND the job) and `delete_runs`, 2026.10.09.1
# `edit_runs` and
# 01_data/collinearity_matrix.csv, 2026.10.07.1 the run groups (the job
# parameter run_group), the four access levels and the standard lists,
# 2026.09.30.1 app_access.yaml and the partial run configs, 2026.09.29.2 the
# run folders
MIN_CODEBASE = "2026.10.09.3"
REFRESH_SECONDS = 300
WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIBLING = os.path.normpath(os.path.join(WEB_DIR, "..", "codebase1_hierarchical_mmm"))


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


# a page (re)load re-checks codebase 1 - at most this often for everyone together
RECHECK_SECONDS = _env_int("BRIDGE_RECHECK_SECONDS", 15)
# worker processes for the heavy calls (0 = everything in this process)
WORKERS = _env_int("BRIDGE_WORKERS", 2)
WORKER_TIMEOUT = _env_int("BRIDGE_WORKER_TIMEOUT", 600)
_HEAVY = frozenset({"read_datacube", "check_datacube", "validate_prior_table",
                    "validate_mapping", "validate_share", "generate_priors"})
# results that depend only on the backend copy (kept longer)
_STATIC = frozenset({"schema", "layout", "base_config", "default_config",
                     "prior_columns", "sample_file", "standard_names"})

# what the app needs from the backend folder (app_access.yaml: who may do what;
# bmc_names.csv and modelling_types.yaml: the standard BMC names, and the
# modelling types with the settings each one sets - optional, and part of the
# fingerprint, so editing one reaches the app like any other re-upload)
_NEEDED_FILES = ("config.yaml", "app_access.yaml", "bmc_names.csv",
                 "modelling_types.yaml")
_NEEDED_DIRS = ("mmm", "samples")
_NEEDED_DOCS = ("CONFIG_GUIDE.md", "FEATURE_PRIOR_GUIDE.md")

# _LOCK guards in-process calls into codebase 1 (and the swap to a new copy);
# _REFRESH_LOCK guards checking/downloading the source. Order: _REFRESH_LOCK
# before _LOCK, never the other way - nothing holds _LOCK while it checks.
_LOCK = threading.RLock()
_REFRESH_LOCK = threading.Lock()
_BG_LOCK = threading.Lock()
_STATE = {"dir": None, "source": "", "where": "", "fingerprint": None,
          "checked": 0.0}
_REFRESHER = {"thread": None}
_CACHE = perf.SharedCache("codebase", ttl=1800, maxsize=256, max_bytes=256 * 2 ** 20)


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


def _refresher_alive() -> bool:
    t = _REFRESHER.get("thread")
    return REFRESH_SECONDS > 0 and t is not None and t.is_alive()


def _ensure_loaded(force: bool = False, from_refresher: bool = False,
                   recheck_after: float | None = None) -> None:
    """Load codebase 1 the first time; afterwards re-check its source every
    REFRESH_SECONDS - on the background thread when it runs, so no user waits
    for the Workspace API, otherwise on the next call that finds it stale.
    `recheck_after` (check_now): re-check if the last check is older than
    that many seconds, whatever the refresher does. A re-check downloads and
    loads the copy again only when it changed. Never called while holding
    _LOCK (see the lock order above)."""
    limit = REFRESH_SECONDS if recheck_after is None else recheck_after
    if not force and _STATE["dir"]:
        if time.time() - _STATE["checked"] < limit:
            return
        if recheck_after is None and not from_refresher and _refresher_alive():
            return
    with _REFRESH_LOCK:
        now = time.time()
        if not force and _STATE["dir"] and now - _STATE["checked"] < limit:
            return                          # another thread has just checked
        t0 = time.perf_counter()
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
                _download_all(listing, folder)     # slow - but no call is blocked
        perf.log_timing("codebase.check_source", t0, kind)
        with _LOCK:                                # swap only between calls
            _STATE["checked"] = now
            if fp != _STATE["fingerprint"] or folder != _STATE["dir"] or force:
                _STATE.update(source=kind, where=where)
                _activate(folder)
                _STATE["fingerprint"] = fp


def check_now(min_seconds: float | None = None) -> bool:
    """Re-check codebase 1's source NOW - someone opened or reloaded the page,
    so an app_access.yaml or CSV just re-uploaded applies to them at once -
    unless it was checked in the last `min_seconds` (RECHECK_SECONDS) by
    anyone. Returns True when a different copy was loaded. Never raises: a
    broken source is reported by the calls themselves."""
    before = _STATE["fingerprint"]
    try:
        _ensure_loaded(recheck_after=RECHECK_SECONDS if min_seconds is None
                       else min_seconds)
    except Exception as e:  # noqa: BLE001
        print(f"[codebase] re-checking codebase 1 for a page load failed: {e}", flush=True)
        return False
    return _STATE["fingerprint"] != before


def start_background_refresh(warm_workers: bool = True) -> bool:
    """Keep codebase 1 fresh on a daemon thread (every REFRESH_SECONDS) and
    start the worker processes ahead of the first heavy click. Safe to call
    on every script run - only the first call starts anything."""
    with _BG_LOCK:
        t = _REFRESHER.get("thread")
        if t is not None and t.is_alive():
            return False
        t = threading.Thread(target=_refresh_loop, args=(warm_workers,),
                             name="bridge-codebase-refresh", daemon=True)
        _REFRESHER["thread"] = t
        t.start()
        return True


def _refresh_loop(warm_workers: bool) -> None:
    try:
        _ensure_loaded(from_refresher=True)
        if warm_workers:
            warm_workers_now()
    except Exception as e:  # noqa: BLE001 - the calls report it themselves
        print(f"[codebase] loading codebase 1 failed: {e}", flush=True)
    while True:
        time.sleep(max(5.0, float(REFRESH_SECONDS)))
        try:
            _ensure_loaded(from_refresher=True)
        except Exception as e:  # noqa: BLE001
            print(f"[codebase] background re-check of codebase 1 failed: {e}", flush=True)


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


@dataclass
class _Checked:
    value: object = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _capture(fn, *args, **kwargs) -> Outcome:
    """Run one codebase call, catching what it prints, warns and raises.
    No lock: in this process the caller holds _LOCK; a worker process runs
    one call at a time anyway."""
    out, buf = Outcome(), io.StringIO()
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


def _load_error(e) -> Outcome:
    return Outcome(ok=False, errors=[f"codebase 1 could not be loaded: {e}"])


def _guarded(fn, *args, **kwargs) -> Outcome:
    """One codebase call in THIS process, under the lock."""
    try:
        _ensure_loaded()
    except Exception as e:  # noqa: BLE001
        return _load_error(e)
    with _LOCK:
        if not _STATE["dir"]:
            return _load_error("no backend folder is active")
        return _capture(fn, *args, **kwargs)


# --------------------------------------------------------------------------- #
# worker processes
# --------------------------------------------------------------------------- #
# Each worker is its own `python -m src.worker_main` process, fed requests over
# a pipe (src/worker_main.py). NOT multiprocessing's "spawn" pool: that starts a
# worker by re-running the program's __main__, and inside a Streamlit app
# __main__ is the app script itself - Streamlit replaces sys.modules["__main__"]
# with app.py on every run - so every worker would have run the whole page.
_POOL = {"pool": None, "disabled": "", "crashes": []}
CRASH_LIMIT = 3              # this many worker failures in CRASH_WINDOW s: workers off
CRASH_WINDOW = 600
_POOL_LOCK = threading.Lock()
_IN_WORKER = os.environ.get("BRIDGE_WORKER_PROCESS") == "1"
_RAN = {}                    # (call, "worker" | "in-process" | "cache") -> count
_RAN_LOCK = threading.Lock()


class _NoPool(Exception):
    """No worker could take the call - run it in this process instead."""


class _WorkerBroken(Exception):
    """The worker died or its reply was unreadable (it is replaced)."""


class _WorkerTimeout(Exception):
    """No answer (or no free worker) within WORKER_TIMEOUT."""


def _read_exact(stream, n: int) -> bytes:
    data = stream.read(n)
    if data is None or len(data) < n:
        raise EOFError("the worker process closed its pipe")
    return data


class _WorkerPool:
    """`size` long-lived worker processes; a call borrows an idle one."""

    def __init__(self, size: int):
        import queue
        self.size = size
        self.closed = False
        self._idle = queue.Queue()
        self._all = set()
        self._guard = threading.Lock()
        for _ in range(size):
            self._idle.put(self._spawn())

    def _spawn(self):
        import subprocess
        env = dict(os.environ, BRIDGE_WORKER_PROCESS="1")
        env["PYTHONPATH"] = os.pathsep.join(
            [WEB_DIR] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
        proc = subprocess.Popen([sys.executable, "-m", "src.worker_main"], cwd=WEB_DIR,
                                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        with self._guard:
            self._all.add(proc)
        return proc

    def _retire(self, proc) -> None:
        """Kill a worker that misbehaved and start a fresh one in its place."""
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait(timeout=5)
        for pipe in (proc.stdin, proc.stdout):
            with contextlib.suppress(Exception):
                pipe.close()
        with self._guard:
            self._all.discard(proc)
        if not self.closed:
            try:
                self._idle.put(self._spawn())
            except Exception as e:  # noqa: BLE001 - the pool runs one short
                print(f"[codebase] could not start a replacement worker: {e}", flush=True)

    def alive(self) -> int:
        with self._guard:
            return sum(1 for p in self._all if p.poll() is None)

    def call(self, request, timeout: float):
        import pickle
        import queue
        import struct
        payload = pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL)  # may raise: caller falls back
        try:
            proc = self._idle.get(timeout=timeout)
        except queue.Empty:
            raise _WorkerTimeout("no worker became free") from None
        box = {}

        def talk():
            try:
                proc.stdin.write(struct.pack(">Q", len(payload)))
                proc.stdin.write(payload)
                proc.stdin.flush()
                size = struct.unpack(">Q", _read_exact(proc.stdout, 8))[0]
                box["reply"] = pickle.loads(_read_exact(proc.stdout, size))
            except BaseException as e:  # noqa: BLE001
                box["error"] = e

        talker = threading.Thread(target=talk, daemon=True)
        talker.start()
        talker.join(timeout)
        if talker.is_alive():
            self._retire(proc)                    # killing it ends the read
            raise _WorkerTimeout(f"no answer within {timeout:.0f} s")
        if "error" in box:
            self._retire(proc)
            raise _WorkerBroken(f"{type(box['error']).__name__}: {box['error']}")
        self._idle.put(proc)
        status, value = box["reply"]
        if status != "ok":                        # the worker is fine; the request was not
            raise RuntimeError(value)
        return value

    def close(self) -> None:
        self.closed = True
        with self._guard:
            procs = list(self._all)
            self._all.clear()
        for p in procs:
            with contextlib.suppress(Exception):
                p.stdin.close()
            with contextlib.suppress(Exception):
                p.wait(timeout=2)
            with contextlib.suppress(Exception):
                p.kill()


def workers_active() -> bool:
    return WORKERS > 0 and not _IN_WORKER and not _POOL["disabled"]


def worker_status() -> dict:
    """For the app's backend line: are the workers running, and if not, why."""
    if _IN_WORKER:
        return {"on": False, "reason": "this is a worker", "size": 0, "alive": 0}
    if WORKERS <= 0:
        return {"on": False, "reason": "BRIDGE_WORKERS=0", "size": 0, "alive": 0}
    if _POOL["disabled"]:
        return {"on": False, "reason": _POOL["disabled"], "size": WORKERS, "alive": 0}
    pool = _POOL["pool"]
    if pool is None:
        return {"on": False, "reason": "not started yet", "size": WORKERS, "alive": 0}
    return {"on": True, "reason": "", "size": pool.size, "alive": pool.alive()}


def ran_counts() -> dict:
    """{(call, where): count} - where each codebase call was answered."""
    with _RAN_LOCK:
        return dict(_RAN)


def _pool():
    if not workers_active():
        return None
    with _POOL_LOCK:
        if _POOL["disabled"]:
            return None
        if _POOL["pool"] is None:
            _POOL["pool"] = _WorkerPool(WORKERS)
            import atexit
            atexit.register(_POOL["pool"].close)
            print(f"[codebase] {WORKERS} worker processes for the heavy codebase 1 calls",
                  flush=True)
        return _POOL["pool"]


def _note_failure(reason: str, disable: bool = False) -> None:
    with _POOL_LOCK:
        now = time.time()
        _POOL["crashes"] = [t for t in _POOL["crashes"] if now - t < CRASH_WINDOW] + [now]
        if len(_POOL["crashes"]) >= CRASH_LIMIT and not disable:
            disable = True
            reason = (f"{reason} - {CRASH_LIMIT} failures in {CRASH_WINDOW // 60} min, "
                      "so the heavy calls run in the app process from now on")
        pool = None
        if disable:
            _POOL["disabled"] = reason
            pool, _POOL["pool"] = _POOL["pool"], None
    if pool is not None:
        pool.close()
    print(f"[codebase] worker {'processes switched off' if disable else 'replaced'}: "
          f"{reason}", flush=True)


def _worker_init() -> None:
    global _IN_WORKER
    _IN_WORKER = True


def _worker_activate(folder: str) -> None:
    if _STATE["dir"] != folder:
        _STATE.update(source="worker", where=folder)
        _activate(folder)


def _worker_task(folder: str, name: str, args, kwargs) -> Outcome:
    """Runs INSIDE a worker process: codebase 1 from `folder`, one call."""
    try:
        _worker_activate(folder)
    except Exception as e:  # noqa: BLE001
        return _load_error(e)
    return _capture(_IMPLS[name], *args, **kwargs)


def _worker_handle(request):
    """A worker's answer to one request from the app (src/worker_main.py)."""
    if request[0] == "ping":
        _worker_activate(request[1])
        time.sleep(0.3)      # keep this worker busy so the next ping reaches another
        return os.getpid()
    _kind, folder, name, args, kwargs = request
    return _worker_task(folder, name, args, kwargs)


def warm_workers_now() -> int:
    """Start the workers and load codebase 1 into them (the first heavy click
    would otherwise wait for that). Returns how many answered; says in the
    app's log why when the workers are off."""
    try:
        pool = _pool()
    except Exception as e:  # noqa: BLE001 - e.g. processes cannot be started here
        _note_failure(f"the worker processes could not start: {e}", disable=True)
        return 0
    folder = _STATE["dir"]
    if pool is None or not folder:
        print(f"[codebase] worker processes are off: {worker_status()['reason']} - "
              "the heavy codebase 1 calls run in the app process", flush=True)
        return 0
    pids, errors = set(), []

    def ping():
        try:
            pids.add(pool.call(("ping", folder), 120))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=ping) for _ in range(pool.size)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    if errors:
        print(f"[codebase] warming the workers: {errors[0]}", flush=True)
    print(f"[codebase] {len(pids)} of {pool.size} worker processes ready", flush=True)
    return len(pids)


def _in_worker(name: str, args, kwargs) -> Outcome:
    try:
        pool = _pool()
    except Exception as e:  # noqa: BLE001 - e.g. processes cannot be started here
        _note_failure(f"the worker processes could not start: {e}", disable=True)
        raise _NoPool() from e
    folder = _STATE["dir"]
    if pool is None or not folder:
        raise _NoPool()
    try:
        return pool.call(("call", folder, name, args, kwargs), WORKER_TIMEOUT)
    except _WorkerTimeout:
        return Outcome(ok=False, errors=[
            f"This step ({name}) took longer than {WORKER_TIMEOUT} s - try again. If "
            "it keeps happening the file may be too large for the app."])
    except _WorkerBroken as e:
        _note_failure(f"a worker process stopped during {name} ({e})")
        raise _NoPool() from e
    except Exception as e:  # noqa: BLE001 - e.g. arguments a process cannot take
        print(f"[codebase] {name} could not go to a worker ({type(e).__name__}: {e}); "
              "running it in the app process", flush=True)
        raise _NoPool() from e


# --------------------------------------------------------------------------- #
# the one entry point of every public call
# --------------------------------------------------------------------------- #
def _call(name: str, *args, **kwargs) -> Outcome:
    """Shared cache -> worker process (heavy calls) -> this process."""
    t0 = time.perf_counter()
    try:
        _ensure_loaded()
    except Exception as e:  # noqa: BLE001
        return _load_error(e)
    fp = _STATE["fingerprint"]
    ran = ["cache"]

    def compute():
        if name in _HEAVY:
            try:
                out = _in_worker(name, args, kwargs)
                ran[0] = "worker"
                return out
            except _NoPool:
                pass
        ran[0] = "in-process"
        return _guarded(_IMPLS[name], *args, **kwargs)

    def worth_keeping(out):
        return fp is not None and not (
            out.errors and str(out.errors[0]).startswith("codebase 1 could not be loaded"))

    out, _hit = _CACHE.get_or_compute(perf.content_key(name, fp, args, kwargs), compute,
                                      ttl=3600 if name in _STATIC else 1800,
                                      cache_if=worth_keeping)
    with _RAN_LOCK:
        _RAN[(name, ran[0])] = _RAN.get((name, ran[0]), 0) + 1
    perf.log_timing(f"codebase.{name}", t0, ran[0])
    return out


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def status(force: bool = False) -> dict:
    """Which backend the app is using. Never raises."""
    try:
        _ensure_loaded(force=force)
        with _LOCK:
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
def _impl_schema():
    st_, aj = _m("mmm.core.settings"), _m("mmm.app_job")
    access_file = os.path.join(_STATE["dir"], st_.ACCESS_FILE)
    try:
        access = dict(st_.app_access(access_file), error="")
    except ValueError as e:
        # a file that cannot be read fixes every setting and grants nobody
        # full access - it never opens anything
        access = {"full_access": [], "config_full_access": [],
                  "config_advanced_access": [], "editable": [], "advanced": [],
                  "show_fixed": True, "mark_reported": [], "edit_runs": [],
                  "delete_runs": [], "unknown": [], "source": access_file,
                  "error": str(e)}
    return {"rows": st_.config_schema(), "job_owned": list(aj.JOB_OWNED_KEYS),
            "folders": dict(aj.FOLDERS), "output_folder": aj.OUTPUT_FOLDER,
            "sections": ["data"] + list(st_.SECTIONS),
            "blurbs": dict(st_.SECTION_BLURB), "access": access}


def schema() -> Outcome:
    """The backend's config schema, which keys/folders the job owns, and who
    may do what in the app (`access`, from app_access.yaml)."""
    return _call("schema")


def _impl_layout():
    aj = _m("mmm.app_job")
    return {"folders": dict(aj.FOLDERS), "output_folder": aj.OUTPUT_FOLDER,
            "shared_folders": list(aj.SHARED_FOLDERS),
            "run_request": aj.RUN_REQUEST, "name_pattern": aj.NAME_PATTERN,
            "group_pattern": aj._GROUP_RE.pattern,
            "reported_folder": aj.REPORTED_FOLDER, "archived_folder": aj.ARCHIVED_FOLDER}


def layout() -> Outcome:
    """Where a run's files live - the job's own folder names and name rules
    (BMC / run group / run), so the app and the job can never disagree about a
    path."""
    return _call("layout")


def _impl_standard_names():
    aj, st_ = _m("mmm.app_job"), _m("mmm.core.settings")
    out, problems = {}, []
    for key, file_name, read in (
            ("bmc_names", aj.BMC_NAMES_FILE, lambda p: aj.read_names(p, "bmc_name")),
            ("modelling_types", aj.MODELLING_TYPES_FILE, aj.read_modelling_types)):
        path = os.path.join(_STATE["dir"], file_name)
        try:
            out[key] = read(path) if os.path.exists(path) else []
            if not os.path.exists(path):
                problems.append(f"{file_name} is not in codebase 1's folder")
        except (OSError, ValueError) as e:
            out[key] = []
            problems.append(str(e))
    try:
        out["type_settings"] = st_.modelling_type_settings(
            os.path.join(_STATE["dir"], aj.MODELLING_TYPES_FILE),
            job_owned=aj.JOB_OWNED_KEYS)
    except (OSError, ValueError) as e:
        out["type_settings"] = {}
        problems.append(str(e))
    out["problems"] = problems
    return out


def standard_names() -> Outcome:
    """The standard BMC names and modelling types - codebase 1's
    bmc_names.csv and modelling_types.yaml: {bmc_names, modelling_types,
    type_settings ({type: {section: {key: value}}} - what each type sets),
    problems}."""
    return _call("standard_names")


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


def _impl_base_config():
    with open(os.path.join(_STATE["dir"], "config.yaml"), encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return _full_config(raw)


def base_config() -> Outcome:
    """The backend folder's config.yaml, every key filled."""
    return _call("base_config")


def _impl_default_config():
    st_ = _m("mmm.core.settings")
    return {sec: st_.section_defaults(sec) for sec in ["data"] + list(st_.SECTIONS)}


def default_config() -> Outcome:
    """The codebase defaults, every key filled (the fallback base)."""
    return _call("default_config")


def _impl_validate_config(cfg):
    st_ = _m("mmm.core.settings")
    return st_.settings_from_dict(copy.deepcopy(cfg), base_dir=tempfile.gettempdir(),
                                  features=[]).as_dict()


def validate_config(cfg: dict) -> Outcome:
    """Load `cfg` exactly as the job will; errors stop a run, warnings inform."""
    return _call("validate_config", cfg)


def _impl_units_problems(cfg):
    st_, pb = _m("mmm.core.settings"), _m("mmm.data.prior_builder")
    s = st_.settings_from_dict(copy.deepcopy(cfg), base_dir=tempfile.gettempdir(),
                               features=[])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pb.check_units(s.run, s.data.get("dv_aggregation") or "mean")


def units_problems(cfg: dict) -> Outcome:
    """Why generated priors would be in the wrong units under `cfg` ([] = fine)."""
    return _call("units_problems", cfg)


def _impl_config_yaml(cfg, only=None):
    return _m("mmm.core.settings").settings_text(copy.deepcopy(cfg), only=only)


def config_yaml(cfg: dict, only=None) -> Outcome:
    """The annotated YAML the job will run: every key (deviations marked) - or,
    with `only` ("section.key" names), just those; the job lays such a file
    over the team's config.yaml."""
    return _call("config_yaml", cfg, only=only)


def _impl_parse_config_yaml(text, base=None):
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ValueError("the file must be a mapping of sections "
                         "(data:, model:, run:, ...)")
    if base:
        raw = _m("mmm.app_job").merge_config(base, raw)
    return _full_config(raw)


def parse_config_yaml(text: str, base: dict | None = None) -> Outcome:
    """An uploaded config.yaml -> the full config dict, validated. With `base`
    (the team's config) the file is laid over it, the way the job does, so a
    file holding only some settings keeps the team's values for the rest."""
    return _call("parse_config_yaml", text, base=base)


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


def _impl_read_datacube(file_bytes, file_name, cfg):
    st_ = _m("mmm.core.settings")
    folder = tempfile.mkdtemp(prefix="bridge_")
    try:
        path = _write_tmp(file_bytes, file_name, folder)
        c = copy.deepcopy(cfg or {})
        data = dict(c.get("data") or {})
        data.update(input_path=path, feature_priors=None, mapping_file=None,
                    share_file=None, pre_model_dir=None)
        c["data"] = data
        s = st_.settings_from_dict(c, base_dir=folder, features=[])
        return st_.load_panel(s)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def read_datacube(file_bytes: bytes, file_name: str, cfg: dict) -> Outcome:
    """The panel exactly as the job reads it (settings.load_panel)."""
    return _call("read_datacube", file_bytes, file_name, cfg)


def datacube_features(df: pd.DataFrame, cfg: dict) -> list:
    dc, rc, yc = _cols(cfg)
    return [str(c) for c in df.columns if c not in (dc, rc, yc)]


def datacube_regions(df: pd.DataFrame, cfg: dict) -> list:
    rc = _cols(cfg)[1]
    return sorted(df[rc].astype(str).unique()) if rc in df.columns else []


def _impl_check_datacube(df, cfg):
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


def check_datacube(df: pd.DataFrame, cfg: dict) -> Outcome:
    """What would STOP the run, found before upload - errors only.

    Per-variable notes (constant columns, dust, uneven periods) are left to
    the run itself: codebase 1 writes them to 00_warnings/ with the EDA, where
    they are grouped and explained, instead of a wall of names here.
    Nothing is renamed or changed: the file that is uploaded is the file that
    was checked (the old check lower-cased the first three columns in place).
    """
    return _call("check_datacube", df, cfg)


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


def _impl_prior_columns():
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


def prior_columns() -> Outcome:
    """Column order, kind, allowed values and help for the prior editor."""
    return _call("prior_columns")


def _impl_read_prior_file(file_bytes, file_name):
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


def read_prior_file(file_bytes: bytes, file_name: str) -> Outcome:
    """A prior file as a table: CSV (any common delimiter, BOM-safe) or xlsx."""
    return _call("read_prior_file", file_bytes, file_name)


def clean_prior_table(df: pd.DataFrame) -> pd.DataFrame:
    """The table as the loader should see it: UI-only columns dropped, text
    trimmed, empty strings blank. Pure pandas - safe without the backend."""
    t = df.copy()
    t = t.drop(columns=[c for c in ("Keep", "Remove", "Serial No") if c in t.columns])
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


def _impl_validate_prior_table(df, datacube_df, cfg):
    c1, pb = _m("mmm.core.config"), _m("mmm.data.prior_builder")
    table = clean_prior_table(df)
    errors, warns = [], []
    extra = [c for c in table.columns
             if c not in pb.PRIOR_COLUMNS and c not in ("hierarchical", "center")]
    if extra:
        warns.append(f"Columns the model ignores: {extra}")
    folder = tempfile.mkdtemp(prefix="bridge_prior_")
    try:
        path = os.path.join(folder, "priors.csv")
        table.to_csv(path, index=False)
        specs = c1.load_feature_config(path)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
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


def validate_prior_table(df: pd.DataFrame, datacube_df: pd.DataFrame | None,
                         cfg: dict) -> Outcome:
    """The loader's verdict on the table, plus the name and region checks the
    run would make - so a bad prior never reaches the cluster."""
    return _call("validate_prior_table", df, datacube_df, cfg)


# --------------------------------------------------------------------------- #
# mapping and share files
# --------------------------------------------------------------------------- #
def _known(datacube_df, cfg, prior_variables):
    mp = _m("mmm.data.mapping")
    if prior_variables:
        return list(prior_variables), mp.PRIOR_FILE
    return (datacube_features(datacube_df, cfg) if datacube_df is not None else None,
            mp.DATACUBE)


def _impl_validate_mapping(file_bytes, file_name, datacube_df, cfg, prior_variables=None):
    mp = _m("mmm.data.mapping")
    folder = tempfile.mkdtemp(prefix="bridge_")
    try:
        path = _write_tmp(file_bytes, file_name, folder)
        known, against = _known(datacube_df, cfg, prior_variables)
        table = mp.load_mapping_table(path, known, against)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if datacube_df is not None:
        table = mp.align_regions(table, datacube_regions(datacube_df, cfg),
                                 _safe_name(file_name))
    return {"table": table, "has_contribution": bool(mp.has_contribution(table)),
            "links": int(len(table)),
            "vendor_variables": int(table["vendor_variable"].nunique())}


def validate_mapping(file_bytes: bytes, file_name: str, datacube_df, cfg: dict,
                     prior_variables=None) -> Outcome:
    """The codebase's own reader: names, regions, one contribution per cell.

    Names are checked against the datacube - or, once a prior file is chosen,
    against its variables, which is the rule the run itself applies."""
    return _call("validate_mapping", file_bytes, file_name, datacube_df, cfg,
                 prior_variables=prior_variables)


def _impl_validate_share(file_bytes, file_name, datacube_df, cfg, prior_variables=None):
    pb = _m("mmm.data.prior_builder")
    folder = tempfile.mkdtemp(prefix="bridge_")
    try:
        path = _write_tmp(file_bytes, file_name, folder)
        known, against = _known(datacube_df, cfg, prior_variables)
        table = pb.load_share_file(path, known, against)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    sections = sorted(table["section"].dropna().unique()) if "section" in table else []
    return {"table": table, "rows": int(len(table)), "sections": sections}


def validate_share(file_bytes: bytes, file_name: str, datacube_df, cfg: dict,
                   prior_variables=None) -> Outcome:
    return _call("validate_share", file_bytes, file_name, datacube_df, cfg,
                 prior_variables=prior_variables)


def _impl_sample_file(kind):
    name = {"mapping": "mapping_sample.csv", "share": "share_sample.csv"}[kind]
    with open(os.path.join(_STATE["dir"], "samples", name), "rb") as fh:
        return fh.read()


def sample_file(kind: str) -> Outcome:
    """The backend's own sample: kind = 'mapping' | 'share'."""
    return _call("sample_file", kind)


def _impl_template_file(kind, datacube_df, cfg):
    name = {"mapping": "mapping_sample.csv", "share": "share_sample.csv"}[kind]
    cols = list(pd.read_csv(os.path.join(_STATE["dir"], "samples", name),
                            nrows=0).columns)
    feats = datacube_features(datacube_df, cfg)
    key = "our_variable" if kind == "mapping" else "variable"
    t = pd.DataFrame({c: ([*feats] if c == key else [""] * len(feats)) for c in cols})
    return t.to_csv(index=False).encode("utf-8")


def template_file(kind: str, datacube_df: pd.DataFrame, cfg: dict) -> Outcome:
    """The sample's columns, one row per datacube variable, the rest blank.

    Mapping: fill vendor_variable (and contribution) for the rows you have and
    leave the others - rows with a blank vendor_variable are skipped. Share:
    fill section/pillar/shares for what you cover and DELETE the other rows."""
    return _call("template_file", kind, datacube_df, cfg)


# --------------------------------------------------------------------------- #
# generating the prior file - codebase 1's pre-model step, nothing of our own
# --------------------------------------------------------------------------- #
def _impl_generate_priors(datacube, cfg, mapping=None, share=None, restrict_to=None):
    pb = _m("mmm.data.prior_builder")
    tmp = tempfile.mkdtemp(prefix="bridge_gen_")
    try:
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
        index_md, rows, docs, texts = "", [], {}, []
        if os.path.isdir(wdir):
            idx = os.path.join(wdir, "00_INDEX.md")
            if os.path.exists(idx):
                with open(idx, encoding="utf-8") as fh:
                    index_md = fh.read()
            table = os.path.join(wdir, "all_warnings.csv")
            if os.path.exists(table):
                rows = pd.read_csv(table).fillna("").to_dict("records")
            table = os.path.join(wdir, "warning_texts.csv")
            if os.path.exists(table):
                texts = pd.read_csv(table).fillna("").to_dict("records")
            for f in sorted(os.listdir(wdir)):
                if f.endswith(".md") and f != "00_INDEX.md":
                    with open(os.path.join(wdir, f), encoding="utf-8") as fh:
                        docs[f[:-3]] = fh.read()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(os.listdir(wdir)):
                    z.write(os.path.join(wdir, f), f"00_warnings/{f}")
            files["00_warnings.zip"] = buf.getvalue()
        return {"case": res.get("case"), "basis": res.get("basis"),
                "case_text": pb.CASE_TEXT.get(res.get("case"), ""),
                "files": files, "index_md": index_md,
                "warnings": rows, "warning_texts": texts, "warning_docs": docs}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def generate_priors(datacube: tuple, cfg: dict, mapping: tuple | None = None,
                    share: tuple | None = None,
                    restrict_to: pd.DataFrame | None = None) -> Outcome:
    """Run `prior_builder.build_priors` on the uploaded files.

    `datacube`, `mapping`, `share` are (bytes, file_name). `restrict_to` is a
    prior table whose variables become the list (the rule "the prior file may
    carry more variables than the mapping/share files, never fewer"); without
    it the datacube's columns are the list. Returns the case, every file it
    wrote as bytes, the warnings index and the printed log."""
    return _call("generate_priors", datacube, cfg, mapping=mapping, share=share,
                 restrict_to=restrict_to)


def _impl_expected_case(mapping_table=None, share_table=None):
    pb, mp = _m("mmm.data.prior_builder"), _m("mmm.data.mapping")
    mapping = mapping_table if mapping_table is not None \
        else mp.load_mapping_table(None)
    shares = share_table if share_table is not None else pd.DataFrame()
    case = pb.decide_case(mapping, shares)
    return {"case": case, "text": pb.CASE_TEXT[case]}


def expected_case(mapping_table=None, share_table=None) -> Outcome:
    """Which of codebase 1's four cases (a-d) the uploaded files lead to - its
    own `decide_case`, so the preview can never disagree with the builder."""
    return _call("expected_case", mapping_table, share_table)


def doc_text(name: str) -> str:
    """A backend guide (CONFIG_GUIDE.md / FEATURE_PRIOR_GUIDE.md), '' if absent."""
    folder = _STATE["dir"]
    if not folder:
        return ""
    for p in (os.path.join(folder, "docs", name), os.path.join(folder, name)):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return fh.read()
    return ""


# every call a worker process (or _call) can run, by name
_IMPLS = {
    "schema": _impl_schema, "layout": _impl_layout, "base_config": _impl_base_config,
    "default_config": _impl_default_config, "validate_config": _impl_validate_config,
    "units_problems": _impl_units_problems, "config_yaml": _impl_config_yaml,
    "parse_config_yaml": _impl_parse_config_yaml, "read_datacube": _impl_read_datacube,
    "check_datacube": _impl_check_datacube, "prior_columns": _impl_prior_columns,
    "read_prior_file": _impl_read_prior_file,
    "validate_prior_table": _impl_validate_prior_table,
    "validate_mapping": _impl_validate_mapping, "validate_share": _impl_validate_share,
    "sample_file": _impl_sample_file, "template_file": _impl_template_file,
    "generate_priors": _impl_generate_priors, "expected_case": _impl_expected_case,
    "standard_names": _impl_standard_names,
}
