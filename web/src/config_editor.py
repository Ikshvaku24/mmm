"""Model settings: codebase 1's config.yaml, edited in the UI.

Everything here is drawn from codebase 1's own schema
(`mmm.core.settings.config_schema`): one widget per key, a dropdown wherever
codebase 1 lists the allowed values, codebase 1's help text as the tooltip. A
key added to codebase 1 therefore appears here by itself.

The values start from the backend folder's config.yaml - the team's current
settings, which differ from the dataclass defaults for several keys - and the
file sent to the job always carries EVERY key, so nothing silently falls back
to a default. The keys the job sets itself (input paths, output folder, run
name) cannot be edited.

Who may change what is codebase 1's app_access.yaml (`settings.app_access`),
four levels by login e-mail:
  * full_access / config_full_access - every setting (the "pro");
  * config_advanced_access - the settings under `editable`, plus those under
    `advanced` behind the "Advanced options" switch;
  * everyone else ("editable only") - the settings under `editable`; no
    Advanced options switch.
Every other setting is FIXED at the team's config.yaml value: it gets no
widget, and `enforce_fixed` puts it back whenever a config.yaml is loaded, a
run is reused or a run starts. The same limit applies to what goes out: the
config.yaml a person downloads or saves with a run holds only the settings
they may change; the job lays it over the team's config.yaml.

The MODELLING TYPE chosen in block ① brings its own settings (codebase 1's
modelling_types.csv - e.g. Primary carries an intercept, Secondary does not):
`cfg_team` is the team's config.yaml, `cfg_base` = the team's settings WITH
the type's, and everything above ("fixed at the team's value", Reset, the
changes table) reads `cfg_base`. Choosing a type switches its settings at
once (`apply_modelling_type`); the job lays the same type settings between
the team's config.yaml and the run's own.
"""
import copy
import hashlib

import pandas as pd
import streamlit as st
import yaml

from src import codebase, page_state

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
    """codebase 1's config schema, cached per backend copy (version AND
    fingerprint - so an edited app_access.yaml reaches open sessions too)."""
    status = codebase.status()
    stamp = (status.get("version"), status.get("fingerprint"))
    cached = st.session_state.get("cfg_schema")
    if cached and cached[0] == stamp:
        return cached[1]
    out = codebase.schema()
    if not out.ok:
        return None
    st.session_state["cfg_schema"] = (stamp, out.value)
    return out.value


# --------------------------------------------------------------------------- #
# who may do what (codebase 1's app_access.yaml)
# --------------------------------------------------------------------------- #
def viewer_email():
    """The login e-mail of the person viewing the page ('' when unknown)."""
    try:
        headers = getattr(st.context, "headers", None) or {}
        return str(headers.get("X-Forwarded-Email", "") or "").strip().lower()
    except Exception:
        return ""


_viewer_email = viewer_email


LEVEL_TEXT = {
    "full_access": "full access",
    "config_full_access": "every setting",
    "config_advanced_access": "the team's editable settings + Advanced options",
    "editable_only": "the team's editable settings",
}


def access():
    """What the person viewing the page may do, from app_access.yaml:
    {"level": "full_access" | "config_full_access" | "config_advanced_access"
     | "editable_only", "full": bool, "config_full": bool,
     "editable": set | None (None = every setting) - the basic settings,
     "advanced": set - the Advanced options this person may change (empty
                 below config_advanced_access),
     "allowed": set | None - everything this person may change,
     "show_fixed": bool, "policy": the file as read}."""
    policy = (get_schema() or {}).get("access") or {"editable": None}
    email = viewer_email()

    def named(key):
        return bool(email) and email in (policy.get(key) or [])

    full = named("full_access")
    config_full = full or named("config_full_access")
    advanced_ok = not config_full and named("config_advanced_access")
    editable = policy.get("editable")
    if config_full or editable is None:
        basic, advanced, allowed = None, set(), None
    else:
        basic = set(editable)
        advanced = (set(policy.get("advanced") or []) - basic) if advanced_ok else set()
        allowed = basic | advanced
    level = ("full_access" if full else "config_full_access" if config_full
             else "config_advanced_access" if advanced_ok else "editable_only")
    return {"level": level, "full": full, "config_full": config_full,
            "editable": basic, "advanced": advanced, "allowed": allowed,
            "show_fixed": bool(policy.get("show_fixed")), "policy": policy}


def has_full_access():
    """Full access: every setting AND the admin tools (Reload codebase 1, the
    backend folder, Open in Databricks) AND naming a new BMC."""
    return access()["full"]


def role():
    """The viewer's level: full_access, config_full_access,
    config_advanced_access or editable_only (recorded in run_request.json)."""
    return access()["level"]


