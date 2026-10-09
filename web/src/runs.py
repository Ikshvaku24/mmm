"""Model runs: the panel for one run, and the job's recent runs.

A run is found by its folder - Secondary Modelling/<BMC>/<run group>/<run
name>/, see projects.py - and, when the Jobs API knows it, by its job run. Its
panel stays on the page until dismissed - it is no longer a popup that loses
the run when closed. While the job runs, the panel refreshes itself every 5
seconds - how long the run has been QUEUED (another run holds the job), how
long the CLUSTER has been starting, or how long the notebook has been
running; Cancel - and shows the job log as it grows (codebase 1's
mmm/app_job.py copies job_log.txt to the run's Outputs/ every 30 s). A run's
run time is the NOTEBOOK's time only (the Jobs API's execution time), never
the queue or the cluster start. When it finishes it shows the notebook's real
error with its full traceback, or the codebase version that ran; the run's
zip (its inputs AND its outputs); the complete job log; and the results as
charts (src/charts.py): fit (the aggregate's R², or the chosen region's),
contributions (by pillar, each opened with its +), decomposition,
collinearity (the design's correlation heatmap and its VIFs) - plus the
convergence report and the warnings (each variable counted once, whatever the
number of regions), picked with a row of buttons. The modeller's note is shown
with the run - and, for app_access.yaml `edit_runs`, it can be edited and the
run renamed afterwards; a run the results were reported from carries its
badge, and "Mark as reported" (`mark_reported`) makes a finished run that. "Reuse
inputs" hands the run to the page, which loads its datacube, settings and
prior (and mapping/share) files to edit and run again. "Open in Databricks" is
for the people with full access in app_access.yaml; everyone else reads the
log here.

"All recent runs" lists the job's runs from the Jobs API - every BMC, and runs
from before the run folders existed.

Shared by every user (perf.SharedCache): a run's status (asked at most every
5 s while it runs, kept once it finished), the notebook's output, the files
read from Outputs/ and the tables parsed from them - ten people watching the
same run cost one set of API and ADLS calls, and one copy in memory.

The run's zip is built only when "Download run (zip)" is clicked - on a
separate thread, so the page never waits for it - and kept on disk, so the
next download of an unchanged run (by anyone) is instant. trace.nc, the
large raw posterior, is its own download. Nothing is held in a session.

Output files are read from the run folder's Outputs/ (an older run:
Secondary Modelling/Outputs/<run_id>/). A file that is not there yet is NOT
remembered as missing (the old viewer cached "not found" for a run opened
before it finished, and kept saying so after it had). A storage error other
than "not found" - e.g. a permission error - is shown as it is.
"""
import contextlib
import hashlib
import io
import json
import os
import posixpath
import shutil
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from src import charts, page_state, perf, projects
from src.config_editor import (has_full_access, may_delete_run, may_edit_run,
                               may_mark_reported, viewer_email)
from src.files import download_from_adls, is_not_found, list_tree_meta
from src.jobs import cancel_run, get_run_output, get_run_status, list_runs

try:
    from streamlit.errors import StreamlitAPIException
except ImportError:                  # a stand-in streamlit (the tests) has no errors module
    class StreamlitAPIException(Exception):
        pass

TERMINAL_STATES = {"TERMINATED", "INTERNAL_ERROR", "SKIPPED"}
QUEUED_STATES = {"QUEUED"}
PENDING_STATES = {"PENDING", "BLOCKED", "WAITING_FOR_RETRY", "WAITING"}
RUNNING_STATES = {"RUNNING", "TERMINATING"}
DONE_RESULTS = {"SUCCESS", "FAILED", "CANCELED", "TIMEDOUT", "UPSTREAM_FAILED",
                "UPSTREAM_CANCELED", "EXCLUDED", "SUCCESS_WITH_FAILURES",
                "MAXIMUM_CONCURRENT_RUNS_REACHED"}
MISS_RETRY_SECONDS = 20
LIVE_LOG_SECONDS = 15        # re-read the log this often while the run runs
LOG_FILE = "job_log.txt"
TRACE_FILE = "trace.nc"
RUN_STATUS_SECONDS = 5       # a running run's status: at most once per 5 s, for everyone

# shared by every session (see the module docstring)
_RUN_STATUS = perf.SharedCache(
    "run_status", ttl=lambda run: 3600 if is_done(run) else RUN_STATUS_SECONDS, maxsize=512)
_RUN_OUTPUT = perf.SharedCache("run_output", ttl=3600, maxsize=256)
_FILES = perf.SharedCache("run_files", ttl=6 * 3600, maxsize=400,
                          max_bytes=300 * 2 ** 20, copy_values=False)
_FRAMES = perf.SharedCache("run_frames", ttl=6 * 3600, maxsize=200, max_bytes=300 * 2 ** 20)
_RECENT = perf.SharedCache("recent_runs", ttl=20, maxsize=4)
_LISTINGS = perf.SharedCache("run_listing", ttl=60, maxsize=256)

# the zips (and trace.nc) built on click, kept on the app's disk
ZIP_DIR = (os.environ.get("BRIDGE_ZIP_CACHE_DIR")
           or os.path.join(tempfile.gettempdir(), "bridge_zips"))
ZIP_CACHE_BYTES = int(float(os.environ.get("BRIDGE_ZIP_CACHE_MB", "2048")) * 2 ** 20)
_BUILD_LOCKS = {}
_BUILD_GUARD = threading.Lock()

RESULT_VIEWS = ("Fit", "Contributions", "Decomposition", "Collinearity",
                "Convergence", "Warnings")
CHART_VIEWS = {"Fit", "Contributions", "Decomposition", "Collinearity"}
RESULT_FILES = {
    "fit": "04_fit/fit_metrics.csv",
    "avp": "04_fit/actual_vs_predicted.csv",
    "summary": "05_contributions/contribution_summary.csv",
    "timeseries": "05_contributions/contribution_timeseries.csv",
    "collin_matrix": "01_data/collinearity_matrix.csv",
    "collin_vif": "01_data/collinearity_vif.csv",
    "collin_summary": "01_data/collinearity_summary.csv",
    "convergence": "02_convergence/convergence_report.txt",
    "warnings": "00_warnings/all_warnings.csv",
    "warning_texts": "00_warnings/warning_texts.csv",
}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _rerun_fragment():
    """Rerun just this fragment - or the whole page when this is not the
    fragment's own rerun (Streamlit allows scope="fragment" only then; a
    full-page run reaches here too, e.g. under AppTest)."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


def _state(run):
    s = (run or {}).get("state") or {}
    return s.get("life_cycle_state", "N/A"), s.get("result_state") or ""


def is_done(run):
    life, result = _state(run)
    return life in TERMINAL_STATES or result in DONE_RESULTS


def _known(run):
    """Did the Jobs API answer for this run? (It forgets runs after 60 days.)"""
    return bool((run or {}).get("state"))


def local_time(ms):
    if not ms:
        return ""
    offset = getattr(st.context, "timezone_offset", None)
    tz = timezone(-timedelta(minutes=offset)) if isinstance(offset, int) else timezone.utc
    label = "" if isinstance(offset, int) else " UTC"
    return datetime.fromtimestamp(int(ms) / 1000, tz=tz).strftime("%Y-%m-%d %H:%M") + label


def fmt_seconds(seconds):
    """1:02:03 / 12:30 - a duration in seconds as a clock ('' when unknown)."""
    if seconds is None:
        return ""
    try:
        seconds = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _duration(run):
    """Start to end (or to now) - the whole wall time, queue and cluster
    start included. The run time shown is run_timing's notebook time."""
    start, end = (run or {}).get("start_time"), (run or {}).get("end_time")
    if not start:
        return ""
    stop = end if end else time.time() * 1000
    return fmt_seconds((stop - start) / 1000)


