
import numpy as np
import pandas as pd
import streamlit as st

from src import codebase


def validate_input_data(df, cfg=None):
    """Check the datacube before it is uploaded, and show what was found.

    The checks are codebase 1's column names (run.date_col / region_col /
    dv_col from the Model settings) plus what would stop or mislead the model:
    missing values, duplicate region x date rows, text columns, unreadable
    dates, dust columns (the Coupon case) and features that are constant
    within a region (the old check). Nothing is renamed: the file that is
    uploaded is the file that was checked. Returns the codebase Outcome; its
    value is a summary (regions, periods, features, period plan).
    """
    if not isinstance(df, pd.DataFrame):
        raise ValueError("Input data must be a pandas DataFrame.")
    outcome = codebase.check_datacube(df, cfg or {})
    for error in outcome.errors:
        st.error(error)
    for _severity, warning in outcome.warnings:
        st.warning(warning)
    return outcome
