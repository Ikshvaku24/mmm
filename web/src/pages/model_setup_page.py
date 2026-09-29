import copy
import hashlib
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from src import codebase, projects
from src.app_functions import (DEFAULTS_TEXT, describe_fill, fill_blank_priors,
                               prepare_prior_table, read_file_bytes_as_table,
                               read_uploaded_file_as_table, render_prior_editor,
                               show_prior_file_popup, show_prior_validation,
                               to_serial_index_table, validate_prior)
from src.clusters import get_cluster_status, start_cluster
from src.config_editor import (init_config_state, job_owned_keys, live_config, load_config,
                               render_settings_section)
from src.generate_prior import generate_prior
from src.jobs import job_parameter_names, run_model_job
from src.runs import (fetch_run, job_params, job_state_label, local_time, render_run_panel,
                      render_runs_section, render_warnings_table)
from src.validation import show_input_check, validate_input_data

uuid = str(uuid.uuid4())

# How the page stays smooth: every block below is an st.fragment, so a click
# or an upload inside it refreshes that block only. A block refreshes the
# WHOLE page (st.rerun()) only when it changed something another block reads
# - the BMC or run name, a new datacube, a new prior file, a reused run - never
# on ordinary clicks.
#
# Where files go: nothing is uploaded block by block any more. Run Model saves
# the datacube, the settings, the prior file (and mapping/share files) into
# the run's own folder, Secondary Modelling/<BMC>/<run name>/, then starts the
# job (see projects.py). Every file on the page came either from an upload box
# ("upload") or from a reused run (its label) - the "origin".


def get_run_id_from_response(response):
    if not isinstance(response, dict):
        return None
    return response.get("run_id") or response.get("runId") or response.get("runID")


def _sha(data):
    return hashlib.sha1(data).hexdigest()


def _user_email():
    try:
        return (getattr(st.context, "headers", None) or {}).get("X-Forwarded-Email", "") or ""
    except Exception:
        return ""


def _now_local():
    offset = getattr(st.context, "timezone_offset", None)
    tz = timezone(-timedelta(minutes=offset)) if isinstance(offset, int) else timezone.utc
    return datetime.now(tz)


def _csv_file_name(name):
    stem = str(name or "feature_priors").rsplit(".", 1)[0]
    return f"{stem}.csv"


def _sentence(text):
    """First letter up, the rest untouched (str.capitalize would lower-case a
    BMC name inside the message)."""
    text = str(text or "")
    return text[:1].upper() + text[1:]


# --------------------------------------------------------------------------- #
# header: cluster and backend status
# --------------------------------------------------------------------------- #
def _cluster_state():
    """The cluster's state, reused for a few seconds so a page refresh does not
    wait on the Clusters API (the badge still polls every 5 s on its own)."""
    cached = st.session_state.get("_cluster_state")
    if cached and time.time() - cached[0] < 4:
        return cached[1]
    try:
        state = str(get_cluster_status() or "UNKNOWN").upper()
    except Exception:
        state = "UNKNOWN"
    st.session_state["_cluster_state"] = (time.time(), state)
    return state


@st.fragment(run_every="5s")
def render_cluster_status_controls():
    cluster_state = _cluster_state()
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
# 1. BMC and run - where the run is saved, and the BMC's earlier runs
# --------------------------------------------------------------------------- #
def _bmc_options():
    """The BMC folders (re-listed every minute)."""
    ss = st.session_state
    cached = ss.get("bmc_list")
    if cached and time.time() - cached[0] < 60:
        return cached[1], None
    try:
        names = projects.list_bmcs()
    except Exception as e:
        return (cached[1] if cached else []), f"Could not list the BMC folders: {e}"
    ss["bmc_list"] = (time.time(), names)
    return names, None


def _bmc_runs(bmc, force=False):
    """(runs, read errors) of a BMC, newest first (kept for 30 s)."""
    ss = st.session_state
    cached = ss.get("bmc_runs")
    if not force and cached and cached[0] == bmc and time.time() - cached[1] < 30:
        return cached[2], cached[3]
    try:
        rows, errors = projects.list_runs(bmc, cache=ss.setdefault("run_json_cache", {}),
                                          job_state=job_state_label)
    except Exception as e:
        return [], [f"Could not list the runs of {bmc}: {e}"]
    ss["bmc_runs"] = (bmc, time.time(), rows, errors)
    return rows, errors


def _forget_bmc_runs():
    st.session_state.pop("bmc_runs", None)
    st.session_state.pop("bmc_list", None)


def _run_target():
    """(bmc, run name, problem or None) for the run about to be started."""
    ss = st.session_state
    bmc = str(ss.get("bmc_name") or "").strip()
    run = str(ss.get("new_run_name") or "")
    if not bmc:
        return bmc, run, "choose or type a BMC name in ①"
    problem = projects.bmc_problem(bmc) or projects.name_problem(run, "run name")
    if problem:
        return bmc, run, problem
    if run in {r["run"] for r in _bmc_runs(bmc)[0]}:
        return bmc, run, f"'{run}' already exists in {bmc} - choose another run name"
    return bmc, run, None


