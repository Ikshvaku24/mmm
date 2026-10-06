
import streamlit as st
from src import codebase
from src.components.app_header import render_app_header
#from src.pages.feasibility_check_page import render_feasibility_check_page
from src.pages.model_setup_page import (render_all_runs_section, render_input_upload_section,
                                        render_run_section)
#from src.pages.transformation_page import render_transformation_page
from src.styles import inject_global_styles
import html

email = getattr(st.context, "headers", {}).get("X-Forwarded-Email", "Unknown User")
username = email.split("@")[0]
parts = username.split(".")
name = "Hello " + f"{parts[0]} {parts[-1]}".title()
st.set_page_config(page_title="Model App", page_icon="🧠", layout="wide")
# once per app process (later calls do nothing): re-check codebase 1 in the
# background instead of on someone's click, and start the worker processes
# that run the heavy codebase 1 steps
codebase.start_background_refresh()
# a page opened or reloaded (a new session) re-checks codebase 1 at once - at
# most every 15 s for everyone together - so an app_access.yaml or a standard
# names CSV re-uploaded a minute ago applies to whoever reloads, without
# waiting for the 5-minute background check
if "_bridge_session" not in st.session_state:
    st.session_state["_bridge_session"] = True
    codebase.check_now()
inject_global_styles()
safe_email = html.escape(name) # Escape the email to prevent XSS attacks
st.markdown(
    f"""
        <div class="top-header-email" title="{safe_email}">{safe_email}</div>
        <style>
            .top-header-email {{
                position: fixed;
                top: 0.46rem;
                right: 3.8rem;
                z-index: 9999;
                color: #2f5567;
                font-size: 0.82rem;
                font-weight: 600;
                line-height: 1.2;
                padding: 0.1rem 0.25rem;
                pointer-events: none;
                white-space: nowrap;
                max-width: min(34vw, 420px);
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            @media (max-width: 1024px) {{
                .top-header-email {{
                    right: 3.45rem;
                    max-width: 46vw;
                }}
            }}
            @media (max-width: 768px) {{
                .top-header-email {{
                    font-size: 0.74rem;
                    right: 3.1rem;
                    top: 0.52rem;
                    max-width: 52vw;
                }}
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )
render_app_header()
# selected_page = st.radio(
#     "Select page",
#     ["Feasibility Check", "Transformation", "Model Setup"],
#     horizontal=True,
#     index=2,
# )
#st.divider()
# if selected_page == "Feasibility Check":
#     render_feasibility_check_page()
# elif selected_page == "Transformation":
#     render_transformation_page()
# else:
# BMC and run (with the BMC's earlier runs), input data, model settings
# (config.yaml), mapping/share files, prior file - each block an st.fragment,
# so a click refreshes that block, not the page
render_input_upload_section()
# checklist + Run Model (saves the inputs in the run's folder, starts the job)
# + the panel of the run started here (it stays until dismissed)
render_run_section()
st.divider()
# every recent run of the job - all BMCs, and runs from before the run folders
render_all_runs_section()