def _task(run):
    """The run's only task (the job runs one notebook), or None."""
    tasks = (run or {}).get("tasks") or []
    return tasks[0] if len(tasks) == 1 else None


def _phase_states(run):
    """(the job run's states, the task's states) - life-cycle and status."""
    def states(obj):
        obj = obj or {}
        return [s for s in ((obj.get("status") or {}).get("state"),
                            (obj.get("state") or {}).get("life_cycle_state")) if s]
    return states(run), states(_task(run))


def _ms_total(run, field):
    """A duration of the run in ms: the sum over its tasks (multi-task job
    runs report 0 at the top), else the run's own field."""
    tasks = (run or {}).get("tasks") or []
    parts = [t.get(field) for t in tasks if t.get(field)]
    return sum(parts) if parts else ((run or {}).get(field) or 0)


def run_timing(run, now_ms=None):
    """Where a job run's time went, from the Jobs API.

    {"phase": "queued" | "pending" | "running" | "done" | "",
     "since_ms": when the current phase began (waiting / running),
     "queue_ms", "setup_ms": time in the job queue and starting the cluster,
     "execution_ms": the NOTEBOOK's own time (finished: the API's execution
                     time; running: so far), "queue_reason": the API's words}"""
    run = run or {}
    now = now_ms if now_ms is not None else time.time() * 1000
    start = run.get("start_time") or 0
    queue = run.get("queue_duration") or 0
    setup = _ms_total(run, "setup_duration")
    out = {"phase": "", "since_ms": None, "queue_ms": queue, "setup_ms": setup,
           "execution_ms": None,
           "queue_reason": (run.get("state") or {}).get("queue_reason") or
           ((run.get("status") or {}).get("queue_details") or {}).get("message") or ""}
    if not run.get("state"):
        return out
    if is_done(run):
        execution = _ms_total(run, "execution_duration")
        out.update(phase="done", execution_ms=execution or None)
        return out
    job_states, task_states = _phase_states(run)
    task = _task(run) or {}
    if QUEUED_STATES & set(job_states + task_states):
        out.update(phase="queued", since_ms=start)
        return out
    current = (task_states or job_states or [""])[0]
    task_start = task.get("start_time") or (start + queue)
    if current in PENDING_STATES:
        out.update(phase="pending", since_ms=task_start)
        return out
    if current in RUNNING_STATES:
        begun = task_start + (task.get("setup_duration") or 0)
        out.update(phase="running", since_ms=begun, execution_ms=max(0, now - begun))
        return out
    return out


def _phase_label(timing):
    """"⏳ Queued" / "⏳ Cluster starting" / "🔄 Running" for an active run."""
    return {"queued": "⏳ Queued", "pending": "⏳ Cluster starting",
            "running": "🔄 Running"}.get(timing.get("phase"), "")


def timing_text(run, info=None, now_ms=None):
    """The panel's line about time: what the run waits for and since when
    while it waits, the notebook time so far while it runs, the notebook time
    (and what was spent waiting) once it finished."""
    now = now_ms if now_ms is not None else time.time() * 1000
    t = run_timing(run, now)
    waited = [f"queued {fmt_seconds(t['queue_ms'] / 1000)}" if t["queue_ms"] else "",
              f"cluster start {fmt_seconds(t['setup_ms'] / 1000)}" if t["setup_ms"] else ""]
    waited = " · ".join(w for w in waited if w)
    if t["phase"] == "queued":
        return (f"Queued for {fmt_seconds((now - t['since_ms']) / 1000)} - waiting for the "
                "job to be free (another run holds it)"
                + (f": {t['queue_reason']}" if t["queue_reason"] else ""))
    if t["phase"] == "pending":
        return (f"Cluster pending for {fmt_seconds((now - t['since_ms']) / 1000)} - the "
                "cluster is starting; the notebook has not begun")
    if t["phase"] == "running":
        return (f"Running for {fmt_seconds(t['execution_ms'] / 1000)} (the notebook)"
                + (f" · before that: {waited}" if waited else ""))
    seconds = (t["execution_ms"] / 1000 if t["execution_ms"]
               else (info or {}).get("seconds"))
    if seconds is None:
        return ""
    return (f"Run time {fmt_seconds(seconds)} (the notebook)"
            + (f" · waited: {waited}" if waited else ""))


def run_seconds(run, info=None):
    """The notebook's time in seconds - the Jobs API's execution time, else
    the job's own run_info.json - or None."""
    t = run_timing(run)
    if t["phase"] == "done" and t["execution_ms"]:
        return t["execution_ms"] / 1000
    return (info or {}).get("seconds")


def _status_label(run):
    life, result = _state(run)
    if result == "SUCCESS":
        return "✅ Success"
    if result:
        return f"❌ {result.replace('_', ' ').title()}"
    phase = _phase_label(run_timing(run))
    if phase:
        return phase
    if life in PENDING_STATES | QUEUED_STATES:
        return "⏳ Waiting to start"
    if life in RUNNING_STATES:
        return f"🔄 {life.title()}"
    if life in TERMINAL_STATES:
        return f"❌ {life.replace('_', ' ').title()}"
    return life.title() if life else "Unknown"


def _param(run, name):
    for p in (run or {}).get("job_parameters") or []:
        if p.get("name") == name:
            return p.get("value", p.get("default", ""))
    return ""


def job_params(run):
    return {p.get("name"): p.get("value", p.get("default", ""))
            for p in (run or {}).get("job_parameters") or [] if p.get("name")}


def ref_from_job_run(run):
    """The run as a reference: its folder when it has one, and its job run."""
    return projects.make_ref(run.get("run_id"), _param(run, "bmc_name"),
                             _param(run, "run_name"), _param(run, "run_group"))


def fetch_run(job_run_id):
    """The run from the Jobs API, shared by every session: a running run is
    asked at most every RUN_STATUS_SECONDS, a finished one is kept (it cannot
    change)."""
    if not job_run_id:
        return {}
    try:
        run, _hit = _RUN_STATUS.get_or_compute(
            str(job_run_id), lambda: get_run_status(job_run_id),
            cache_if=lambda r: isinstance(r, dict) and bool(r.get("state")))
    except Exception as e:
        return {"_error": f"Could not read the run's status: {e}"}
    return run if isinstance(run, dict) else {}


def forget_run_status(job_run_id):
    _RUN_STATUS.invalidate(str(job_run_id))


def job_state_label(job_run_id):
    """The Jobs API's state as a label, or None (for the BMC's run list) -
    for a run that waits or runs, with the time its phase began."""
    run = fetch_run(job_run_id)
    if run.get("_error") or not _known(run):
        return None
    label = _status_label(run)
    since = run_timing(run).get("since_ms")
    if not since or is_done(run):
        return label
    clock = local_time(since).split(" ")       # "2026-10-07 14:02" (+ " UTC")
    return f"{label} since {' '.join(clock[1:])}"


def job_done(job_run_id):
    """True when the Jobs API has the run finished - or no longer knows it
    (it forgets runs after 60 days); False while it runs; None when the API
    could not be asked."""
    run = fetch_run(job_run_id)
    if run.get("_error"):
        return None
    return is_done(run) if _known(run) else True


def _run_output(job_run_id):
    """runs/get-output for the run's task: the notebook's exit JSON, or its
    error. Asked only for a finished run, so it is kept for everyone."""
    try:
        out, _hit = _RUN_OUTPUT.get_or_compute(str(job_run_id),
                                               lambda: get_run_output(job_run_id) or {})
        return out
    except Exception as e:
        return {"error": f"Could not read the run output: {e}"}