@st.fragment
def _project_fragment():
    ss = st.session_state
    # values chosen by "Reuse inputs" or by a run that just started - set
    # before the widgets exist (a widget's value cannot change afterwards)
    for key in ("bmc_name", "new_run_name"):
        pending = ss.pop(f"_pending_{key}", None)
        if pending is not None:
            ss[key] = pending
            ss["_project_sig"] = None
    if "new_run_name" not in ss:
        ss["new_run_name"] = projects.default_run_name(_now_local())

    with st.container(border=True):
        bmc, run, problem = _run_target()
        st.markdown("### ① BMC and run" + ("" if problem else " ✅"))
        st.caption("Every run is saved in its own folder, Secondary Modelling/<BMC>/<run "
                   "name>/, with the inputs it used and its outputs. Pick a BMC to see its "
                   "runs - and reuse one's inputs to change them and run again.")
        options, list_problem = _bmc_options()
        current = ss.get("bmc_name")
        if current and current not in options:
            options = [current] + options
        left, right = st.columns(2)
        with left:
            st.selectbox("BMC name", options, index=None, key="bmc_name",
                         accept_new_options=True,
                         placeholder="Choose a BMC, or type a new name",
                         help="A folder under Secondary Modelling. Type a new name to start "
                              "a new BMC - its folder is created with the first run.")
        with right:
            st.text_input("New run name", key="new_run_name",
                          help="The run's folder inside the BMC. Letters, digits, spaces, "
                               "_ - and . - and not the name of an earlier run.")
        if list_problem:
            st.warning(list_problem)
        bmc, run, problem = _run_target()
        if problem and bmc:
            st.error(_sentence(problem))
        elif not problem:
            st.caption(f"This run will be saved in **Secondary Modelling/{bmc}/{run}/**.")

        # the Run block reads the BMC and the run name: refresh the page once
        signature = (bmc, run)
        before = ss.get("_project_sig")
        ss["_project_sig"] = signature
        if before is not None and before != signature:
            st.rerun()

        for note in ss.pop("reuse_notes", None) or []:
            st.info(note)
        if not bmc or projects.bmc_problem(bmc):
            return
        _render_bmc_runs(bmc)


def _render_bmc_runs(bmc):
    rows, errors = _bmc_runs(bmc)
    head, refresh = st.columns([5, 1], vertical_alignment="center")
    with head:
        st.markdown(f"**Runs in {bmc}**")
    with refresh:
        if st.button("↻ Refresh list", key="bmc_runs_refresh",
                     help="Read this BMC's runs and their status again. The list is "
                          "otherwise re-read at most every 30 s; the panel of a running "
                          "run updates itself every 5 s."):
            _bmc_runs(bmc, force=True)
            st.rerun(scope="fragment")
    for error in errors[:3]:
        st.warning(error)
    if not rows:
        st.caption(f"No runs in {bmc} yet - this run will be its first.")
        return
    table = pd.DataFrame([{
        "run": r["run"],
        "status": r["status"],
        "submitted": local_time(r["submitted_ms"]),
        "by": r["submitted_by"],
        "reused from": r["source"],
        "changed": ", ".join(r["changed"]),
    } for r in rows])
    event = st.dataframe(table, use_container_width=True, hide_index=True,
                         on_select="rerun", selection_mode="single-row",
                         key=f"bmc_runs_table_{_sha(bmc.encode())[:8]}")
    picked = list(getattr(getattr(event, "selection", None), "rows", []) or [])
    if not picked:
        st.caption("Select a run to see its results, job log and zip - or to reuse its inputs.")
        return
    chosen = rows[picked[0]]
    render_run_panel(projects.make_ref(chosen["job_run_id"], bmc, chosen["run"]), "bmc",
                     on_reuse=reuse_run)


def _fingerprints():
    """What the run would use - to tell whether anything changed since the
    run the inputs came from."""
    ss = st.session_state
    return {"data_file": projects.sha(ss.get("datacube_bytes")),
            "prior_file": projects.prior_signature(ss.get("prior_working_table")),
            "mapping_file": projects.sha(ss.get("mapping_bytes")) if ss.get("mapping_bytes") else "",
            "share_file": projects.sha(ss.get("share_bytes")) if ss.get("share_bytes") else ""}


def _remember_source(ref, label):
    st.session_state["source_run"] = {
        "ref": dict(ref), "label": label, "fingerprints": _fingerprints(),
        "config": copy.deepcopy(st.session_state.get("cfg_values") or {})}


def _changes_since_source():
    """(source label, [what changed], [setting changes as rows]) - None
    without a source run."""
    ss = st.session_state
    src = ss.get("source_run")
    if not src:
        return None
    now = _fingerprints()
    items = [projects.KIND_LABELS[k] for k in ("data_file", "prior_file", "mapping_file",
                                                "share_file")
             if now[k] != (src.get("fingerprints") or {}).get(k, "")]
    settings = projects.config_changes(src.get("config"), ss.get("cfg_values") or {},
                                       skip=job_owned_keys())
    if settings:
        items.insert(0, f"settings ({len(settings)})")
    return src["label"], items, settings


def changes_table(rows, before="before", after="now"):
    """Setting changes as a small table - one row each, easy to scan."""
    st.dataframe(pd.DataFrame([{"setting": r["setting"], before: r["before"],
                                after: r["after"]} for r in rows]),
                 use_container_width=True, hide_index=True)


