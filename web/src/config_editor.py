"""Model settings: codebase 1's config.yaml, edited in the UI.

Everything here is drawn from codebase 1's own schema
(`mmm.core.settings.config_schema`): one widget per key, a dropdown wherever
codebase 1 lists the allowed values, codebase 1's help text as the tooltip. A
key added to codebase 1 therefore appears here by itself.

The values start from the backend folder's config.yaml - the team's current
settings, which differ from the dataclass defaults for several keys - and the
file sent to the job always carries EVERY key, so nothing silently falls back
to a default. The keys the job sets itself (input paths, output folder, run
name) are shown but cannot be edited.
"""
import copy

import pandas as pd
import streamlit as st
import yaml

from src import codebase

SECTION_LABELS = {"model": "Model", "run": "Run", "sampler": "Sampler",
                  "cv": "Cross-validation", "output": "Output",
                  "assumptions": "Assumptions", "data": "Data"}
TAB_ORDER = ("model", "run", "sampler", "cv", "output", "assumptions", "data")


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    try:
        return a == b
    except Exception:
        return False


def _fmt(v):
    if v is None:
        return "null"
    if isinstance(v, dict):
        return yaml.safe_dump(v, default_flow_style=True).strip() if v else "{}"
    return str(v).lower() if isinstance(v, bool) else str(v)


def get_schema():
    """codebase 1's config schema, cached per backend version."""
    version = codebase.status().get("version")
    cached = st.session_state.get("cfg_schema")
    if cached and cached[0] == version:
        return cached[1]
    out = codebase.schema()
    if not out.ok:
        return None
    st.session_state["cfg_schema"] = (version, out.value)
    return out.value


def _sync_with_schema():
    """Keep the session's config in step with codebase 1's CURRENT schema.

    codebase 1 can be re-uploaded while a session is open: a key it no longer
    has would stop the YAML being written, a key it gained would be missing.
    Runs at the top of every rerun, before anything reads the config."""
    schema = get_schema()
    values, base = st.session_state.get("cfg_values"), st.session_state.get("cfg_base")
    if not schema or values is None or base is None:
        return
    known = {(r["section"], r["key"]) for r in schema["rows"]}
    for block in (values, base):
        for section in list(block):
            for key in list(block.get(section) or {}):
                if (section, key) not in known:
                    del block[section][key]
            if not block.get(section):
                block.pop(section, None)
    for row in schema["rows"]:
        section, key = row["section"], row["key"]
        base.setdefault(section, {}).setdefault(key, row["default"])
        values.setdefault(section, {}).setdefault(key, base[section][key])


def init_config_state():
    """Load the base config once per session (backend config.yaml, else
    defaults), then keep it in step with the backend's schema."""
    if "cfg_values" in st.session_state:
        _sync_with_schema()
        return True
    base = codebase.base_config()
    if base.ok:
        values = base.value
        st.session_state.pop("cfg_base_error", None)
    else:
        st.session_state["cfg_base_error"] = base.errors
        fallback = codebase.default_config()
        if not fallback.ok:
            return False
        values = fallback.value
    st.session_state["cfg_base"] = values
    st.session_state["cfg_values"] = copy.deepcopy(values)
    st.session_state["cfg_version"] = 0
    return True


def get_config():
    """The config the next run will use (every key)."""
    return st.session_state.get("cfg_values")


def live_config():
    """The config INCLUDING widget changes made in this rerun.

    The datacube check and the prior generator run above the settings block,
    before its widgets copy their values into cfg_values. Streamlit already
    holds each widget's new value in session_state under its key, so read it
    from there - otherwise changing run.date_col would be one rerun late."""
    values = st.session_state.get("cfg_values")
    if values is None:
        return None
    schema = get_schema()
    if not schema:
        return values
    version = st.session_state.get("cfg_version", 0)
    out = copy.deepcopy(values)
    for row in schema["rows"]:
        section, name, kind = row["section"], row["key"], row["kind"]
        key = f"cfg_{version}_{section}_{name}"
        block = out.setdefault(section, {})
        if kind in ("int_or_null", "float_or_null"):
            if f"{key}__auto" in st.session_state:
                block[name] = None if st.session_state[f"{key}__auto"] else \
                    st.session_state.get(f"{key}__num", block.get(name))
        elif kind in ("mapping", "path"):
            continue
        elif key in st.session_state:
            v = st.session_state[key]
            if kind == "str_or_null":
                v = (v or "").strip() or None
            block[name] = v
    return out


def config_is_valid():
    return bool(st.session_state.get("cfg_valid", False))


def _bump():
    st.session_state["cfg_version"] = st.session_state.get("cfg_version", 0) + 1