def may_mark_reported():
    """May the viewer mark the run a group's results were reported from?
    (app_access.yaml `mark_reported` lists the levels.)"""
    a = access()
    return a["level"] in (a["policy"].get("mark_reported") or [])


def may_edit_run(request) -> bool:
    """May the viewer rename this run and edit its note? (app_access.yaml
    `edit_runs`: levels, and "submitter" = the person who started the run.)"""
    a = access()
    allowed = a["policy"].get("edit_runs") or []
    if a["level"] in allowed:
        return True
    who = str((request or {}).get("submitted_by") or "").strip().lower()
    return "submitter" in allowed and bool(who) and who == viewer_email()


def ui_policy():
    """(allowed 'section.key' set - None = every setting -, show_fixed, the
    policy as read) for the person viewing the page."""
    a = access()
    return a["allowed"], a["show_fixed"], a["policy"]


def enforce_fixed(values, base):
    """Every setting the person may not change takes the base config's value
    (the team's config.yaml). Returns the 'section.key' that were put back."""
    editable, _show, _ui = ui_policy()
    if editable is None or not values or not base:
        return []
    job_owned = job_owned_keys()
    reset = []
    for section, block in values.items():
        for key in list(block or {}):
            name = f"{section}.{key}"
            if name in editable or name in job_owned:
                continue
            fixed = (base.get(section) or {}).get(key)
            if not _same(block[key], fixed):
                block[key] = copy.deepcopy(fixed)
                reset.append(name)
    return reset


# --------------------------------------------------------------------------- #
# the modelling type's own settings (codebase 1's modelling_types.csv)
# --------------------------------------------------------------------------- #
def all_type_settings():
    """{modelling type: {section: {key: value}}} - what each type sets."""
    out = codebase.standard_names()
    return dict((out.value or {}).get("type_settings") or {}) if out.ok else {}


def type_settings_of(kind):
    """What the modelling type `kind` sets ({} for none)."""
    return copy.deepcopy(all_type_settings().get(kind) or {}) if kind else {}


def _merge(base, over):
    out = copy.deepcopy(base or {})
    for section, block in (over or {}).items():
        out.setdefault(section, {}).update(copy.deepcopy(block or {}))
    return out


def _flat(settings):
    return {(sec, key) for sec, block in (settings or {}).items() for key in (block or {})}


def set_type_base(kind):
    """Make the team's settings for `kind` the base - fixed settings follow
    it - without touching the current values (a reused run brings its own)."""
    ss = st.session_state
    team = ss.get("cfg_team") or ss.get("cfg_base") or {}
    ss["cfg_base"] = _merge(team, type_settings_of(kind))
    ss["_type_applied"] = kind


def apply_modelling_type(kind):
    """The modelling type was chosen (or changed): every setting the old or
    the new type sets takes the new type's value - or the team's, where the
    new type sets nothing. Returns [(setting, before, after)] that changed."""
    ss = st.session_state
    if ss.get("cfg_values") is None:
        return []
    old = ss.get("_type_applied")
    keys = _flat(type_settings_of(old)) | _flat(type_settings_of(kind))
    set_type_base(kind)
    base, values = ss["cfg_base"], ss["cfg_values"]
    changes = []
    for section, key in sorted(keys):
        new = (base.get(section) or {}).get(key)
        now = (values.get(section) or {}).get(key)
        if not _same(now, new):
            values.setdefault(section, {})[key] = copy.deepcopy(new)
            changes.append((f"{section}.{key}", _fmt(now), _fmt(new)))
    if changes:
        _bump()
    return changes


def _sync_with_schema():
    """Keep the session's config in step with codebase 1's CURRENT schema.

    codebase 1 can be re-uploaded while a session is open: a key it no longer
    has would stop the YAML being written, a key it gained would be missing,
    and the modelling types' settings may have changed. Runs at the top of
    every rerun, before anything reads the config."""
    ss = st.session_state
    schema = get_schema()
    values = ss.get("cfg_values")
    team = ss.get("cfg_team") or ss.get("cfg_base")
    if not schema or values is None or team is None:
        return
    known = {(r["section"], r["key"]) for r in schema["rows"]}
    for block in (values, team):
        for section in list(block):
            for key in list(block.get(section) or {}):
                if (section, key) not in known:
                    del block[section][key]
            if not block.get(section):
                block.pop(section, None)
    for row in schema["rows"]:
        section, key = row["section"], row["key"]
        team.setdefault(section, {}).setdefault(key, row["default"])
    ss["cfg_team"] = team
    base = _merge(team, type_settings_of(ss.get("_type_applied")))
    ss["cfg_base"] = base
    for row in schema["rows"]:
        section, key = row["section"], row["key"]
        values.setdefault(section, {}).setdefault(key, base[section][key])
    # app_access.yaml (or a type's settings) may have changed with the
    # re-upload: a setting that is now fixed goes back to the base value
    if enforce_fixed(values, base):
        _bump()


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
    st.session_state["cfg_team"] = copy.deepcopy(values)
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


