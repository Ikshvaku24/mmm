import hashlib
import io
import json

import pandas as pd
import streamlit as st

from src import codebase
from src.files import adls_dir, download_from_adls, unique_name, upload_to_adls

# where each kind of file lives under the ADLS root (codebase 1's app_job.FOLDERS
# says the same; the backend's copy wins when it is loaded)
DEFAULT_FOLDERS = {"data_file": "Data", "prior_file": "Prior", "config_file": "Config",
                   "mapping_file": "Mapping", "share_file": "Share"}
PASTE_HINT = ("Copy / paste: click a cell and Shift+click another to select a range, "
              "Ctrl+C to copy. To paste a column copied from Excel, click its first "
              "cell here and press Ctrl+V - it fills downward. For a whole column, "
              "'Fill or paste a whole column' below always works.")


def read_uploaded_file_as_table(uploaded_file):
    if uploaded_file is None:
        return None, "No file selected."

    file_name = uploaded_file.name.lower()
    uploaded_file.seek(0)
    file_bytes = uploaded_file.read()

    table, error = read_file_bytes_as_table(file_bytes, file_name)
    uploaded_file.seek(0)
    return table, error


def read_file_bytes_as_table(file_bytes, file_name):
    file_name = file_name.lower()

    try:
        if file_name.endswith(".csv"):
            # sep=None sniffs ; or , and utf-8-sig drops the BOM Excel adds -
            # an Excel-resaved prior file otherwise loses its `variable` header
            table = pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python",
                                encoding="utf-8-sig")
        elif file_name.endswith(".xlsx"):
            table = pd.read_excel(io.BytesIO(file_bytes))
        elif file_name.endswith(".json"):
            try:
                table = pd.read_json(io.BytesIO(file_bytes))
            except ValueError:
                parsed = json.loads(file_bytes.decode("utf-8"))
                if isinstance(parsed, list):
                    table = pd.json_normalize(parsed)
                elif isinstance(parsed, dict):
                    table = pd.json_normalize([parsed])
                else:
                    return None, "JSON format is not tabular."
        elif file_name.endswith(".txt"):
            lines = file_bytes.decode("utf-8", errors="ignore").splitlines()
            table = pd.DataFrame({"text": lines})
        else:
            return None, "Unsupported file format."
    except Exception as exc:
        return None, f"Failed to read file: {exc}"

    if table is None or table.empty:
        return None, "The file is empty or has no tabular rows."

    table = table.copy()
    table.index = pd.RangeIndex(start=1, stop=len(table) + 1, step=1, name="Serial No")
    return table, None


def to_serial_index_table(table):
    serial_table = table.copy().reset_index(drop=True)
    serial_table.index = pd.RangeIndex(
        start=1, stop=len(serial_table) + 1, step=1, name="Serial No"
    )
    return serial_table


def table_to_csv_bytes(table):
    plain_table = table.reset_index(drop=True)
    return plain_table.to_csv(index=False).encode("utf-8")


def build_edit_table_with_remove_column(table):
    remove_column_name = "Remove"
    edit_table = table.copy()

    if remove_column_name in edit_table.columns:
        return edit_table, remove_column_name

    # first column, so it is visible without scrolling across 14 prior columns
    # (it used to sit after the old format's B0 column)
    edit_table.insert(0, remove_column_name, False)
    return edit_table, remove_column_name


def apply_row_removals(edited_table, remove_column_name):
    if remove_column_name not in edited_table.columns:
        return edited_table

    removal_mask = edited_table[remove_column_name].fillna(False).astype(bool)
    cleaned_table = edited_table.loc[~removal_mask].drop(columns=[remove_column_name])
    return cleaned_table


def has_table_changed(original_table, edited_table):
    original_plain = original_table.reset_index(drop=True)
    edited_plain = edited_table.reset_index(drop=True)
    return not original_plain.equals(edited_plain)


# --------------------------------------------------------------------------- #
# the prior table: columns, types and dropdowns from codebase 1
# --------------------------------------------------------------------------- #
def get_prior_spec():
    """codebase 1's prior columns, kinds and allowed values (cached per version)."""
    version = codebase.status().get("version")
    cached = st.session_state.get("prior_spec")
    if cached and cached[0] == version:
        return cached[1]
    out = codebase.prior_columns()
    if not out.ok:
        return None
    st.session_state["prior_spec"] = (version, out.value)
    return out.value


