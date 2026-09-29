
import streamlit as st
from src.app_functions import read_uploaded_file_as_table, show_model_file_adls_popup, show_prior_file_popup
from src.components.app_header import render_app_header
#from src.pages.feasibility_check_page import render_feasibility_check_page
from src.pages.model_setup_page import handle_prior_file_section, handle_run_and_status, render_input_upload_section, render_model_file_section, render_run_section
#from src.pages.transformation_page import render_transformation_page
from src.styles import inject_global_styles
import html

email = getattr(st.context, "headers", {}).get("X-Forwarded-Email", "Unknown User")
username = email.split("@")[0]
parts = username.split(".")
name = "Hello " + f"{parts[0]} {parts[-1]}".title()
st.set_page_config(page_title="Model App", page_icon="🧠", layout="wide")
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
# input data, model settings (config.yaml), mapping/share files, prior file
prior_file, input_data_file, run_config = render_input_upload_section()
handle_prior_file_section(prior_file, read_uploaded_file_as_table, show_prior_file_popup)
# the Run block comes after the prior section so its checklist is never a rerun behind
submit_disabled, run_model_clicked = render_run_section()
handle_run_and_status(prior_file, input_data_file, run_model_clicked, submit_disabled, run_config)
st.divider()
render_model_file_section(show_model_file_adls_popup)