def read_run_file(ref, rel, max_age=None):
    """(bytes, None) or (None, message) for a file in the run's Outputs/.

    A found file is cached - for `max_age` seconds when given (a file the job
    is still writing: the live log), otherwise until "Re-read files". A miss
    is retried after MISS_RETRY_SECONDS - the run may still be publishing.
    Anything cached by a live read (a half-written log, or "not there yet")
    is read again by the first read without `max_age`, so a finished run
    shows its complete log at once."""
    key = f"{projects.ref_key(ref)}::{rel}"
    _hit, cached = _FILES.peek(key)
    now = time.time()
    live = max_age is not None
    if cached is not None and not (len(cached) > 3 and cached[3] and not live):
        status, payload, stamp = cached[0], cached[1], cached[2]
        fresh_for = max_age if live else (MISS_RETRY_SECONDS if status == "miss"
                                          else float("inf"))
        if now - stamp < fresh_for:
            return (payload, None) if status == "ok" else (None, payload)
    folder = projects.outputs_dir(ref)
    try:
        data = download_from_adls(f"{folder}/{rel}")
        _FILES.put(key, ("ok", data, now, live))
        return data, None
    except Exception as e:
        if is_not_found(e):
            message = f"`{rel}` is not in {folder}/ (yet)."
        else:
            message = (f"Could not read `{rel}`: {e}. If this is a permission error, the "
                       "app's storage identity cannot read folders the job creates - "
                       "see changes.md, Step 1 (ADLS).")
        _FILES.put(key, ("miss", message, now, live))
        return None, message


def read_run_frame(ref, rel):
    """(DataFrame, None) or (None, message) - a CSV of a run, parsed once for
    every session (by its content; "Re-read files" forgets it)."""
    data, problem = read_run_file(ref, rel)
    if data is None:
        return None, problem
    key = f"{projects.ref_key(ref)}::{rel}::{hashlib.sha1(data).hexdigest()}"
    try:
        frame, _hit = _FRAMES.get_or_compute(key, lambda: pd.read_csv(io.BytesIO(data)))
    except Exception as e:  # noqa: BLE001 - a bad file is a message, not a crash
        return None, f"Could not read `{rel}`: {e}"
    return frame, None


def _run_info(ref):
    data, _problem = read_run_file(ref, "run_info.json")
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _forget_files(ref):
    """Read this run's files (and its file list) from ADLS again - for everyone."""
    key = projects.ref_key(ref)
    _FILES.invalidate(prefix=f"{key}::")
    _FRAMES.invalidate(prefix=f"{key}::")
    _LISTINGS.invalidate(key)


# --------------------------------------------------------------------------- #
# the panel
# --------------------------------------------------------------------------- #
def render_run_panel(ref, where, on_reuse=None):
    """The panel for one run. `where` is 'current' (the run just started here,
    dismissable), 'bmc' (picked in the BMC's run list) or 'history'.
    `on_reuse(ref)`, when given, adds a "Reuse inputs" button. A ref with a
    run's old name (it was renamed since) opens the run under its new one."""
    ref = projects.resolve_ref(ref)
    gone = projects.deleted_record(ref)
    if gone:
        _deleted_panel(ref, where, gone)
        return
    run = fetch_run(ref.get("job_run_id"))
    if _known(run) and not is_done(run):
        _live_panel(ref, where, on_reuse)
    else:
        _static_panel(ref, where, on_reuse)


def _deleted_panel(ref, where, gone):
    """A run that was deleted - its folder and files are gone; who and when."""
    key = projects.ref_key(ref)
    with st.container(border=True):
        when = str(gone.get("at") or "").replace("T", " ").rstrip("Z")
        st.markdown(f"**{projects.ref_label(ref)}** · 🗑️ deleted")
        st.caption("Deleted" + (f" by {gone['by']}" if gone.get("by") else "")
                   + (f" on {when} UTC" if when else "")
                   + " - its folder, its inputs and its outputs are gone, and its name is "
                     "not used again.")
        if where == "current" and st.button("Dismiss", key=f"dismiss_{key}"):
            st.session_state.pop("current_run", None)
            st.rerun()


@st.fragment(run_every="5s")
def _live_panel(ref, where, on_reuse):
    run = fetch_run(ref.get("job_run_id"))
    if not _known(run) or is_done(run):
        _forget_files(ref)         # anything read while it ran (the log) is stale
        st.rerun()                 # finished: switch to the static panel, stop polling
    _panel_body(ref, where, run, on_reuse)


@st.fragment
def _static_panel(ref, where, on_reuse):
    _panel_body(ref, where, fetch_run(ref.get("job_run_id")), on_reuse)


def _is_reported(ref):
    """Is this run the one its group's results were reported from?"""
    group = projects.ref_group(ref)
    if not (group and projects.has_folder(ref)):
        return False
    try:
        return projects.run_place(ref["bmc"], group, ref["run"]) == projects.reported_folder()
    except Exception:  # noqa: BLE001 - a badge is not worth an error
        return False


def _panel_body(ref, where, run, on_reuse):
    key = projects.ref_key(ref)
    running = _known(run) and not is_done(run)
    info = None if running else _run_info(ref)
    reported = _is_reported(ref)
    with st.container(border=True):
        if run.get("_error") and not info:
            st.warning(run["_error"])
        if _known(run):
            status = _status_label(run)
        elif info:
            status = "✅ Success" if info.get("status") == "success" else "❌ Failed"
        else:
            status = "no outputs yet"
        st.markdown(f"**{projects.ref_label(ref)}** · {status}"
                    + (f" · **{projects.REPORTED_BADGE}** - the results were reported "
                       "from this run" if reported else ""))
        started = local_time(run.get("start_time"))
        timing = timing_text(run, info) if _known(run) else (
            f"Run time {fmt_seconds(info['seconds'])} (the notebook)"
            if (info or {}).get("seconds") is not None else "")
        bits = [f"Started {started}" if started else "", timing,
                "refreshes every 5 s" if running else "",
                f"job run {ref['job_run_id']}" if projects.has_folder(ref) and ref.get("job_run_id") else ""]
        if any(bits):
            st.caption(" · ".join(b for b in bits if b))
        request = projects.request_of(ref)
        if request.get("note"):
            st.info(f"📝 **Note** ({request.get('submitted_by') or 'the modeller'}): "
                    f"{request['note']}")
        with st.container(horizontal=True):
            if running and st.button("Cancel run", key=f"cancel_{where}_{key}", type="secondary"):
                try:
                    response = cancel_run(ref["job_run_id"])
                    forget_run_status(ref["job_run_id"])
                    if response.ok:
                        st.toast("Cancel requested - the status updates shortly.")
                    else:
                        st.error(f"Cancel failed: status code {response.status_code}")
                except Exception as e:
                    st.error(f"Cancel failed: {e}")
            if run.get("run_page_url") and has_full_access():
                st.link_button("Open in Databricks", run["run_page_url"],
                               help="Shown to the people with full access in "
                                    "app_access.yaml.")
            if on_reuse and st.button(
                    "Reuse inputs", key=f"reuse_{where}_{key}", type="primary",
                    help="Load this run's datacube, settings and prior file (and its "
                         "mapping/share files) into the page - edit them, then run again."):
                on_reuse(ref)
            if where == "current" and st.button("Dismiss", key=f"dismiss_{key}",
                                                help="Hide this panel. The run stays in "
                                                     "its BMC's run list."):
                st.session_state.pop("current_run", None)
                st.rerun()
        _render_edit_controls(ref, where, running, request, reported)
        if not running and _succeeded(run, info):
            _render_mark_reported(ref, where, reported)

        if running:
            st.caption("The results and the run's zip appear here when the run "
                       "finishes. The job log below is copied from the cluster about "
                       "every 30 seconds.")
            _render_job_log(ref, where, live=True)
            return
        if not _known(run) and not info:
            st.caption("This run has no outputs yet. Its inputs can still be reused.")
            return

        succeeded = _succeeded(run, info)
        _render_result(ref, run, info)
        _render_run_zip(ref, where)
        _render_job_log(ref, where, expanded=not succeeded)
        if succeeded:
            _render_results(ref, where)