def adls_folder(kind):
    """'Secondary Modelling/<Folder>' for data_file / prior_file / config_file / ..."""
    from src.config_editor import get_schema
    schema = get_schema() or {}
    folders = dict(DEFAULT_FOLDERS)
    folders.update(schema.get("folders") or {})
    return adls_dir(folders[kind])


def _as_text(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    text = str(v).strip()
    return text or None


def prepare_prior_table(table, spec=None):
    """A prior table ready for the editor: every template column present, text
    columns as text, numbers as numbers, dropdown values matched ignoring case
    ('Positive' -> 'positive'). Values that still match nothing are left for
    the validation to name."""
    spec = spec or get_prior_spec()
    t = table.copy().reset_index(drop=True)
    t = t.drop(columns=[c for c in ("Serial No",) if c in t.columns])
    if spec:
        for col in spec["columns"]:
            if col not in t.columns:
                t[col] = None
        order = [c for c in spec["columns"]] + [
            c for c in t.columns if c not in spec["columns"]]
        t = t[order]
        for col, kind in spec["kinds"].items():
            if col not in t.columns:
                continue
            if kind in ("number", "flag"):
                t[col] = pd.to_numeric(t[col], errors="coerce")
            else:
                t[col] = t[col].map(_as_text).astype(object)
                options = spec["options"].get(col)
                if options:
                    lookup = {o.lower(): o for o in options}
                    t[col] = t[col].map(
                        lambda v: lookup.get(v.lower(), v) if isinstance(v, str) else v)
    return to_serial_index_table(t)


def prior_column_config(spec, regions, remove_column_name):
    config = {
        remove_column_name: st.column_config.CheckboxColumn(
            "Remove", help="Check to remove this row on Save or Upload.", default=False)
    }
    if not spec:
        return config
    for col in spec["columns"]:
        kind, help_text = spec["kinds"][col], spec["help"].get(col)
        if kind == "choice":
            config[col] = st.column_config.SelectboxColumn(
                col, options=spec["options"][col], help=help_text)
        elif kind == "region" and regions:
            config[col] = st.column_config.SelectboxColumn(
                col, options=list(regions), help=help_text)
        elif kind == "number":
            config[col] = st.column_config.NumberColumn(col, help=help_text, format="%.6g")
        elif kind == "flag":
            config[col] = st.column_config.NumberColumn(
                col, help=help_text, min_value=0, max_value=1, step=1)
        else:
            config[col] = st.column_config.TextColumn(col, help=help_text)
    return config


def _row_mask(table, scope, pattern=""):
    if scope == "all rows":
        return pd.Series(True, index=table.index)
    if scope == "feature rows (region blank)":
        return table["region"].isna() if "region" in table else pd.Series(True, index=table.index)
    if scope.startswith("rows whose variable contains"):
        pat = str(pattern or "").strip().lower()
        return table["variable"].astype(str).str.lower().str.contains(pat, regex=False) \
            if pat else pd.Series(False, index=table.index)
    return pd.Series(True, index=table.index)


def fill_column(table, column, value, scope="all rows", pattern="", blanks_only=False):
    """Set `column` to `value` on the rows in `scope` (optionally only where blank)."""
    t = table.copy()
    mask = _row_mask(t, scope, pattern)
    if blanks_only:
        mask &= t[column].isna() | (t[column].astype(str).str.strip() == "")
    if pd.api.types.is_numeric_dtype(t[column]) and not isinstance(value, (int, float)):
        t[column] = t[column].astype(object)
    t.loc[mask, column] = value
    return t, int(mask.sum())


def parse_cell(text, kind, options=None, regions=None):
    """One pasted cell -> (value, problem). Blank is always legal."""
    s = str(text).strip()
    if s == "":
        return None, None
    if kind == "choice":
        lookup = {o.lower(): o for o in options or []}
        return (lookup[s.lower()], None) if s.lower() in lookup else \
            (None, f"'{s}' is not one of {list(options or [])}")
    if kind == "region":
        if not regions:
            return s, None
        lookup = {r.lower(): r for r in regions}
        return (lookup[s.lower()], None) if s.lower() in lookup else \
            (None, f"'{s}' is not a region of the datacube")
    if kind in ("number", "flag"):
        num = s.replace(" ", "")
        if "," in num and "." not in num:
            num = num.replace(",", ".")        # 0,5 (decimal comma)
        else:
            num = num.replace(",", "")         # 1,234.5 (thousands)
        if kind == "flag":
            flags = {"1": 1, "0": 0, "true": 1, "false": 0, "yes": 1, "no": 0,
                     "1.0": 1, "0.0": 0}
            return (flags[num.lower()], None) if num.lower() in flags else \
                (None, f"'{s}' is not 0 or 1")
        try:
            return float(num), None
        except ValueError:
            return None, f"'{s}' is not a number"
    return s, None


def paste_column(table, column, text, spec, regions=None, scope="all rows"):
    """Assign a pasted column (one value per line, top to bottom) to the rows in
    `scope`. Returns (table, problems); nothing changes when there are problems."""
    lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]                       # the newline Excel adds at the end
    t = table.copy()
    mask = _row_mask(t, scope)
    rows = list(t.index[mask])
    if len(lines) != len(rows):
        return table, [f"{len(lines)} pasted values for {len(rows)} rows - they must "
                       "match (copy exactly as many cells as there are rows)."]
    kind = (spec or {}).get("kinds", {}).get(column, "text")
    options = (spec or {}).get("options", {}).get(column)
    values, problems = [], []
    for i, line in enumerate(lines, start=1):
        value, problem = parse_cell(line, kind, options, regions)
        if problem:
            problems.append(f"line {i}: {problem}")
        values.append(value)
    if problems:
        return table, problems
    if kind not in ("number", "flag"):
        t[column] = t[column].astype(object)
    for row, value in zip(rows, values):
        t.at[row, column] = value
    return t, []


