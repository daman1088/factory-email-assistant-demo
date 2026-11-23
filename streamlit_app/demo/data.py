"""Read-only queries for the Streamlit pages. Dashboard numbers come from the main app's own services
(app.services.dashboard / summary); the explorer works on a DataFrame of every email."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import pandas as pd
from sqlalchemy import and_, select

from app.domain import CATEGORY_LABELS, Category
from app.models import ActionItem, Email, EmailClassification
from app.providers.llm.fake import FakeLLM
from app.services import dashboard as dash
from app.services import email_query as q
from app.services.dates import DateRange, resolve_range
from app.services.offline import extract as x
from app.services.offline.reports import Msg
from app.services.summary import get_summary
from demo.bootstrap import DemoData

PRESETS = {"Today": "today", "Yesterday": "yesterday", "7 days": "last_7_days", "30 days": "last_30_days",
           "All time": "all", "Custom": "custom"}
CATEGORY_NAMES = {c.value: CATEGORY_LABELS[c] for c in Category}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def date_range(demo: DemoData, label: str, start: date | None = None, end: date | None = None) -> DateRange:
    preset = PRESETS[label]
    if preset == "custom":
        return resolve_range("custom", demo.tz, start_date=start, end_date=end)
    return resolve_range(preset, demo.tz)


# ---- dashboard ------------------------------------------------------------------------------------------

@dataclass
class Dashboard:
    kpis: dict
    categories: list[dict]
    summary: str
    priority: list[dict]
    actions: list[dict]
    recent: list[dict]


def _event(e: Email, c: EmailClassification | None, tz: str) -> dict:
    m = Msg(e, c, tz)
    return {"id": m.id, "when": m.local, "who": m.who, "event": m.event, "subject": m.subject, "says": m.key,
            "category": CATEGORY_NAMES.get(m.category or "", "—"), "priority": c.priority if c else None}


def dashboard(demo: DemoData, rng: DateRange) -> Dashboard:
    with demo.session() as db:
        org_id = demo.org_id
        kpis = dash.kpis(db, org_id, rng)
        categories = dash.category_breakdown(db, org_id, rng)
        summary = get_summary(db, org_id, rng, FakeLLM())["text"]
        # Priority inbox: the main app's ordering (high first, then newest) - kept to the high/medium tiers.
        priority = dash.priority_inbox(db, org_id, rng, demo.tz, limit=8)
        actions = dash.action_items(db, org_id, rng, demo.tz, status="open", limit=12)
        # Recent important communications: newest-first timeline of high/medium received emails, with the
        # rules-based event label and the most informative sentence copied from the email.
        f = q.EmailFilters(start=rng.start, end=rng.end, direction="inbound", priorities=["high", "medium"])
        recent = [_event(e, c, demo.tz) for e, c in q.list_emails(db, org_id, f, limit=8, order="newest")]
    return Dashboard(kpis, categories, summary, priority, actions, recent)


# ---- explorer -------------------------------------------------------------------------------------------

def emails_frame(demo: DemoData) -> pd.DataFrame:
    """Every email in the demo mailbox (both directions), newest first."""
    with demo.session() as db:
        rows = db.execute(
            select(Email, EmailClassification)
            .outerjoin(EmailClassification, and_(EmailClassification.email_id == Email.id,
                                                 EmailClassification.is_current))
            .where(Email.organization_id == demo.org_id)
            .order_by(Email.sent_at.desc())).all()
    records = []
    for e, c in rows:
        m = Msg(e, c, demo.tz)
        to = ", ".join(a.get("name") or a.get("email", "") for a in (e.to_addrs or []))
        records.append({
            "id": str(e.id), "thread_id": str(e.thread_id), "sent_at": m.local, "direction": e.direction,
            "from_name": e.from_name or e.from_email, "from_email": e.from_email, "to": to,
            "company": m.who if m.inbound else (to or "—"),
            "subject": e.subject or "(no subject)", "body": m.body,
            "category": c.primary_category if c else None,
            "category_label": CATEGORY_NAMES.get(c.primary_category, "—") if c else "Sent (not classified)",
            "priority": c.priority if c else None, "action_required": bool(c and c.action_required),
            "summary": c.summary if c else None,
        })
    return pd.DataFrame.from_records(records)


def filter_emails(df: pd.DataFrame, *, query: str = "", categories: list[str] | None = None,
                  priorities: list[str] | None = None, direction: str | None = None,
                  rng: DateRange | None = None) -> pd.DataFrame:
    """Case-insensitive search across sender, company, subject and body: every word must appear."""
    out = df
    if rng is not None:
        start, end = pd.Timestamp(rng.start), pd.Timestamp(rng.end)
        sent_utc = pd.to_datetime(out["sent_at"], utc=True)
        out = out[(sent_utc >= start) & (sent_utc < end)]
    if direction in ("inbound", "outbound"):
        out = out[out["direction"] == direction]
    if categories:
        out = out[out["category"].isin(categories)]
    if priorities:
        out = out[out["priority"].isin(priorities)]
    words = [w for w in (query or "").lower().split() if w]
    if words:
        haystack = (out["from_name"].fillna("") + " " + out["from_email"].fillna("") + " " + out["to"].fillna("")
                    + " " + out["company"].fillna("") + " " + out["subject"].fillna("") + " "
                    + out["body"].fillna("")).str.lower()
        mask = pd.Series(True, index=out.index)
        for w in words:
            mask &= haystack.str.contains(w, regex=False)
        out = out[mask]
    return out


def email_detail(demo: DemoData, email_id: str) -> dict | None:
    """Full email, its classification, facts extracted by the offline rules, the conversation and actions."""
    try:
        eid = uuid.UUID(email_id)
    except ValueError:
        return None
    with demo.session() as db:
        row = db.execute(q.base_query(demo.org_id).where(Email.id == eid)).first()
        if row is None:
            return None  # also the result for an id from another organisation
        e, c = row
        thread = db.execute(q.base_query(demo.org_id).where(Email.thread_id == e.thread_id)
                            .order_by(Email.sent_at.asc())).all()
        actions = db.execute(select(ActionItem).where(ActionItem.organization_id == demo.org_id,
                                                      ActionItem.email_id == e.id)).scalars().all()
    m = Msg(e, c, demo.tz)
    text = f"{e.subject or ''}\n{m.body}"
    amounts, rates = x.money(m.body)
    pay, ship = m.payment, m.shipment
    facts = {
        "references": {x.REF_LABEL.get(k, k): v for k, v in x.references(text).items()},
        "quantities": x.quantities(m.body),
        "amounts": amounts,
        "rates": rates,
        "dates": x.deadlines(m.body),
        "payment": {"kind": pay[0], "quote": pay[1]} if pay else None,
        "shipment": {"status": x.SHIPMENT_LABEL[ship[0]], "quote": ship[1]} if ship else None,
        "event": m.event,
        "key_sentence": m.key,
    }
    return {
        "id": str(e.id), "subject": e.subject or "(no subject)", "direction": e.direction,
        "from_name": e.from_name, "from_email": e.from_email,
        "to": [a.get("email") for a in (e.to_addrs or [])], "cc": [a.get("email") for a in (e.cc_addrs or [])],
        "sent_at": m.local, "who": m.who, "body": m.body,
        "attachments": [a.get("filename") for a in (e.attachments or []) if a.get("filename")],
        "classification": None if c is None else {
            "category": CATEGORY_NAMES.get(c.primary_category, c.primary_category), "priority": c.priority,
            "action_required": c.action_required, "suggested_action": c.suggested_action,
            "company": c.company_name, "summary": c.summary, "financial_signal": c.financial_signal,
            "amounts": c.amounts or [], "references": c.references or [], "important_dates": c.important_dates or [],
            "model": c.model,
        },
        "facts": facts,
        "actions": [{"title": a.title, "priority": a.priority, "status": a.status,
                     "due": a.due_date.isoformat() if a.due_date else None} for a in actions],
        "thread": [{"id": str(t.id), "direction": t.direction, "from": t.from_name or t.from_email,
                    "sent_at": Msg(t, tc, demo.tz).local, "subject": t.subject, "body": Msg(t, tc, demo.tz).body,
                    "current": t.id == e.id} for t, tc in thread],
    }


def classifier_accuracy(demo: DemoData) -> tuple[int, int]:
    """How many received emails the offline rules classified into the dataset's gold category."""
    from app.providers.mail.mock import load_dataset

    gold = {m["id"]: m["gold"]["category"] for m in load_dataset()["messages"] if m.get("gold")}
    with demo.session() as db:
        rows = db.execute(select(Email.provider_message_id, EmailClassification.primary_category)
                          .join(EmailClassification, and_(EmailClassification.email_id == Email.id,
                                                          EmailClassification.is_current))
                          .where(Email.organization_id == demo.org_id)).all()
    scored = [(pid, cat) for pid, cat in rows if pid in gold]
    return sum(1 for pid, cat in scored if gold[pid] == cat), len(scored)
