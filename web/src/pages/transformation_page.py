
import streamlit as st

def render_transformation_page():
    with st.container(border=True):
        st.markdown("### Transformation")
        st.caption("Prepare and transform datasets before running model setup.")
        st.info(
           "Use this page for data transformations, feature engineering, and quality checks before model setup."
       )
        st.text_area(
           "Transformation Notes",
           placeholder="Document transformation rules, derived columns, and assumptions here.",
           key="transformation_notes",
           height=140,
       )
