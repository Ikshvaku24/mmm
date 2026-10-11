"""Keeping what a person chose while they move between the app's pages.

The app is several pages (st.navigation, the left-hand panel). Streamlit
deletes a widget's value whenever the widget is not drawn - at the end of a
run that does not draw it, and at a PAGE CHANGE: then every value owned by a
widget of the block (fragment) that ran last is deleted, and a page switch
made from inside a block ("See the runs and their results" on ①) deletes all
of that block's values. A click inside a block reruns only that block - so
choosing a filter and then opening another page lost the filter, and ①'s
BMC, period and type were gone after "See the runs and their results"
(found in the real browser; AppTest always reruns the whole script and never
showed it). So, for the widgets in KEEP / KEEP_PREFIXES (only select boxes,
text boxes and switches - Streamlit refuses this for buttons, file uploaders
and tables):

* `remember()` copies each value to COPY + its key - a key no widget owns,
  which Streamlit never deletes - at the start of every full run
  (`start_run`) and of every block's own run (`fragment`, which every block
  uses instead of st.fragment).
* `start_run()` - called by app.py at the top of every full run, before any
  widget is drawn - puts back each value Streamlit dropped, from its copy,
  and re-saves every kept value (a value set this way belongs to the
  session, not to the widget, and survives the run).
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
import functools

import streamlit as st

# widgets whose values are kept across pages (re-saved on every full run)
KEEP = ("bmc_name", "period_start_q", "period_start_y", "period_end_q", "period_end_y",
        "modelling_type", "new_run_name", "run_note", "cfg_advanced_open",
        "rf_bmc", "rf_start_q", "rf_start_y", "rf_end_q", "rf_end_y", "rf_type",
        "gen_restrict", "runs_typed_id")
KEEP_PREFIXES = ("fit_region_", "contrib_region_", "contrib_period_", "decomp_drivers_",
                 "warn_cat_", "collin_region_", "collin_count_")
RUN_NO = "_run_no"
COPY = "_keep::"            # + a kept widget's key: a copy of its value no widget owns


def kept(key) -> bool:
    """Is `key` a widget whose value is kept across pages?"""
    return isinstance(key, str) and (key in KEEP or key.startswith(KEEP_PREFIXES))


def remember() -> None:
    """Copy every kept widget's value to COPY + its key (see the module's
    docstring). Safe at any point of a run: only the copies are written."""
    ss = st.session_state
    for key in list(ss.keys()):
        if kept(key):
            ss[COPY + key] = ss[key]


def start_run() -> int:
    """Count this full run, put back the kept values Streamlit dropped and
    re-save them all. Returns the run's number. Call it before any widget is
    drawn (app.py does)."""
    ss = st.session_state
    ss[RUN_NO] = int(ss.get(RUN_NO, 0)) + 1
    for key in list(ss.keys()):
        if isinstance(key, str) and key.startswith(COPY) and key[len(COPY):] not in ss:
            ss[key[len(COPY):]] = ss[key]           # dropped by Streamlit: put it back
    for key in list(ss.keys()):
        if kept(key):
            ss[key] = ss[key]
    remember()
    return ss[RUN_NO]


def fragment(func=None, *, run_every=None):
    """st.fragment that first remembers the kept values - each time the block
    runs on its own (a click inside it, or its run_every timer). Every block
    of the app uses it: the value a click just chose is copied before a page
    change can delete it."""
    def wrap(f):
        @functools.wraps(f)          # the same name, so the same fragment id
        def run(*args, **kwargs):
            remember()
            return f(*args, **kwargs)
        return st.fragment(run, run_every=run_every)
    return wrap(func) if func is not None else wrap


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
