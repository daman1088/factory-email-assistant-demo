"""Simple demo access gate: one shared access code, read from Streamlit secrets (or the DEMO_ACCESS_CODE
environment variable for local runs). Fails closed: with no code configured, nobody gets in.

This keeps a shared demo link from being casually browsed; it is not user authentication."""
from __future__ import annotations

import hmac
import time

import streamlit as st

from demo import ui
from demo.state import setting

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
SESSION_KEY = "demo_access_granted"


def configured_code() -> str | None:
    code = (setting("DEMO_ACCESS_CODE") or "").strip()
    return code or None


def sign_out() -> None:
    for key in (SESSION_KEY, "chat", "_attempts", "_locked_until"):
        st.session_state.pop(key, None)


def require_access() -> bool:
    if st.session_state.get(SESSION_KEY):
        return True
    code = configured_code()
    _, centre, _ = st.columns([1, 1.25, 1])
    with centre:
        st.markdown(ui.gate_header(), unsafe_allow_html=True)
        if code is None:
            st.error("This demo is locked because no access code has been configured.")
            st.caption("Administrators: set `DEMO_ACCESS_CODE` in the app's Streamlit secrets "
                       "(or in `.streamlit/secrets.toml` when running locally).")
            return False
        locked_until = st.session_state.get("_locked_until", 0.0)
        if time.time() < locked_until:
            st.warning(f"Too many attempts. Try again in {int(locked_until - time.time()) + 1} seconds.")
            return False
        with st.form("access", border=False):
            entered = st.text_input("Access code", type="password", placeholder="Enter the code you were given")
            submitted = st.form_submit_button("Open demo", type="primary", use_container_width=True)
        if submitted:
            if hmac.compare_digest(entered.strip().encode(), code.encode()):
                st.session_state[SESSION_KEY] = True
                st.session_state.pop("_attempts", None)
                st.rerun()
            attempts = st.session_state.get("_attempts", 0) + 1
            st.session_state["_attempts"] = attempts
            if attempts >= MAX_ATTEMPTS:
                st.session_state["_attempts"] = 0
                st.session_state["_locked_until"] = time.time() + LOCKOUT_SECONDS
            st.error("That access code isn't right.")
        st.caption("All emails in this demo are fictional. No real mailbox is connected.")
    return False
