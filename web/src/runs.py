"""Model runs: the panel for one run, and the job's recent runs.

A run is found by its folder - Secondary Modelling/<BMC>/<run name>/, see
projects.py - and, when the Jobs API knows it, by its job run. Its panel
stays on the page until dismissed - it is no longer a popup that loses the
run when closed. While the job runs, the panel refreshes itself every 5
seconds (status, elapsed time, Cancel) and shows the job log as it grows
(codebase 1's mmm/app_job.py copies job_log.txt to the run's Outputs/ every
30 s). When it finishes it shows the notebook's real error with its full
traceback, or the codebase version that ran; the run's zip (its inputs AND its
outputs); the complete job log; and the results as charts (src/charts.py):
fit, contributions, decomposition, prior vs posterior - plus the convergence
report and the warnings. "Reuse inputs" hands the run to the page, which loads
its datacube, settings and prior (and mapping/share) files to edit and run
again. "Open in Databricks" is for the people with full access in
app_access.yaml; everyone else reads the log here.

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

from src import charts, projects
from src.config_editor import has_full_access
from src.files import download_folder, download_from_adls, is_not_found, zip_folder
from src.jobs import cancel_run, get_run_output, get_run_status, list_runs

try:
    from streamlit.errors import StreamlitAPIException
except ImportError:                  # a stand-in streamlit (the tests) has no errors module
    class StreamlitAPIException(Exception):
        pass

TERMINAL_STATES = {"TERMINATED", "INTERNAL_ERROR", "SKIPPED"}
DONE_RESULTS = {"SUCCESS", "FAILED", "CANCELED", "TIMEDOUT", "UPSTREAM_FAILED",
                "UPSTREAM_CANCELED", "EXCLUDED", "SUCCESS_WITH_FAILURES",
                "MAXIMUM_CONCURRENT_RUNS_REACHED"}
MISS_RETRY_SECONDS = 20
LIVE_LOG_SECONDS = 15        # re-read the log this often while the run runs
LOG_FILE = "job_log.txt"

RESULT_VIEWS = ("Fit", "Contributions", "Decomposition", "Prior vs posterior",
                "Convergence", "Warnings")
CHART_VIEWS = {"Fit", "Contributions", "Decomposition", "Prior vs posterior"}
RESULT_FILES = {
    "fit": "04_fit/fit_metrics.csv",
    "avp": "04_fit/actual_vs_predicted.csv",
    "summary": "05_contributions/contribution_summary.csv",
    "timeseries": "05_contributions/contribution_timeseries.csv",
    "contraction": "02_convergence/prior_posterior_contraction.csv",
    "convergence": "02_convergence/convergence_report.txt",
    "warnings": "00_warnings/all_warnings.csv",
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


def read_run_file(ref, rel, max_age=None):
    """(bytes, None) or (None, message) for a file in the run's Outputs/.

    A found file is cached - for `max_age` seconds when given (a file the job
    is still writing: the live log), otherwise until "Re-read files". A miss
    is retried after MISS_RETRY_SECONDS - the run may still be publishing.
    Anything cached by a live read (a half-written log, or "not there yet")
    is read again by the first read without `max_age`, so a finished run
    shows its complete log at once."""
    key = f"run_file::{projects.ref_key(ref)}::{rel}"
    cached = st.session_state.get(key)
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
        st.session_state[key] = ("ok", data, now, live)
        return data, None
    except Exception as e:
        if is_not_found(e):
            message = f"`{rel}` is not in {folder}/ (yet)."
        else:
            message = (f"Could not read `{rel}`: {e}. If this is a permission error, the "
                       "app's storage identity cannot read folders the job creates - "
                       "see changes.md, Step 1 (ADLS).")
        st.session_state[key] = ("miss", message, now, live)
        return None, message


def read_run_frame(ref, rel):
    """(DataFrame, None) or (None, message) - a CSV of a finished run, parsed
    once per session (forgotten with its bytes by "Re-read files")."""
    key = f"run_file::{projects.ref_key(ref)}::{rel}::frame"
    cached = st.session_state.get(key)
    if cached is not None:
        return cached, None
    data, problem = read_run_file(ref, rel)
    if data is None:
        return None, problem
    try:
        frame = pd.read_csv(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001 - a bad file is a message, not a crash
        return None, f"Could not read `{rel}`: {e}"
    st.session_state[key] = frame
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
        _forget_files(ref)         # anything read while it ran (the log) is stale
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


def _succeeded(run, info):
    _life, result = _state(run)
    return (result == "SUCCESS") if _known(run) else (info or {}).get("status") == "success"


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
            _rerun_fragment()


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


def _render_results(ref, where):
    key = projects.ref_key(ref)
    tag = f"{where}_{key}"
    st.markdown("**Results**")
    view = st.radio("View", list(RESULT_VIEWS), horizontal=True, key=f"view_{tag}",
                    label_visibility="collapsed")
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
    elif view == "Prior vs posterior":
        _view_prior_posterior(ref, tag, mode, plotted)
    elif view == "Convergence":
        _view_convergence(ref, tag)
    else:
        table, problem = read_run_frame(ref, RESULT_FILES["warnings"])
        if table is None:
            st.info(problem)
            return
        render_warnings_table(table.fillna(""),
                              lambda slug: read_run_file(ref, f"00_warnings/{slug}.md")[0],
                              key=tag)


def _view_fit(ref, tag, mode, plotted):
    fit, problem = read_run_frame(ref, RESULT_FILES["fit"])
    if fit is None:
        st.info(problem)
    else:
        tiles = charts.fit_tiles(fit)
        for col, (label, value, help_text) in zip(st.columns(len(tiles) or 1), tiles):
            col.metric(label, value, help=help_text)
    avp, problem = read_run_frame(ref, RESULT_FILES["avp"])
    if avp is None:
        st.info(problem)
        return
    region = st.selectbox("Region", charts.fit_regions(avp), key=f"fit_region_{tag}")
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
    c1, c2, c3 = st.columns([2, 2, 2], vertical_alignment="bottom")
    region = c1.selectbox("Region", regions, key=f"contrib_region_{tag}",
                          format_func=_region_label)
    period = c2.selectbox("Period", periods, key=f"contrib_period_{tag}")
    level = c3.radio("Show", ["By variable", "By pillar"], horizontal=True,
                     key=f"contrib_level_{tag}")
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
    shown = charts.by_pillar(bars) if level == "By pillar" else bars
    if plotted:
        colours = charts.colour_map(charts.pillar_order(summary), mode)
        _plot(charts.contribution_figure(shown, colours, mode), key=f"contrib_chart_{tag}")
        st.caption(f"Each driver's contribution as a share of actual sales - "
                   f"{_region_label(region)}, {period}. The colours are the pillars; "
                   "each keeps its colour in every region, period and chart.")
    _table(shown.iloc[::-1].rename(columns={"pct": "contribution_pct"}), ref,
           RESULT_FILES["summary"], tag, expanded=not plotted)


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


def _clicked(event):
    """The `key` (customdata[0]) of the point clicked on a chart, or None."""
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")
    points = (selection.get("points") if isinstance(selection, dict)
              else getattr(selection, "points", None)) or []
    for p in points:
        data = p.get("customdata") if isinstance(p, dict) else None
        if data is not None and len(data):
            return str(data[0])
    return None


def _view_prior_posterior(ref, tag, mode, plotted):
    contr, problem = read_run_frame(ref, RESULT_FILES["contraction"])
    if contr is None:
        st.info(problem)
        return
    points = charts.contraction_points(contr)
    if points.empty:
        st.info("prior_posterior_contraction.csv has no coefficient rows to show.")
        return
    keys = list(points["key"])
    labels = dict(zip(points["key"], points["label"]))
    pick_key, click_key, ver_key = f"pp_pick_{tag}", f"pp_click_{tag}", f"pp_ver_{tag}"
    if st.session_state.get(pick_key) not in keys:
        worst = points[charts.flagged(points, limit=1)]
        st.session_state[pick_key] = worst["key"].iloc[0] if len(worst) else keys[0]
    if plotted:
        event = _plot(charts.contraction_figure(points, st.session_state[pick_key], mode),
                      key=f"pp_chart_{tag}_{st.session_state.get(ver_key, 0)}",
                      on_select="rerun", selection_mode="points")
        clicked = _clicked(event)
        if clicked in labels and clicked != st.session_state.get(click_key):
            st.session_state[click_key] = clicked
            st.session_state[pick_key] = clicked
            _rerun_fragment()                   # redraw with the new point highlighted
        st.caption("One point per variable: how much the data sharpened its prior "
                   "(contraction - further right, more learned) and how far it moved "
                   "from the prior mean (shift, in prior sds). Points left of 0.2 or "
                   "beyond ±2 are labelled - read those first. **Click a point** (or "
                   "pick it below) to see its prior and posterior.")

    def _picked():
        # a pick from the list starts a fresh chart, so clicking the point that
        # was clicked before still registers
        st.session_state[ver_key] = st.session_state.get(ver_key, 0) + 1
        st.session_state[click_key] = None

    st.selectbox("Variable", keys, key=pick_key, format_func=lambda k: labels.get(k, k),
                 on_change=_picked)
    point = points[points["key"] == st.session_state[pick_key]].iloc[0]
    reading = charts.contraction_reading(point)
    if reading:
        st.markdown(reading)
    fig = charts.prior_posterior_figure(point, mode) if plotted else None
    if fig is not None:
        _plot(fig, key=f"pp_detail_{tag}")
        st.caption("Prior = what the prior file asserted. Data = what the data alone says "
                   "(recovered from the two; missing when the posterior is not narrower "
                   "than the prior). Posterior = the two combined."
                   + (" On the log scale the coefficient is ±exp(x)."
                      if point.get("scale") == "log" else ""))
    cols = ["label", "scale", "prior_mean", "prior_sd", "posterior_mean", "posterior_sd",
            "contraction", "shift"]
    _table(points[cols].rename(columns={"label": "variable", "shift": "shift_prior_sd"}),
           ref, RESULT_FILES["contraction"], tag, expanded=not plotted)


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
                _rerun_fragment()
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