def _follow_rename(ref, new):
    """Point the page's remembered runs (the current run, the run open in
    the results) at the new name."""
    ss = st.session_state
    for key in ("current_run", "results_selected"):
        held = ss.get(key)
        if (held and held.get("bmc") == ref.get("bmc") and held.get("run") == ref.get("run")
                and str(held.get("group") or "") == projects.ref_group(ref)):
            ss[key] = dict(held, run=new)


def _forget_deleted(ref):
    """The page no longer remembers a deleted run as the current one or the
    one open in the results."""
    ss = st.session_state
    for key in ("current_run", "results_selected"):
        held = ss.get(key)
        if (held and held.get("bmc") == ref.get("bmc") and held.get("run") == ref.get("run")
                and str(held.get("group") or "") == projects.ref_group(ref)):
            ss.pop(key, None)
    # the run list's rows moved up by one: draw it under a new key, so its
    # selection (a row number) does not land on another run
    ss["_rf_version"] = int(ss.get("_rf_version", 0)) + 1


def _render_edit_controls(ref, where, running, request, reported=False):
    """📝 Note (any time) and ✏️ Rename (once the run has finished) - for the
    people app_access.yaml `edit_runs` names (by default the run's author and
    full_access) - and 🗑️ Delete, for those `delete_runs` names (nobody unless
    the file lists them; shipped: config_advanced_access and above)."""
    if not projects.has_folder(ref):
        return
    can_edit, can_delete = may_edit_run(request), may_delete_run(request)
    if not (can_edit or can_delete):
        return
    key = projects.ref_key(ref)
    group = projects.ref_group(ref)
    note_col, name_col, delete_col, _rest = st.columns([1, 1, 1, 2])
    if can_delete:
        with delete_col:
            _render_delete(ref, where, running, reported, key, group)
    if not can_edit:
        return
    with note_col:
        with st.popover("📝 Edit note",
                        help="Why the run was made - and, now that you have seen its "
                             "results, what happened. Saved in the run's folder "
                             "(run_request.json keeps every version, note.txt the "
                             "latest)."):
            text = st.text_area("Note", value=request.get("note", ""),
                                key=f"note_text_{where}_{key}", max_chars=projects.NOTE_MAX,
                                height=140)
            if st.button("Save note", key=f"note_save_{where}_{key}", type="primary"):
                try:
                    projects.update_note(ref["bmc"], group, ref["run"], text,
                                         user=viewer_email())
                except Exception as e:  # noqa: BLE001 - shown, the user can try again
                    st.error(f"Could not save the note: {e}")
                    return
                st.toast("Note saved.", icon="📝")
                st.rerun()
    with name_col:
        with st.popover("✏️ Rename", disabled=running,
                        help="Give the run a name that says what it is (e.g. 'mid model "
                             "result'). Its folder is renamed; the old name is kept in its "
                             "run_request.json. Not while it runs."):
            new = st.text_input("New name", value=ref["run"], key=f"rename_text_{where}_{key}")
            if st.button("Rename", key=f"rename_save_{where}_{key}", type="primary"):
                try:
                    new = projects.rename_run(ref["bmc"], group, ref["run"], new,
                                              user=viewer_email(), running=running)
                except Exception as e:  # noqa: BLE001 - shown, the user can try again
                    st.error(f"Could not rename it: {e}")
                    return
                _LISTINGS.invalidate()
                _forget_files(ref)
                _follow_rename(ref, new)
                st.toast(f"Renamed to {new}.", icon="✏️")
                st.rerun()


def _delete_now(ref, group, running, text_key):
    """Delete Run's callback - it runs BEFORE the page is drawn again, so the
    page is drawn once, without the deleted run's panel."""
    ss = st.session_state
    if str(ss.get(text_key) or "").strip() != ref["run"]:
        return
    try:
        projects.delete_run(ref["bmc"], group, ref["run"], user=viewer_email(),
                            running=running)
    except Exception as e:  # noqa: BLE001 - shown in the popover, nothing was deleted
        ss["delete_error"] = (projects.ref_key(ref), f"Could not delete it: {e}")
        return
    _LISTINGS.invalidate()
    _forget_files(ref)
    _forget_deleted(ref)
    st.toast(f"Deleted {ref['run']}.", icon="🗑️")


def _render_delete(ref, where, running, reported, key, group):
    """🗑️ Delete: the whole run folder, for good - after typing its name."""
    with st.popover("🗑️ Delete", disabled=running,
                    help="Delete this run for good: its folder in ADLS - inputs, outputs "
                         "and note. Not while it runs."):
        failed = st.session_state.get("delete_error")
        if failed and failed[0] == key:
            st.session_state.pop("delete_error", None)
            st.error(failed[1])
        # the same widgets whether or not it may go (a reported run: disabled), so
        # marking a run reported never leaves a stale widget behind
        if reported:
            st.warning(f"This is the run the results of {group} were reported from - mark "
                       "another run as reported first, then delete it.")
        else:
            st.markdown(f"Deletes **{ref['run']}** for good: its folder in ADLS - the "
                        "inputs, the outputs and the note. It cannot be undone, and its "
                        "name is never used again.")
        text_key = f"delete_text_{where}_{key}"
        typed = st.text_input("Type the run's name to confirm", key=text_key,
                              placeholder=ref["run"], disabled=reported)
        st.button("Delete this run", key=f"delete_go_{where}_{key}", type="primary",
                  disabled=reported or typed.strip() != ref["run"],
                  on_click=_delete_now, args=(ref, group, running, text_key),
                  help="Enabled once the run's name is typed above.")


def _succeeded(run, info):
    _life, result = _state(run)
    return (result == "SUCCESS") if _known(run) else (info or {}).get("status") == "success"


def _group_rows(ref):
    """The runs of the ref's group (the BMC's shared run list)."""
    try:
        rows, _errors = projects.list_runs_shared(ref["bmc"], job_state=job_state_label)
    except Exception:  # noqa: BLE001
        return []
    return [r for r in rows if r["group"] == projects.ref_group(ref)]


def _render_mark_reported(ref, where, reported):
    """"Mark as reported" for a finished run of a group - for the levels
    app_access.yaml `mark_reported` names - with a confirm step, since it
    moves folders."""
    group = projects.ref_group(ref)
    if not group or reported or not may_mark_reported():
        return
    key = projects.ref_key(ref)
    rows = _group_rows(ref)
    current = next((r["run"] for r in rows if r["reported"]), None)
    others = [r for r in rows if r["run"] != ref["run"]]
    busy = [r["run"] for r in rows if projects.is_running(r, job_done)]
    confirm_key = f"confirm_mark_{where}_{key}"
    label = ("Make this the reported run" if current else "Mark as reported")
    text = (f"instead of **{current}**" if current else
            "the run the results of this period and modelling type were reported from")
    if not st.session_state.get(confirm_key):
        if st.button(f"⭐ {label}", key=f"mark_{where}_{key}", disabled=bool(busy),
                     help=(f"Wait until every run of {group} has finished - still running: "
                           f"{', '.join(busy)}." if busy else
                           f"Marks this run as {text.replace('**', '')}. Its folder moves to "
                           f"{group}/{projects.reported_folder()}/ and the other runs of the "
                           f"group to {group}/{projects.archived_folder()}/.")):
            st.session_state[confirm_key] = True
            _rerun_fragment()
        if busy:
            st.caption(f"Marking waits until every run of {group} has finished "
                       f"(still running: {', '.join(busy)}).")
        return
    st.warning(f"Mark **{ref['run']}** as the reported run of **{group}** {text if current else ''}? "
               f"Its folder moves to {group}/{projects.reported_folder()}/"
               + (f" and the {len(others)} other run(s) of the group to "
                  f"{group}/{projects.archived_folder()}/" if others else "")
               + ". Everything stays readable and reusable here; the mark can be moved to "
                 "another run later.")
    yes, no = st.columns(2)
    with yes:
        if st.button("Yes, mark it", key=f"mark_yes_{where}_{key}", type="primary",
                     use_container_width=True):
            st.session_state.pop(confirm_key, None)
            try:
                with st.spinner("Moving the run folders ..."):
                    done = projects.mark_reported(ref["bmc"], group, ref["run"],
                                                  user=viewer_email(), running=busy)
            except Exception as e:  # noqa: BLE001 - shown, the user can try again
                st.error(f"Could not mark it: {e}")
                return
            _LISTINGS.invalidate()          # the moved runs' file lists point elsewhere now
            st.toast(f"{ref['run']} is now the reported run of {group}"
                     + (f" (was {done['previous']})" if done.get("previous") else "") + ".",
                     icon="⭐")
            st.rerun()
    with no:
        if st.button("Cancel", key=f"mark_no_{where}_{key}", use_container_width=True):
            st.session_state.pop(confirm_key, None)
            _rerun_fragment()