def config_valid():
    """Do the current settings load the way the job will load them? Asked of
    codebase 1 (cached by content) - also when the settings page was never
    opened. Kept in cfg_valid for the page titles and the checklist."""
    values = st.session_state.get("cfg_values")
    ok = values is not None and codebase.validate_config(values).ok
    st.session_state["cfg_valid"] = ok
    return ok


def _bump():
    st.session_state["cfg_version"] = st.session_state.get("cfg_version", 0) + 1


def _set_config(values):
    st.session_state["cfg_values"] = copy.deepcopy(values)
    _bump()


def load_config(values, origin=None, name=""):
    """Replace every setting - with a loaded file (origin "file") or a reused
    run's config.yaml (origin "run", `name` = its label). The widgets redraw
    with the new values; the base config stays the team's. Settings the
    person may not change keep the team's values; returns the 'section.key'
    that were put back (the file's values that were ignored)."""
    values = copy.deepcopy(values)
    reset = enforce_fixed(values, st.session_state.get("cfg_base") or {})
    _set_config(values)
    st.session_state["cfg_origin"] = origin
    st.session_state["cfg_origin_name"] = name
    if origin != "file":
        _clear_config_upload()
    return reset


def _clear_config_upload():
    """Empty the Load box (a new key) - its file no longer describes the settings."""
    st.session_state["cfg_upload_version"] = st.session_state.get("cfg_upload_version", 0) + 1
    st.session_state.pop("cfg_file_sig", None)


def reset_config_to_base():
    """Back to the team's config.yaml (with the modelling type's settings) -
    what the ✕ on a loaded or reused config does."""
    _set_config(st.session_state["cfg_base"])
    st.session_state["cfg_origin"] = None
    st.session_state["cfg_origin_name"] = ""
    _clear_config_upload()


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


