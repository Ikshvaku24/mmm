
import os

import streamlit as st
from src import brand, charts, codebase, page_state
from src.components.app_header import render_app_header
from src.config_editor import init_config_state
#from src.pages.feasibility_check_page import render_feasibility_check_page
from src.pages.model_setup_page import navigation, render_sidebar_status, render_top_bar
#from src.pages.transformation_page import render_transformation_page
from src.styles import inject_global_styles

email = getattr(st.context, "headers", {}).get("X-Forwarded-Email", "Unknown User")
username = email.split("@")[0]
parts = username.split(".")
name = "Hello " + f"{parts[0]} {parts[-1]}".title()
st.set_page_config(page_title="BRIDGE · Always-on MMM",
                   page_icon=brand.AOMMM_LOGO if os.path.exists(brand.AOMMM_LOGO)
                   else ":material/insights:",
                   layout="wide")
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
# the company theme this viewer chose (the logos at the foot of the panel):
# a new session starts with the person's last choice - one rerun when the
# theme already sent for this run was the other company's (src/brand.py)
if brand.sync_session(email):
    st.rerun()
# count this run and keep what was chosen on the other pages (src/page_state.py)
page_state.start_run()
# the viewer's company look (web/themes/*.toml - src/brand.py): the page's
# accents, AOMMM at the top of the side panel, the charts' font
inject_global_styles()
brand.render_logo()
charts.use_theme(font=brand.tokens()["font"],
                 surface={kind: brand.tokens(kind=kind)["surface"] for kind in ("light", "dark")})
# top right on every page, where Streamlit's ⋮ menu was: who is signed in, and
# the cluster with its Start button (the user's name is escaped there)
render_top_bar(name)
# the settings every page reads (Model settings, the datacube check, the run)
init_config_state()
# the pages, in the left-hand panel - each step of a new run, then the runs and
# their results; a step's title carries ✅ once it is done
page = st.navigation(navigation(), position="sidebar", expanded=True)
with st.sidebar:
    render_sidebar_status()
    brand.render_cobrand(email)     # Haleon and Capgemini: each logo switches the look
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
# the chosen page (views/*.py - each calls one block of
# src/pages/model_setup_page.py, an st.fragment: a click refreshes that block)
page.run()
