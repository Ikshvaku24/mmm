

import base64
import os
import streamlit as st
def render_app_header():
    left_col, right_col = st.columns([6, 2], vertical_alignment="center")
    with left_col:
        st.markdown(
            """
            <div class="app-header">
            <h1>BRIDGE</h1>
            <p>Bayesian Regression for Insights, Decisions, and Generalized Estimation</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right_col:
        logo_path = os.path.join("assest", "aommm.png")
        logo_html = ""
        if os.path.exists(logo_path):
            with open(logo_path, "rb") as logo_file:
                logo_b64 = base64.b64encode(logo_file.read()).decode("utf-8")
                logo_html = f'<img class="header-logo-img" src="data:image/png;base64,{logo_b64}" alt="AOMMM logo" />'
        st.markdown(
            f"""
            <style>
            .header-logo-wrap {{
                display: flex;
                flex-direction: column;
                justify-content: flex-end;
                align-items: flex-end;
                width: 100%;
                padding-top: 0.4rem;
                padding-right: 0.55rem;
                box-sizing: border-box;
            }}
            .header-logo-img {{
                max-width: 100%;
                height: auto;
                mix-blend-mode: multiply;
                background: transparent;
            }}
            </style>
            <div class="header-logo-wrap">
                {logo_html}
            </div>
            """,
            unsafe_allow_html=True,
        )
