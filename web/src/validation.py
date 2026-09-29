
import numpy as np
import pandas as pd
import streamlit as st

from src import codebase


def validate_input_data(df, cfg=None, show=True):
    """Check the datacube before it is uploaded.

    Only what would STOP the run is reported: the date / region / KPI columns
    named in Model settings (run.date_col / region_col / dv_col), missing
    values, duplicate region x date rows, text columns, unreadable dates.
    Per-variable notes (constant columns and the like) are NOT listed here -
    codebase 1 writes them to the run's 00_warnings/ with the EDA. Nothing is
    renamed: the file that is uploaded is the file that was checked.

    Returns the codebase Outcome; its value is a summary (regions, periods,
    features, period plan). show=False only computes it (the page stores the
    result and draws it with show_input_check on every refresh).
    """
    if not isinstance(df, pd.DataFrame):
        raise ValueError("Input data must be a pandas DataFrame.")
    outcome = codebase.check_datacube(df, cfg or {})
    if show:
        show_input_check(outcome)
    return outcome


def show_input_check(outcome):
    """Draw a stored datacube check: its errors, then a one-line summary."""
    for error in outcome.errors:
        st.error(error)
    summary = outcome.value or {}
    if summary.get("regions"):
        st.caption(
            f"{len(summary['regions'])} regions · {summary.get('n_periods')} periods "
            f"({summary.get('date_min')} … {summary.get('date_max')}) · "
            f"{len(summary.get('features', []))} features"
            + (f" · {summary['plan']}" if summary.get("plan") else ""))
