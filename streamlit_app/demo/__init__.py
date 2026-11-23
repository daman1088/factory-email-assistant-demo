"""Streamlit demo of the Factory Email Assistant, built on the main application's backend code.

Importing this package makes `backend/` importable and pins the offline-only configuration before any
backend module reads its settings: rules-based LLM provider, mock mailbox, no API key.
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ["LLM_PROVIDER"] = "fake"
os.environ["MAIL_PROVIDER"] = "mock"
os.environ.pop("ANTHROPIC_API_KEY", None)
