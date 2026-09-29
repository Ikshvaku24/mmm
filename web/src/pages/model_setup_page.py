import hashlib
import json
import os
import shutil
import time
import uuid
import pandas as pd
import streamlit as st
from src import codebase
from src.app_functions import (adls_folder, prepare_prior_table,
                               read_file_bytes_as_table, show_prior_validation,
                               upload_active_prior, validate_prior)
from src.clusters import get_cluster_status, start_cluster
from src.config_editor import init_config_state, live_config, render_settings_section
from src.files import adls_dir, download_folder, unique_name, upload_to_adls, zip_folder
from src.generate_prior import generate_prior
from src.jobs import cancel_run, get_run_output, get_run_status, run_model_job
from src.validation import validate_input_data

uuid = str(uuid.uuid4())

# Jobs API life-cycle states that mean "finished, one way or another"
TERMINAL_STATES = {"TERMINATED", "INTERNAL_ERROR", "SKIPPED"}
DONE_RESULTS = {"SUCCESS", "FAILED", "CANCELED", "TIMEDOUT", "UPSTREAM_FAILED"}


def get_run_id_from_response(response):
    if not isinstance(response, dict):
        return None
    return response.get("run_id") or response.get("runId") or response.get("runID")


def _run_output(run_id):
    """The notebook's exit JSON or its error, fetched once per run."""
    key = f"run_output_{run_id}"
    if key not in st.session_state:
        try:
            st.session_state[key] = get_run_output(run_id)
        except Exception as e:
            st.session_state[key] = {"error": f"Could not read the run output: {e}"}
    return st.session_state[key] or {}


def _render_run_result(run_id, workflow_state, result_state):
    """Success: which codebase ran. Failure: the notebook's own error."""
    if result_state == "SUCCESS":
        output = _run_output(run_id)
        try:
            info = json.loads((output.get("notebook_output") or {}).get("result") or "{}")
        except (TypeError, ValueError):
            info = {}
        if info.get("codebase"):
            app_version = codebase.status().get("version")
            st.caption(f"Ran codebase 1 {info['codebase']} in {info.get('seconds', '?')} s; "
                       f"outputs in {info.get('output_dir', '')}")
            if app_version and app_version != info["codebase"]:
                st.warning(f"The job ran codebase 1 {info['codebase']} but this app "
                           f"loaded {app_version} - someone re-uploaded it in between. "
                           "Press 'Reload codebase 1' and check the settings.")
    elif workflow_state in TERMINAL_STATES or result_state in DONE_RESULTS:
        output = _run_output(run_id)
        error = output.get("error") or (output.get("metadata") or {}).get(
            "state", {}).get("state_message")
        if error:
            st.error(f"The run stopped: {error}")
        trace = output.get("error_trace")
        if trace:
            with st.expander("Error details"):
                st.code("\n".join(str(trace).splitlines()[-40:]), language="text")


def _render_output_download(run_id, include_trace):
    tag = "trace" if include_trace else "notrace"
    folder_path = f"{adls_dir(_output_folder())}/{run_id}"
    summary_bytes_key = f"summary_download_bytes_{run_id}_{tag}"
    summary_error_key = f"summary_download_error_{run_id}_{tag}"
    summary_attempted_key = f"summary_download_attempted_{run_id}_{tag}"
    if not st.session_state.get(summary_attempted_key, False):
        st.session_state[summary_attempted_key] = True
        try:
            local_folder = os.path.join(".", "local", f"{run_id}_{tag}")
            zip_path = os.path.join(".", "local", f"{run_id}_{tag}.zip")
            shutil.rmtree(local_folder, ignore_errors=True)
            download_folder(folder_path, local_folder,
                            exclude=() if include_trace else ("trace.nc",))
            zip_folder(local_folder, zip_path)
            with open(zip_path, "rb") as f:
                st.session_state[summary_bytes_key] = f.read()
            shutil.rmtree(local_folder, ignore_errors=True)
            os.remove(zip_path)
            st.session_state.pop(summary_error_key, None)
        except Exception as e:
            st.session_state[summary_error_key] = str(e)
    if st.session_state.get(summary_error_key):
        st.error(f"Output download failed from ADLS: {st.session_state[summary_error_key]}")
    elif st.session_state.get(summary_bytes_key):
        st.download_button(
            label="Download Folder",
            data=st.session_state[summary_bytes_key],
            file_name=f"{run_id}_outputs.zip",
            mime="application/zip",
            key=f"download_outputs_{run_id}_{tag}",
        )


