"""Model runs: the panel for one run, and the job's recent runs.

A run is found by its folder - Secondary Modelling/<BMC>/<run name>/, see
projects.py - and, when the Jobs API knows it, by its job run. Its panel
stays on the page until dismissed - it is no longer a popup that loses the
run when closed. While the job runs, the panel refreshes itself every 5
seconds (status, elapsed time, Cancel). When it finishes it shows the
notebook's real error or the codebase version that ran, the run's zip (its
inputs AND its outputs), the job log (job_log.txt, written by codebase 1's
mmm/app_job.py) and the key result tables. "Reuse inputs" hands the run to
the page, which loads its datacube, settings and prior (and mapping/share)
files to edit and run again.

"All recent runs" lists the job's runs from the Jobs API - every BMC, and runs
from before the run folders existed.

Output files are read from the run folder's Outputs/ (an older run:
Secondary Modelling/Outputs/<run_id>/). A file that is not there yet is NOT
remembered as missing (the old viewer cached "not found" for a run opened
before it finished, and kept saying so after it had). A storage error other
than "not found" - e.g. a permission error - is shown as it is.
"""
import io
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from src import projects
from src.files import download_folder, download_from_adls, is_not_found, zip_folder
from src.jobs import cancel_run, get_run_output, get_run_status, list_runs

TERMINAL_STATES = {"TERMINATED", "INTERNAL_ERROR", "SKIPPED"}
DONE_RESULTS = {"SUCCESS", "FAILED", "CANCELED", "TIMEDOUT", "UPSTREAM_FAILED",
                "UPSTREAM_CANCELED", "EXCLUDED", "SUCCESS_WITH_FAILURES",
                "MAXIMUM_CONCURRENT_RUNS_REACHED"}
MISS_RETRY_SECONDS = 20

RESULT_VIEWS = {
    "Warnings": "00_warnings/all_warnings.csv",
    "Convergence": "02_convergence/convergence_report.txt",
    "Fit": "04_fit/fit_metrics.csv",
    "Contributions": "05_contributions/contribution_summary.csv",
    "Coefficients": "03_coefficients/coefficient_report.csv",
    "Run info": "run_info.json",
}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
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


def _duration(run):
    start, end = (run or {}).get("start_time"), (run or {}).get("end_time")
    if not start:
        return ""
    stop = end if end else time.time() * 1000
    seconds = max(0, int((stop - start) / 1000))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _status_label(run):
    life, result = _state(run)
    if result == "SUCCESS":
        return "✅ Success"
    if result:
        return f"❌ {result.replace('_', ' ').title()}"
    if life in {"PENDING", "QUEUED", "BLOCKED", "WAITING_FOR_RETRY"}:
        return "⏳ Waiting to start"
    if life in {"RUNNING", "TERMINATING"}:
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
    return projects.make_ref(run.get("run_id"), _param(run, "bmc_name"), _param(run, "run_name"))


def fetch_run(job_run_id):
    """The run from the Jobs API; a finished run is remembered (it cannot change)."""
    if not job_run_id:
        return {}
    key = f"run_meta_{job_run_id}"
    cached = st.session_state.get(key)
    if cached and is_done(cached):
        return cached
    try:
        run = get_run_status(job_run_id)
    except Exception as e:
        return {"_error": f"Could not read the run's status: {e}"}
    if isinstance(run, dict) and is_done(run):
        st.session_state[key] = run
    return run if isinstance(run, dict) else {}


def job_state_label(job_run_id):
    """The Jobs API's state as a label, or None (for the BMC's run list)."""
    try:
        run = get_run_status(job_run_id)
    except Exception:
        return None
    return _status_label(run) if _known(run) else None


def _run_output(job_run_id):
    """runs/get-output for the run's task: the notebook's exit JSON, or its error."""
    key = f"run_output_{job_run_id}"
    if key not in st.session_state:
        try:
            st.session_state[key] = get_run_output(job_run_id) or {}
        except Exception as e:
            return {"error": f"Could not read the run output: {e}"}
    return st.session_state[key]


