
import streamlit as st
def inject_global_styles():
    st.markdown(
        """
        <style>
            :root {
                --ink: #123042;
               --muted-ink: #3e5f70;
               --glass: rgba(255, 255, 255, 0.28);
               --glass-border: rgba(255, 255, 255, 0.46);
           }
           .stApp {
               background:
                   radial-gradient(circle at 8% 12%, rgba(146, 208, 224, 0.45), transparent 33%),
                   radial-gradient(circle at 92% 18%, rgba(184, 214, 255, 0.5), transparent 31%),
                   radial-gradient(circle at 46% 88%, rgba(252, 221, 186, 0.46), transparent 34%),
                   linear-gradient(145deg, #f7fbfd 0%, #e8f2f8 44%, #dcecf4 100%);
           }
           [data-testid="stHeader"] {
               background: transparent;
           }
           [data-testid="stAppViewContainer"] {
               background: transparent;
           }
           .block-container {
               padding-top: 2rem;
               padding-bottom: 2.5rem;
           }
           .app-header {
               padding: 1.4rem 1.55rem;
               border-radius: 20px;
               background: transparent;
               border: none;
               box-shadow: none;
               backdrop-filter: none;
               -webkit-backdrop-filter: none;
               margin-bottom: 1rem;
           }
           .app-header h1 {
               margin: 0;
               color: var(--ink);
               font-weight: 700;
               letter-spacing: 0.15px;
           }
           .app-header p {
               margin: 0.42rem 0 0;
               color: var(--muted-ink);
               font-size: 0.98rem;
           }
           div[data-testid="stVerticalBlockBorderWrapper"] {
               border-radius: 18px;
               border: 1px solid rgba(131, 185, 214, 0.9) !important;
               background: linear-gradient(145deg, rgba(255, 255, 255, 0.34), rgba(255, 255, 255, 0.2));
               box-shadow: 0 10px 28px rgba(21, 54, 72, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.45);
               backdrop-filter: blur(8px);
               -webkit-backdrop-filter: blur(8px);
               padding: 0.25rem;
           }
           [data-testid="stFileUploaderDropzone"] {
               border-radius: 14px;
               border: 1px dashed rgba(55, 91, 114, 0.45);
               background: rgba(255, 255, 255, 0.28);
               transition: border-color 0.2s ease, transform 0.2s ease;
           }
           [data-testid="stFileUploaderDropzone"]:hover {
               border-color: rgba(33, 93, 126, 0.72);
               transform: translateY(-1px);
           }
           [data-testid="stButton"] button {
               border-radius: 999px;
               border: 1px solid rgba(16, 75, 109, 0.2);
               box-shadow: 0 8px 18px rgba(14, 62, 90, 0.17);
               font-weight: 600;
               padding-left: 1.25rem;
               padding-right: 1.25rem;
           }
           [data-testid="stButton"] button[kind="secondary"] {
               background: linear-gradient(135deg, rgba(216, 236, 246, 0.96), rgba(198, 225, 239, 0.96));
               color: var(--ink);
               border: 1px solid rgba(88, 142, 170, 0.5);
           }
           [data-testid="stButton"] button[kind="secondary"]:hover {
               background: linear-gradient(135deg, rgba(204, 229, 242, 0.98), rgba(184, 216, 233, 0.98));
               border-color: rgba(67, 121, 151, 0.62);
               color: #0f2c3b;
            }

            [data-testid="stButton"] button:hover {
                transform: translateY(-1px);
                box-shadow: 0 10px 20px rgba(14, 62, 90, 0.2);
            }
        </style>
        """
        ,
        unsafe_allow_html=True,
    )