@st.fragment(run_every="5s")
def render_run_status_section(run_id):
    run_status_response = None
    workflow_state = "N/A"
    result_state = "N/A"
    if run_id:
        try:
            run_status_response = get_run_status(run_id)
            state = (
                run_status_response.get("state", {})
                if isinstance(run_status_response, dict)
                else {}
            )
            workflow_state = state.get("life_cycle_state", "N/A")
            result_state = state.get("result_state", "N/A")
        except Exception as e:
            st.warning(f"Unable to fetch run status: {e}")
    st.session_state[f"_ws_{run_id}"] = workflow_state
    st.session_state[f"_rs_{run_id}"] = result_state
    st.markdown(
        """
        <style>
            .run-status {
                display: flex;
                align-items: center;
                gap: 0.75rem;
                padding: 0.75rem 0.9rem;
                border-radius: 0.75rem;
                background: rgba(15, 23, 42, 0.04);
                margin: 0.75rem 0 1rem 0;
            }
            .run-status-icon {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 1.3rem;
                height: 1.3rem;
                flex: 0 0 auto;
            }
            .run-status-spinner {
                width: 1.05rem;
                height: 1.05rem;
                border: 0.18rem solid rgba(34, 197, 94, 0.20);
                border-top-color: #16a34a;
                border-radius: 50%;
                animation: run-status-spin 0.9s linear infinite;
            }
            .run-status-success,
            .run-status-terminated {
                font-size: 1.15rem;
                font-weight: 700;
                line-height: 1;
            }
            .run-status-success {
                color: #16a34a;
            }
            .run-status-terminated {
                color: #dc2626;
            }
            .run-status-text {
                font-size: 0.98rem;
                color: #0f172a;
            }
            .run-status-dots span {
                display: inline-block;
                color: #475569;
                animation: run-status-dots 1.4s infinite ease-in-out;
            }
            .run-status-dots span:nth-child(2) {
                animation-delay: 0.2s;
            }
            .run-status-dots span:nth-child(3) {
                animation-delay: 0.4s;
            }
            div[data-testid="stDownloadButton"] > button {
                background-color: #16a34a !important;
                color: #ffffff !important;
                border: 1px solid #15803d !important;
            }
            div[data-testid="stDownloadButton"] > button:hover {
                background-color: #15803d !important;
                border-color: #166534 !important;
                color: #ffffff !important;
            }
            @keyframes run-status-spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
            @keyframes run-status-dots {
                0%, 80%, 100% { opacity: 0.2; transform: translateY(0); }
                40% { opacity: 1; transform: translateY(-2px); }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    status_markup = None
    if workflow_state in {"PENDING", "QUEUED", "BLOCKED", "WAITING_FOR_RETRY"}:
        status_markup = """
            <div class="run-status">
                <span class="run-status-icon run-status-dots"><span>.</span><span>.</span><span>.</span></span>
                <span class="run-status-text">Pending</span>
            </div>
            """
    elif workflow_state in {"RUNNING", "TERMINATING"}:
        status_markup = f"""
            <div class="run-status">
                <span class="run-status-icon"><span class="run-status-spinner"></span></span>
                <span class="run-status-text">{workflow_state.title()}</span>
            </div>
            """
    elif result_state == "SUCCESS":
        status_markup = """
            <div class="run-status">
                <span class="run-status-icon run-status-success">&#10003;</span>
                <span class="run-status-text">Success</span>
            </div>
            """
    elif workflow_state in TERMINAL_STATES:
        label = str(result_state).title() if result_state not in ("N/A", None) else "Terminated"
        status_markup = f"""
            <div class="run-status">
                <span class="run-status-icon run-status-terminated">&#10005;</span>
                <span class="run-status-text">{label}</span>
            </div>
            """
    elif workflow_state != "N/A":
        status_markup = f"""
            <div class="run-status">
                <span class="run-status-text">Workflow state: {workflow_state}</span>
            </div>
            """
    if status_markup:
        st.markdown(status_markup, unsafe_allow_html=True)
    if workflow_state != "N/A" or result_state != "N/A":
        st.caption(f"Workflow state: {workflow_state} | Result state: {result_state}")
    if run_id:
        _render_run_result(run_id, workflow_state, result_state)
        done = workflow_state in TERMINAL_STATES or result_state in DONE_RESULTS
        button_col_cancel, button_col_download = st.columns(2)
        cancel_clicked_once = st.session_state.get("cancel_run_clicked_once", False)
        with button_col_cancel:
            if st.button(
                "Cancel Run",
                type="secondary",
                key="cancel_run_button",
                disabled=cancel_clicked_once or done,
            ):
                st.session_state["cancel_run_clicked_once"] = True
                try:
                    cancel_response = cancel_run(run_id)
                    if cancel_response.ok:
                        st.success(f"Cancel request submitted for run_id: {run_id}")
                        try:
                            st.session_state["cancel_run_response"] = (
                                cancel_response.json()
                            )
                        except Exception:
                            st.session_state["cancel_run_response"] = {
                                "status_code": cancel_response.status_code
                            }
                    else:
                        st.error(
                            f"Failed to cancel run_id {run_id}. Status code: {cancel_response.status_code}"
                        )
                except Exception as e:
                    st.error(f"Cancel request failed: {e}")

        with button_col_download:
            if done:
                # a failed run publishes what it wrote too - its 00_warnings
                # usually says why it failed
                include_trace = st.checkbox(
                    "Include trace.nc", key=f"include_trace_{run_id}",
                    help="The raw posterior (all draws) - large. Off by default.")
                _render_output_download(run_id, include_trace)
        if st.session_state.get("cancel_run_clicked_once"):
            st.caption("Cancel already requested for this run.")
    if st.session_state.get("cancel_run_response"):
        st.caption("Cancel response")
        st.json(st.session_state["cancel_run_response"])


@st.fragment(run_every="1s")
def render_elapsed_timer(run_id):
    start = st.session_state.get("run_start_time")
    if start is None:
        return
    workflow_state = st.session_state.get(f"_ws_{run_id}", "N/A")
    result_state = st.session_state.get(f"_rs_{run_id}", "N/A")
    cancelled = st.session_state.get("cancel_run_clicked_once", False)
    is_done = result_state in DONE_RESULTS or workflow_state in TERMINAL_STATES or cancelled
    if is_done:
        end = st.session_state.get(f"_run_end_{run_id}")
        if end is None:
            end = time.time()
            st.session_state[f"_run_end_{run_id}"] = end
        elapsed = int(end - start)
        label = "Elapsed"
    else:
        elapsed = int(time.time() - start)
        label = "Running"
    mins, secs = divmod(elapsed, 60)
    st.markdown(
        f"""
        <div style="font-size:0.85rem; color:#475569; margin: 0.25rem 0 0.5rem 0;">
            &#9201; {label}: <strong>{mins:02d}:{secs:02d}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.dialog("Model Run Response")
def show_run_model_response_popup():
    response = st.session_state.get("run_model_response")
    run_id = st.session_state.get("current_run_id") or get_run_id_from_response(
        response
    )
    if response is None:
        st.info("No response available.")
    elif run_id:
        st.success(f"Model started. Run ID: {run_id}")
    else:
        st.error("Model run response did not include a run ID.")
        st.json(response)
    if run_id:
        st.caption("Status refreshes automatically every 5 seconds.")
        render_elapsed_timer(run_id)
        render_run_status_section(run_id)
    if st.button("Close", type="primary"):
        st.session_state["show_run_model_response"] = False
        st.session_state.pop("cancel_run_response", None)
        st.session_state.pop("cancel_run_clicked_once", None)
        st.session_state.pop("current_run_id", None)
        st.session_state.pop("run_start_time", None)
        st.session_state.pop(f"_ws_{run_id}", None)
        st.session_state.pop(f"_rs_{run_id}", None)
        st.session_state.pop(f"_run_end_{run_id}", None)
        st.session_state.pop(f"run_output_{run_id}", None)
        for tag in ("trace", "notrace"):
            st.session_state.pop(f"summary_download_bytes_{run_id}_{tag}", None)
            st.session_state.pop(f"summary_download_error_{run_id}_{tag}", None)
            st.session_state.pop(f"summary_download_attempted_{run_id}_{tag}", None)
        st.rerun()


@st.fragment(run_every="5s")
def render_cluster_status_controls():
    try:
        cluster_state = str(get_cluster_status() or "UNKNOWN").upper()
    except Exception:
        cluster_state = "UNKNOWN"
    cluster_state_color = {
        "TERMINATED": "#dc2626",
        "PENDING": "#eab308",
        "RUNNING": "#16a34a",
    }.get(cluster_state, "#64748b")
    st.markdown(
        f"""
        <style>
            .cluster-status-wrap {{
                display: flex;
                justify-content: flex-end;
                margin-bottom: 0.4rem;
            }}
            .cluster-status-radio {{
                display: inline-flex;
                align-items: center;
                gap: 0.45rem;
                padding: 0.25rem 0.5rem;
                border-radius: 999px;
                border: 1px solid color-mix(in srgb, {cluster_state_color} 35%, transparent);
                background: var(--background-color, transparent);
                font-size: 0.82rem;
                color: #0f172a;
                line-height: 1;
            }}
            .cluster-status-dot {{
                width: 0.68rem;
                height: 0.68rem;
                border-radius: 50%;
                border: 1px solid color-mix(in srgb, {cluster_state_color} 70%, #0f172a 30%);
                background: {cluster_state_color};
                box-shadow: 0 0 2px color-mix(in srgb, {cluster_state_color} 15%, transparent);
                flex: 0 0 auto;
            }}
            .cluster-status-label {{
                font-weight: 600;
                color: #334155;
            }}
            .cluster-status-value {{
                font-weight: 700;
                color: {cluster_state_color};
            }}
            .st-key-start_cluster_button button {{
                padding: 0.15rem 0.55rem;
                min-height: 1.8rem;
                font-size: 0.78rem;
                line-height: 1.1;
            }}
            .cluster-start-note {{
                display: flex;
                justify-content: flex-end;
                font-size: 0.75rem;
                color: #475569;
                margin: 0.2rem 0 0.4rem 0;
            }}
        </style>
        <div class="cluster-status-wrap">
            <label class="cluster-status-radio">
                <span class="cluster-status-dot" aria-hidden="true"></span>
                <span class="cluster-status-label">Cluster</span>
                <span class="cluster-status-value">{cluster_state}</span>
            </label>
        </div>
        """,
        unsafe_allow_html=True,
    )
    start_requested_key = "cluster_start_requested"
    if cluster_state != "TERMINATED":
        st.session_state[start_requested_key] = False
    show_start_button = cluster_state == "TERMINATED" and not st.session_state.get(
        start_requested_key, False
    )
    if show_start_button:
        _, start_btn_col = st.columns([1, 1])
        with start_btn_col:
            if st.button(
                "Start Cluster",
                type="secondary",
                key="start_cluster_button",
                use_container_width=False,
            ):
                st.session_state[start_requested_key] = True
                try:
                    status_code = start_cluster()
                    if status_code in {200, 202}:
                        st.success(
                            "Cluster start requested. Status refreshes every 5 seconds."
                        )
                    else:
                        st.error(f"Failed to start cluster. Status code: {status_code}")
                except Exception as e:
                    st.error(f"Unable to start cluster: {e}")
    elif cluster_state == "TERMINATED":
        st.markdown(
            '<div class="cluster-start-note">Start request submitted.</div>',
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------- #
# the backend (codebase 1, loaded live from the workspace)
# --------------------------------------------------------------------------- #
def _output_folder():
    from src.config_editor import get_schema
    return (get_schema() or {}).get("output_folder", "Outputs")


def render_backend_status():
    status = codebase.status()
    if status.get("ok"):
        where = status.get("where", "")
        source = "workspace" if status.get("source") == "workspace" else "local folder"
        st.caption(f"Backend: codebase 1 **{status['version']}** from the {source} `{where}`")
        if status.get("out_of_sync"):
            stale = ", ".join(f"{m} ({s})" for m, s in status["out_of_sync"])
            st.warning("codebase 1 is only partly updated - these modules are from "
                       f"another version: {stale}. Re-upload the WHOLE mmm/ folder.")
    else:
        st.error(f"codebase 1 (the backend) could not be loaded: {status.get('error')}")
    if st.button("Reload codebase 1", key="reload_backend", type="secondary",
                 help="Read codebase 1 from the workspace again now. It is also "
                      "re-checked automatically every few minutes."):
        codebase.status(force=True)
        for key in ("cfg_schema", "prior_spec", "prior_validation", "datacube_key"):
            st.session_state.pop(key, None)
        st.rerun()


# --------------------------------------------------------------------------- #
# 1. input data
# --------------------------------------------------------------------------- #
def _sha(data):
    return hashlib.sha1(data).hexdigest()


def _reset_datacube():
    st.session_state["input_uploaded_to_adls"] = False
    for key in ("uploaded_input_name", "uploaded_input_source", "datacube_df",
                "datacube_key", "datacube_hash", "datacube_bytes", "datacube_name",
                "datacube_regions", "datacube_summary", "datacube_ok",
                "datacube_read_errors"):
        st.session_state.pop(key, None)


def _render_data_block():
    st.markdown("#### Input data")
    input_uploaded_to_adls = st.session_state.get("input_uploaded_to_adls", False)
    input_data_file = st.file_uploader(
        "Choose input data file",
        type=["xlsx", "csv"],
        key="input_data_file",
        help="The datacube: one row per region x date, with the date, region, KPI "
             "and feature columns named in Model settings (run.date_col / "
             "region_col / dv_col).",
    )
    cfg = live_config() or {}

    if input_data_file:
        if st.session_state.get("uploaded_input_source") != input_data_file.name:
            st.session_state["input_uploaded_to_adls"] = False
            input_uploaded_to_adls = False
        file_bytes = input_data_file.getvalue()
        data_cfg, run_cfg = cfg.get("data") or {}, cfg.get("run") or {}
        key = (_sha(file_bytes), data_cfg.get("sheet"), data_cfg.get("date_format"),
               run_cfg.get("date_col"), run_cfg.get("region_col"), run_cfg.get("dv_col"))
        if st.session_state.get("datacube_key") != key:
            read = codebase.read_datacube(file_bytes, input_data_file.name, cfg)
            st.session_state["datacube_key"] = key
            st.session_state["datacube_hash"] = key[0]
            st.session_state["datacube_bytes"] = file_bytes
            st.session_state["datacube_name"] = input_data_file.name
            if read.ok:
                st.session_state["datacube_df"] = read.value
                st.session_state["datacube_regions"] = codebase.datacube_regions(read.value, cfg)
                st.session_state.pop("datacube_read_errors", None)
            else:
                st.session_state["datacube_df"] = None
                st.session_state["datacube_regions"] = []
                st.session_state["datacube_read_errors"] = read.errors
        df = st.session_state.get("datacube_df")
        for error in st.session_state.get("datacube_read_errors") or []:
            st.error(f"Could not read the datacube: {error}")
        if df is not None:
            outcome = validate_input_data(df, cfg)
            st.session_state["datacube_ok"] = outcome.ok
            summary = outcome.value or {}
            st.session_state["datacube_summary"] = summary
            if summary.get("regions"):
                st.caption(
                    f"{len(summary['regions'])} regions · {summary.get('n_periods')} periods "
                    f"({summary.get('date_min')} … {summary.get('date_max')}) · "
                    f"{len(summary.get('features', []))} features"
                    + (f" · {summary['plan']}" if summary.get("plan") else ""))
        else:
            st.session_state["datacube_ok"] = False

        if st.button("Upload", type="secondary", key="upload_input_to_adls",
                     disabled=not st.session_state.get("datacube_ok", False)):
            try:
                name = unique_name(input_data_file.name)
                upload_to_adls(file_bytes, name, adls_folder("data_file"))
                st.session_state["input_uploaded_to_adls"] = True
                st.session_state["uploaded_input_name"] = name
                st.session_state["uploaded_input_source"] = input_data_file.name
                input_uploaded_to_adls = True
                st.success(f"Uploaded **{input_data_file.name}** to ADLS as {name}.")
            except Exception as e:
                st.session_state["input_uploaded_to_adls"] = False
                input_uploaded_to_adls = False
                st.error(f"Upload failed: {e}")
        if input_uploaded_to_adls:
            st.caption(f"Input data file uploaded to ADLS as "
                       f"{st.session_state.get('uploaded_input_name')}.")
    else:
        _reset_datacube()
    return input_data_file


# --------------------------------------------------------------------------- #
# 3. mapping and share files
# --------------------------------------------------------------------------- #
SIDE_FILES = {
    "mapping": ("Mapping file",
                "vendor_variable, our_variable[, region][, contribution]: which "
                "vendor variable is which of ours. With contributions it drives the "
                "prior generator (case a) and pre-fills the benchmark sheet."),
    "share": ("Share file",
              "section, pillar, pillar_share_pct, variable, spend, "
              "variable_share_pct[, sign_constraint]: expected shares of sales "
              "(sections media, expert, comp_media, trade, baseline)."),
}


def _reset_side(kind):
    for key in (f"{kind}_bytes", f"{kind}_name", f"{kind}_ok", f"{kind}_check",
                f"{kind}_uploaded_to_adls", f"uploaded_{kind}_name",
                f"uploaded_{kind}_source"):
        st.session_state.pop(key, None)


def _render_side_file(kind, df, cfg):
    title, help_text = SIDE_FILES[kind]
    st.markdown(f"**{title}**")
    st.caption(help_text)
    sample_col, template_col = st.columns(2)
    with sample_col:
        sample = codebase.sample_file(kind)
        st.download_button("Download sample", data=sample.value or b"",
                           file_name=f"{kind}_sample.csv", mime="text/csv",
                           disabled=not sample.ok, key=f"{kind}_sample_download",
                           help="codebase 1's example file - the format, with example names.")
    with template_col:
        template = codebase.template_file(kind, df, cfg) if df is not None else None
        st.download_button("Download template", data=(template.value if template and template.ok else b""),
                           file_name=f"{kind}_template.csv", mime="text/csv",
                           disabled=template is None or not template.ok,
                           key=f"{kind}_template_download",
                           help="The sample's columns with one row per datacube variable. "
                                + ("Leave vendor_variable blank on rows you do not map."
                                   if kind == "mapping" else
                                   "Delete the rows you have no share for."))
    uploaded = st.file_uploader(f"Choose {title.lower()}", type=["csv", "xlsx"],
                                key=f"{kind}_file")
    if not uploaded:
        _reset_side(kind)
        return
    file_bytes = uploaded.getvalue()
    if st.session_state.get(f"uploaded_{kind}_source") != uploaded.name:
        st.session_state[f"{kind}_uploaded_to_adls"] = False
    check_key = (_sha(file_bytes), st.session_state.get("datacube_hash"))
    cached = st.session_state.get(f"{kind}_check")
    if not cached or cached[0] != check_key:
        validate = codebase.validate_mapping if kind == "mapping" else codebase.validate_share
        cached = (check_key, validate(file_bytes, uploaded.name, df, cfg))
        st.session_state[f"{kind}_check"] = cached
    outcome = cached[1]
    st.session_state[f"{kind}_bytes"] = file_bytes
    st.session_state[f"{kind}_name"] = uploaded.name
    st.session_state[f"{kind}_ok"] = outcome.ok
    if outcome.ok:
        v = outcome.value
        if kind == "mapping":
            st.success(f"{v['links']} links, {v['vendor_variables']} vendor variables - "
                       + ("with contributions (the prior generator inverts them: case a)."
                          if v["has_contribution"] else "no contributions (grouping only)."))
        else:
            st.success(f"{v['rows']} rows in sections {', '.join(v['sections'])}.")
        with st.expander("Preview"):
            st.dataframe(v["table"], use_container_width=True, height=240)
    for error in outcome.errors:
        st.error(error)
    for _severity, warning in outcome.warnings:
        st.warning(warning)
    if df is None:
        st.caption("Upload the datacube to check the names in this file against it.")
    if st.button("Upload", type="secondary", key=f"upload_{kind}_to_adls",
                 disabled=not outcome.ok):
        try:
            name = unique_name(uploaded.name)
            upload_to_adls(file_bytes, name, adls_folder(f"{kind}_file"))
            st.session_state[f"{kind}_uploaded_to_adls"] = True
            st.session_state[f"uploaded_{kind}_name"] = name
            st.session_state[f"uploaded_{kind}_source"] = uploaded.name
            st.success(f"Uploaded **{uploaded.name}** to ADLS as {name}.")
        except Exception as e:
            st.session_state[f"{kind}_uploaded_to_adls"] = False
            st.error(f"Upload failed: {e}")
    if st.session_state.get(f"{kind}_uploaded_to_adls"):
        st.caption(f"{title} uploaded to ADLS as {st.session_state.get(f'uploaded_{kind}_name')}.")


def _render_pre_model_block():
    cfg = live_config() or {}
    df = st.session_state.get("datacube_df")
    with st.container(border=True):
        st.markdown("### Mapping and share files (optional)")
        st.caption("Inputs to codebase 1's prior generator. With neither, the generated "
                   "prior file is the skeleton: one row per datacube variable, means blank.")
        left, right = st.columns(2, gap="large")
        with left:
            _render_side_file("mapping", df, cfg)
        with right:
            _render_side_file("share", df, cfg)


# --------------------------------------------------------------------------- #
# 4. the prior file: generate it (codebase 1) or upload your own
# --------------------------------------------------------------------------- #
def _generation_signature(cfg):
    data, run = cfg.get("data") or {}, cfg.get("run") or {}
    keys = (data.get("dv_aggregation"), data.get("national_basis"), data.get("sheet"),
            data.get("date_format"), run.get("date_col"), run.get("region_col"),
            run.get("dv_col"), run.get("holdout_periods"), run.get("holdout_fraction"),
            run.get("cadence"), run.get("scaling_window"))
    files = tuple(_sha(st.session_state[k]) if st.session_state.get(k) else ""
                  for k in ("datacube_bytes", "mapping_bytes", "share_bytes"))
    return (keys, files)


def _use_generated(file_name, file_bytes):
    table, error = read_file_bytes_as_table(file_bytes, file_name)
    if error:
        st.error(error)
        return
    prepared = prepare_prior_table(table)
    st.session_state["prior_source_name"] = file_name
    st.session_state["prior_working_table"] = prepared
    st.session_state["prior_effective_bytes"] = codebase.prior_csv_bytes(prepared)
    st.session_state["prior_effective_name"] = file_name
    st.session_state["prior_is_edited"] = False
    st.session_state["prior_popup_toggle_version"] = 0
    st.session_state["prior_popup_editor_version"] = st.session_state.get("prior_popup_editor_version", 0) + 1
    st.session_state["prior_uploaded_to_adls"] = False
    st.session_state.pop("uploaded_prior_name", None)
    st.session_state["prior_from_generator"] = True
    # clear the "upload your own" box, or the file in it would take over again
    st.session_state["prior_uploader_version"] = st.session_state.get("prior_uploader_version", 0) + 1
    st.rerun()


def _render_generator(cfg, df):
    st.markdown("**Generate from the datacube**")
    mapping_ok = bool(st.session_state.get("mapping_ok"))
    share_ok = bool(st.session_state.get("share_ok"))
    used = ["the datacube"] + (["the mapping file"] if mapping_ok else []) + (
        ["the share file"] if share_ok else [])
    st.caption("codebase 1's pre-model step (build_priors), using " + ", ".join(used) + ".")
    has_table = st.session_state.get("prior_working_table") is not None
    restrict = st.checkbox("Only the variables of the current prior table",
                           key="gen_restrict", disabled=not has_table,
                           help="The prior file may carry MORE variables than the "
                                "mapping/share files, never fewer.")
    ready = df is not None and st.session_state.get("datacube_ok", False)
    if st.button("Generate prior file", key="generate_prior_button", type="primary",
                 disabled=not ready):
        with st.spinner("Running codebase 1's pre-model step ..."):
            outcome = generate_prior(
                (st.session_state["datacube_bytes"], st.session_state["datacube_name"]), cfg,
                mapping=(st.session_state["mapping_bytes"], st.session_state["mapping_name"])
                if mapping_ok else None,
                share=(st.session_state["share_bytes"], st.session_state["share_name"])
                if share_ok else None,
                restrict_to=st.session_state.get("prior_working_table") if restrict else None)
        st.session_state["gen_result"] = outcome
        st.session_state["gen_signature"] = _generation_signature(cfg)
    if not ready:
        st.caption("Upload a datacube that passes the checks first.")

    outcome = st.session_state.get("gen_result")
    if outcome is None:
        return
    if not outcome.ok:
        for error in outcome.errors:
            st.error(error)
        return
    result = outcome.value
    st.success(f"Case {result['case']}: {result['case_text']}")
    if st.session_state.get("gen_signature") != _generation_signature(cfg):
        st.warning("The datacube, a mapping/share file or a setting it depends on has "
                   "changed since this was generated - generate again.")
    files = result["files"]
    mimes = {".csv": "text/csv", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             ".zip": "application/zip"}
    for name, data in files.items():
        st.download_button(f"Download {name}", data=data, file_name=name,
                           mime=mimes.get(os.path.splitext(name)[1], "application/octet-stream"),
                           key=f"gen_download_{name}")
    use_cols = st.columns(2)
    if "feature_priors_national.csv" in files:
        with use_cols[0]:
            if st.button("Use national (hierarchical)", key="use_generated_national",
                         help="One row per variable - for pooling: hierarchical."):
                _use_generated("feature_priors_national.csv", files["feature_priors_national.csv"])
    if "feature_priors_regional.csv" in files:
        with use_cols[1]:
            if st.button("Use regional (independent)", key="use_generated_regional",
                         help="Plus one override row per region - for pooling: independent."):
                _use_generated("feature_priors_regional.csv", files["feature_priors_regional.csv"])
    if result.get("index_md"):
        with st.expander("Warnings from the pre-model step"):
            st.markdown(result["index_md"])
    if outcome.log:
        with st.expander("Log"):
            st.code(outcome.log, language="text")


def _render_prior_block():
    cfg = live_config() or {}
    df = st.session_state.get("datacube_df")
    with st.container(border=True):
        st.markdown("### Prior file")
        st.caption("Generate it with codebase 1, or upload your own. Then preview and edit "
                   "it here - or download it, edit it in Excel and upload it again - and "
                   "upload it to ADLS.")
        gen_col, upload_col = st.columns(2, gap="large")
        with gen_col:
            _render_generator(cfg, df)
        with upload_col:
            st.markdown("**Or upload your own prior file**")
            prior_file = st.file_uploader(
                "Choose prior file",
                type=["csv", "xlsx"],
                key=f"prior_file_{st.session_state.get('prior_uploader_version', 0)}",
                help="A feature-prior file (CSV or Excel): a downloaded generated file "
                     "you edited, or one from an earlier run.",
            )
    return prior_file


# --------------------------------------------------------------------------- #
# 5. run
# --------------------------------------------------------------------------- #
def _prior_variables():
    table = st.session_state.get("prior_working_table")
    if table is None:
        return []
    t = codebase.clean_prior_table(table)
    if "region" in t.columns:
        t = t[t["region"].isna()]
    return [str(v) for v in t["variable"]]


def _side_against_prior(kind):
    """The run re-checks the mapping/share names against the PRIOR file's variables."""
    names = _prior_variables()
    key = (_sha(st.session_state[f"{kind}_bytes"]), tuple(names))
    cached = st.session_state.get(f"{kind}_prior_check")
    if not cached or cached[0] != key:
        validate = codebase.validate_mapping if kind == "mapping" else codebase.validate_share
        outcome = validate(st.session_state[f"{kind}_bytes"], st.session_state[f"{kind}_name"],
                           st.session_state.get("datacube_df"),
                           st.session_state.get("cfg_values") or {}, prior_variables=names)
        cached = (key, outcome)
        st.session_state[f"{kind}_prior_check"] = cached
    return cached[1]


def _readiness():
    ss = st.session_state
    ready = {
        "Input data uploaded to ADLS": (bool(ss.get("input_uploaded_to_adls")),
                                        ss.get("uploaded_input_name", "")),
        "Model settings valid": (bool(ss.get("cfg_valid")), ""),
        "Prior file validated and uploaded to ADLS": (bool(ss.get("prior_uploaded_to_adls")),
                                                     ss.get("uploaded_prior_name", "")),
    }
    for kind, label in (("mapping", "Mapping file"), ("share", "Share file")):
        if not ss.get(f"{kind}_bytes"):
            continue
        ok = bool(ss.get(f"{kind}_ok")) and bool(ss.get(f"{kind}_uploaded_to_adls"))
        note = ss.get(f"uploaded_{kind}_name", "") if ok else "validate and upload it, or remove it"
        if ok and ss.get("prior_working_table") is not None:
            against = _side_against_prior(kind)
            if not against.ok:
                ok, note = False, "names variables the prior file does not have: " + \
                    " ".join(against.errors)[:300]
        ready[f"{label} uploaded to ADLS"] = (ok, note)
    return ready


def render_run_section():
    """The Run block - rendered AFTER the prior section, so a prior uploaded in
    this rerun is already ticked in the checklist."""
    with st.container(border=True):
        st.markdown("### Run")
        ready = _readiness()
        for label, (ok, note) in ready.items():
            st.markdown(f"{'✅' if ok else '⬜'} {label}" + (f" - {note}" if note else ""))
        submit_disabled = not all(ok for ok, _ in ready.values())
        _, center_col, _ = st.columns([1, 1, 1])
        with center_col:
            run_model_clicked = st.button(
                "Run Model",
                type="primary",
                disabled=submit_disabled,
                use_container_width=True,
            )
    return submit_disabled, run_model_clicked


def render_input_upload_section():
    """Input data, Model settings, mapping/share files and the prior file.
    Returns (prior_file, input_data_file, run_config)."""
    init_config_state()
    with st.container(border=True):
        header_left, header_right = st.columns([3, 1])
        with header_left:
            st.markdown("### Model Setup")
            st.caption("Upload the datacube, check the settings, prepare the prior file, then run.")
            render_backend_status()
        with header_right:
            render_cluster_status_controls()
        input_data_file = _render_data_block()

    run_config, _config_ok = render_settings_section()
    _render_pre_model_block()
    prior_file = _render_prior_block()
    return prior_file, input_data_file, run_config


def handle_prior_file_section(prior_file, read_uploaded_file_as_table, show_prior_file_popup):
    if prior_file:
        st.success(f"Prior file selected: {prior_file.name}")
        prior_table, prior_error = read_uploaded_file_as_table(prior_file)
        current_source_name = st.session_state.get("prior_source_name")
        if current_source_name != prior_file.name and prior_table is not None:
            prepared = prepare_prior_table(prior_table)
            st.session_state["prior_source_name"] = prior_file.name
            st.session_state["prior_working_table"] = prepared
            st.session_state["prior_effective_bytes"] = codebase.prior_csv_bytes(prepared)
            st.session_state["prior_effective_name"] = prior_file.name.rsplit(".", 1)[0] + ".csv"
            st.session_state["prior_is_edited"] = False
            st.session_state["prior_popup_toggle_version"] = 0
            st.session_state["prior_popup_editor_version"] = st.session_state.get("prior_popup_editor_version", 0) + 1
            st.session_state["prior_uploaded_to_adls"] = False
            st.session_state.pop("uploaded_prior_name", None)
            st.session_state["prior_from_generator"] = False
        if prior_error:
            st.toast(f"Error: {prior_error}", icon="🚨")
    elif st.session_state.get("prior_working_table") is not None:
        st.success(
            f"Prior file in use: {st.session_state.get('prior_effective_name', 'feature_priors.csv')}"
            + (" (generated by codebase 1)" if st.session_state.get("prior_from_generator") else "")
        )
    else:
        st.session_state.pop("prior_source_name", None)
        st.session_state.pop("prior_working_table", None)
        st.session_state.pop("prior_effective_bytes", None)
        st.session_state.pop("prior_effective_name", None)
        st.session_state["prior_is_edited"] = False
        st.session_state["prior_uploaded_to_adls"] = False
        return

    table = st.session_state.get("prior_working_table")
    if table is None:
        return
    if st.button("Preview / Edit Prior File"):
        show_prior_file_popup()
    if st.session_state.get("prior_is_edited"):
        st.info("Edited prior file is active and will be used for model run.")
    outcome = validate_prior(table)
    show_prior_validation(outcome, compact=True)
    if st.button("Upload prior file to ADLS", type="secondary", key="upload_prior_to_adls",
                 disabled=not outcome.ok):
        try:
            ok, result = upload_active_prior()
            if ok:
                st.success(f"Uploaded **{result[0]}** to ADLS.")
            else:
                st.warning(result)
        except Exception as e:
            st.session_state["prior_uploaded_to_adls"] = False
            st.error(f"Upload failed: {e}")
    if st.session_state.get("prior_uploaded_to_adls"):
        st.caption(f"Prior file uploaded to ADLS as {st.session_state.get('uploaded_prior_name')}.")


def handle_run_and_status(
    prior_file, input_data_file, run_model_clicked, submit_disabled, run_config=None
):
    if input_data_file:
        st.success(f"Input data file selected: {input_data_file.name}")

    if run_model_clicked:
        if submit_disabled:
            st.error("Complete the checklist in the Run section before running the model.")
            return
        cfg = st.session_state.get("cfg_values") or run_config
        config_text = codebase.config_yaml(cfg)
        if not config_text.ok:
            st.error("The settings could not be written: " + " | ".join(config_text.errors))
            return
        config_file_name = unique_name("config.yaml")
        try:
            upload_to_adls(config_text.value.encode("utf-8"), config_file_name,
                           adls_folder("config_file"))
        except Exception as e:
            st.error(f"Could not upload the settings to ADLS: {e}")
            return

        st.session_state["prior_file_name"] = st.session_state.get("uploaded_prior_name")
        st.session_state["input_data_file_name"] = st.session_state.get("uploaded_input_name")
        st.session_state["config_file_name"] = config_file_name
        st.session_state.pop("cancel_run_clicked_once", None)
        st.session_state.pop("cancel_run_response", None)
        st.session_state["run_start_time"] = time.time()
        st.toast("Model run started with selected files.")
        mapping_name = st.session_state.get("uploaded_mapping_name", "") \
            if st.session_state.get("mapping_bytes") else ""
        share_name = st.session_state.get("uploaded_share_name", "") \
            if st.session_state.get("share_bytes") else ""
        try:
            response = run_model_job(
                prior_file=st.session_state["prior_file_name"],
                data_file=st.session_state["input_data_file_name"],
                job_id=os.environ.get("MDR_JOB_ID"),
                config_file=config_file_name,
                mapping_file=mapping_name,
                share_file=share_name,
            )
            run_model_response = response.json()
            st.session_state["run_model_response"] = run_model_response
            st.session_state["current_run_id"] = get_run_id_from_response(
                run_model_response
            )
            if st.session_state["current_run_id"]:
                st.session_state["last_run_id"] = st.session_state["current_run_id"]
        except Exception as e:
            st.session_state["run_model_response"] = {"error": str(e)}
            st.session_state.pop("current_run_id", None)

        st.session_state["show_run_model_response"] = True

    if st.session_state.get("show_run_model_response"):
        show_run_model_response_popup()

    if submit_disabled:
        st.info("Complete the checklist in the Run section to run the model.")
    else:
        st.caption("Everything is ready.")


def render_model_file_section(show_model_file_adls_popup):
    with st.container(border=True):
        st.subheader("Run outputs from ADLS")
        st.caption(
            "Open a finished run's key tables (warnings, convergence, fit, contributions) "
            "by its run ID - also after the status popup has been closed."
        )
        if st.button(
            "Open Run Outputs",
            type="secondary",
            key="open_model_file_adls_popup",
        ):
            show_model_file_adls_popup()