def read_run_file(ref, rel):
    """(bytes, None) or (None, message) for a file in the run's Outputs/.
    Found files are cached; a miss is retried after MISS_RETRY_SECONDS - the
    run may still be publishing."""
    key = f"run_file::{projects.ref_key(ref)}::{rel}"
    cached = st.session_state.get(key)
    if cached is not None:
        if cached[0] == "ok":
            return cached[1], None
        if time.time() - cached[2] < MISS_RETRY_SECONDS:
            return None, cached[1]
    folder = projects.outputs_dir(ref)
    try:
        data = download_from_adls(f"{folder}/{rel}")
        st.session_state[key] = ("ok", data, time.time())
        return data, None
    except Exception as e:
        if is_not_found(e):
            message = f"`{rel}` is not in {folder}/ (yet)."
        else:
            message = (f"Could not read `{rel}`: {e}. If this is a permission error, the "
                       "app's storage identity cannot read folders the job creates - "
                       "see changes.md, Step 1 (ADLS).")
        st.session_state[key] = ("miss", message, time.time())
        return None, message


def _run_info(ref):
    data, _problem = read_run_file(ref, "run_info.json")
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _forget_files(ref):
    key = projects.ref_key(ref)
    for k in [k for k in st.session_state if str(k).startswith(f"run_file::{key}::")]:
        del st.session_state[k]
    for tag in ("trace", "notrace"):
        st.session_state.pop(f"run_zip_{key}_{tag}", None)


# --------------------------------------------------------------------------- #
# the panel
# --------------------------------------------------------------------------- #
def render_run_panel(ref, where, on_reuse=None):
    """The panel for one run. `where` is 'current' (the run just started here,
    dismissable), 'bmc' (picked in the BMC's run list) or 'history'.
    `on_reuse(ref)`, when given, adds a "Reuse inputs" button."""
    run = fetch_run(ref.get("job_run_id"))
    if _known(run) and not is_done(run):
        _live_panel(ref, where, on_reuse)
    else:
        _static_panel(ref, where, on_reuse)


@st.fragment(run_every="5s")
def _live_panel(ref, where, on_reuse):
    run = fetch_run(ref.get("job_run_id"))
    if not _known(run) or is_done(run):
        st.rerun()                 # finished: switch to the static panel, stop polling
    _panel_body(ref, where, run, on_reuse)


@st.fragment
def _static_panel(ref, where, on_reuse):
    _panel_body(ref, where, fetch_run(ref.get("job_run_id")), on_reuse)


def _panel_body(ref, where, run, on_reuse):
    key = projects.ref_key(ref)
    running = _known(run) and not is_done(run)
    info = None if running else _run_info(ref)
    with st.container(border=True):
        if run.get("_error") and not info:
            st.warning(run["_error"])
        if _known(run):
            status = _status_label(run)
        elif info:
            status = "✅ Success" if info.get("status") == "success" else "❌ Failed"
        else:
            status = "no outputs yet"
        st.markdown(f"**{projects.ref_label(ref)}** · {status}")
        started = local_time(run.get("start_time"))
        bits = [f"Started {started}" if started else "",
                (f"took {_duration(run)}" if not running else
                 f"running for {_duration(run)} · refreshes every 5 s") if started else "",
                f"job run {ref['job_run_id']}" if projects.has_folder(ref) and ref.get("job_run_id") else ""]
        if any(bits):
            st.caption(" · ".join(b for b in bits if b))
        with st.container(horizontal=True):
            if running and st.button("Cancel run", key=f"cancel_{where}_{key}", type="secondary"):
                try:
                    response = cancel_run(ref["job_run_id"])
                    if response.ok:
                        st.toast("Cancel requested - the status updates shortly.")
                    else:
                        st.error(f"Cancel failed: status code {response.status_code}")
                except Exception as e:
                    st.error(f"Cancel failed: {e}")
            if run.get("run_page_url"):
                st.link_button("Open in Databricks", run["run_page_url"])
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

        if running:
            st.caption("The job log, the results and the run's zip appear here when "
                       "the run finishes.")
            return
        if not _known(run) and not info:
            st.caption("This run has no outputs yet. Its inputs can still be reused.")
            return

        _render_result(ref, run, info)
        _render_run_zip(ref, where)
        _render_job_log(ref, where)
        _render_results(ref, where)


def _render_result(ref, run, info):
    _life, result = _state(run)
    succeeded =(result == "SUCCESS") if _known(run) else (info or {}).get("status") == "success"
    if succeeded:
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
        with st.expander("Error details"):
            st.code("\n".join(str(trace).splitlines()[-60:]), language="text")