def reuse_run(ref):
    """Load a run's datacube, settings, prior file (and mapping/share files)
    into the page - to edit and run again as a NEW run."""
    ss = st.session_state
    label = projects.ref_label(ref)
    with st.spinner(f"Loading the inputs of {label} ..."):
        try:
            if projects.has_folder(ref):
                paths = projects.run_inputs(ref, request=projects.read_request(ref["bmc"], ref["run"]))
            else:
                paths = projects.run_inputs(ref, job_params=job_params(fetch_run(ref.get("job_run_id"))))
            got, errors = projects.fetch_files(paths)
        except Exception as e:
            st.error(f"Could not read the inputs of {label}: {e}")
            return
    missing = [projects.KIND_LABELS[k] for k in ("data_file", "prior_file") if k not in got]
    if missing:
        st.error(f"{label} has no {' or '.join(missing)} to reuse"
                 + (f" ({'; '.join(errors.values())})" if errors else "") + ".")
        return
    notes = []
    # the settings first: the datacube is read with their column names
    if "config_file" in got:
        parsed = codebase.parse_config_yaml(
            got["config_file"][1].decode("utf-8-sig", errors="replace"))
        if parsed.ok:
            load_config(parsed.value)
        else:
            notes.append(f"The settings of {label} could not be read ("
                         + "; ".join(parsed.errors) + ") - the current settings are kept.")
    else:
        notes.append(f"{label} has no settings file - the current settings are kept.")
    cfg = ss.get("cfg_values") or {}
    name, data = got["data_file"]
    _load_datacube(data, name, cfg, origin=label)
    name, data = got["prior_file"]
    table, error = read_file_bytes_as_table(data, name)
    if error:
        notes.append(f"The prior file of {label} could not be read: {error}")
    else:
        _set_prior(prepare_prior_table(table), name, origin=label)
    for kind in ("mapping", "share"):
        item = got.get(f"{kind}_file")
        if item:
            _set_side(kind, item[1], item[0], origin=label)
            _check_side(kind, ss.get("datacube_df"), cfg)   # now, not on a later refresh
        else:
            _clear_side(kind)
    _remember_source(ref, label)
    if projects.has_folder(ref):
        taken = {r["run"] for r in _bmc_runs(ref["bmc"])[0]}
        ss["_pending_bmc_name"] = ref["bmc"]
        ss["_pending_new_run_name"] = projects.next_free_name(ref["run"], taken)
    ss.pop("gen_result", None)              # a generated file belongs to other inputs
    notes.insert(0, f"Loaded the inputs of **{label}** - datacube, settings, prior file"
                 + ("".join(f", {k} file" for k in ("mapping", "share") if f"{k}_file" in got))
                 + ". Change what you need below, then Run Model saves them as a new run.")
    ss["reuse_notes"] = notes
    st.rerun()


# --------------------------------------------------------------------------- #
# 2. input data
# --------------------------------------------------------------------------- #
def _datacube_key(file_bytes, cfg):
    data_cfg, run_cfg = cfg.get("data") or {}, cfg.get("run") or {}
    return (_sha(file_bytes), data_cfg.get("sheet"), data_cfg.get("date_format"),
            run_cfg.get("date_col"), run_cfg.get("region_col"), run_cfg.get("dv_col"))


def _load_datacube(file_bytes, name, cfg, origin="upload"):
    """Read and check a datacube (from an upload or a reused run)."""
    ss = st.session_state
    with st.spinner("Reading and checking the datacube ..."):
        read = codebase.read_datacube(file_bytes, name, cfg)
        ss["datacube_hash"] = _sha(file_bytes)
        ss["datacube_bytes"] = file_bytes
        ss["datacube_name"] = name
        ss["datacube_origin"] = origin
        if read.ok:
            check = validate_input_data(read.value, cfg, show=False)
            ss["datacube_df"] = read.value
            ss["datacube_regions"] = codebase.datacube_regions(read.value, cfg)
            ss["datacube_check"] = check
            ss["datacube_ok"] = check.ok
            ss.pop("datacube_read_errors", None)
        else:
            ss["datacube_df"] = None
            ss["datacube_regions"] = []
            ss["datacube_check"] = None
            ss["datacube_ok"] = False
            ss["datacube_read_errors"] = read.errors
    ss["datacube_key"] = _datacube_key(file_bytes, cfg)
    if origin != "upload":                  # empty the upload box: this file replaces it
        ss["datacube_uploader_version"] = ss.get("datacube_uploader_version", 0) + 1
        ss.pop("datacube_upload_sig", None)


def _reset_datacube():
    for key in ("datacube_df", "datacube_key", "datacube_hash", "datacube_bytes",
                "datacube_name", "datacube_regions", "datacube_check", "datacube_ok",
                "datacube_read_errors", "datacube_origin", "datacube_upload_sig"):
        st.session_state.pop(key, None)


@st.fragment
def _data_fragment():
    ss = st.session_state
    with st.container(border=True):
        st.markdown("### ② Input data" + (" ✅" if ss.get("datacube_ok") else ""))
        uploaded = st.file_uploader(
            "Choose input data file",
            type=["xlsx", "csv"],
            key=f"input_data_file_{ss.get('datacube_uploader_version', 0)}",
            help="The datacube: one row per region x date, with the date, region, KPI "
                 "and feature columns named in Model settings (run.date_col / "
                 "region_col / dv_col).",
        )
        cfg = live_config() or {}
        if uploaded is not None:
            data = uploaded.getvalue()
            signature = (uploaded.name, _sha(data))
            if ss.get("datacube_upload_sig") != signature:
                _load_datacube(data, uploaded.name, cfg, origin="upload")
                ss["datacube_upload_sig"] = signature
                st.rerun()                # templates, generation and priors use it
        elif ss.get("datacube_origin") == "upload":
            _reset_datacube()
            st.rerun()                    # the other blocks forget the datacube too
        if ss.get("datacube_bytes") is None:
            st.caption("Upload the datacube - or reuse a run's inputs in ①.")
            return
        if ss.get("datacube_key") != _datacube_key(ss["datacube_bytes"], cfg):
            # a column name or the sheet changed in Model settings: read it again
            _load_datacube(ss["datacube_bytes"], ss["datacube_name"], cfg,
                           origin=ss.get("datacube_origin") or "upload")
            st.rerun()

        origin = ss.get("datacube_origin")
        if origin and origin != "upload":
            st.caption(f"Using **{ss.get('datacube_name')}** from {origin}. Upload a file "
                       "above to replace it.")
        for error in ss.get("datacube_read_errors") or []:
            st.error(f"Could not read the datacube: {error}")
        if ss.get("datacube_check") is not None:
            show_input_check(ss["datacube_check"])


# --------------------------------------------------------------------------- #
# 4. mapping and share files
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


def _set_side(kind, file_bytes, name, origin):
    ss = st.session_state
    ss[f"{kind}_bytes"] = file_bytes
    ss[f"{kind}_name"] = name
    ss[f"{kind}_origin"] = origin
    ss[f"{kind}_ok"] = False
    ss.pop(f"{kind}_check", None)
    ss.pop(f"{kind}_prior_check", None)
    if origin != "upload":
        ss[f"{kind}_uploader_version"] = ss.get(f"{kind}_uploader_version", 0) + 1
        ss.pop(f"{kind}_upload_sig", None)