def _render_result(ref, run, info):
    if _succeeded(run, info):
        done = {}
        if _known(run) and ref.get("job_run_id"):
            try:
                done = json.loads((_run_output(ref["job_run_id"]).get("notebook_output") or {})
                                  .get("result") or "{}")
            except (TypeError, ValueError):
                done = {}
        done = done or info or {}
        if done.get("codebase"):
            from src import codebase
            app_version = codebase.status().get("version")
            st.success(f"Finished: codebase 1 {done['codebase']}, {done.get('seconds', '?')} s.")
            if app_version and app_version != done["codebase"]:
                st.warning(f"The job ran codebase 1 {done['codebase']} but this app has "
                           f"{app_version} loaded - codebase 1 was re-uploaded in between.")
        return
    if _known(run):
        output = _run_output(ref["job_run_id"])
        error = output.get("error") or ((run.get("state") or {}).get("state_message"))
        trace = output.get("error_trace")
    else:
        error, trace = (info or {}).get("error"), (info or {}).get("traceback")
    if error:
        st.error(f"The run stopped: {error}")
    if trace:
        text = str(trace)
        with st.expander(f"Error details - the full traceback "
                         f"({len(text.splitlines())} lines)"):
            with st.container(height=380):
                st.code(text, language="text")
    if error or trace:
        st.caption("The job log below has everything the run printed before it "
                   "stopped.")


def run_contents(ref):
    """What the run's downloads hold: [(path in the zip, ADLS path, bytes,
    modified)] - the run folder (inputs, run_request.json, Outputs/), or for
    a run from before the run folders its Outputs plus the input files its
    job parameters name. Listed at most once a minute, for everyone."""
    def build():
        lay = projects.layout()
        if projects.has_folder(ref):
            root = projects.ref_dir(ref)
            return [(rel, f"{root}/{rel}", size, mod)
                    for rel, size, mod in list_tree_meta(root)]
        out_dir = projects.outputs_dir(ref)
        items = [(f"{lay['output_folder']}/{rel}", f"{out_dir}/{rel}", size, mod)
                 for rel, size, mod in list_tree_meta(out_dir)]
        params = job_params(fetch_run(ref.get("job_run_id")))
        for kind, path in projects.run_inputs(ref, job_params=params).items():
            items.append((f"{lay['folders'][kind]}/{posixpath.basename(path)}", path, 0, ""))
        return items
    items, _hit = _LISTINGS.get_or_compute(projects.ref_key(ref), build)
    return items


def _split_trace(items):
    trace = [i for i in items if posixpath.basename(i[0]) == TRACE_FILE]
    return ([i for i in items if posixpath.basename(i[0]) != TRACE_FILE],
            trace[0] if trace else None)


def _build_lock(name):
    with _BUILD_GUARD:
        return _BUILD_LOCKS.setdefault(name, threading.Lock())


def _prune_zip_cache(keep=None):
    """Keep the disk cache under ZIP_CACHE_BYTES, newest-used first."""
    try:
        entries = [os.path.join(ZIP_DIR, f) for f in os.listdir(ZIP_DIR)]
    except FileNotFoundError:
        return
    files = sorted((p for p in entries if os.path.isfile(p) and not p.endswith(".partial")),
                   key=os.path.getmtime, reverse=True)
    total = 0
    for path in files:
        total += os.path.getsize(path)
        if total > ZIP_CACHE_BYTES and path != keep:
            with contextlib.suppress(OSError):
                os.remove(path)


def _cached_build(stamp, suffix, make):
    """The file ZIP_DIR/<stamp><suffix>, made once by `make(path)` (one build
    at a time per stamp - a second click waits for the first), as bytes."""
    os.makedirs(ZIP_DIR, exist_ok=True)
    path = os.path.join(ZIP_DIR, f"{stamp}{suffix}")
    with _build_lock(stamp):
        if not os.path.exists(path):
            make(path)
        os.utime(path)                     # newest-used survives the pruning
    _prune_zip_cache(keep=path)
    with open(path, "rb") as fh:
        return fh.read()


