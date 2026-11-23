"""Serves the bundled textile-factory dataset through the same interface as Gmail.

Timestamps in emails.json are relative (days_ago + IST clock time). They are anchored to `now` when the
provider is created, so the demo always has mail for Today and Yesterday. Messages scheduled later
today than the current time are compressed into the part of today that has already passed, keeping
their order.
"""
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.providers.mail.base import Address, ListPage, RawMessage

DATASET_PATH = Path(__file__).resolve().parents[3] / "mock_data" / "emails.json"
IST = ZoneInfo("Asia/Kolkata")
PAGE_SIZE = 50  # small page size so pagination is exercised


def load_dataset(path: Path = DATASET_PATH) -> dict:
    return json.loads(path.read_text())


def materialize_timestamps(messages: list[dict], now: datetime) -> dict[str, datetime]:
    now_ist = now.astimezone(IST)
    today = now_ist.date()
    day_start = datetime.combine(today, time.min, tzinfo=IST)
    elapsed = max((now_ist - day_start).total_seconds() - 60, 60)
    today_times = [_clock(x["time"]) for x in messages if x["days_ago"] == 0]
    latest_today = max(today_times, default=0)
    scale = min(1.0, elapsed / latest_today) if latest_today else 1.0

    out: dict[str, datetime] = {}
    for msg in messages:
        secs = _clock(msg["time"])
        if msg["days_ago"] == 0:
            secs = secs * scale
        base = datetime.combine(today - timedelta(days=msg["days_ago"]), time.min, tzinfo=IST)
        out[msg["id"]] = base + timedelta(seconds=secs)
    return out


def _clock(hhmm: str) -> float:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


class MockMailProvider:
    def __init__(self, now: datetime, dataset: dict | None = None):
        data = dataset or load_dataset()
        self._mailbox = data["mailbox"]["email"]
        self._messages = {m["id"]: m for m in data["messages"]}
        self._timestamps = materialize_timestamps(data["messages"], now)
        # Thread replies reference the first message of the thread.
        self._thread_root: dict[str, str] = {}
        for msg in sorted(data["messages"], key=lambda x: self._timestamps[x["id"]]):
            self._thread_root.setdefault(msg["thread"], msg["id"])

    def mailbox_address(self) -> str:
        return self._mailbox

    def current_history_id(self) -> str:
        return "mock-history-1"

    def list_message_ids(self, since: datetime, page_token: str | None = None) -> ListPage:
        ids = sorted((mid for mid, ts in self._timestamps.items() if ts >= since),
                     key=lambda mid: self._timestamps[mid], reverse=True)
        start = int(page_token or 0)
        page = ids[start:start + PAGE_SIZE]
        nxt = str(start + PAGE_SIZE) if start + PAGE_SIZE < len(ids) else None
        return ListPage(message_ids=page, next_page_token=nxt)

    def list_new_message_ids(self, history_id: str) -> tuple[list[str], str]:
        return [], history_id  # the static dataset never receives new mail

    def get_messages(self, message_ids: list[str]) -> list[RawMessage]:
        return [self._to_raw(self._messages[mid]) for mid in message_ids]

    def _to_raw(self, msg: dict) -> RawMessage:
        root = self._thread_root[msg["thread"]]
        return RawMessage(
            provider_message_id=msg["id"],
            provider_thread_id=msg["thread"],
            sent_at=self._timestamps[msg["id"]],
            from_addr=Address(msg["from"]["name"], msg["from"]["email"]),
            to=[Address(a["name"], a["email"]) for a in msg["to"]],
            cc=[Address(a["name"], a["email"]) for a in msg["cc"]],
            subject=msg["subject"],
            body_text=msg["body"],
            body_html=None,
            labels=msg["labels"],
            rfc822_message_id=f"<{msg['id']}@mock.suryodaya>",
            in_reply_to=None if root == msg["id"] else f"<{root}@mock.suryodaya>",
            attachments=msg["attachments"],
        )
