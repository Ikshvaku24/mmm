"""Keeping what a person chose while they move between the app's pages.

The app is several pages (st.navigation, the left-hand panel). Streamlit
forgets a widget's value at the end of any run that does not draw it - so
without this, a BMC, a period or a chosen region would be gone after a visit
to another page. Two things keep them:

* `start_run()` - called by app.py at the top of every full run - re-saves
  the values of the widgets in KEEP / KEEP_PREFIXES. A value re-saved this way
  belongs to the session, not to the widget, and survives the run. (Only
  select boxes, text boxes and switches are listed: Streamlit refuses that for
  buttons, file uploaders and tables.)
* `emptied(key, value)` - for the widgets Streamlit will not let us keep: file
  uploaders and selectable tables. A page drawn again after a visit elsewhere
  gets a NEW, empty uploader, and that must not read as "the person removed
  the file". `emptied` is True only when the widget HAD a value the last time
  it was drawn - in this run or the one before - and has none now: the person
  pressed its ✕ (or cleared the selection). The files themselves are kept in
  the session anyway (datacube_bytes, ...), so nothing is lost by a switch.

Every other piece of state (the files, the settings, the prior table, the
run panels) already lives in the session under keys no widget owns.
"""
import streamlit as st

# widgets whose values are kept across pages (re-saved on every full run)
KEEP = ("bmc_name", "period_start_q", "period_start_y", "period_end_q", "period_end_y",
        "modelling_type", "new_run_name", "run_note", "cfg_advanced_open",
        "bmc_runs_group", "results_bmc", "gen_restrict", "runs_typed_id")
KEEP_PREFIXES = ("fit_region_", "contrib_region_", "contrib_period_", "decomp_drivers_",
                 "warn_cat_", "collin_region_", "collin_count_")
RUN_NO = "_run_no"


def start_run() -> int:
    """Count this full run and re-save the kept widget values. Returns the
    run's number. Call it before any widget is drawn (app.py does)."""
    ss = st.session_state
    ss[RUN_NO] = int(ss.get(RUN_NO, 0)) + 1
    for key in list(ss.keys()):
        if isinstance(key, str) and (key in KEEP or key.startswith(KEEP_PREFIXES)):
            ss[key] = ss[key]
    return ss[RUN_NO]


def run_no() -> int:
    return int(st.session_state.get(RUN_NO, 0))


def _has(value) -> bool:
    if value is None:
        return False
    try:
        return len(value) > 0
    except TypeError:
        return True


def emptied(key, value) -> bool:
    """Did the person just empty the widget `key` (its ✕, a cleared table
    selection)? True when it had a value the last time it was drawn - in
    this run or the one before (a fragment's own rerun does not start a new
    run) - and has none now. A widget drawn new after a page switch was not
    emptied: it was simply not there. Records the widget's state for the next
    call; call it once per drawing, after the widget."""
    ss = st.session_state
    mark = f"_seen::{key}"
    before = ss.get(mark)
    has_now = _has(value)
    ss[mark] = (run_no(), has_now)
    if not before:
        return False
    when, had = before
    return bool(had) and not has_now and int(when) >= run_no() - 1
