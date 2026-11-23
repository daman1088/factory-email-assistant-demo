"""Executive summary for a date range. Built from structured aggregates (not raw email bodies), cached
by a hash of those aggregates so it's regenerated only when the underlying data changes."""
import hashlib
import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import Category
from app.logging_setup import log_ctx
from app.models import DailySummary
from app.prompts import summary_v1
from app.services import email_query as q
from app.services.dashboard import kpis
from app.services.dates import DateRange

logger = logging.getLogger(__name__)
TEMPLATE_MODEL = "template-v1"


def build_facts(db: Session, org_id: uuid.UUID, rng: DateRange) -> dict:
    f = q.EmailFilters(start=rng.start, end=rng.end)
    highlights = []
    important = q.EmailFilters(start=rng.start, end=rng.end, priorities=["high", "medium"])
    for _email, c in q.list_emails(db, org_id, important, limit=15, order="priority"):
        if c is None:
            continue
        highlights.append({
            "category": c.primary_category, "priority": c.priority, "company": c.company_name,
            "summary": c.summary[:240], "action_required": c.action_required,
            "financial_signal": c.financial_signal,
        })
    signals = dict(q.count_by(db, org_id, f, q.EmailClassification.financial_signal))
    open_enquiries = q.unanswered(db, org_id, q.EmailFilters(start=rng.start, end=rng.end,
                                                              categories=[Category.ENQUIRY.value]))
    return {
        "range": rng.label,
        "counts": kpis(db, org_id, rng),
        "financial_signals": {k: v for k, v in signals.items() if k and k != "none"},
        "unanswered_enquiries": len(open_enquiries),
        "highlights": highlights,
    }


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def template_summary(facts: dict) -> str:
    c = facts["counts"]
    if c["total_received"] == 0:
        return f"No emails were received in this period ({facts['range'].lower()})."
    period = {"Today": "Today you", "Yesterday": "Yesterday you"}.get(
        facts["range"], f"In {facts['range'].lower()} you")
    parts = [f"{period} received {_plural(c['total_received'], 'email')}, including "
             f"{_plural(c['enquiries'], 'new enquiry', 'new enquiries')}, "
             f"{c['orders']} order-related, {c['financial']} financial, {c['suppliers']} supplier and "
             f"{c['logistics']} logistics emails."]
    parts.append(f"{c['high_priority']} are high priority and {c['action_required']} need action.")
    if facts["unanswered_enquiries"]:
        parts.append(f"{_plural(facts['unanswered_enquiries'], 'enquiry thread')} still "
                     f"{'has' if facts['unanswered_enquiries'] == 1 else 'have'} no reply.")
    sig = facts["financial_signals"]
    money = []
    if sig.get("payment_reminder"):
        money.append(_plural(sig["payment_reminder"], "payment reminder"))
    if sig.get("payment_claimed"):
        money.append(f"{sig['payment_claimed']} payment claim{'s' if sig['payment_claimed'] != 1 else ''} to verify")
    if sig.get("payment_dispute"):
        money.append(_plural(sig["payment_dispute"], "payment dispute"))
    if money:
        parts.append("Finance: " + ", ".join(money) + ".")
    if c["pending_analysis"]:
        parts.append(f"{c['pending_analysis']} emails are still being analysed.")
    return " ".join(parts)


def get_summary(db: Session, org_id: uuid.UUID, rng: DateRange, llm) -> dict:
    facts = build_facts(db, org_id, rng)
    use_llm = getattr(llm, "name", "") == "anthropic" and facts["counts"]["total_received"] > 0
    model_key = getattr(llm, "summary_model", TEMPLATE_MODEL) if use_llm else TEMPLATE_MODEL
    digest = hashlib.sha256(json.dumps({"facts": facts, "model": model_key, "v": summary_v1.VERSION},
                                       sort_keys=True, default=str).encode()).hexdigest()
    cached = db.execute(select(DailySummary).where(DailySummary.organization_id == org_id,
                                                   DailySummary.input_hash == digest)).scalars().first()
    if cached:
        return {"text": cached.summary_text, "model": cached.model, "cached": True}

    text, model = template_summary(facts), TEMPLATE_MODEL
    if use_llm:
        try:
            text, model = llm.summarize(summary_v1.SYSTEM, json.dumps(facts, ensure_ascii=False, default=str))
        except Exception as exc:  # summary is non-critical: fall back to the template
            logger.warning("summary generation failed", extra=log_ctx(error=type(exc).__name__))
    db.add(DailySummary(organization_id=org_id, range_start=rng.start, range_end=rng.end, input_hash=digest,
                        summary_text=text, model=model))
    db.commit()
    return {"text": text, "model": model, "cached": False}
