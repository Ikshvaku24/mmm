import streamlit as st
import re
import os
import time
import pandas as pd
from io import BytesIO
from src.clusters import get_cluster_status, start_cluster
from src.files import download_from_adls, upload_to_adls
from src.warehouse import get_bmc_list, get_channel_list, get_kpi_attributes
from src.jobs import cancel_run, get_run_status, run_feasibility_check, run_mapping_job
from src.plots_charts import generate_heatmap, create_line_chart, generate_pie_chart


def _clear_missing_selectbox_value(key, options):
    if key not in st.session_state:
        return
    value = st.session_state[key]
    if isinstance(value, list):
        st.session_state[key] = [item for item in value if item in options]
    elif value not in options:
        st.session_state.pop(key, None)


def _get_run_id(response):
    if not isinstance(response, dict):
        return None
    return response.get("run_id") or response.get("runId") or response.get("runID")


@st.fragment(run_every="5s")
def _render_mapping_run_status(run_id):
    try:
        response = get_run_status(run_id)
        state = response.get("state", {})
        workflow_state = state.get("life_cycle_state", "N/A")
        result_state = state.get("result_state", "N/A")
        st.session_state[f"mapping_workflow_state_{run_id}"] = workflow_state
        st.session_state[f"mapping_result_state_{run_id}"] = result_state
        if workflow_state in {"PENDING", "RUNNING", "TERMINATING"}:
            st.markdown(
                f"""
                <div style="display:flex; align-items:center; gap:0.75rem; padding:0.75rem 0.9rem;
                        border-radius:0.75rem; background:rgba(15,23,42,0.04); margin:0.75rem 0 1rem;">
                    <span style="width:1.05rem; height:1.05rem; border:0.18rem solid rgba(34,197,94,0.20);
                            border-top-color:#16a34a; border-radius:50%; animation:mapping-spin 0.9s linear infinite;"></span>
                    <span style="font-size:0.98rem; color:#0f172a;">{workflow_state.title()}</span>
                </div>
                <style>@keyframes mapping-spin {{ from {{ transform:rotate(0deg); }} to {{ transform:rotate(360deg); }} }}</style>
                """,
                unsafe_allow_html=True,
            )
        elif result_state == "SUCCESS":
            st.success("Mapping job completed successfully.")
        elif result_state == "FAILED":
            st.error("Mapping job failed.")
        elif workflow_state == "TERMINATED":
            st.error("Mapping job terminated.")
        st.caption(f"Workflow state: {workflow_state} | Result state: {result_state}")
        st.markdown(
            """
            <style>
            .st-key-mapping-download-button [data-testid="stDownloadButton"] button {
                background: #16a34a !important;
                border: 1px solid #15803d !important;
                color: #ffffff !important;
            }
            .st-key-mapping-download-button [data-testid="stDownloadButton"] button:hover {
                background: #15803d !important;
                border-color: #166534 !important;
                color: #ffffff !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        cancel_col, download_col = st.columns(2)
        with cancel_col:
            cancel_requested = st.session_state.get("mapping_cancel_requested", False)
            if st.button(
                "Cancel Job",
                type="secondary",
                key="cancel_mapping_job",
                disabled=cancel_requested
                or result_state == "SUCCESS"
                or workflow_state == "TERMINATED",
                use_container_width=True,
            ):
                st.session_state["mapping_cancel_requested"] = True
                try:
                    cancel_response = cancel_run(run_id)
                    if cancel_response.ok:
                        st.success("Cancel request submitted.")
                    else:
                        st.error(
                            f"Failed to cancel mapping job. Status code: {cancel_response.status_code}"
                        )
                except Exception as error:
                    st.error(f"Cancel request failed: {error}")
        with download_col:
            if result_state == "SUCCESS":
                mapping_file_path = f"feasibility_check/user_mapping/{run_id}/{run_id}_group_mapping.csv"
                mapping_bytes_key = f"mapping_download_bytes_{run_id}"
                mapping_error_key = f"mapping_download_error_{run_id}"
                mapping_attempted_key = f"mapping_download_attempted_{run_id}"
                if not st.session_state.get(mapping_attempted_key, False):
                    st.session_state[mapping_attempted_key] = True
                    try:
                        st.session_state[mapping_bytes_key] = download_from_adls(
                            mapping_file_path
                        )
                        st.session_state.pop(mapping_error_key, None)
                    except Exception as error:
                        st.session_state[mapping_error_key] = str(error)
                if st.session_state.get(mapping_error_key):
                    st.error(
                        f"Mapping file download failed from ADLS: {st.session_state[mapping_error_key]}"
                    )
                elif st.session_state.get(mapping_bytes_key):
                    with st.container(key="mapping-download-button"):
                        st.download_button(
                            "Download Mapping",
                            data=st.session_state[mapping_bytes_key],
                            file_name=f"{run_id}_group_mapping.csv",
                            mime="text/csv",
                            key=f"download_mapping_button_{run_id}",
                            type="primary",
                            use_container_width=True,
                        )
                if st.session_state.get("mapping_cancel_requested"):
                    st.caption("Cancel request already submitted for this job.")
    except Exception as error:
        st.warning(f"Unable to fetch mapping job status: {error}")


@st.fragment(run_every="1s")
def _render_mapping_elapsed_timer(run_id):
    start_time = st.session_state.get("mapping_run_start_time")
    if start_time is None:
        return
    workflow_state = st.session_state.get(f"mapping_workflow_state_{run_id}", "N/A")
    result_state = st.session_state.get(f"mapping_result_state_{run_id}", "N/A")
    is_done = (
        result_state in {"SUCCESS", "FAILED", "CANCELED"}
        or workflow_state == "TERMINATED"
    )
    if is_done:
        end_time = st.session_state.get(f"mapping_run_end_{run_id}")
        if end_time is None:
            end_time = time.time()
            st.session_state[f"mapping_run_end_{run_id}"] = end_time
        elapsed_seconds = int(end_time - start_time)
        label = "Elapsed"
    else:
        elapsed_seconds = int(time.time() - start_time)
        label = "Running"
    minutes, seconds = divmod(elapsed_seconds, 60)
    st.caption(f"{label}: {minutes:02d}:{seconds:02d}")


@st.dialog("Mapping Job Status")
def _show_mapping_status_popup():
    response = st.session_state.get("mapping_job_response")
    run_id = st.session_state.get("mapping_run_id") or _get_run_id(response)
    if run_id:
        st.success(f"Mapping job started. Run ID: {run_id}")
        st.caption("Status refreshes automatically every 5 seconds.")
        _render_mapping_elapsed_timer(run_id)
        _render_mapping_run_status(run_id)
    else:
        st.error("Mapping job response did not include a run ID.")
        if response:
            st.json(response)
    if st.button("Close", type="primary", key="close_mapping_status"):
        st.session_state["show_mapping_status"] = False
        st.session_state.pop("mapping_cancel_requested", None)
        st.session_state.pop("mapping_run_start_time", None)
        st.session_state.pop(f"mapping_workflow_state_{run_id}", None)
        st.session_state.pop(f"mapping_result_state_{run_id}", None)
        st.session_state.pop(f"mapping_run_end_{run_id}", None)
        st.session_state.pop(f"mapping_download_bytes_{run_id}", None)
        st.session_state.pop(f"mapping_download_error_{run_id}", None)
        st.session_state.pop(f"mapping_download_attempted_{run_id}", None)
        st.rerun()


@st.fragment(run_every="5s")
def _render_feasibility_run_status(run_id):
    try:
        response = get_run_status(run_id)
        state = response.get("state", {})
        workflow_state = state.get("life_cycle_state", "N/A")
        result_state = state.get("result_state", "N/A")
        st.session_state[f"feasibility_workflow_state_{run_id}"] = workflow_state
        st.session_state[f"feasibility_result_state_{run_id}"] = result_state
        is_done = (
            result_state in {"SUCCESS", "FAILED", "CANCELED"}
            or workflow_state == "TERMINATED"
        )
        st.session_state[f"feasibility_run_done_{run_id}"] = is_done
        if workflow_state in {"PENDING", "RUNNING", "TERMINATING"}:
            st.markdown(
                f"""
                <div style="display: flex; align-items: center; gap: 0.75rem; padding: 0.75rem 0.9rem;
                border-radius: 0.75rem; background: rgba(15, 23, 42, 0.04); margin: 0.75rem 0 1rem;">
                <span style="width: 1.05rem; height: 1.05rem; border: 0.18rem solid rgba(34, 197, 94, 0.20);
                border-top-color: #16a34a; border-radius: 50%; animation: feasibility-spin 0.9s linear infinite;"></span>
                <span style="font-size: 0.98rem; color: #0f172a;">{workflow_state.title()}</span>
                </div>
                <style>@keyframes feasibility-spin {{ from {{ transform: rotate(0deg); }} to {{ transform: rotate(360deg); }} }}</style>
                """,
                unsafe_allow_html=True,
            )
        elif result_state == "SUCCESS":
            st.success("Feasibility check succeeded.")
        elif result_state == "FAILED":
            st.error("Feasibility check failed.")
        elif workflow_state == "TERMINATED":
            st.error("Feasibility check terminated.")
        st.caption(f"Workflow state: {workflow_state} | Result state: {result_state}")
        if is_done:
            st.rerun()
    except Exception as error:
        st.warning(f"Unable to fetch feasibility check status: {error}")


def _render_feasibility_final_status(run_id):
    workflow_state = st.session_state.get(f"feasibility_workflow_state_{run_id}", "N/A")
    result_state = st.session_state.get(f"feasibility_result_state_{run_id}", "N/A")
    if result_state == "SUCCESS":
        mapping_id = st.session_state.get("uploaded_feasibility_mapping_id")
        if mapping_id:
            st.success(
                f"Feasibility check completed successfully. Mapping ID: {mapping_id}"
            )
            st.markdown("**Heatmap:**")
            heat_map_path = (
                f"feasibility_check/heatmap/{mapping_id}/{mapping_id}_corr.csv"
            )
            download_heatmap_csv = download_from_adls(heat_map_path)
            df = pd.read_csv(BytesIO(download_heatmap_csv))
            generate_heatmap(df)
            st.markdown("-----")
            st.markdown("**Line Chart:**")
            line_chart_path = (
                f"feasibility_check/line_chart/{mapping_id}/{mapping_id}_line_chart.csv"
            )
            download_line_chart_csv = download_from_adls(line_chart_path)
            df_line_chart = pd.read_csv(BytesIO(download_line_chart_csv))
            create_line_chart(df_line_chart)
            st.markdown("-----")
            summary_df_path = (
                f"feasibility_check/summary/{mapping_id}/{mapping_id}_summary.csv"
            )
            download_summary_df_csv = download_from_adls(summary_df_path)
            df_summary = pd.read_csv(BytesIO(download_summary_df_csv))
            df_summary = df_summary[
                [
                    "final_group",
                    "weeks_on_air",
                    "average_weekly_execution",
                    "cost_per_point",
                ]
            ]
            st.markdown("**Summary Table:**")
            st.dataframe(df_summary, hide_index=True)
            st.markdown("-----")
            st.markdown("**Pie Charts:**")
            pie_df_path = (
                f"feasibility_check/pie_chart/{mapping_id}/{mapping_id}_pie_chart.csv"
            )
            download_pie_df_csv = download_from_adls(pie_df_path)
            df_pie = pd.read_csv(BytesIO(download_pie_df_csv))
            generate_pie_chart(df_pie)
        else:
            st.success("Feasibility check completed successfully.")
    elif result_state == "FAILED":
        st.error("Feasibility check failed.")
    elif workflow_state == "TERMINATED":
        st.error("Feasibility check terminated.")
    st.caption(f"Workflow state: {workflow_state} | Result state: {result_state}")


@st.fragment(run_every="1s")
def _render_feasibility_elapsed_timer(run_id):
    start_time = st.session_state.get("feasibility_run_start_time")
    if start_time is None:
        return
    workflow_state = st.session_state.get(f"feasibility_workflow_state_{run_id}", "N/A")
    result_state = st.session_state.get(f"feasibility_result_state_{run_id}", "N/A")
    is_done = (
        result_state in {"SUCCESS", "FAILED", "CANCELED"}
        or workflow_state == "TERMINATED"
    )
    if is_done:
        end_time = st.session_state.get(f"feasibility_run_end_{run_id}")
        if end_time is None:
            end_time = time.time()
            st.session_state[f"feasibility_run_end_{run_id}"] = end_time
        elapsed_seconds = int(end_time - start_time)
        label = "Elapsed"
    else:
        elapsed_seconds = int(time.time() - start_time)
        label = "Running"
    minutes, seconds = divmod(elapsed_seconds, 60)
    st.caption(f"{label}: {minutes:02d}:{seconds:02d}")


def _render_feasibility_final_elapsed_timer(run_id):
    start_time = st.session_state.get("feasibility_run_start_time")
    if start_time is None:
        return
    end_time = st.session_state.get(f"feasibility_run_end_{run_id}")
    if end_time is None:
        end_time = time.time()
        st.session_state[f"feasibility_run_end_{run_id}"] = end_time
    elapsed_seconds = int(end_time - start_time)
    minutes, seconds = divmod(elapsed_seconds, 60)
    st.caption(f"Elapsed: {minutes:02d}:{seconds:02d}")


def _render_feasibility_status_section():
    response = st.session_state.get("feasibility_check_response")
    run_id = st.session_state.get("feasibility_run_id") or _get_run_id(response)
    if not st.session_state.get("show_feasibility_status"):
        return
    with st.container(border=True):
        st.markdown("### Feasibility Check Status")
        if run_id:
            st.success(f"Feasibility check started. Run ID: {run_id}")
            if st.session_state.get(f"feasibility_run_done_{run_id}", False):
                _render_feasibility_final_elapsed_timer(run_id)
                _render_feasibility_final_status(run_id)
            else:
                st.caption("Status refreshes automatically every 5 seconds.")
                _render_feasibility_elapsed_timer(run_id)
                _render_feasibility_run_status(run_id)
        else:
            st.error("Feasibility check response did not include a run ID.")
            if response:
                st.json(response)


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


def render_feasibility_check_page():
    st.markdown(
        """
        <style>
        .st-key-feasibility-panel {
            background: transparent;
            width: 100%;
        }
        .st-key-feasibility-panel [data-testid="stHorizontalBlock"] {
            align-items: end;
            margin-bottom: 0.7rem;
        }
        .st-key-feasibility-panel [data-testid="stSelectbox"] > label,
        .st-key-feasibility-panel [data-testid="stFileUploader"] > label,
        .st-key-feasibility-panel [data-testid="stTextInput"] > label {
            display: none;
        }
        .feasibility-field-label {
            margin: 0 0 0.35rem;
            color: #123042;
            font-size: 0.88rem;
            font-weight: 600;
        }
        .st-key-feasibility-panel [data-testid="stSelectbox"] > div > div,
        .st-key-feasibility-panel [data-testid="stTextInput"] input {
            width: 100%;
            min-height: 2.45rem;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.88);
        }
        .st-key-feasibility-panel [data-testid="stFileUploader"] section {
            width: 100%;
            min-height: 2.45rem;
            border-radius: 8px;
            background: transparent;
        }
        .st-key-feasibility-actions [data-testid="stButton"] button,
        .st-key-feasibility-actions [data-testid="stDownloadButton"] button {
            min-height: 2.45rem;
            width: min(100%, 18rem);
            margin: 0 auto;
            border-radius: 8px;
            color: white;
            border: 0;
            box-shadow: none;
        }
        .st-key-feasibility-run [data-testid="stButton"] button {
            background: #20c45b;
        }
        .st-key-feasibility-mapping [data-testid="stButton"] button {
            background: #ff5b63;
        }
        .st-key-feasibility-download [data-testid="stDownloadButton"] button {
            background: #20d900;
        }
        .st-key-feasibility-actions [data-testid="stButton"],
        .st-key-feasibility-actions [data-testid="stDownloadButton"] {
            display: flex;
            justify-content: center;
        }
    </style>
    """,
        unsafe_allow_html=True,
    )


    with st.container(border=True, key="feasibility-panel"):
        header_col, right_col = st.columns([4, 1], vertical_alignment="center")
        with header_col:
            st.markdown("### Feasibility Check")
            st.caption(
                "Validate data readiness, dependencies, and run prerequisites before transformation."
            )
        with right_col:
            render_cluster_status_controls()
        first_row = st.columns([1, 1, 1, 1], gap="medium")
        with first_row[0]:
            st.markdown(
                '<div class="feasibility-field-label">Vendor</div>', unsafe_allow_html=True
            )
            selected_vendor = st.selectbox(
                "Vendor",
                ["eki", "mphasize"],
                label_visibility="collapsed",
                key="feasibility_vendor",
            )
        with first_row[1]:
            st.markdown(
                '<div class="feasibility-field-label">Channel</div>', unsafe_allow_html=True
            )
            selected_channel = st.selectbox(
                "Channel",
                ["Media"],
                label_visibility="collapsed",
                key="feasibility_channel",
            )
        with first_row[2]:
            st.markdown(
                '<div class="feasibility-field-label">BMC</div>', unsafe_allow_html=True
            )
            bmc_list = get_bmc_list(selected_vendor, selected_channel)
            selected_bmc = st.selectbox(
                "BMC", bmc_list, label_visibility="collapsed", key="feasibility_bmc"
            )
        with first_row[3]:
            st.markdown(
                '<div class="feasibility-field-label">Channel List</div>',
                unsafe_allow_html=True,
            )
            channel_list = get_channel_list(selected_vendor, selected_channel)
            selected_channel_list = st.selectbox(
                "Channel List",
                channel_list,
                label_visibility="collapsed",
                key="feasibility_channel_list",
            )
        common_columns_str, common_columns_num = get_kpi_attributes(
            selected_vendor, selected_bmc, selected_channel, selected_channel_list
        )
        _clear_missing_selectbox_value("feasibility_kpi", common_columns_num)
        _clear_missing_selectbox_value("feasibility_attributes", common_columns_str)
        second_row = st.columns([1, 1, 1, 1], gap="medium")
        with second_row[0]:
            st.markdown(
                '<div class="feasibility-field-label">Start Date(YYYY-MM-DD)</div>',
                unsafe_allow_html=True,
            )
            start_date = st.date_input(
                "Start Date",
                value=None,
                format="YYYY-MM-DD",
                label_visibility="collapsed",
                key="feasibility_start_date",
            )
            # st.write("Selected Start Date:", start_date)

        with second_row[1]:
            st.markdown(
                '<div class="feasibility-field-label">End Date(YYYY-MM-DD)</div>',
                unsafe_allow_html=True,
            )
            end_date = st.date_input(
                "End Date",
                value=None,
                format="YYYY-MM-DD",
                label_visibility="collapsed",
                key="feasibility_end_date",
            )
            # st.write("Selected End Date:", end_date)
        with second_row[2]:
            st.markdown(
                '<div class="feasibility-field-label">KPI</div>', unsafe_allow_html=True
            )
            selected_kpi = st.selectbox(
                "KPI",
                common_columns_num,
                label_visibility="collapsed",
                key="feasibility_kpi",
            )
            # st.write("Selected KPI:", selected_kpi)
        with second_row[3]:
            st.markdown(
                '<div class="feasibility-field-label">Attributes</div>',
                unsafe_allow_html=True,
            )
            selected_attributes = st.multiselect(
                "Attributes",
                common_columns_str,
                label_visibility="collapsed",
                key="feasibility_attributes",
            )
            # st.write("Selected Attributes:", selected_attributes)
        mapping_inputs_ready = all(
            [
                selected_vendor,
                selected_channel,
                selected_bmc,
                selected_channel_list,
                selected_kpi,
                selected_attributes,
                start_date,
                end_date,
            ]
        )
        mapping_file_uploaded = False
        third_row = st.columns([1, 1, 1, 1], gap="medium")
        with third_row[0]:
            st.markdown(
                '<div class="feasibility-field-label">Mapping File</div>',
                unsafe_allow_html=True,
            )
            mapping_file = st.file_uploader(
                "Mapping File CSV",
                type=["csv"],
                key="feasibility_mapping_file",
                label_visibility="collapsed",
            )
            if mapping_file:
                if (
                    st.session_state.get("uploaded_feasibility_mapping_file_name")
                    != mapping_file.name
                ):
                    try:
                        mapping_id = re.search(r"^\d+", mapping_file.name).group()
                        location_path = f"feasibility_check/user_mapping/{mapping_id}"
                        upload_to_adls(
                            mapping_file.getvalue(), mapping_file.name, location_path
                        )
                        st.session_state["feasibility_mapping_file_uploaded"] = True
                        st.session_state["uploaded_feasibility_mapping_file_name"] = (
                            mapping_file.name
                        )
                        st.session_state["uploaded_feasibility_mapping_id"] = mapping_id
                        mapping_file_uploaded = True
                        st.success(f"Uploaded **{mapping_file.name}** to ADLS.")
                    except Exception as error:
                        st.session_state["feasibility_mapping_file_uploaded"] = False
                        mapping_file_uploaded = False
                        st.error(f"Upload failed: {error}")
                else:
                    mapping_file_uploaded = st.session_state.get(
                        "feasibility_mapping_file_uploaded", False
                    )
            else:
                st.session_state["feasibility_mapping_file_uploaded"] = False
                st.session_state.pop("uploaded_feasibility_mapping_file_name", None)
                st.session_state.pop("uploaded_feasibility_mapping_id", None)
                mapping_file_uploaded = False
        if mapping_file_uploaded:
            st.caption("Mapping file uploaded to ADLS.")
        with st.container(key="feasibility-actions"):
            action_columns = st.columns([1, 1, 1], gap="small")
            selected_mapping_file_uploaded = (
                mapping_file is not None
                and mapping_file_uploaded
                and st.session_state.get("uploaded_feasibility_mapping_file_name")
                == mapping_file.name
            )
            mapping_ready = selected_mapping_file_uploaded
            with action_columns[0], st.container(key="feasibility-run"):
                run_clicked = st.button(
                    "Run",
                    type="primary",
                    use_container_width=True,
                    disabled=not (mapping_ready),
                    key="feasibility_run",
                )
            with action_columns[1], st.container(key="feasibility-mapping"):
                mapping_clicked = st.button(
                    "Mapping",
                    type="secondary",
                    use_container_width=True,
                    disabled=not mapping_inputs_ready,
                    key="feasibility_mapping",
                )
            with action_columns[2], st.container(key="feasibility-download"):
                st.download_button(
                    "Download BMC",
                    data="Vendor,Channel,BMC,Channel List,KPI,Attributes\n",
                    file_name="bmc.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="feasibility_download_bmc",
                )

        if run_clicked and not selected_mapping_file_uploaded:
            st.error("Upload the mapping file to ADLS before running feasibility check.")
        elif run_clicked:
            # feasibility_job_id = os.environ.get("FEASIBILITY_JOB_ID")
            feasibility_job_id = 197359166162639
            mapping_id = st.session_state.get("uploaded_feasibility_mapping_id")
            mapping_name = st.session_state.get("uploaded_feasibility_mapping_file_name")
            if not feasibility_job_id:
                st.error("FEASIBILITY_JOB_ID is not configured.")
                return
            if not mapping_id or not mapping_name:
                st.error(
                    "Mapping file upload details are missing. Upload the mapping file again."
                )
                return
            try:
                response = run_feasibility_check(
                    mapping=mapping_name,
                    id=mapping_id,
                    job_id=feasibility_job_id,
                )
                if response.ok:
                    feasibility_response = response.json()
                    feasibility_run_id = _get_run_id(feasibility_response)
                    st.session_state["feasibility_check_response"] = feasibility_response
                    st.session_state["feasibility_run_id"] = feasibility_run_id
                    st.session_state["feasibility_run_start_time"] = time.time()
                    st.session_state["show_feasibility_status"] = True
                    st.success("Feasibility check submitted.")
                    if not feasibility_run_id:
                        st.warning("Feasibility check response did not include a run ID.")
                else:
                    st.error(
                        f"Feasibility check submission failed. Status code: {response.status_code}"
                    )
            except Exception as error:
                st.error(f"Feasibility check submission failed: {error}")
        elif mapping_clicked:
            # mapping_job_id = os.environ.get("MAPPING_JOB_ID")
            mapping_job_id = 47044413669720
            if not mapping_job_id:
                st.error("MAPPING_JOB_ID is not configured.")
                return
            try:
                response = run_mapping_job(
                    vendor=selected_vendor,
                    channel=selected_channel.lower(),
                    bmc=selected_bmc,
                    channel_list=selected_channel_list,
                    start_date=start_date,
                    end_date=end_date,
                    kpi=selected_kpi,
                    attributes=selected_attributes,
                    job_id=mapping_job_id,
                )
                if response.ok:
                    mapping_response = response.json()
                    st.session_state["mapping_job_response"] = mapping_response
                    st.session_state["mapping_run_id"] = _get_run_id(mapping_response)
                    st.session_state["mapping_run_start_time"] = time.time()
                    st.session_state.pop("mapping_cancel_requested", None)
                    st.session_state["feasibility_mapping_ready"] = True
                    st.session_state["show_mapping_status"] = True
                else:
                    st.error(
                        f"Mapping job submission failed. Status code: {response.status_code}"
                    )
            except Exception as error:
                st.error(f"Mapping job submission failed: {error}")

        if st.session_state.get("show_mapping_status"):
            _show_mapping_status_popup()

    _render_feasibility_status_section()