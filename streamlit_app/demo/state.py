"""Process-wide cached demo data. Built once per process and again when the date changes in India, so
'Today' always contains the dataset's 'today' emails."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from demo import bootstrap, data

IST = ZoneInfo(bootstrap.ORG_TZ)
logger = logging.getLogger("fea.demo")


def setting(name: str, default: str | None = None) -> str | None:
    """Streamlit secrets first (Community Cloud), then environment variables (local runs, tests)."""
    try:
        value = st.secrets.get(name)
    except Exception:  # no secrets file at all
        value = None
    value = value if value not in (None, "") else os.environ.get(name, default)
    return None if value is None else str(value)


def day_key() -> str:
    return datetime.now(IST).date().isoformat()


@st.cache_resource(show_spinner=False, max_entries=2)
def _build(day: str, embeddings: str) -> bootstrap.DemoData:
    return bootstrap.build(embeddings=embeddings)


def get_demo() -> bootstrap.DemoData:
    day = day_key()
    if st.session_state.get("_demo_day") not in (None, day):
        st.session_state.pop("chat", None)  # citations point at the previous day's build
    st.session_state["_demo_day"] = day
    try:
        with st.spinner("Preparing the demo mailbox — loading and analysing 123 sample emails…"):
            return _build(day, (setting("DEMO_EMBEDDINGS", "fastembed") or "fastembed").lower())
    except Exception:
        logger.exception("demo data build failed")  # details go to the server log, not the visitor
        st.error("The demo couldn't start its built-in database on this server. Please try again in a minute; "
                 "if it keeps happening, the app owner can find the details in the Streamlit logs.")
        st.stop()


@st.cache_data(show_spinner=False, max_entries=2)
def _frame(day: str) -> pd.DataFrame:
    return data.emails_frame(get_demo())


def emails() -> pd.DataFrame:
    return _frame(day_key())
