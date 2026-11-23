"""Date-range resolution in the organisation's timezone. All presets return a half-open UTC interval
[start, end) so 'today' means local midnight to the next local midnight."""
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

Preset = Literal["today", "yesterday", "this_week", "last_7_days", "this_month", "last_30_days", "all", "custom"]
PRESETS: tuple[str, ...] = ("today", "yesterday", "this_week", "last_7_days", "this_month", "last_30_days",
                            "all", "custom")


@dataclass(frozen=True)
class DateRange:
    start: datetime  # UTC, inclusive
    end: datetime  # UTC, exclusive
    label: str
    start_date: date  # local
    end_date: date  # local, inclusive


def _midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tzinfo=tz).astimezone(UTC)


def resolve_range(preset: str, tz_name: str, *, now: datetime | None = None,
                  start_date: date | None = None, end_date: date | None = None) -> DateRange:
    tz = ZoneInfo(tz_name)
    now = (now or datetime.now(UTC)).astimezone(tz)
    today = now.date()
    if preset == "today":
        s, e, label = today, today, "Today"
    elif preset == "yesterday":
        s = e = today - timedelta(days=1)
        label = "Yesterday"
    elif preset == "this_week":
        s, e, label = today - timedelta(days=today.weekday()), today, "This week"
    elif preset == "last_7_days":
        s, e, label = today - timedelta(days=6), today, "Last 7 days"
    elif preset == "this_month":
        s, e, label = today.replace(day=1), today, "This month"
    elif preset == "last_30_days":
        s, e, label = today - timedelta(days=29), today, "Last 30 days"
    elif preset == "all":
        s, e, label = date(2000, 1, 1), today, "All time"
    elif preset == "custom":
        if not start_date or not end_date:
            raise ValueError("custom range needs start_date and end_date")
        if end_date < start_date:
            raise ValueError("end_date is before start_date")
        s, e = start_date, end_date
        label = f"{s:%d %b %Y} – {e:%d %b %Y}"
    else:
        raise ValueError(f"unknown date preset: {preset}")
    return DateRange(start=_midnight(s, tz), end=_midnight(e + timedelta(days=1), tz), label=label,
                     start_date=s, end_date=e)