def _collect_run(ref, local, with_trace):
    """Download the run's inputs and outputs into `local`; returns the count."""
    exclude = () if with_trace else ("trace.nc",)
    if projects.has_folder(ref):
        # the run folder holds both: Config/ Data/ Prior/ [Mapping/ Share/] Outputs/
        return download_folder(projects.run_dir(ref["bmc"], ref["run"]), local,
                               exclude=exclude)
    # a run from before the run folders: its outputs, plus the inputs its job
    # parameters name in the shared folders
    lay = projects.layout()
    n = download_folder(projects.outputs_dir(ref), os.path.join(local, lay["output_folder"]),
                        exclude=exclude)
    params = job_params(fetch_run(ref.get("job_run_id")))
    got, _errors = projects.fetch_files(projects.run_inputs(ref, job_params=params))
    for kind, (name, data) in got.items():
        target = os.path.join(local, lay["folders"][kind], name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(data)
        n += 1
    return n


def _render_run_zip(ref, where):
    key = projects.ref_key(ref)
    cols = st.columns([2, 2, 1], vertical_alignment="center")
    with cols[0]:
        with_trace = st.checkbox("Include trace.nc", key=f"trace_{where}_{key}",
                                 help="The raw posterior (all draws) - large.")
    tag = "trace" if with_trace else "notrace"
    zip_key = f"run_zip_{key}_{tag}"
    with cols[1]:
        if st.session_state.get(zip_key) is None:
            if st.button("Prepare run zip", key=f"prep_{where}_{key}_{tag}",
                         help="The settings, datacube and prior file the run used (and "
                              "its mapping/share files), with the whole Outputs folder."):
                with st.spinner("Collecting the run's inputs and outputs from ADLS ..."):
                    local = os.path.join(".", "local", f"{key}_{tag}")
                    zip_path = local + ".zip"
                    try:
                        shutil.rmtree(local, ignore_errors=True)
                        n = _collect_run(ref, local, with_trace)
                        zip_folder(local, zip_path)
                        with open(zip_path, "rb") as f:
                            st.session_state[zip_key] = f.read()
                        st.toast(f"{n} files collected.")
                    except Exception as e:
                        st.error(f"Could not collect the run's files: {e}")
                    finally:
                        shutil.rmtree(local, ignore_errors=True)
                        if os.path.exists(zip_path):
                            os.remove(zip_path)
        if st.session_state.get(zip_key) is not None:
            name = projects.safe_file_name(projects.ref_label(ref).replace(" / ", "__"), "run")
            st.download_button("Download run (zip)", data=st.session_state[zip_key],
                               file_name=f"{name}.zip", mime="application/zip",
                               key=f"dl_{where}_{key}_{tag}", on_click="ignore")
    with cols[2]:
        if st.button("↻ Re-read files", key=f"refresh_{where}_{key}",
                     help="Read this run's files (results, job log, zip) from ADLS again - "
                          "only needed if they changed after you opened the run. A file "
                          "that was missing is looked up again by itself after 20 s."):
            _forget_files(ref)
            st.rerun(scope="fragment")


def _render_job_log(ref, where):
    key = projects.ref_key(ref)
    with st.expander("Job log"):
        data, problem = read_run_file(ref, "job_log.txt")
        if data is None:
            st.caption(problem + " Runs made with a codebase older than 2026.09.29.1 "
                       "have no job log - open the run in Databricks instead.")
            return
        text = data.decode("utf-8", errors="replace")
        with st.container(height=380):
            st.code(text, language="text")
        st.download_button("Download job_log.txt", data=data, file_name="job_log.txt",
                           key=f"dl_log_{where}_{key}", on_click="ignore")


def _render_results(ref, where):
    key = projects.ref_key(ref)
    st.markdown("**Results**")
    view = st.radio("View", list(RESULT_VIEWS), horizontal=True,
                    key=f"view_{where}_{key}", label_visibility="collapsed")
    if not view:
        return
    rel = RESULT_VIEWS[view]
    data, problem = read_run_file(ref, rel)
    if data is None:
        st.info(problem)
        return
    if view == "Warnings":
        render_warnings_table(pd.read_csv(io.BytesIO(data)).fillna(""),
                              lambda slug: read_run_file(ref, f"00_warnings/{slug}.md")[0],
                              key=f"{where}_{key}")
    elif rel.endswith(".csv"):
        st.dataframe(pd.read_csv(io.BytesIO(data)), use_container_width=True, height=380)
    elif rel.endswith(".json"):
        st.json(json.loads(data.decode("utf-8")))
    else:
        with st.container(height=380):
            st.code(data.decode("utf-8", errors="replace"), language="text")
    st.download_button(f"Download {rel.rsplit('/', 1)[-1]}", data=data,
                       file_name=rel.rsplit("/", 1)[-1], key=f"dl_{where}_{key}_{view}",
                       on_click="ignore")


def render_warnings_table(table, read_doc, key):
    """codebase 1's warnings as a table, full width, with each category's own
    explanation one click away (instead of raw markdown with dead links)."""
    if table is None or table.empty:
        st.success("No warnings.")
        return
    counts = (table.groupby(["severity", "category"]).size().reset_index(name="count")
              .sort_values(["severity", "count"], ascending=[True, False]))
    st.dataframe(counts, use_container_width=True, hide_index=True)
    categories = list(counts["category"])
    chosen = st.selectbox("Read a category", categories, key=f"warn_cat_{key}")
    if chosen:
        rows = table[table["category"] == chosen]
        cols = [c for c in ("feature", "region", "detail", "message") if c in rows.columns]
        st.dataframe(rows[cols], use_container_width=True, hide_index=True, height=260)
        doc = read_doc(chosen)
        if doc:
            with st.expander(f"What '{chosen}' means and what to do"):
                text = doc.decode("utf-8", errors="replace") if isinstance(doc, bytes) else doc
                st.markdown(text)


# --------------------------------------------------------------------------- #
# All recent runs: the job's runs from the Jobs API
# --------------------------------------------------------------------------- #
def _recent_runs(job_id):
    cached = st.session_state.get("recent_runs")
    if cached and time.time() - cached[0] < 20:
        return cached[1], None
    try:
        runs = list_runs(job_id, limit=20)
    except Exception as e:
        return [], f"Could not list the job's runs: {e}"
    st.session_state["recent_runs"] = (time.time(), runs)
    return runs, None


@st.fragment
def render_runs_section(on_reuse=None):
    with st.expander("All recent runs of the model job - every BMC, and runs from "
                     "before the run folders"):
        head, refresh = st.columns([5, 1], vertical_alignment="center")
        with head:
            st.caption("From the Jobs API: running or finished, from this session or "
                       "any other. Select one to see its status, log, results and zip.")
        with refresh:
            if st.button("↻ Refresh list", key="runs_refresh",
                         help="Ask the Jobs API for the job's runs again (otherwise at "
                              "most every 20 s)."):
                st.session_state.pop("recent_runs", None)
                st.rerun(scope="fragment")
        job_id = os.environ.get("MDR_JOB_ID", "")
        runs, problem = _recent_runs(job_id) if job_id else ([], "MDR_JOB_ID is not set.")
        if problem:
            st.warning(problem)
        chosen = None
        if runs:
            table = pd.DataFrame([{
                "run_id": str(r.get("run_id")),
                "started": local_time(r.get("start_time")),
                "status": _status_label(r),
                "duration": _duration(r),
                "bmc": _param(r, "bmc_name"),
                "run name": _param(r, "run_name"),
                "data_file": _param(r, "data_file"),
            } for r in runs])
            event = st.dataframe(table, use_container_width=True, hide_index=True,
                                 on_select="rerun", selection_mode="single-row",
                                 key="runs_table")
            rows = list(getattr(getattr(event, "selection", None), "rows", []) or [])
            if rows:
                chosen = runs[rows[0]]
        typed = (st.text_input("Or open a run by its job run ID", key="runs_typed_id") or "").strip()
        if typed:
            ref = ref_from_job_run(fetch_run(typed) or {"run_id": typed})
            if not ref.get("job_run_id"):
                ref = projects.make_ref(typed)
            render_run_panel(ref, "history", on_reuse)
        elif chosen is not None:
            render_run_panel(ref_from_job_run(chosen), "history", on_reuse)