def _check_side(kind, df, cfg):
    """codebase 1's verdict on the current mapping/share file, cached per file
    and datacube. True when it had to be (re)checked."""
    ss = st.session_state
    check_key = (_sha(ss[f"{kind}_bytes"]), ss.get("datacube_hash"))
    cached = ss.get(f"{kind}_check")
    if cached and cached[0] == check_key:
        return False
    validate = codebase.validate_mapping if kind == "mapping" else codebase.validate_share
    with st.spinner(f"Checking the {SIDE_FILES[kind][0].lower()} ..."):
        ss[f"{kind}_check"] = (check_key, validate(ss[f"{kind}_bytes"], ss[f"{kind}_name"],
                                                   df, cfg))
    ss[f"{kind}_ok"] = ss[f"{kind}_check"][1].ok
    return True


def _clear_side(kind):
    had = bool(st.session_state.get(f"{kind}_bytes"))
    for key in (f"{kind}_bytes", f"{kind}_name", f"{kind}_ok", f"{kind}_check",
                f"{kind}_prior_check", f"{kind}_origin", f"{kind}_upload_sig"):
        st.session_state.pop(key, None)
    return had


def _cached(key, stamp, build):
    """build() once per stamp (backend version, datacube) - not on every refresh."""
    cached = st.session_state.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    value = build()
    st.session_state[key] = (stamp, value)
    return value


def _render_side_file(kind, df, cfg):
    ss = st.session_state
    title, help_text = SIDE_FILES[kind]
    version = codebase.status().get("version")
    st.markdown(f"**{title}**" + (" ✅" if ss.get(f"{kind}_ok") else ""))
    st.caption(help_text)
    sample_col, template_col = st.columns(2)
    with sample_col:
        sample = _cached(f"sample_{kind}", version, lambda: codebase.sample_file(kind))
        st.download_button("Download sample", data=sample.value or b"",
                           file_name=f"{kind}_sample.csv", mime="text/csv",
                           disabled=not sample.ok, key=f"{kind}_sample_download",
                           on_click="ignore",
                           help="codebase 1's example file - the format, with example names.")
    with template_col:
        template = None
        if df is not None:
            template = _cached(f"template_{kind}",
                               (version, ss.get("datacube_hash")),
                               lambda: codebase.template_file(kind, df, cfg))
        st.download_button("Download template", data=(template.value if template and template.ok else b""),
                           file_name=f"{kind}_template.csv", mime="text/csv",
                           disabled=template is None or not template.ok,
                           key=f"{kind}_template_download", on_click="ignore",
                           help="The sample's columns with one row per datacube variable. "
                                + ("Leave vendor_variable blank on rows you do not map."
                                   if kind == "mapping" else
                                   "Delete the rows you have no share for."))
    uploaded = st.file_uploader(f"Choose {title.lower()}", type=["csv", "xlsx"],
                                key=f"{kind}_file_{ss.get(f'{kind}_uploader_version', 0)}")
    if uploaded is not None:
        data = uploaded.getvalue()
        signature = (uploaded.name, _sha(data))
        if ss.get(f"{kind}_upload_sig") != signature:
            _set_side(kind, data, uploaded.name, "upload")
            ss[f"{kind}_upload_sig"] = signature
    elif ss.get(f"{kind}_origin") == "upload":
        if _clear_side(kind):
            st.rerun()                    # the case preview and the checklist change
    if not ss.get(f"{kind}_bytes"):
        return
    if _check_side(kind, df, cfg):
        st.rerun()                        # the case preview and the checklist change
    outcome = ss[f"{kind}_check"][1]
    origin = ss.get(f"{kind}_origin")
    if origin and origin != "upload":
        note, remove = st.columns([4, 1], vertical_alignment="center")
        with note:
            st.caption(f"Using **{ss.get(f'{kind}_name')}** from {origin}.")
        with remove:
            if st.button("Remove", key=f"remove_{kind}", help=f"Run without a {title.lower()}."):
                _clear_side(kind)
                st.rerun()
    if outcome.ok:
        v = outcome.value
        if kind == "mapping":
            st.success(f"{v['links']} links, {v['vendor_variables']} vendor variables - "
                       + ("with contributions." if v["has_contribution"]
                          else "no contributions (grouping only)."))
        else:
            st.success(f"{v['rows']} rows in sections {', '.join(v['sections'])}.")
        with st.expander("Preview"):
            st.dataframe(v["table"], use_container_width=True, height=240)
    for error in outcome.errors:
        st.error(error)
    if df is None:
        st.caption("Upload the datacube to check the names in this file against it.")


@st.fragment
def _pre_model_fragment():
    cfg = live_config() or {}
    df = st.session_state.get("datacube_df")
    with st.container(border=True):
        st.markdown("### ④ Mapping and share files (optional)")
        st.caption("Inputs to codebase 1's prior generator (the Prior file block below "
                   "says which case they lead to). Without either, it writes a blank "
                   "template for you to fill in.")
        left, right = st.columns(2, gap="large")
        with left:
            _render_side_file("mapping", df, cfg)
        with right:
            _render_side_file("share", df, cfg)