def _write_zip(items, path):
    tmp = tempfile.mkdtemp(prefix="bridge_zip_")
    try:
        def fetch(item):
            arc, src = item[0], item[1]
            try:
                data = download_from_adls(src)
            except Exception as e:  # noqa: BLE001
                if is_not_found(e):        # an old run's input that is gone
                    return None
                raise
            dst = os.path.join(tmp, *arc.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(data)
            return arc

        with ThreadPoolExecutor(max_workers=8) as pool:
            got = [a for a in pool.map(fetch, items) if a]
        part = path + ".partial"
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            for arc in sorted(got):
                z.write(os.path.join(tmp, *arc.split("/")), arc)
        os.replace(part, path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_run_zip(ref) -> bytes:
    """The run's zip - every file but trace.nc - built when the button is
    clicked (on Streamlit's download thread, not the page's) and kept on
    disk: the next download of the unchanged run, by anyone, is instant."""
    items, _trace = _split_trace(run_contents(ref))
    if not items:
        raise FileNotFoundError("the run has no files yet")
    stamp = perf.content_key("zip", projects.ref_key(ref),
                             [(i[0], i[2], i[3]) for i in items])

    def make(path):
        t0 = time.perf_counter()
        _write_zip(items, path)
        perf.log_timing("runs.build_zip", t0, f"{projects.ref_label(ref)} ({len(items)} files)")
    return _cached_build(stamp, ".zip", make)


def build_trace(ref) -> bytes:
    """trace.nc on its own (it is large and rarely wanted), cached on disk too."""
    _items, trace = _split_trace(run_contents(ref))
    if trace is None:
        raise FileNotFoundError("this run has no trace.nc")
    stamp = perf.content_key("trace", projects.ref_key(ref), trace[2], trace[3])

    def make(path):
        t0 = time.perf_counter()
        with open(path + ".partial", "wb") as fh:
            fh.write(download_from_adls(trace[1]))
        os.replace(path + ".partial", path)
        perf.log_timing("runs.fetch_trace", t0, projects.ref_label(ref))
    return _cached_build(stamp, ".nc", make)


def _mb(n):
    return f"{n / 2 ** 20:.1f} MB" if n >= 2 ** 20 else f"{max(n, 0) / 1024:.0f} KB"


def _render_run_zip(ref, where):
    key = projects.ref_key(ref)
    try:
        items, problem = run_contents(ref), None
    except Exception as e:  # noqa: BLE001 - shown, never fatal
        items, problem = [], f"Could not list the run's files: {e}"
    files, trace = _split_trace(items)
    name = projects.safe_file_name(projects.ref_label(ref).replace(" / ", "__"), "run")
    cols = st.columns([2, 2, 1], vertical_alignment="center")
    with cols[0]:
        st.download_button(
            "Download run (zip)", data=lambda: build_run_zip(ref), file_name=f"{name}.zip",
            mime="application/zip", key=f"dl_{where}_{key}_zip", on_click="ignore",
            disabled=not files,
            help="Built when you click, from the run's files in ADLS: the settings, "
                 "datacube and prior file it used (and its mapping/share files), "
                 "run_request.json and the whole Outputs folder - all but trace.nc. "
                 "Downloading the same run again is instant.")
        if files:
            st.caption(f"{len(files)} files · {_mb(sum(i[2] for i in files))}")
    with cols[1]:
        if trace is not None:
            st.download_button(
                "Download trace.nc", data=lambda: build_trace(ref),
                file_name=f"{name}__trace.nc", mime="application/x-netcdf",
                key=f"dl_{where}_{key}_trace", on_click="ignore",
                help="The raw posterior - every draw of every parameter. Large, so "
                     "it is not in the zip.")
            st.caption(_mb(trace[2]))
    with cols[2]:
        if st.button("↻ Re-read files", key=f"refresh_{where}_{key}",
                     help="Read this run's files (results, job log, zip) from ADLS again - "
                          "only needed if they changed after you opened the run. A file "
                          "that was missing is looked up again by itself after 20 s."):
            _forget_files(ref)
            _rerun_fragment()
    if problem:
        st.caption(problem)


def _render_job_log(ref, where, live=False, expanded=False):
    """The complete job log. While the run runs it is re-read every
    LIVE_LOG_SECONDS (the job copies it every 30 s) and its newest line is
    shown above it; the log itself scrolls."""
    key = projects.ref_key(ref)
    with st.expander("Job log" + (" - live" if live else ""), expanded=expanded or live):
        data, problem = read_run_file(ref, LOG_FILE,
                                      max_age=LIVE_LOG_SECONDS if live else None)
        if data is None:
            if live:
                st.caption("No log yet - the job writes its first copy within about 30 "
                           "seconds of starting (codebase 1 2026.09.30.1 or later).")
            elif has_full_access():
                st.caption(problem + " Runs made with a codebase older than 2026.09.29.1 "
                           "have no job log - open the run in Databricks instead.")
            else:
                st.caption(problem + " Runs made with a codebase older than 2026.09.29.1 "
                           "have no job log - ask a BRIDGE admin for the Databricks log.")
            return
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        bits = [f"{len(lines)} lines"]
        if live:
            bits.append(f"re-read every {LIVE_LOG_SECONDS} s")
            last = next((ln for ln in reversed(lines) if ln.strip()), "")
            if last:
                bits.append(f"latest: {last.strip()[:160]}")
        st.caption(" · ".join(bits))
        with st.container(height=420):
            st.code(text, language="text")
        if not live:
            st.download_button("Download job_log.txt", data=data, file_name=LOG_FILE,
                               key=f"dl_log_{where}_{key}", on_click="ignore")


def _theme():
    """"light" or "dark" - the viewer's Streamlit theme (light when unknown)."""
    theme = getattr(getattr(st, "context", None), "theme", None)
    kind = getattr(theme, "type", None)
    if kind is None and isinstance(theme, dict):
        kind = theme.get("type")
    return "dark" if kind == "dark" else "light"


def _plot(fig, key, **kw):
    return st.plotly_chart(fig, key=key, theme=None, config=charts.PLOTLY_CONFIG, **kw)


def _table(frame, ref, rel, tag, expanded=False):
    """The chart's table twin, and the file it came from."""
    name = rel.rsplit("/", 1)[-1]
    with st.expander("Table", expanded=expanded):
        st.dataframe(frame, use_container_width=True, hide_index=True, height=300)
        data, _problem = read_run_file(ref, rel)
        if data is not None:
            st.download_button(f"Download {name}", data=data, file_name=name,
                               key=f"dl_{tag}_{name}", on_click="ignore")


def _set_state(key, value):
    st.session_state[key] = value


def _view_picker(tag):
    """The result views as a row of buttons - the one shown is highlighted
    (a click target the size of a button, not a radio dot)."""
    key = f"view_{tag}"
    if st.session_state.get(key) not in RESULT_VIEWS:
        st.session_state[key] = RESULT_VIEWS[0]
    current = st.session_state[key]
    for col, view in zip(st.columns(len(RESULT_VIEWS)), RESULT_VIEWS):
        with col:
            st.button(view, key=f"{key}__{view.lower().replace(' ', '_')}",
                      type="primary" if view == current else "secondary",
                      use_container_width=True, on_click=_set_state, args=(key, view))
    return st.session_state[key]


def _render_results(ref, where):
    key = projects.ref_key(ref)
    tag = f"{where}_{key}"
    st.markdown("**Results**")
    view = _view_picker(tag)
    if not view:
        return
    plotted = charts.available()
    if view in CHART_VIEWS and not plotted:
        st.caption("Charts need the plotly package (it is in web/requirements.txt - "
                   "redeploy the app to install it). Showing the tables instead.")
    mode = _theme()
    if view == "Fit":
        _view_fit(ref, tag, mode, plotted)
    elif view == "Contributions":
        _view_contributions(ref, tag, mode, plotted)
    elif view == "Decomposition":
        _view_decomposition(ref, tag, mode, plotted)
    elif view == "Collinearity":
        _view_collinearity(ref, tag, mode, plotted)
    elif view == "Convergence":
        _view_convergence(ref, tag)
    else:
        table, problem = read_run_frame(ref, RESULT_FILES["warnings"])
        if table is None:
            st.info(problem)
            return
        texts, _problem = read_run_frame(ref, RESULT_FILES["warning_texts"])
        render_warnings_table(table.fillna(""),
                              lambda slug: read_run_file(ref, f"00_warnings/{slug}.md")[0],
                              key=tag, texts=texts)


def _view_fit(ref, tag, mode, plotted):
    fit, fit_problem = read_run_frame(ref, RESULT_FILES["fit"])
    avp, problem = read_run_frame(ref, RESULT_FILES["avp"])
    regions = charts.fit_regions(avp) if avp is not None else [charts.ALL]
    region = st.selectbox("Region", regions, key=f"fit_region_{tag}",
                          help="All regions = the aggregate: every region summed per date, "
                               "one national series. The numbers below follow the choice.")
    if fit is None:
        st.info(fit_problem)
    else:
        tiles = charts.fit_tiles(fit, region)
        for col, (label, value, help_text) in zip(st.columns(len(tiles) or 1), tiles):
            col.metric(label, value, help=help_text)
    if avp is None:
        st.info(problem)
        return
    frame, holdout = charts.fit_series(avp, region)
    if plotted:
        _plot(charts.fit_figure(frame, holdout, mode), key=f"fit_chart_{tag}")
        st.caption("Actual sales against the model's fit (the posterior median). "
                   + ("The band is the 90% prediction interval - about 9 weeks in 10 "
                      "should fall inside it. " if region != charts.ALL else
                      "Summed over the regions, so there is no band (a band cannot be "
                      "summed) - pick a region to see it. ")
                   + ("The grey block is the holdout: weeks the model never saw."
                      if holdout is not None else ""))
    _table(frame, ref, RESULT_FILES["avp"], tag, expanded=not plotted)


def _region_label(region):
    return "All regions (portfolio)" if region == charts.PORTFOLIO else region


def _view_contributions(ref, tag, mode, plotted):
    summary, problem = read_run_frame(ref, RESULT_FILES["summary"])
    if summary is None:
        st.info(problem)
        return
    regions, periods = charts.summary_choices(summary)
    c1, c2 = st.columns(2, vertical_alignment="bottom")
    region = c1.selectbox("Region", regions, key=f"contrib_region_{tag}",
                          format_func=_region_label)
    period = c2.selectbox("Period", periods, key=f"contrib_period_{tag}")
    bars, totals = charts.contribution_bars(summary, region, period)
    t1, t2, t3 = st.columns(3)
    t1.metric("Baseline core", f"{totals['core']:.1f}%",
              help="The region intercept + seasonality + trend - the sales the drivers "
                   "do not explain. A tile, not a bar, so the drivers stay readable.")
    t2.metric("Drivers (the bars)", f"{totals['drivers']:.1f}%",
              help="Every baseline feature and incremental driver in the chart.")
    t3.metric("Residual + median gap", f"{totals['other']:.1f}%",
              help="Actual - fitted (what the model cannot explain) and the "
                   "sum-of-medians gap. The three tiles add up to 100%.")
    if bars.empty:
        st.info("No drivers in this region and period.")
        return
    open_key = f"contrib_open_{tag}"
    pillars = list(charts.pillar_rows(bars)["pillar"])
    opened = [p for p in st.session_state.get(open_key) or [] if p in pillars]
    tree = charts.contribution_tree(bars, opened)
    if plotted:
        colours = charts.colour_map(charts.pillar_order(summary), mode)
        _plot(charts.contribution_tree_figure(tree, colours, mode), key=f"contrib_chart_{tag}")
        st.caption(f"Each pillar's contribution as a share of actual sales - "
                   f"{_region_label(region)}, {period}. Open a pillar with its **+** "
                   "below to see its variables (the lighter bars). Each pillar keeps its "
                   "colour in every region, period and chart"
                   + (f"; {charts.UNASSIGNED} = the variables the prior file gives no "
                      "pillar." if charts.UNASSIGNED in pillars else "."))
    _pillar_tree(bars, opened, pillars, open_key, tag)
    _table(bars.iloc[::-1].rename(columns={"pct": "contribution_pct"}), ref,
           RESULT_FILES["summary"], tag, expanded=False)


def _toggle_pillar(open_key, pillar):
    opened = list(st.session_state.get(open_key) or [])
    st.session_state[open_key] = ([p for p in opened if p != pillar] if pillar in opened
                                  else opened + [pillar])


def _pillar_tree(bars, opened, pillars, open_key, tag):
    """The pillars as rows - press one (its +) to list its variables under it."""
    first, second, _rest = st.columns([1, 1, 4])
    with first:
        st.button("Open all", key=f"contrib_open_all_{tag}", use_container_width=True,
                  on_click=_set_state, args=(open_key, list(pillars)),
                  disabled=len(opened) == len(pillars))
    with second:
        st.button("Close all", key=f"contrib_close_all_{tag}", use_container_width=True,
                  on_click=_set_state, args=(open_key, []), disabled=not opened)
    for p in charts.pillar_rows(bars).itertuples(index=False):
        is_open = p.pillar in opened
        digest = hashlib.sha1(str(p.pillar).encode()).hexdigest()[:8]
        st.button(f"{p.pillar}  ·  {charts.pct_label(p.pct)} of sales  ·  "
                  f"{p.n} variable{'s' if p.n != 1 else ''}",
                  key=f"ctree_{tag}_{digest}", use_container_width=True,
                  icon=":material/remove:" if is_open else ":material/add:",
                  on_click=_toggle_pillar, args=(open_key, p.pillar),
                  help=("Hide" if is_open else "Show") + f" the variables of {p.pillar}.")
        if is_open:
            members = bars[bars["pillar"] == p.pillar].sort_values("pct", ascending=False)
            st.dataframe(pd.DataFrame({"variable": members["feature"],
                                       "% of sales": members["pct"].round(3),
                                       "volume": members["volume"].round(0)}),
                         use_container_width=True, hide_index=True,
                         height=min(36 * (len(members) + 1) + 2, 320))


def _view_decomposition(ref, tag, mode, plotted):
    ts, problem = read_run_frame(ref, RESULT_FILES["timeseries"])
    if ts is None:
        st.info(problem + " (codebase 1 writes it unless output.contribution_timeseries "
                "is switched off.)")
        return
    summary, _problem = read_run_frame(ref, RESULT_FILES["summary"])
    pillars = charts.pillar_order(summary if summary is not None else ts)
    regions = [charts.ALL] + sorted(ts["region"].astype(str).unique())
    c1, c2 = st.columns([2, 3], vertical_alignment="bottom")
    region = c1.selectbox("Region", regions, key=f"decomp_region_{tag}")
    drivers_only = c2.toggle("Drivers only", key=f"decomp_drivers_{tag}",
                             help="Leave out the baseline core and the actual-sales "
                                  "line, so the drivers fill the chart.")
    wide, actual = charts.decomposition_frame(ts, pillars, region)
    if drivers_only:
        wide = wide.drop(columns=[charts.BASELINE_CORE], errors="ignore")
        actual = actual.iloc[0:0]
    if plotted:
        colours = charts.colour_map(pillars, mode)
        _plot(charts.decomposition_figure(wide, actual, colours, mode),
              key=f"decomp_chart_{tag}")
        st.caption("Sales each week, split into the baseline core (grey) and each "
                   "pillar's contribution; negative contributions sit below zero. Click "
                   "a legend entry to hide or show it.")
    table = wide.copy()
    if len(actual):
        table["Actual sales"] = actual
    _table(table.reset_index(), ref, RESULT_FILES["timeseries"], tag, expanded=not plotted)


def _safe_name(name) -> str:
    """codebase 1's file-name rule for a region (collinearity_heatmap_<region>.png)."""
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name))