def _set_config(values):
    st.session_state["cfg_values"] = copy.deepcopy(values)
    _bump()


def load_config(values):
    """Replace every setting - e.g. with a reused run's config.yaml. The
    widgets redraw with the new values; the base config stays the team's."""
    _set_config(values)


def job_owned_keys():
    """'section.key' of the settings the job sets itself (paths, run name)."""
    return set((get_schema() or {}).get("job_owned") or [])


def _widget(row, value, base_value, key):
    """One widget for one config key; returns the (typed) value."""
    kind, name = row["kind"], row["key"]
    changed = not _same(value, base_value)
    label = f"{name} :orange[●]" if changed else name
    help_text = (f"{row['help']}\n\nCodebase default: `{_fmt(row['default'])}`"
                 + (f"  ·  base config: `{_fmt(base_value)}`" if changed else ""))

    if kind == "choice":
        options = list(row["choices"])
        current = value if value in options else (
            row["default"] if row["default"] in options else options[0])
        return st.selectbox(label, options, index=options.index(current), key=key,
                            help=help_text)
    if kind == "bool":
        return st.toggle(label, value=bool(value), key=key, help=help_text)
    if kind == "int":
        return int(st.number_input(label, value=int(value or 0), step=1, key=key,
                                   help=help_text))
    if kind == "float":
        return float(st.number_input(label, value=float(value or 0.0), key=key,
                                     help=help_text, format="%g"))
    if kind in ("int_or_null", "float_or_null"):
        is_int = kind == "int_or_null"
        auto = st.checkbox(f"{name}: auto (null)", value=value is None,
                           key=f"{key}__auto",
                           help="null = let codebase 1 work it out (see the help "
                                "on the number below)")
        start = value if value is not None else (
            row["default"] if row["default"] is not None else (0 if is_int else 0.1))
        if is_int:
            number = int(st.number_input(label, value=int(start), step=1,
                                         key=f"{key}__num", help=help_text,
                                         disabled=auto))
        else:
            number = float(st.number_input(label, value=float(start),
                                           key=f"{key}__num", help=help_text,
                                           format="%g", disabled=auto))
        return None if auto else number
    if kind == "mapping":
        text = st.text_input(label, value=_fmt(value or {}), key=key,
                             help=help_text + "\n\nA YAML mapping, e.g. "
                                              "`{max_treedepth: 12}`")
        try:
            parsed = yaml.safe_load(text) if text.strip() else {}
        except yaml.YAMLError:
            parsed = None
        if not isinstance(parsed, dict):
            st.error(f"{name}: not a mapping - write it like {{max_treedepth: 12}}")
            return value
        return parsed
    if kind == "str_or_null":
        text = st.text_input(label, value="" if value is None else str(value),
                             key=key, help=help_text + "\n\nBlank = null")
        return text.strip() or None
    text = st.text_input(label, value="" if value is None else str(value), key=key,
                         help=help_text)
    return text


def _render_section(section, rows, values, base, job_owned, version):
    cols = st.columns(2, gap="large")
    for i, row in enumerate(rows):
        key = row["key"]
        with cols[i % 2]:
            if f"{section}.{key}" in job_owned or row["kind"] == "path":
                st.text_input(key, value="set by the job at run time",
                              key=f"cfg_{version}_{section}_{key}_ro", disabled=True,
                              help=f"{row['help']}\n\nThe job points this at the "
                                   "files you upload here, so it cannot be edited.")
                continue
            new = _widget(row, values[section].get(key), base[section].get(key),
                          f"cfg_{version}_{section}_{key}")
            values[section][key] = new


def _changed_keys(values, base, job_owned):
    """One row per setting that differs from the base config."""
    out = []
    for section, block in values.items():
        for key, v in block.items():
            if f"{section}.{key}" in job_owned:
                continue
            b = (base.get(section) or {}).get(key)
            if not _same(v, b):
                out.append({"setting": f"{section}.{key}", "base config": _fmt(b),
                            "now": _fmt(v)})
    return out


# settings other blocks read: changing one refreshes the whole page once
# (the datacube check reads the column names, the prior generator the rest)
WATCHED = (("run", "date_col"), ("run", "region_col"), ("run", "dv_col"),
           ("data", "sheet"), ("data", "date_format"), ("run", "holdout_periods"),
           ("run", "holdout_fraction"), ("run", "cadence"), ("run", "scaling_window"),
           ("data", "dv_aggregation"), ("data", "national_basis"))


def _watched(values):
    return tuple((values.get(s) or {}).get(k) for s, k in WATCHED)


