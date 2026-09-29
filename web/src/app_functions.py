import csv
import hashlib
import io
import json

import pandas as pd
import streamlit as st

from src import codebase

# Why Ctrl+V onto the grid can do nothing: st.data_editor's grid reads the
# clipboard with the browser's async Clipboard API (navigator.clipboard.read),
# which needs the "clipboard read" permission - it never falls back to the
# paste event's own data. When a company policy, a dismissed prompt or the
# site settings block that permission, the paste is dropped silently, for one
# cell or many. A text box needs no permission, hence "Paste cells from Excel".
PASTE_HINT = ("**Editing:** double-click a cell to type in it, or to paste ONE value "
              "copied from Excel (Ctrl+V, then Enter). **Many cells:** use *Paste cells "
              "from Excel* below - it always works. Ctrl+V straight onto the grid works "
              "only if your browser lets this page read the clipboard (Chrome and Edge "
              "ask the first time; if nothing happens: the icon left of the address bar "
              "→ Site settings → Clipboard → Allow - unless your company blocks it). "
              "Copying out of the grid (select cells, Ctrl+C) always works.")


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


def _blank(v):
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(v, str) and not v.strip()


def parse_pasted_block(text):
    """Excel's clipboard text -> a rectangle of cells: one row per line, cells
    split on tabs (Excel quotes a cell holding a tab, a line break or a quote)."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    while rows and all(not str(c).strip() for c in rows[-1]):
        rows.pop()                               # the line break Excel adds at the end
    if not rows:
        return []
    width = max(len(r) for r in rows)
    return [[str(c) for c in r] + [""] * (width - len(r)) for r in rows]


def detect_header(first_row, columns):
    """{position: column} when every non-blank cell of the first pasted row
    names a column (any case; 'Serial No' / 'Remove' map to None = skip).
    None when the first row is data."""
    lookup = {str(c).strip().lower(): c for c in columns}
    lookup.update({"serial no": None, "remove": None})
    named = [(j, str(v).strip()) for j, v in enumerate(first_row) if str(v).strip()]
    if not named or not all(v.lower() in lookup for _, v in named):
        return None
    return {j: lookup[v.lower()] for j, v in named}


def paste_block(table, text, spec, regions=None, start_row=0, start_col=None,
                skip_columns=("Remove",)):
    """Cells copied from Excel -> (new table, summary, problems). Nothing
    changes when there is any problem.

    With a header row the columns are matched by name - and when `variable`
    is one of them the rows are matched by variable (and region, when that
    column is pasted too) instead of by position. Without one the block goes
    where an Excel paste would put it: its top-left cell at row position
    `start_row` and column `start_col`, filling right and down."""
    rows = parse_pasted_block(text)
    if not rows:
        return table, None, ["Nothing to paste yet."]
    t = table.copy()
    columns = [c for c in t.columns if c not in skip_columns]
    header = detect_header(rows[0], columns)
    body = rows[1:] if header else rows
    if not body:
        return table, None, ["Only a header row was pasted - copy the values too."]
    kinds = (spec or {}).get("kinds", {})
    options = (spec or {}).get("options", {})
    first_line = 2 if header else 1

    if header:
        targets = {j: c for j, c in header.items() if c is not None}
    else:
        if start_col not in columns:
            return table, None, ["Pick the column of the top-left cell."]
        c0, width = columns.index(start_col), len(body[0])
        if c0 + width > len(columns):
            return table, None, [f"The block is {width} columns wide, but only "
                                 f"{len(columns) - c0} columns start at '{start_col}'."]
        targets = {j: columns[c0 + j] for j in range(width)}

    problems = []
    by_variable = header is not None and "variable" in targets.values()
    index = list(t.index)
    if by_variable:
        var_pos = next(j for j, c in targets.items() if c == "variable")
        reg_pos = next((j for j, c in targets.items() if c == "region"), None)
        keys = {}
        for i in index:
            reg = t.at[i, "region"] if "region" in t.columns else None
            keys.setdefault((str(t.at[i, "variable"]).strip().lower(),
                             "" if _blank(reg) else str(reg).strip().lower()), i)
        row_for = []
        for n, r in enumerate(body, start=first_line):
            var = str(r[var_pos]).strip()
            reg = str(r[reg_pos]).strip() if reg_pos is not None else ""
            i = keys.get((var.lower(), reg.lower()))
            if i is None:
                problems.append(f"line {n}: '{var}'" + (f" / region '{reg}'" if reg else "")
                                + " is not a row of the table")
            row_for.append(i)
        write = {j: c for j, c in targets.items() if c not in ("variable", "region")}
    else:
        start_row = int(start_row or 0)
        if start_row + len(body) > len(index):
            return table, None, [f"{len(body)} pasted rows, but only {len(index) - start_row} "
                                 f"rows from row {start_row + 1} down."]
        row_for = index[start_row:start_row + len(body)]
        write = targets
    if problems:
        return table, None, problems
    if not write:
        return table, None, ["The pasted columns only name the rows (variable / region) "
                             "- copy the columns to change too."]

    values = []
    for n, (r, i) in enumerate(zip(body, row_for), start=first_line):
        for j, col in write.items():
            value, problem = parse_cell(r[j], kinds.get(col, "text"), options.get(col), regions)
            if problem:
                problems.append(f"line {n}, {col}: {problem}")
            else:
                values.append((i, col, value))
    if problems:
        return table, None, problems
    for col in dict.fromkeys(c for _, c, _ in values):
        if kinds.get(col, "text") in ("number", "flag"):
            t[col] = pd.to_numeric(t[col], errors="coerce").astype(float)
        else:
            t[col] = t[col].astype(object)
    for i, col, value in values:
        t.at[i, col] = value
    summary = {"cells": len(values), "rows": len(body),
               "columns": list(dict.fromkeys(write.values())),
               "by": "variable" if by_variable else "position"}
    return t, summary, []


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
    """What Save does: the edited table becomes the active prior file (it is
    saved to the run's folder when the run starts)."""
    serial_table = to_serial_index_table(effective_table)
    st.session_state["prior_working_table"] = serial_table
    st.session_state["prior_effective_bytes"] = codebase.prior_csv_bytes(serial_table)
    st.session_state["prior_is_edited"] = True
    st.session_state["prior_effective_name"] = _csv_name(st.session_state.get("prior_source_name"))
    st.session_state["prior_popup_editor_version"] = st.session_state.get("prior_popup_editor_version", 0) + 1


# --------------------------------------------------------------------------- #
# blanks -> the modeller's defaults (what "Use" does to a generated file)
# --------------------------------------------------------------------------- #
PRIOR_DEFAULTS = {"pooling": "global", "sign_constraint": "free",
                  "global_prior_sd": 1.0, "regional_sd_prior": 0.0}
DEFAULTS_TEXT = ("pooling **global** (**independent** for a variable that has per-region "
                 "rows - global cannot carry them), sign_constraint **free**, "
                 "global_prior_sd **1**, regional_sd_prior **0** (**0.5** where pooling is "
                 "hierarchical, which needs more than 0). Region rows and the other columns "
                 "stay as they are.")


def fill_blank_priors(table):
    """Fill the blank cells of the FEATURE rows with the modeller's defaults,
    so a generated file is complete before it is used (PRIOR_DEFAULTS, with
    two exceptions codebase 1's loader forces):
      * pooling: global - but independent for a variable with per-region rows,
        because pooling global cannot carry per-region priors;
      * regional_sd_prior: 0 - but 0.5 (codebase 1's default) where pooling is
        hierarchical, which requires a value above 0.
    Region rows (their pooling / sign / sd are not read) and every other column
    are left as they are. Returns (table, [(column, value, cells filled)])."""
    t = table.copy()
    for col in PRIOR_DEFAULTS:
        if col not in t.columns:
            t[col] = None
    region = t["region"] if "region" in t.columns else pd.Series(None, index=t.index)
    feature = region.map(_blank)
    var = t["variable"].map(lambda v: "" if _blank(v) else str(v).strip())
    with_regions = set(var[~feature])
    changes = []

    def fill(col, mask, value):
        mask = mask & t[col].map(_blank)
        if not mask.any():
            return
        if isinstance(value, str):
            t[col] = t[col].astype(object)
        else:
            t[col] = pd.to_numeric(t[col], errors="coerce").astype(float)
        t.loc[mask, col] = value
        changes.append((col, value, int(mask.sum())))

    fill("pooling", feature & ~var.isin(with_regions), "global")
    fill("pooling", feature & var.isin(with_regions), "independent")
    fill("sign_constraint", feature, "free")
    fill("global_prior_sd", feature, 1.0)
    hierarchical = t["pooling"].map(lambda v: str(v).strip().lower() == "hierarchical")
    fill("regional_sd_prior", feature & ~hierarchical, 0.0)
    fill("regional_sd_prior", feature & hierarchical, 0.5)
    return t, changes


def describe_fill(changes):
    """[(column, value, n)] -> 'pooling → global (32), ...'."""
    return ", ".join(f"{col} → {value:g} ({n})" if isinstance(value, float)
                     else f"{col} → {value} ({n})" for col, value, n in changes)


# --------------------------------------------------------------------------- #
# the prior editor - for the run's prior file ("prior") or a generated draft
# --------------------------------------------------------------------------- #
def _row_label(table, position):
    row = table.iloc[position]
    var = row.get("variable")
    reg = row.get("region") if "region" in table.columns else None
    return (f"{position + 1} · {'(blank)' if _blank(var) else var}"
            + ("" if _blank(reg) else f" · {reg}"))


def _render_block_paste(edited_table, remove_column_name, spec, regions, columns, version,
                        prefix="prior", save=None):
    """Paste cells copied from Excel through a text box - no clipboard permission needed."""
    save = save or save_prior_table
    text = st.text_area(
        "Cells copied from Excel", key=f"{prefix}_block_text_{version}", height=140,
        placeholder="In Excel, copy one cell or a block - with or without the header "
                    "row. Click here and press Ctrl+V.")
    rows = parse_pasted_block(text)
    if not rows:
        st.caption("With the header row copied too, columns are matched by name - and if "
                   "`variable` is among them, rows are matched by variable (and region), "
                   "so the row order in Excel does not matter.")
        return
    header = detect_header(rows[0], columns)
    start_row, start_col = 0, None
    if header and "variable" in header.values():
        named = [c for c in header.values() if c and c not in ("variable", "region")]
        st.caption(f"Header row found: {len(rows) - 1} row(s) matched by variable"
                   + (" and region" if "region" in header.values() else "")
                   + (f"; columns {', '.join(named)}." if named else "."))
    else:
        left, right = st.columns(2)
        with left:
            start_row = st.selectbox("Top-left cell - row", list(range(len(edited_table))),
                                     format_func=lambda i: _row_label(edited_table, i),
                                     key=f"{prefix}_block_row_{version}")
        with right:
            if header:
                st.caption("Header row found: columns matched by name, rows from the "
                           "row on the left down.")
            else:
                start_col = st.selectbox(
                    "Top-left cell - column", columns, key=f"{prefix}_block_col_{version}",
                    index=columns.index("global_prior_sd") if "global_prior_sd" in columns else 0)
    new, summary, problems = paste_block(edited_table, text, spec, regions, start_row,
                                         start_col, skip_columns=(remove_column_name,))
    for p in problems[:12]:
        st.error(p)
    if len(problems) > 12:
        st.caption(f"... and {len(problems) - 12} more.")
    if summary:
        st.info(f"Ready: {summary['cells']} cell(s) in {summary['rows']} row(s) - "
                f"{', '.join(summary['columns'])}.")
    if st.button("Apply paste", key=f"{prefix}_block_apply_{version}", type="primary",
                 disabled=bool(problems)):
        save(apply_row_removals(new, remove_column_name))
        st.toast(f"Pasted {summary['cells']} cell(s).", icon="✅")
        st.rerun(scope="fragment")


def _render_column_tools(edited_table, remove_column_name, spec, prefix="prior", save=None):
    """Paste cells from Excel, fill a column, or fill every blank with the
    defaults; the result is saved and the dialog stays open."""
    save = save or save_prior_table
    regions = st.session_state.get("datacube_regions") or []
    columns = [c for c in edited_table.columns if c not in (remove_column_name,)]
    version = st.session_state.get(f"{prefix}_popup_editor_version", 0)
    with st.expander("Paste cells from Excel · Fill a column · Fill blanks with the defaults"):
        paste_tab, fill_tab, defaults_tab = st.tabs(
            ["Paste cells from Excel", "Fill a column", "Fill blanks with the defaults"])
        with paste_tab:
            _render_block_paste(edited_table, remove_column_name, spec, regions, columns,
                                version, prefix, save)
        with fill_tab:
            col = st.selectbox("Column", columns, key=f"{prefix}_fill_col_{version}",
                               index=columns.index("global_prior_sd") if "global_prior_sd" in columns else 0)
            kind = (spec or {}).get("kinds", {}).get(col, "text")
            if kind == "choice":
                value = st.selectbox("Value", spec["options"][col], key=f"{prefix}_fill_val_{version}_{col}")
            elif kind == "region" and regions:
                value = st.selectbox("Value", regions, key=f"{prefix}_fill_val_{version}_{col}")
            elif kind == "number":
                value = st.number_input("Value", value=0.2, format="%g", key=f"{prefix}_fill_val_{version}_{col}")
            elif kind == "flag":
                value = st.selectbox("Value", [0, 1], key=f"{prefix}_fill_val_{version}_{col}")
            else:
                value = st.text_input("Value", key=f"{prefix}_fill_val_{version}_{col}")
            scope = st.radio("Rows", ["all rows", "feature rows (region blank)",
                                      "rows whose variable contains ..."],
                             horizontal=True, key=f"{prefix}_fill_scope_{version}")
            pattern = st.text_input("Variable contains", key=f"{prefix}_fill_pat_{version}") \
                if scope.startswith("rows whose") else ""
            blanks = st.checkbox("Only where the cell is blank", key=f"{prefix}_fill_blank_{version}")
            if st.button("Apply fill", key=f"{prefix}_fill_apply_{version}", type="primary"):
                new, n = fill_column(edited_table, col, value, scope, pattern, blanks)
                save(apply_row_removals(new, remove_column_name))
                st.toast(f"Filled {n} cell(s) of {col}.", icon="✅")
                st.rerun(scope="fragment")
        with defaults_tab:
            st.caption("Every blank cell of the feature rows gets: " + DEFAULTS_TEXT)
            if st.button("Fill every blank with the defaults", key=f"{prefix}_defaults_{version}",
                         type="primary"):
                new, changes = fill_blank_priors(apply_row_removals(edited_table, remove_column_name))
                save(new)
                st.toast(("Filled: " + describe_fill(changes)) if changes else "Nothing was blank.",
                         icon="✅")
                st.rerun(scope="fragment")


def render_prior_editor(table, prefix, save, use=None, download_name="feature_priors.csv",
                        saved_message="Changes saved.", after_save="app"):
    """The body of a prior-file dialog: view (table, validation, download) or
    edit (the grid, paste from Excel, fill a column, fill blanks with the
    defaults, Save). With `use`, a "Use this file" button hands the table to
    it. `prefix` keeps each target's widgets apart ("prior" = the run's prior
    file); `after_save` = "app" closes the dialog on Save, "dialog" keeps it
    open."""
    ss = st.session_state
    spec = get_prior_spec()
    regions = ss.get("datacube_regions") or []
    toggle_version = ss.get(f"{prefix}_popup_toggle_version", 0)
    edit_mode = st.toggle("Edit", key=f"{prefix}_popup_edit_mode_{toggle_version}")
    use_help = "Make it the run's prior file. Blank cells get the defaults: pooling " \
               "global, sign free, sd 1, regional sd 0."
    if edit_mode:
        edit_table, remove_column_name = build_edit_table_with_remove_column(table)
        editor_version = ss.get(f"{prefix}_popup_editor_version", 0)
        edited_table = st.data_editor(
            edit_table,
            use_container_width=True,
            height=420,
            num_rows="dynamic",
            key=f"{prefix}_popup_editor_{editor_version}",
            column_config=prior_column_config(spec, regions, remove_column_name),
        )
        st.caption(PASTE_HINT)
        _render_column_tools(edited_table, remove_column_name, spec, prefix, save)

        effective_edited_table = apply_row_removals(edited_table, remove_column_name)
        table_changed = has_table_changed(table, effective_edited_table)
        if not effective_edited_table.empty:
            show_prior_validation(validate_prior(effective_edited_table), compact=True)
        save_col, use_col = st.columns(2) if use else (st.container(), None)
        with save_col:
            if table_changed:
                if st.button("Save Changes", type="primary", key=f"{prefix}_save",
                             use_container_width=True):
                    if effective_edited_table.empty:
                        st.warning("At least one row must remain after removal.")
                        return
                    save(effective_edited_table)
                    ss[f"{prefix}_popup_toggle_version"] = toggle_version + 1
                    st.toast(saved_message, icon="✅")
                    if after_save == "dialog":
                        st.rerun(scope="fragment")
                    st.rerun()
            else:
                st.caption("Make a change or tick Remove to enable Save.")
        if use:
            with use_col:
                if st.button("Use this file", key=f"{prefix}_use_edit", use_container_width=True,
                             disabled=effective_edited_table.empty,
                             help=use_help + " Your unsaved edits are included."):
                    use(effective_edited_table)
    else:
        st.dataframe(table, use_container_width=True, height=420)
        outcome = validate_prior(table)
        show_prior_validation(outcome)
        csv_bytes = codebase.prior_csv_bytes(table)
        dl_col, use_col = st.columns(2) if use else (st.container(), None)
        with dl_col:
            st.download_button(
                "Download CSV",
                data=csv_bytes,
                file_name=_csv_name(download_name),
                mime="text/csv",
                key="download_prior_csv_from_popup" if prefix == "prior" else f"{prefix}_download",
                use_container_width=True,
                on_click="ignore",
            )
        if use:
            with use_col:
                if st.button("Use this file", key=f"{prefix}_use", type="primary",
                             use_container_width=True, help=use_help):
                    use(table)


def close_prior_file_popup():
    st.session_state["prior_popup_toggle_version"] = st.session_state.get("prior_popup_toggle_version", 0) + 1


@st.dialog("Prior File Preview", width="large", dismissible=True, on_dismiss=close_prior_file_popup)
def show_prior_file_popup():
    if "prior_working_table" not in st.session_state:
        st.warning("No prior file data available for preview.")
        return

    st.caption("Preview, edit or download the prior file the run will use. It is saved to "
               "the run's folder when you press Run Model.")
    render_prior_editor(
        st.session_state["prior_working_table"], "prior", save_prior_table,
        download_name=st.session_state.get("prior_effective_name",
                                           st.session_state.get("prior_source_name", "prior.csv")),
        saved_message="Changes saved - the edited prior file is the one the run will use.")