def _view_collinearity(ref, tag, mode, plotted):
    """How tangled the model's inputs are, per region - the correlation between
    every pair of the design's columns (features, seasonality, trend) as a
    heatmap, and each variable's VIF - from 01_data/collinearity_*.csv."""
    matrix, _p1 = read_run_frame(ref, RESULT_FILES["collin_matrix"])
    vif, _p2 = read_run_frame(ref, RESULT_FILES["collin_vif"])
    summary, _p3 = read_run_frame(ref, RESULT_FILES["collin_summary"])
    if matrix is None and vif is None and summary is None:
        st.info("This run has no collinearity files (01_data/collinearity_*.csv) - "
                "output.collinearity was switched off.")
        return
    regions = charts.collinearity_regions(summary, vif, matrix)
    if not regions:
        st.info("The collinearity files have no rows.")
        return
    region = st.selectbox("Region", regions, key=f"collin_region_{tag}",
                          help="Collinearity is measured per region, on the model's own "
                               "design (the training weeks).")
    tiles = charts.collinearity_tiles(summary, region)
    for col, (label, value, help_text) in zip(st.columns(len(tiles) or 1), tiles):
        col.metric(label, value, help=help_text)
    if summary is not None and "note" in summary:
        notes = summary.loc[summary["region"].astype(str) == region, "note"]
        if len(notes) and str(notes.iloc[0]).strip() not in ("", "nan"):
            st.caption(str(notes.iloc[0]))

    st.markdown("**Correlation between the model's columns**")
    if matrix is not None:
        c1, c2 = st.columns(2, vertical_alignment="bottom")
        count = c1.selectbox("Columns", ["The 25 most correlated", "All"],
                             key=f"collin_count_{tag}")
        features_only = c2.toggle("Features only (no seasonality, no trend)",
                                  key=f"collin_feats_{tag}")
        labels, grid = charts.correlation_grid(
            matrix, region, top=25 if count != "All" else None, with_extras=not features_only)
        if plotted and labels:
            _plot(charts.correlation_heatmap_figure(labels, grid, mode),
                  key=f"collin_heat_{tag}")
            st.caption("Red = move together, blue = move opposite, grey = unrelated. A "
                       "cell at |r| ≥ 0.8 carries its number: two such columns are "
                       "identified only as a sum - drop one, combine them into one "
                       "variable, or hold one with a tight prior. A block of red cells is "
                       "a group moving together; a feature red with the seasonality or "
                       "trend is timed with them.")
        pairs = charts.strongest_pairs(matrix, region, limit=15,
                                       with_extras=not features_only)
        pairs = pairs.assign(column_a=pairs["column_a"].map(charts.design_label),
                             column_b=pairs["column_b"].map(charts.design_label))
        _table(pairs[["column_a", "column_b", "correlation"]], ref,
               RESULT_FILES["collin_matrix"], f"{tag}_pairs", expanded=not plotted)
    else:
        png, _problem = read_run_file(ref, f"01_data/collinearity_heatmap_{_safe_name(region)}.png")
        if png is not None:
            st.image(png, caption="The heatmap codebase 1 drew (this run has no "
                                  "collinearity_matrix.csv - it predates 2026.10.09.1).")
        else:
            st.caption("No correlation matrix for this run (codebase 1 2026.10.09.1 or "
                       "later writes one).")

    st.markdown("**Variance inflation (VIF)**")
    points = charts.vif_points(vif, region)
    if plotted and points["vif"].notna().any():
        _plot(charts.vif_figure(points, mode), key=f"collin_vif_{tag}")
        st.caption("How much each variable's coefficient uncertainty is inflated by the "
                   "others: 1 = none, above 5 worth a look, above 10 the coefficient is "
                   "barely identified by the data. The hover says what explains it. A "
                   "blank VIF (in the table) could not be computed - its note says why.")
    if not points.empty:
        shown = points.assign(column=points["column"].map(charts.design_label))
        _table(shown, ref, RESULT_FILES["collin_vif"], f"{tag}_vif", expanded=not plotted)


