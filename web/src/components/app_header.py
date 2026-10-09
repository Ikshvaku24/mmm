"""The page header: BRIDGE, what it stands for, and the baseline under it -
in the lead company's colours (src/styles.py, src/brand.py). The AOMMM logo
is in the side panel now (brand.render_logo)."""
import streamlit as st


def render_app_header():
    st.markdown(
        """
        <div class="bridge-header">
            <div class="bridge-title">BRIDGE</div>
            <div class="bridge-subtitle">Bayesian Regression for Insights, Decisions, and
            Generalized Estimation</div>
            <div class="bridge-baseline" aria-hidden="true"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