def _prior_cache_key(table):
    csv = codebase.prior_csv_bytes(table)
    cube = st.session_state.get("datacube_hash") or ""
    cfg = st.session_state.get("cfg_values") or {}
    region_col = (cfg.get("run") or {}).get("region_col", "region")
    return hashlib.sha1(csv + str(cube).encode() + region_col.encode()).hexdigest()


def validate_prior(table):
    """codebase 1's verdict on a prior table (cached until it or the data changes)."""
    key = _prior_cache_key(table)
    cached = st.session_state.get("prior_validation")
    if cached and cached[0] == key:
        return cached[1]
    outcome = codebase.validate_prior_table(
        table, st.session_state.get("datacube_df"), st.session_state.get("cfg_values") or {})
    st.session_state["prior_validation"] = (key, outcome)
    return outcome


def show_prior_validation(outcome, compact=False):
    if outcome.ok:
        v = outcome.value or {}
        note = "" if st.session_state.get("datacube_df") is not None else \
            " (upload the datacube to also check the names and regions against it)"
        st.success(f"Valid prior file: {v.get('features', 0)} variables, "
                   f"{v.get('region_rows', 0)} region rows{note}.")
    for error in outcome.errors:
        st.error(error)
    if outcome.warnings:
        with st.expander(f"{len(outcome.warnings)} note(s) from codebase 1",
                         expanded=not compact and any(s == "high" for s, _ in outcome.warnings)):
            for severity, text in outcome.warnings:
                (st.warning if severity == "high" else st.caption)(text)


def _csv_name(name):
    stem = str(name or "feature_priors").rsplit(".", 1)[0]
    return f"{stem}.csv"


def save_prior_table(effective_table):
    """What Save does: the edited table becomes the active prior file."""
    serial_table = to_serial_index_table(effective_table)
    st.session_state["prior_working_table"] = serial_table
    st.session_state["prior_effective_bytes"] = codebase.prior_csv_bytes(serial_table)
    st.session_state["prior_is_edited"] = True
    st.session_state["prior_effective_name"] = _csv_name(st.session_state.get("prior_source_name"))
    st.session_state["prior_uploaded_to_adls"] = False
    st.session_state.pop("uploaded_prior_name", None)
    st.session_state["prior_popup_editor_version"] = st.session_state.get("prior_popup_editor_version", 0) + 1


def upload_active_prior():
    """Validate the active prior table and upload it (a unique name). -> (ok, message)"""
    table = st.session_state.get("prior_working_table")
    if table is None or table.empty:
        return False, "At least one row is required before upload."
    outcome = validate_prior(table)
    if not outcome.ok:
        return False, "Fix the prior file first: " + " | ".join(outcome.errors)
    name = unique_name(_csv_name(st.session_state.get("prior_effective_name")))
    url = upload_to_adls(outcome.value["csv"], name, adls_folder("prior_file"))
    st.session_state["prior_effective_bytes"] = outcome.value["csv"]
    st.session_state["prior_uploaded_to_adls"] = True
    st.session_state["uploaded_prior_name"] = name
    return True, (name, url)