def _view_convergence(ref, tag):
    data, problem = read_run_file(ref, RESULT_FILES["convergence"])
    if data is None:
        st.info(problem)
        return
    st.caption("R-hat < 1.01 and ESS > 400 pass; divergences should be 0 (under 1% of "
               "draws is tolerable); a saturated tree depth means the sampler ran out of "
               "room. The warnings at the end name the parameters to look at.")
    with st.container(height=420):
        st.code(data.decode("utf-8", errors="replace"), language="text")
    st.download_button("Download convergence_report.txt", data=data,
                       file_name="convergence_report.txt", key=f"dl_{tag}_convergence",
                       on_click="ignore")


def render_warnings_table(table, read_doc, key, texts=None):
    """codebase 1's warnings: one row per category - how many DIFFERENT
    variables (one warned in five regions counts once), in how many regions,
    how many warnings - then, for a chosen category, what it says
    (warning_texts.csv) and each variable with its regions, and the
    category's own explanation one click away."""
    if table is None or table.empty:
        st.success("No warnings.")
        return
    summary = charts.warning_summary(table)
    st.dataframe(summary, use_container_width=True, hide_index=True,
                 column_config={
                     "variables": st.column_config.NumberColumn(
                         "variables", help="How many different variables - one warned in "
                                           "several regions counts once."),
                     "regions": st.column_config.NumberColumn(
                         "regions", help="In how many regions."),
                     "warnings": st.column_config.NumberColumn(
                         "warnings", help="How many warnings in all (a variable x region "
                                          "is one each).")})
    chosen = st.selectbox("Read a category", list(summary["category"]), key=f"warn_cat_{key}")
    if chosen:
        for example in charts.warning_examples(texts, chosen):
            st.caption(f"“{example}”")
        detail = charts.warning_detail(table, chosen)
        st.dataframe(detail, use_container_width=True, hide_index=True,
                     height=min(36 * (len(detail) + 1) + 2, 300))
        doc = read_doc(chosen)
        if doc:
            with st.expander(f"What '{chosen}' means and what to do"):
                text = doc.decode("utf-8", errors="replace") if isinstance(doc, bytes) else doc
                st.markdown(text)


# --------------------------------------------------------------------------- #
# All recent runs: the job's runs from the Jobs API
# --------------------------------------------------------------------------- #
def _recent_runs(job_id):
    """The job's 20 latest runs, asked at most every 20 s for everyone."""
    try:
        runs, _hit = _RECENT.get_or_compute(str(job_id), lambda: list_runs(job_id, limit=20))
    except Exception as e:
        return [], f"Could not list the job's runs: {e}"
    return runs, None


def _reported_in_group(bmc, group, run):
    if not (bmc and group and run):
        return False
    try:
        return projects.run_place(bmc, group, run) == projects.reported_folder()
    except Exception:  # noqa: BLE001
        return False


def _recent_row(r):
    """One row of All recent runs: the run time is the notebook's alone; the
    time spent queued and starting the cluster is its own column."""
    t = run_timing(r)
    if t["phase"] == "done":
        run_time = fmt_seconds(t["execution_ms"] / 1000) if t["execution_ms"] else ""
    elif t["phase"] in ("queued", "pending"):
        run_time = "not started"
    elif t["phase"] == "running":
        run_time = f"{fmt_seconds(t['execution_ms'] / 1000)} so far"
    else:
        run_time = ""
    waited = (t["queue_ms"] or 0) + (t["setup_ms"] or 0)
    bmc, group, name = (_param(r, "bmc_name"), _param(r, "run_group"),
                        _param(r, "run_name"))
    if bmc and name:
        try:
            now = projects.current_run_name(bmc, group, name)
            gone = now in projects.deleted_runs(bmc, group)
        except Exception:  # noqa: BLE001 - the name it ran under will do
            now, gone = name, False
        name = name if now == name else f"{now} (was {name})"
        if gone:
            name += " (deleted)"
    return {"run_id": str(r.get("run_id")),
            "started": local_time(r.get("start_time")),
            "status": _status_label(r),
            "run time": run_time,
            "waited": fmt_seconds(waited / 1000) if waited else "",
            "bmc": bmc, "period · type": group, "run name": name,
            "reported": (projects.REPORTED_BADGE
                         if _reported_in_group(bmc, group,
                                               name.split(" (was ")[0].split(" (deleted)")[0])
                         else ""),
            "data_file": _param(r, "data_file")}


@st.fragment
def render_runs_section(on_reuse=None):
    with st.expander("All recent runs of the model job - every BMC, and runs from "
                     "before the run folders"):
        head, refresh = st.columns([5, 1], vertical_alignment="center")
        with head:
            st.caption("From the Jobs API: running or finished, from this session or "
                       "any other. Select one to see its status, log, results and zip. "
                       "Run time is the notebook's own; waited = queued + cluster start.")
        with refresh:
            if st.button("↻ Refresh list", key="runs_refresh",
                         help="Ask the Jobs API for the job's runs again (otherwise at "
                              "most every 20 s)."):
                _RECENT.invalidate()
                _rerun_fragment()
        job_id = os.environ.get("MDR_JOB_ID", "")
        runs, problem = _recent_runs(job_id) if job_id else ([], "MDR_JOB_ID is not set.")
        if problem:
            st.warning(problem)
        chosen = None
        if runs:
            table = pd.DataFrame([_recent_row(r) for r in runs])
            event = st.dataframe(table, use_container_width=True, hide_index=True,
                                 on_select="rerun", selection_mode="single-row",
                                 key="runs_table")
            rows = list(getattr(getattr(event, "selection", None), "rows", []) or [])
            ss = st.session_state
            if page_state.emptied("runs_table", rows):
                ss.pop("recent_selected", None)        # the person cleared the selection
            if rows:
                ss["recent_selected"] = str(runs[rows[0]].get("run_id"))
            # a run picked before the page was left stays open on the way back
            held = ss.get("recent_selected")
            chosen = next((r for r in runs if str(r.get("run_id")) == held), None)
        typed = (st.text_input("Or open a run by its job run ID", key="runs_typed_id") or "").strip()
        if typed:
            ref = ref_from_job_run(fetch_run(typed) or {"run_id": typed})
            if not ref.get("job_run_id"):
                ref = projects.make_ref(typed)
            render_run_panel(ref, "history", on_reuse)
        elif chosen is not None:
            render_run_panel(ref_from_job_run(chosen), "history", on_reuse)