# --------------------------------------------------------------------------- #
# 5. the prior file - codebase 1 generates it, you fill it in, you upload it
# --------------------------------------------------------------------------- #
# FEATURE_PRIOR_GUIDE.md section 5, in the app's words
CASE_EXPLAIN = {
    "a": "The mapping file has the vendor's **contributions**: codebase 1 inverts them "
         "into prior means (contribution / support / mean KPI, per region) and takes the "
         "signs from them. A share file, if given, supplies the pillars and the baseline "
         "flag, and the means of variables the vendor did not report.",
    "b": "The mapping file has **no contributions**, so the means come from the **share "
         "file** (each piece's share of sales, split by spend inside media and expert "
         "pillars). The mapping only groups variables.",
    "c": "The means come from the **share file**: each piece's share of sales, split by "
         "spend inside media and expert pillars.",
    "d": "No mapping contributions and no share file: codebase 1 writes a **blank "
         "template** - one row per datacube variable, means and signs left for you to "
         "fill in.",
}
FILE_TEXT = {
    "feature_priors_national.csv": (
        "One row per variable with its national prior mean.",
        "For **pooling: hierarchical** - the regions share one prior and borrow "
        "strength from each other (codebase 1's default)."),
    "feature_priors_regional.csv": (
        "The same rows, plus one override row per region with that region's own mean.",
        "For **pooling: independent** - each region gets its own prior."),
    "prior_calculation.xlsx": ("The arithmetic behind every generated mean.", ""),
    "prior_calculation.csv": ("The arithmetic behind every generated mean.", ""),
    "00_warnings.zip": ("What the generator objected to - listed below as well.", ""),
}
FILLED_TEXT = (
    "**What the generated file fills in:** `variable`; `global_prior_mean` and "
    "`sign_constraint` where the mapping or share file covered the variable; "
    "`prior_sd_basis` = relative and `prior_mean_basis` = median; `pillar` and "
    "`baseline` from the share file.  \n"
    "**What it leaves blank - yours to decide:** `pooling`, `global_prior_sd` (read as "
    "relative: 0.02 pins the variable, 0.3–0.5 lets the data speak), `regional_sd_prior`, "
    "`center_mode` (consider `mean` for TDP, price and category), `scale_mode`, "
    "`contribution_reference` - and the mean and sign of a variable neither file "
    "covered.  \n"
    "**Use** fills what is still blank with the defaults above. A file you fill in Excel "
    "and choose in step 3 keeps codebase 1's own defaults for any blank (pooling "
    "hierarchical, sd 1.0 - 0.5 for a free sign - regional sd 0.5).")


def _generation_signature(cfg):
    data, run = cfg.get("data") or {}, cfg.get("run") or {}
    keys = (data.get("dv_aggregation"), data.get("national_basis"), data.get("sheet"),
            data.get("date_format"), run.get("date_col"), run.get("region_col"),
            run.get("dv_col"), run.get("holdout_periods"), run.get("holdout_fraction"),
            run.get("cadence"), run.get("scaling_window"))
    files = tuple(_sha(st.session_state[k]) if st.session_state.get(k) else ""
                  for k in ("datacube_bytes", "mapping_bytes", "share_bytes"))
    return (keys, files)


def _set_prior(prepared, name, origin, from_generator=False, note=None):
    """A prior table (uploaded, generated or from a reused run) becomes the
    prior file the run will use. `note` is shown under it (what Use filled)."""
    ss = st.session_state
    ss["prior_source_name"] = name
    ss["prior_working_table"] = prepared
    ss["prior_effective_bytes"] = codebase.prior_csv_bytes(prepared)
    ss["prior_effective_name"] = _csv_file_name(name)
    ss["prior_is_edited"] = False
    ss["prior_popup_toggle_version"] = 0
    ss["prior_popup_editor_version"] = ss.get("prior_popup_editor_version", 0) + 1
    ss["prior_from_generator"] = from_generator
    ss["prior_origin"] = origin
    ss["prior_fill_note"] = note
    if origin != "upload":                  # empty the upload box: this file replaces it
        ss["prior_uploader_version"] = ss.get("prior_uploader_version", 0) + 1
        ss.pop("prior_upload_sig", None)


# --- a generated file is a DRAFT: preview / edit it here, download it, or Use it
def _draft_prefix(name):
    return "gen_" + re.sub(r"[^A-Za-z0-9]+", "_", str(name).rsplit(".", 1)[0]).strip("_")


def _draft(name, data):
    """The generated prior file as an editable draft (kept until the next
    generation), or None when it cannot be read."""
    drafts = st.session_state.setdefault("gen_drafts", {})
    if name not in drafts:
        table, error = read_file_bytes_as_table(data, name)
        if error:
            return None
        drafts[name] = {"table": prepare_prior_table(table), "edited": False}
    return drafts[name]["table"]


def _save_draft(name, table):
    ss = st.session_state
    draft = ss["gen_drafts"][name]
    draft["table"] = to_serial_index_table(table)
    draft["edited"] = True
    prefix = _draft_prefix(name)
    ss[f"{prefix}_popup_editor_version"] = ss.get(f"{prefix}_popup_editor_version", 0) + 1


def _use_generated(file_name, table):
    """'Use': the draft becomes the run's prior file, its blanks filled with
    the defaults (pooling global, sign free, sd 1, regional sd 0)."""
    filled, changes = fill_blank_priors(table)
    note = (f"Blanks filled with the defaults: {describe_fill(changes)}."
            if changes else "Nothing was blank - used as it is.")
    _set_prior(prepare_prior_table(filled), file_name, origin="generated",
               from_generator=True, note=note)
    st.toast(f"{file_name} is now the run's prior file.", icon="✅")
    st.rerun()


def _close_draft_popup():
    for name in st.session_state.get("gen_drafts") or {}:   # reopen in view mode
        key = f"{_draft_prefix(name)}_popup_toggle_version"
        st.session_state[key] = st.session_state.get(key, 0) + 1


@st.dialog("Generated prior file", width="large", dismissible=True,
           on_dismiss=_close_draft_popup)
def show_generated_prior_popup(name):
    draft = (st.session_state.get("gen_drafts") or {}).get(name)
    if draft is None:
        st.warning("This generated file is no longer available - generate it again.")
        return
    st.markdown(f"**{name}** - a draft from codebase 1's generator"
                + (", edited here" if draft["edited"] else "")
                + ". It is not the run's prior file until you press **Use this file**.")
    st.caption("Fill in what only you can decide - the sd, the sign, the pooling ... - "
               "then **Use this file**, or download it. Use fills whatever is still "
               "blank: " + DEFAULTS_TEXT)
    render_prior_editor(draft["table"], _draft_prefix(name), lambda t: _save_draft(name, t),
                        use=lambda t: _use_generated(name, t), download_name=name,
                        saved_message="Saved to the draft - press Use this file to make it "
                                      "the run's prior file.",
                        after_save="dialog")


