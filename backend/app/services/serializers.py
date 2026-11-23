"""Plain-dict views of ORM rows for the API and the chat tools."""
from app.models import Email, EmailClassification
from app.services.email_query import to_local


def classification_dict(c: EmailClassification | None) -> dict | None:
    if c is None:
        return None
    return {
        "primary_category": c.primary_category, "secondary_tags": c.secondary_tags or [],
        "company_name": c.company_name, "summary": c.summary, "priority": c.priority,
        "action_required": c.action_required, "suggested_action": c.suggested_action,
        "important_dates": c.important_dates or [], "amounts": c.amounts or [], "references": c.references or [],
        "financial_signal": c.financial_signal, "confidence": c.confidence, "model": c.model,
        "prompt_version": c.prompt_version,
    }


def email_row(e: Email, c: EmailClassification | None, tz: str) -> dict:
    return {
        "id": str(e.id), "thread_id": str(e.thread_id), "direction": e.direction,
        "from_name": e.from_name, "from_email": e.from_email,
        "to": e.to_addrs or [], "subject": e.subject, "snippet": e.snippet,
        "sent_at": to_local(e.sent_at, tz).isoformat(), "has_attachments": e.has_attachments,
        "processing_state": e.processing_state,
        "classification": classification_dict(c),
    }


def email_detail(e: Email, c: EmailClassification | None, tz: str) -> dict:
    return {**email_row(e, c, tz), "cc": e.cc_addrs or [], "body_text": e.body_text,
            "attachments": e.attachments or [], "labels": e.labels or [],
            "processing_error": e.processing_error}