def render_settings_section():
    """The Model settings block. Returns (config dict or None, is_valid)."""
    _settings_fragment()
    return st.session_state.get("cfg_values"), bool(st.session_state.get("cfg_valid"))


@st.fragment
def _settings_fragment():
    """A fragment: editing a setting refreshes this block only, not the page."""
    with st.container(border=True):
        st.markdown("### ③ Model settings")
        st.caption("codebase 1's config.yaml. Dropdowns list the values codebase 1 "
                   "accepts; hover a setting for its help. This exact file is sent "
                   "with the run.")
        if not init_config_state():
            st.error("Model settings are unavailable until codebase 1 loads (see the "
                     "message at the top).")
            st.session_state["cfg_valid"] = False
            return
        schema = get_schema()
        if schema is None:
            st.error("Could not read codebase 1's settings schema.")
            st.session_state["cfg_valid"] = False
            return
        for err in st.session_state.get("cfg_base_error") or []:
            st.warning("The backend's config.yaml could not be read, so these start "
                       f"from the codebase defaults: {err}")

        values = st.session_state["cfg_values"]
        base = st.session_state["cfg_base"]
        version = st.session_state.get("cfg_version", 0)
        job_owned = set(schema["job_owned"])
        watched_before = _watched(values)
        valid_before = st.session_state.get("cfg_valid")
        rows_by = {}
        for row in schema["rows"]:
            rows_by.setdefault(row["section"], []).append(row)

        # The widgets are only drawn while the editor is open - about a hundred
        # of them, which every page refresh would otherwise redraw.
        if st.toggle("Edit settings", key="cfg_editor_open"):
            order = [s for s in TAB_ORDER if s in rows_by] + [
                s for s in rows_by if s not in TAB_ORDER]
            tabs = st.tabs([SECTION_LABELS.get(s, s) for s in order])
            for tab, section in zip(tabs, order):
                with tab:
                    blurb = schema["blurbs"].get(section)
                    if blurb:
                        st.caption(blurb)
                    _render_section(section, rows_by[section], values, base,
                                    job_owned, version)

        up_col, dl_col, reset_col = st.columns([2, 1, 1], vertical_alignment="bottom")
        with up_col:
            uploaded = st.file_uploader("Load a config.yaml", type=["yaml", "yml"],
                                        key=f"cfg_upload_{version}",
                                        help="Replaces every setting with the file's. "
                                             "Keys the file leaves out take codebase 1's "
                                             "defaults.")
            if uploaded is not None:
                parsed = codebase.parse_config_yaml(
                    uploaded.getvalue().decode("utf-8-sig", errors="replace"))
                if parsed.ok:
                    _set_config(parsed.value)
                    st.rerun()
                for err in parsed.errors:
                    st.error(err)
        text = codebase.config_yaml(values)
        with dl_col:
            st.download_button("Download config.yaml",
                               data=(text.value or "").encode("utf-8"),
                               file_name="config.yaml", mime="text/yaml",
                               disabled=not text.ok, key="cfg_download",
                               on_click="ignore")
        with reset_col:
            if st.button("Reset to base", key="cfg_reset", type="secondary"):
                _set_config(base)
                st.rerun()

        check = codebase.validate_config(values)
        st.session_state["cfg_valid"] = check.ok
        changed = _changed_keys(values, base, job_owned)
        if check.ok:
            st.success("Settings are valid"
                       + (f" - {len(changed)} differ from the base config:" if changed
                          else " - the base config, unchanged."))
        elif changed:
            st.caption(f"{len(changed)} setting(s) differ from the base config:")
        if changed:
            st.dataframe(pd.DataFrame(changed), use_container_width=True, hide_index=True)
        for err in check.errors:
            st.error(err)
        for _severity, warning in check.warnings:
            st.warning(warning)

        if st.session_state.get("prior_from_generator"):
            units = codebase.units_problems(values)
            if units.ok and units.value:
                st.warning("The generated prior file is per unit of each region's "
                           "MEAN KPI, but " + "; ".join(units.value)
                           + ". Every generated mean would be off by a constant.")
                if st.button("Use dv_scale: mean, dv_scale_scope: region and "
                             "dv_aggregation: mean", key="cfg_fix_units"):
                    fixed = copy.deepcopy(values)
                    fixed["run"]["dv_scale"] = "mean"
                    fixed["run"]["dv_scale_scope"] = "region"
                    fixed["data"]["dv_aggregation"] = "mean"
                    _set_config(fixed)
                    st.rerun()

        # the datacube check, the prior generator and the Run checklist read
        # these - refresh the page once so they see the change
        if _watched(values) != watched_before or (
                valid_before is not None and valid_before != check.ok):
            st.rerun()