def _side_table(kind):
    """The validated mapping/share table, or None."""
    if not st.session_state.get(f"{kind}_ok"):
        return None
    cached = st.session_state.get(f"{kind}_check")
    return cached[1].value["table"] if cached and cached[1].ok else None


def _render_generate_step(cfg, df):
    st.markdown("#### 1 · Generate it")
    mapping_bad = bool(st.session_state.get("mapping_bytes")) and not st.session_state.get("mapping_ok")
    share_bad = bool(st.session_state.get("share_bytes")) and not st.session_state.get("share_ok")
    case = codebase.expected_case(_side_table("mapping"), _side_table("share"))
    letter = case.value["case"] if case.ok else "?"
    if case.ok:
        st.info(f"**Case {letter}** with the files in ④. {CASE_EXPLAIN[letter]}")
    if mapping_bad or share_bad:
        st.warning("Fix or remove the mapping / share file above first - generation "
                   "would stop on it.")
    has_table = st.session_state.get("prior_working_table") is not None
    restrict = False
    if has_table and letter in ("a", "b", "c"):
        restrict = st.checkbox(
            "List only the variables of my current prior file", key="gen_restrict",
            help="Instead of every datacube column. The mapping and share files may then "
                 "name only those variables - codebase 1's rule: the prior file may carry "
                 "more variables than they do, never fewer.")
    ready = (df is not None and st.session_state.get("datacube_ok", False)
             and not (mapping_bad or share_bad))
    if st.button("Generate prior file", key="generate_prior_button", type="primary",
                 disabled=not ready):
        with st.spinner("Running codebase 1's pre-model step ..."):
            outcome = generate_prior(
                (st.session_state["datacube_bytes"], st.session_state["datacube_name"]), cfg,
                mapping=(st.session_state["mapping_bytes"], st.session_state["mapping_name"])
                if st.session_state.get("mapping_ok") else None,
                share=(st.session_state["share_bytes"], st.session_state["share_name"])
                if st.session_state.get("share_ok") else None,
                restrict_to=st.session_state.get("prior_working_table") if restrict else None)
        st.session_state["gen_result"] = outcome
        st.session_state["gen_signature"] = _generation_signature(cfg)
        st.session_state.pop("gen_drafts", None)     # the drafts of the previous generation
    if df is None:
        st.caption("Upload a datacube that passes the checks first.")


def _render_fill_step(cfg):
    outcome = st.session_state.get("gen_result")
    if outcome is None:
        return
    st.markdown("#### 2 · Fill it in")
    if not outcome.ok:
        for error in outcome.errors:
            st.error(error)
        return
    result = outcome.value
    if st.session_state.get("gen_signature") != _generation_signature(cfg):
        st.warning("The datacube, a mapping/share file or a setting it depends on has "
                   "changed since this was generated - generate it again.")
    st.caption("A generated prior file is a DRAFT: the sd, the sign, the pooling and the "
               "rest are yours to decide. For the file of your pooling choice: **Download** "
               "it, fill it in Excel and choose it in step 3 - or **Preview / Edit** it here "
               "and then use or download it - or **Use** it as it is. **Use** makes it the "
               "run's prior file and fills whatever is still blank: " + DEFAULTS_TEXT)
    drafts = st.session_state.get("gen_drafts") or {}
    for name, data in result["files"].items():
        what, use = FILE_TEXT.get(name, ("", ""))
        text_col, dl_col, edit_col, use_col = st.columns([4, 1.2, 1.4, 1],
                                                         vertical_alignment="center")
        if not name.startswith("feature_priors_"):
            with text_col:
                st.markdown(f"**{name}**  \n{what} {use}")
            with dl_col:
                st.download_button("Download", data=data, file_name=name,
                                   key=f"gen_download_{name}", on_click="ignore",
                                   use_container_width=True)
            continue
        draft = _draft(name, data)
        edited = bool((st.session_state.get("gen_drafts") or drafts).get(name, {}).get("edited"))
        with text_col:
            st.markdown(f"**{name}**" + (" · *edited here, not used yet*" if edited else "")
                        + f"  \n{what} {use}")
        with dl_col:
            st.download_button("Download",
                               data=codebase.prior_csv_bytes(draft) if edited else data,
                               file_name=name, key=f"gen_download_{name}", on_click="ignore",
                               use_container_width=True,
                               help="The draft as it is now" + (" - with your edits."
                                                                if edited else "."))
        with edit_col:
            if st.button("Preview / Edit", key=f"gen_preview_{name}", use_container_width=True,
                         disabled=draft is None,
                         help="See the draft, fill it in here (paste from Excel too), then "
                              "use it or download it. It is not used until you say so."):
                show_generated_prior_popup(name)
        with use_col:
            if st.button("Use", key=f"gen_use_{name}", type="primary",
                         use_container_width=True, disabled=draft is None,
                         help="Make it the run's prior file. Blank cells get the defaults: "
                              "pooling global, sign free, sd 1, regional sd 0."):
                _use_generated(name, draft)
    st.markdown(FILLED_TEXT)
    rows = result.get("warnings") or []
    if rows:
        with st.expander(f"Warnings from the generator ({len(rows)})"):
            docs = result.get("warning_docs") or {}
            render_warnings_table(pd.DataFrame(rows), lambda slug: docs.get(slug), key="gen")
    if outcome.log:
        with st.expander("Log"):
            with st.container(height=300):
                st.code(outcome.log, language="text")


def _prior_is_valid():
    table = st.session_state.get("prior_working_table")
    return table is not None and validate_prior(table).ok