def _render_tabs(rows_by, schema, values, base, job_owned, version):
    """One tab per section of `rows_by` ({section: [schema rows]})."""
    order = [s for s in TAB_ORDER if s in rows_by] + [s for s in rows_by if s not in TAB_ORDER]
    tabs = st.tabs([SECTION_LABELS.get(s, s) for s in order]) if order else []
    for tab, section in zip(tabs, order):
        with tab:
            blurb = schema["blurbs"].get(section)
            if blurb:
                st.caption(blurb)
            _render_section(section, rows_by[section], values, base, job_owned, version)


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
        who = access()
        editable, show_fixed, policy = who["allowed"], who["show_fixed"], who["policy"]
        basic, advanced = who["editable"], who["advanced"]
        st.caption("codebase 1's config.yaml. Dropdowns list the values codebase 1 "
                   "accepts; hover a setting for its help."
                   + (" You may change every setting." if editable is None else
                      f" You can change the {len(basic)} settings the team opened"
                      + (f" and {len(advanced)} more under Advanced options" if advanced
                         else "")
                      + " (app_access.yaml); every other setting keeps the team's value, "
                        "and the config.yaml you download or save with your run holds "
                        "only yours."))
        for err in st.session_state.get("cfg_base_error") or []:
            st.warning("The backend's config.yaml could not be read, so these start "
                       f"from the codebase defaults: {err}")
        if policy.get("error"):
            st.error("codebase 1's app_access.yaml could not be read, so every setting "
                     f"is fixed until it is corrected: {policy['error']}")
        if policy.get("unknown"):
            st.warning("app_access.yaml names settings codebase 1 does not have - they "
                       "are ignored: " + ", ".join(policy["unknown"]))
        note = st.session_state.pop("cfg_load_note", None)
        if note:
            st.info(note)
        kind = st.session_state.get("_type_applied")
        by_type = type_settings_of(kind)
        if by_type:
            st.info(f"Modelling type **{kind}** (chosen in ①) sets "
                    + ", ".join(f"`{sec}.{key}` = {_fmt(v)}" for sec, block in by_type.items()
                                for key, v in block.items())
                    + " - codebase 1's modelling_types.csv. Those values are the base below; "
                      "Reset goes back to them.")

        values = st.session_state["cfg_values"]
        base = st.session_state["cfg_base"]
        version = st.session_state.get("cfg_version", 0)
        job_owned = set(schema["job_owned"])
        watched_before = _watched(values)
        valid_before = st.session_state.get("cfg_valid")

        def shown(row):
            return editable is None or f"{row['section']}.{row['key']}" in editable

        def is_advanced(row):
            return f"{row['section']}.{row['key']}" in advanced

        rows_by, advanced_by = {}, {}
        for row in schema["rows"]:
            if shown(row):
                (advanced_by if is_advanced(row) else rows_by).setdefault(
                    row["section"], []).append(row)

        # The settings have a page of their own, so they are drawn as soon as
        # it opens - no "Edit settings" switch any more.
        if not rows_by and not advanced_by:
            st.caption("Every setting is fixed by the team (app_access.yaml).")
        _render_tabs(rows_by, schema, values, base, job_owned, version)
        n_advanced = sum(len(v) for v in advanced_by.values())
        if n_advanced and st.toggle(
                f"Advanced options ({n_advanced} settings)", key="cfg_advanced_open",
                help="The settings app_access.yaml opens to config_advanced_access - "
                     "needed once in a while. Your changes to them count whether "
                     "this is open or not."):
            st.markdown("##### Advanced options")
            _render_tabs(advanced_by, schema, values, base, job_owned, version)
        if show_fixed and editable is not None:
            fixed = [{"setting": f"{r['section']}.{r['key']}",
                      "value": _fmt((values.get(r["section"]) or {}).get(r["key"])),
                      "what it does": " ".join(str(r["help"]).split())[:120]}
                     for r in schema["rows"]
                     if not shown(r) and f"{r['section']}.{r['key']}" not in job_owned
                     and r["kind"] != "path"]
            with st.expander(f"Fixed by the team ({len(fixed)} settings)"):
                st.dataframe(pd.DataFrame(fixed), use_container_width=True,
                             hide_index=True)

        upload_key = f"cfg_upload_{st.session_state.get('cfg_upload_version', 0)}"
        up_col, dl_col, reset_col = st.columns([2, 1, 1], vertical_alignment="bottom")
        with up_col:
            uploaded = st.file_uploader(
                "Load a config.yaml", type=["yaml", "yml"], key=upload_key,
                help="Takes the settings you may change from the file; a setting the file "
                     "leaves out, and every setting fixed by the team, keeps the team's "
                     "value. Remove the file (✕) to go back to the team's settings.")
            removed = page_state.emptied(upload_key, uploaded)
            if uploaded is not None:
                data = uploaded.getvalue()
                signature = (uploaded.name, hashlib.sha1(data).hexdigest())
                if st.session_state.get("cfg_file_sig") != signature:
                    parsed = codebase.parse_config_yaml(
                        data.decode("utf-8-sig", errors="replace"), base=base)
                    if parsed.ok:
                        reset = load_config(parsed.value, origin="file", name=uploaded.name)
                        st.session_state["cfg_file_sig"] = signature
                        if reset:
                            st.session_state["cfg_load_note"] = (
                                f"Loaded {uploaded.name}. {len(reset)} of its settings are "
                                "fixed by the team and were ignored: "
                                + ", ".join(reset[:12]) + (" ..." if len(reset) > 12 else ""))
                        st.rerun()
                    for err in parsed.errors:
                        st.error(err)
            elif removed and st.session_state.get("cfg_origin") == "file":
                reset_config_to_base()          # the file was taken out of the box (✕)
                st.rerun()
        text = codebase.config_yaml(values, only=editable)
        with dl_col:
            st.download_button("Download config.yaml",
                               data=(text.value or "").encode("utf-8"),
                               file_name="config.yaml", mime="text/yaml",
                               disabled=not text.ok, key="cfg_download",
                               on_click="ignore",
                               help="Every setting." if editable is None else
                               "The settings you may change"
                               + (" (Advanced options included)" if advanced else "")
                               + "; the team's config.yaml supplies the rest when the "
                                 "file is run.")
        with reset_col:
            if st.button("Reset to base", key="cfg_reset", type="secondary",
                         help="Every setting back to the team's config.yaml (with the "
                              "modelling type's settings)."):
                reset_config_to_base()
                st.rerun()
        # a reused run's settings - or a loaded file whose box is empty because
        # the page was opened again - are named here, with their own ✕
        origin = st.session_state.get("cfg_origin")
        if origin == "run" or (origin == "file" and uploaded is None):
            note_col, x_col = st.columns([14, 1], vertical_alignment="center")
            with note_col:
                st.caption("Settings from **"
                           + str(st.session_state.get("cfg_origin_name") or "a reused run")
                           + "**.")
            with x_col:
                if st.button("✕", key="cfg_origin_clear",
                             help="Remove these settings - back to the team's config.yaml."):
                    reset_config_to_base()
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
            unit_keys = {"run.dv_scale", "run.dv_scale_scope", "data.dv_aggregation"}
            if units.ok and units.value:
                st.warning("The generated prior file is per unit of each region's "
                           "MEAN KPI, but " + "; ".join(units.value)
                           + ". Every generated mean would be off by a constant."
                           + ("" if editable is None or unit_keys <= editable else
                              " These settings are fixed by the team - ask whoever "
                              "maintains config.yaml."))
                if (editable is None or unit_keys <= editable) and st.button(
                        "Use dv_scale: mean, dv_scale_scope: region and "
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