def _render_column_tools(edited_table, remove_column_name, spec):
    """Fill / paste a whole column; the result is saved and the dialog stays open."""
    regions = st.session_state.get("datacube_regions") or []
    columns = [c for c in edited_table.columns if c not in (remove_column_name,)]
    version = st.session_state.get("prior_popup_editor_version", 0)
    with st.expander("Fill or paste a whole column"):
        fill_tab, paste_tab = st.tabs(["Fill a column", "Paste a column"])
        with fill_tab:
            col = st.selectbox("Column", columns, key=f"prior_fill_col_{version}",
                               index=columns.index("global_prior_sd") if "global_prior_sd" in columns else 0)
            kind = (spec or {}).get("kinds", {}).get(col, "text")
            if kind == "choice":
                value = st.selectbox("Value", spec["options"][col], key=f"prior_fill_val_{version}_{col}")
            elif kind == "region" and regions:
                value = st.selectbox("Value", regions, key=f"prior_fill_val_{version}_{col}")
            elif kind == "number":
                value = st.number_input("Value", value=0.2, format="%g", key=f"prior_fill_val_{version}_{col}")
            elif kind == "flag":
                value = st.selectbox("Value", [0, 1], key=f"prior_fill_val_{version}_{col}")
            else:
                value = st.text_input("Value", key=f"prior_fill_val_{version}_{col}")
            scope = st.radio("Rows", ["all rows", "feature rows (region blank)",
                                      "rows whose variable contains ..."],
                             horizontal=True, key=f"prior_fill_scope_{version}")
            pattern = st.text_input("Variable contains", key=f"prior_fill_pat_{version}") \
                if scope.startswith("rows whose") else ""
            blanks = st.checkbox("Only where the cell is blank", key=f"prior_fill_blank_{version}")
            if st.button("Apply fill", key=f"prior_fill_apply_{version}", type="primary"):
                new, n = fill_column(edited_table, col, value, scope, pattern, blanks)
                save_prior_table(apply_row_removals(new, remove_column_name))
                st.toast(f"Filled {n} cell(s) of {col}.", icon="✅")
                st.rerun(scope="fragment")
        with paste_tab:
            col = st.selectbox("Column", columns, key=f"prior_paste_col_{version}")
            scope = st.radio("Rows", ["all rows", "feature rows (region blank)"],
                             horizontal=True, key=f"prior_paste_scope_{version}")
            n_rows = int(_row_mask(edited_table, scope).sum())
            text = st.text_area(f"Paste {n_rows} values, one per line (copy the column "
                                "in Excel, Ctrl+V here)", key=f"prior_paste_text_{version}",
                                height=150)
            if st.button("Apply paste", key=f"prior_paste_apply_{version}", type="primary"):
                new, problems = paste_column(edited_table, col, text, spec, regions, scope)
                if problems:
                    for p in problems[:15]:
                        st.error(p)
                else:
                    save_prior_table(apply_row_removals(new, remove_column_name))
                    st.toast(f"Pasted {n_rows} value(s) into {col}.", icon="✅")
                    st.rerun(scope="fragment")


def close_prior_file_popup():
    st.session_state["prior_popup_toggle_version"] = st.session_state.get("prior_popup_toggle_version", 0) + 1


