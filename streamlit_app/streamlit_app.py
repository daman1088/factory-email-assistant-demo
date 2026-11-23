"""Factory Email Assistant - Streamlit customer demo.

Run from the repository root:   streamlit run streamlit_app/streamlit_app.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Factory Email Assistant · Demo", page_icon=":material/mark_email_read:",
                   layout="wide", initial_sidebar_state="expanded")

from demo import auth, state, ui  # noqa: E402

ui.inject_css()

if not auth.require_access():
    st.stop()

demo = state.get_demo()

with st.sidebar:
    st.markdown(f'<div class="side-brand"><div class="name">{ui.PRODUCT}</div>'
                f'<div class="by">Customer demo · {ui.VENDOR}</div></div>', unsafe_allow_html=True)

pages = [
    st.Page("views/dashboard.py", title="Dashboard", icon=":material/space_dashboard:", default=True),
    st.Page("views/explorer.py", title="Email Explorer", icon=":material/inbox:"),
    st.Page("views/assistant.py", title="Ask Your Emails", icon=":material/forum:"),
    st.Page("views/about.py", title="About / Demo Guide", icon=":material/info:"),
]
nav = st.navigation(pages, position="sidebar")
ui.sidebar(demo, auth.sign_out)
nav.run()