@st.fragment
def _prior_fragment():
    ss = st.session_state
    cfg = live_config() or {}
    df = ss.get("datacube_df")
    with st.container(border=True):
        st.markdown("### ⑤ Prior file" + (" ✅" if _prior_is_valid() else ""))
        st.caption("Every run needs a prior file: one row per variable with its prior "
                   "mean, sign and width. codebase 1 generates it from the datacube, you "
                   "fill in what only you can decide, then choose it in step 3.")
        with st.expander("1 · Generate it from the datacube · 2 · Download it and fill it in",
                         expanded=ss.get("prior_working_table") is None):
            _render_generate_step(cfg, df)
            _render_fill_step(cfg)
        st.markdown("#### 3 · Your prior file")
        st.caption("The file you filled in (CSV or Excel), or a prior file from an earlier "
                   "run - or reuse a run's inputs in ①. It is saved in the run's folder "
                   "when you press Run Model.")
        up_col, edit_col = st.columns([3, 1], vertical_alignment="bottom")
        with up_col:
            prior_file = st.file_uploader(
                "Choose prior file",
                type=["csv", "xlsx"],
                key=f"prior_file_{ss.get('prior_uploader_version', 0)}",
            )
        with edit_col:
            edit_clicked = st.button("Preview / Edit", key="prior_preview_edit",
                                     use_container_width=True,
                                     disabled=ss.get("prior_working_table") is None,
                                     help="See the table, edit it, paste from Excel, "
                                          "download it.")
        handle_prior_file_section(prior_file, read_uploaded_file_as_table, show_prior_file_popup)
        if edit_clicked:
            show_prior_file_popup()


def handle_prior_file_section(prior_file, read_uploaded_file_as_table, show_prior_file_popup):
    ss = st.session_state
    if prior_file:
        prior_table, prior_error = read_uploaded_file_as_table(prior_file)
        signature = (prior_file.name, _sha(prior_file.getvalue()))
        if ss.get("prior_upload_sig") != signature and prior_table is not None:
            _set_prior(prepare_prior_table(prior_table), prior_file.name, origin="upload")
            ss["prior_upload_sig"] = signature
            st.rerun()                    # the Run checklist re-checks against it
        if prior_error:
            st.toast(f"Error: {prior_error}", icon="🚨")
    table = ss.get("prior_working_table")
    if table is None:
        ss["prior_is_edited"] = False
        return
    origin = ss.get("prior_origin")
    where = (" (generated by codebase 1)" if origin == "generated" or
             (origin is None and ss.get("prior_from_generator"))
             else f" (from {origin})" if origin and origin != "upload" else "")
    st.success(f"Prior file in use: {ss.get('prior_effective_name', 'feature_priors.csv')}"
               + where + (" - edited here" if ss.get("prior_is_edited") else ""))
    if ss.get("prior_fill_note"):
        st.caption(ss["prior_fill_note"])
    show_prior_validation(validate_prior(table), compact=True)


# --------------------------------------------------------------------------- #
# 6. run
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


def _job_parameters():
    """The job's parameter names (re-read every 5 minutes); None if unreadable."""
    ss = st.session_state
    cached = ss.get("_job_params")
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    job_id = os.environ.get("MDR_JOB_ID", "")
    try:
        names = job_parameter_names(job_id) if job_id else None
    except Exception:
        names = None
    ss["_job_params"] = (time.time(), names)
    return names


def _readiness():
    ss = st.session_state
    bmc, run, problem = _run_target()
    ready = {"BMC and run name": (problem is None,
                                  problem or f"Secondary Modelling/{bmc}/{run}/")}
    ready["Input data checked"] = (ss.get("datacube_bytes") is not None
                                   and bool(ss.get("datacube_ok")),
                                   ss.get("datacube_name", ""))
    ready["Model settings valid"] = (bool(ss.get("cfg_valid")), "")
    has_prior = ss.get("prior_working_table") is not None
    ready["Prior file valid"] = (_prior_is_valid(),
                                 ss.get("prior_effective_name", "") if has_prior else "")
    for kind, label in (("mapping", "Mapping file"), ("share", "Share file")):
        if not ss.get(f"{kind}_bytes"):
            continue
        ok = bool(ss.get(f"{kind}_ok"))
        note = ss.get(f"{kind}_name", "") if ok else "fix it, or remove it"
        if ok and has_prior:
            against = _side_against_prior(kind)
            if not against.ok:
                ok, note = False, "names variables the prior file does not have: " + \
                    " ".join(against.errors)[:300]
        ready[f"{label} valid"] = (ok, note)
    params = _job_parameters()
    if params is not None and not {"bmc_name", "run_name"} <= params:
        ready["The job has the parameters bmc_name and run_name"] = (
            False, "add both to the job's Job parameters, default empty - see changes.md")
    return ready


@st.dialog("Nothing has changed")
def _confirm_unchanged(label):
    st.warning(f"The datacube, the settings and the prior file (and the mapping/share "
               f"files) are exactly those of **{label}**. With the same seed "
               "(sampler.seed) a new run gives the same result.")
    st.markdown("Run it again anyway?")
    yes, no = st.columns(2)
    with yes:
        if st.button("Run anyway", type="primary", key="confirm_rerun",
                     use_container_width=True):
            _start_run()
    with no:
        if st.button("Cancel", key="cancel_rerun", use_container_width=True):
            st.rerun()