@st.dialog("Prior File Preview", width="large", dismissible=True, on_dismiss=close_prior_file_popup)
def show_prior_file_popup():
    if "prior_working_table" not in st.session_state:
        st.warning("No prior file data available for preview.")
        return

    st.caption("Preview, edit, download, or upload the prior file.")
    spec = get_prior_spec()
    regions = st.session_state.get("datacube_regions") or []

    toggle_version = st.session_state.get("prior_popup_toggle_version", 0)
    edit_mode = st.toggle("Edit", key=f"prior_popup_edit_mode_{toggle_version}")
    if edit_mode:
        edit_table, remove_column_name = build_edit_table_with_remove_column(st.session_state["prior_working_table"])
        editor_version = st.session_state.get("prior_popup_editor_version", 0)
        edited_table = st.data_editor(
            edit_table,
            use_container_width=True,
            height=420,
            num_rows="dynamic",
            key=f"prior_popup_editor_{editor_version}",
            column_config=prior_column_config(spec, regions, remove_column_name),
        )
        st.caption(PASTE_HINT)
        _render_column_tools(edited_table, remove_column_name, spec)

        effective_edited_table = apply_row_removals(edited_table, remove_column_name)
        table_changed = has_table_changed(st.session_state["prior_working_table"], effective_edited_table)
        if not effective_edited_table.empty:
            show_prior_validation(validate_prior(effective_edited_table), compact=True)

        if table_changed:
            save_clicked = st.button("Save Changes", type="primary", use_container_width=True)

            if save_clicked:
                if effective_edited_table.empty:
                    st.warning("At least one row must remain after removal.")
                    return

                save_prior_table(effective_edited_table)
                st.session_state["prior_popup_toggle_version"] = toggle_version + 1
                st.toast("Changes saved. Edited prior file is active and ready to upload to ADLS.", icon="✅")

                st.rerun()
            else:
                st.caption("Make a change or tick Remove to enable Save action.")
    else:
        active_table = st.session_state["prior_working_table"]
        st.dataframe(active_table, use_container_width=True, height=420)
        outcome = validate_prior(active_table)
        show_prior_validation(outcome)

        file_name = _csv_name(st.session_state.get("prior_effective_name", st.session_state.get("prior_source_name", "prior.csv")))
        csv_bytes = codebase.prior_csv_bytes(active_table)

        download_col, upload_col = st.columns(2)
        with download_col:
            st.download_button(
                "Download CSV",
                data=csv_bytes,
                file_name=file_name,
                mime="text/csv",
                key="download_prior_csv_from_popup",
                use_container_width=True,
            )

        with upload_col:
            if st.button("Upload to ADLS", type="secondary", key="upload_prior_to_adls_from_popup",
                         use_container_width=True, disabled=not outcome.ok):
                try:
                    ok, result = upload_active_prior()
                    if ok:
                        name, url = result
                        st.success(f"Uploaded **{name}** to ADLS.")
                        st.caption(url)
                    else:
                        st.warning(result)
                except Exception as e:
                    st.session_state["prior_uploaded_to_adls"] = False
                    st.error(f"Upload failed: {e}")


# --------------------------------------------------------------------------- #
# a finished run's outputs (the old version showed Secondary Modelling/Model/summary.csv)
# --------------------------------------------------------------------------- #
RUN_FILES = [
    ("Run info", "run_info.json", "json"),
    ("Warnings", "00_warnings/00_INDEX.md", "markdown"),
    ("Convergence", "02_convergence/convergence_report.txt", "text"),
    ("Fit", "04_fit/fit_metrics.csv", "csv"),
    ("Contributions", "05_contributions/contribution_summary.csv", "csv"),
    ("Coefficients", "03_coefficients/coefficient_report.csv", "csv"),
]


def _output_folder():
    from src.config_editor import get_schema
    return (get_schema() or {}).get("output_folder", "Outputs")


@st.dialog("Run outputs from ADLS", width="large")
def show_model_file_adls_popup():
    run_id = st.text_input("Run ID", value=str(st.session_state.get("last_run_id") or ""),
                           help="The job run ID - the folder name under "
                                "Secondary Modelling/Outputs/.")
    if not run_id.strip():
        st.info("Enter the run ID of a finished run.")
        return
    base = f"{adls_dir(_output_folder())}/{run_id.strip()}"
    tabs = st.tabs([label for label, _, _ in RUN_FILES])
    for tab, (label, rel, kind) in zip(tabs, RUN_FILES):
        with tab:
            cache_key = f"run_file_{run_id}_{rel}"
            if cache_key not in st.session_state:
                try:
                    st.session_state[cache_key] = download_from_adls(f"{base}/{rel}")
                except Exception:
                    st.session_state[cache_key] = None
            data = st.session_state[cache_key]
            if data is None:
                st.info(f"{rel} is not in this run's folder (the run failed early, or "
                        "config.yaml's output settings switched it off).")
                continue
            if kind == "json":
                st.json(json.loads(data.decode("utf-8")))
            elif kind == "markdown":
                st.markdown(data.decode("utf-8"))
            elif kind == "text":
                st.code(data.decode("utf-8"), language="text")
            else:
                table, error = read_file_bytes_as_table(data, rel)
                if error:
                    st.error(error)
                else:
                    st.dataframe(table, use_container_width=True, height=380)
            st.download_button(f"Download {rel.rsplit('/', 1)[-1]}", data=data,
                               file_name=rel.rsplit("/", 1)[-1],
                               key=f"download_{cache_key}")
