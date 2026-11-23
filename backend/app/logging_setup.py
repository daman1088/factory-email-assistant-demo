"""Structured JSON logging with a guard against leaking secrets or email bodies.

Rule of thumb for the codebase: log ids, counts and timings - never bodies, tokens or API keys.
The filter below is a safety net for mistakes, not a licence to log sensitive data.
"""
import json
import logging
import re
import sys
from datetime import UTC, datetime

_SENSITIVE_KEYS = {"body", "body_text", "token", "access_token", "refresh_token", "api_key", "password",
                   "authorization", "content", "text"}
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"ya29\.[A-Za-z0-9_\-\.]+"),  # Google access tokens
    re.compile(r"1//[A-Za-z0-9_\-]{20,}"),  # Google refresh tokens
]


def _scrub(value: str) -> str:
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": _scrub(record.getMessage()),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            for key, val in extra.items():
                payload[key] = "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else val
        if record.exc_info:
            payload["exc"] = _scrub(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # The HTTP client libraries can log request bodies at DEBUG; keep them quiet.
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_ctx(**kwargs) -> dict:
    """Usage: logger.info("classified", extra=log_ctx(email_id=..., ms=...))"""
    return {"ctx": kwargs}