def _start_run():
    """Save the inputs into the run's folder, record the request, start the job."""
    ss = st.session_state
    bmc, run, problem = _run_target()
    if problem:
        st.error(_sentence(problem))
        return None
    cfg = ss.get("cfg_values")
    check = codebase.validate_config(cfg)
    if not check.ok:
        st.error("The settings are not valid: " + " | ".join(check.errors))
        return None
    config_text = codebase.config_yaml(cfg)
    if not config_text.ok:
        st.error("The settings could not be written: " + " | ".join(config_text.errors))
        return None
    prior = validate_prior(ss["prior_working_table"])
    if not prior.ok:
        st.error("Fix the prior file first: " + " | ".join(prior.errors))
        return None
    files = {"config_file": ("config.yaml", config_text.value.encode("utf-8")),
             "data_file": (ss["datacube_name"], ss["datacube_bytes"]),
             "prior_file": (_csv_file_name(ss.get("prior_effective_name")), prior.value["csv"])}
    for kind in ("mapping", "share"):
        if ss.get(f"{kind}_bytes"):
            files[f"{kind}_file"] = (ss[f"{kind}_name"], ss[f"{kind}_bytes"])
    changes = _changes_since_source()
    src = ss.get("source_run")
    source = None
    if src:
        ref = src.get("ref") or {}
        source = {"bmc": ref.get("bmc"), "run": ref.get("run"),
                  "job_run_id": ref.get("job_run_id")}
    job_id = os.environ.get("MDR_JOB_ID", "")
    try:
        if projects.run_exists(bmc, run):
            _forget_bmc_runs()
            st.error(f"{bmc} / {run} already exists - choose another run name.")
            return None
        with st.spinner(f"Saving the inputs to Secondary Modelling/{bmc}/{run}/ ..."):
            names = projects.save_inputs(bmc, run, files)
            request = projects.new_request(
                bmc, run, names, user=_user_email(), source=source,
                changed=changes[1] if changes else None, job_id=job_id,
                app_codebase=codebase.status().get("version", ""))
            if changes and changes[2]:
                request["changed_settings"] = [f"{c['setting']}: {c['before']} → {c['after']}"
                                               for c in changes[2]]
            projects.write_request(bmc, run, request)
    except Exception as e:
        st.error(f"The inputs could not be saved to ADLS: {e}")
        return None
    try:
        with st.spinner("Starting the model job ..."):
            response = run_model_job(
                prior_file=names["prior_file"],
                data_file=names["data_file"],
                job_id=job_id,
                config_file=names["config_file"],
                mapping_file=names.get("mapping_file", ""),
                share_file=names.get("share_file", ""),
                bmc_name=bmc,
                run_name=run,
            )
            payload = response.json()
    except Exception as e:
        payload = {"message": str(e)}
    run_id = get_run_id_from_response(payload)
    _forget_bmc_runs()
    if not run_id:
        message = str(payload.get("message") or payload.get("error") or payload) \
            if isinstance(payload, dict) else str(payload)
        request.update(job_error=message, state="not started")
        try:
            projects.write_request(bmc, run, request)
        except Exception:
            pass
        # the folder now holds this attempt's inputs, so its name is taken:
        # propose the next one and say what happened on the refreshed page
        ss["run_start_error"] = (f"The job did not start: {message}. The inputs are saved "
                                 f"in {bmc} / {run} (listed as not started); fix the "
                                 "problem, then press Run Model again - a new run name "
                                 "is proposed.")
        ss["_pending_new_run_name"] = projects.next_free_name(run, {run})
        st.rerun()
    request.update(job_run_id=str(run_id), state="submitted")
    try:
        projects.write_request(bmc, run, request)
    except Exception as e:
        st.warning(f"The run started, but its run_request.json could not be updated: {e}")
    ss["current_run"] = projects.make_ref(run_id, bmc, run)
    ss["last_run_id"] = str(run_id)
    ss.pop("recent_runs", None)
    # the run just started is now what "changed since" compares with
    _remember_source(ss["current_run"], f"{bmc} / {run}")
    ss["_pending_new_run_name"] = projects.next_free_name(run, {run})
    st.toast(f"Run {bmc} / {run} started (job run {run_id}).")
    st.rerun()


@st.fragment
def render_run_section():
    """The checklist, Run Model, and the panel of the run started here - which
    stays until dismissed, refreshing itself while the job runs."""
    ss = st.session_state
    with st.container(border=True):
        st.markdown("### ⑥ Run")
        start_error = ss.pop("run_start_error", None)
        if start_error:
            st.error(start_error)
        ready = _readiness()
        for label, (ok, note) in ready.items():
            st.markdown(f"{'✅' if ok else '⬜'} {label}" + (f" - {note}" if note else ""))
        changes = _changes_since_source()
        if changes:
            label, items, settings = changes
            if items:
                st.info(f"Changed since **{label}**: {', '.join(items)}.")
                if settings:
                    changes_table(settings, before=f"in {label}", after="now")
            else:
                st.caption(f"Nothing has changed since **{label}** yet.")
        submit_disabled = not all(ok for ok, _ in ready.values())
        _, center_col, _ = st.columns([1, 1, 1])
        with center_col:
            run_model_clicked = st.button(
                "Run Model",
                type="primary",
                key="run_model_button",
                disabled=submit_disabled,
                use_container_width=True,
                help="Saves the inputs in the run's folder, then starts the job.",
            )
        if run_model_clicked:
            if changes and not changes[1]:
                _confirm_unchanged(changes[0])
            else:
                _start_run()
        current = ss.get("current_run")
        if current:
            render_run_panel(current, "current", on_reuse=reuse_run)


def render_all_runs_section():
    """Every recent run of the job (all BMCs, and runs from before the run
    folders), each with its panel and "Reuse inputs"."""
    render_runs_section(on_reuse=reuse_run)


def render_input_upload_section():
    """The header, then the BMC and run, the input data, Model settings, the
    mapping/share files and the prior file - each block a fragment."""
    init_config_state()
    with st.container(border=True):
        header_left, header_right = st.columns([3, 1])
        with header_left:
            st.markdown("### Model Setup")
            st.caption("① choose the BMC and name the run · ② datacube · ③ settings · "
                       "④ mapping/share files · ⑤ prior file · ⑥ run. A run's inputs can "
                       "be reused from any earlier run.")
            render_backend_status()
        with header_right:
            render_cluster_status_controls()

    _project_fragment()
    _data_fragment()
    render_settings_section()
    _pre_model_fragment()
    _prior_fragment()
