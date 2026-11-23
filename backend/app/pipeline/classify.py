"""Classify inbound emails. Idempotent: only emails in state 'ingested' are picked up, and writing a
classification swaps `is_current` so there is always exactly one current row per email."""
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain import Category, Direction, ProcessingState
from app.logging_setup import log_ctx
from app.models import ActionItem, Contact, Email, EmailClassification, EmailThread, MailboxAccount
from app.prompts import classify_v1
from app.providers.llm.base import ClassifyRequest, ClassifyResult

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
CONCURRENCY = 6


def build_request(email: Email, own_domain: str) -> ClassifyRequest:
    return ClassifyRequest(
        key=str(email.id),
        user_message=classify_v1.user_message(
            sent_at_ist=email.sent_at.astimezone(IST).strftime("%Y-%m-%d %H:%M (%A)"),
            direction=email.direction,
            from_name=email.from_name or email.from_email.split("@")[0],
            from_domain=email.from_domain or "",
            subject=email.subject,
            attachments=[a.get("filename", "") for a in email.attachments or []],
            body=email.body_clean or email.body_text,
        ),
        sender_is_internal=(email.from_domain == own_domain),
    )


def apply_result(db: Session, email: Email, result: ClassifyResult) -> bool:
    if result.output is None:
        email.processing_state = ProcessingState.FAILED.value
        email.processing_error = result.error
        logger.warning("classification failed", extra=log_ctx(email_id=str(email.id), error=result.error))
        return False
    out = result.output
    db.execute(update(EmailClassification).where(EmailClassification.email_id == email.id,
                                                 EmailClassification.is_current).values(is_current=False))
    db.add(EmailClassification(
        organization_id=email.organization_id, email_id=email.id, is_current=True,
        primary_category=out.primary_category.value, secondary_tags=out.secondary_tags,
        company_name=out.company_name or None, summary=out.summary, priority=out.priority.value,
        action_required=out.action_required, suggested_action=out.suggested_action or None,
        important_dates=[d.model_dump() for d in out.important_dates],
        amounts=[a.model_dump() for a in out.amounts], references=[r.model_dump() for r in out.references],
        financial_signal=out.financial_signal.value, confidence=out.confidence, model=result.model,
        prompt_version=classify_v1.VERSION, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
    ))
    if out.company_name and out.primary_category is not Category.INTERNAL:
        db.execute(update(Contact).where(Contact.organization_id == email.organization_id,
                                         Contact.email == email.from_email).values(company_name=out.company_name))
    _sync_action_item(db, email, out)
    email.processing_state = ProcessingState.CLASSIFIED.value
    email.processing_error = None
    return True


def _sync_action_item(db: Session, email: Email, out) -> None:
    if not out.action_required:
        return
    kind = "respond" if out.primary_category in (Category.ENQUIRY, Category.ORDER) else "follow_up"
    due: date | None = None
    for d in out.important_dates:
        if d.kind in ("deadline", "payment_due", "delivery"):
            parsed = date.fromisoformat(d.date)
            due = parsed if due is None else min(due, parsed)
    answered = kind == "respond" and db.execute(
        select(EmailThread.last_outbound_at).where(EmailThread.id == email.thread_id)
    ).scalar_one_or_none()
    status = "done" if answered and answered > email.sent_at else "open"
    stmt = insert(ActionItem).values(
        id=uuid.uuid4(), organization_id=email.organization_id, email_id=email.id, thread_id=email.thread_id,
        title=out.suggested_action or f"Review: {email.subject}", due_date=due, priority=out.priority.value,
        kind=kind, status=status, source="ai",
    )
    # Re-classification refreshes the text but keeps a status the user may have changed.
    db.execute(stmt.on_conflict_do_update(
        index_elements=["email_id"], index_where=text("source = 'ai'"),  # literal: must match the partial index
        set_={"title": stmt.excluded.title, "due_date": stmt.excluded.due_date,
              "priority": stmt.excluded.priority, "kind": stmt.excluded.kind},
    ))


def _pending_inbound(db: Session, org_id: uuid.UUID, limit: int) -> list[Email]:
    return list(db.execute(
        select(Email).where(Email.organization_id == org_id,
                            Email.processing_state == ProcessingState.INGESTED.value,
                            Email.direction == Direction.INBOUND.value)
        .order_by(Email.sent_at.desc()).limit(limit)
    ).scalars())


def _own_domain(db: Session, org_id: uuid.UUID) -> str:
    addr = db.execute(select(MailboxAccount.email_address).where(MailboxAccount.organization_id == org_id)
                      .limit(1)).scalar_one_or_none() or ""
    return addr.rsplit("@", 1)[-1].lower()


def skip_outbound(db: Session, org_id: uuid.UUID) -> None:
    """Our own sent mail isn't classified (saves cost); it is still embedded for search."""
    db.execute(update(Email).where(Email.organization_id == org_id, Email.direction == Direction.OUTBOUND.value,
                                   Email.processing_state == ProcessingState.INGESTED.value)
               .values(processing_state=ProcessingState.CLASSIFIED.value))


def classify_pending(db: Session, org_id: uuid.UUID, llm, *, limit: int = 500,
                     allow_batch: bool = True) -> dict:
    """Classify pending inbound emails. Returns stats; may return {'batch_id': ...} when the
    Message Batches API was used (results are applied later by apply_batch_results)."""
    skip_outbound(db, org_id)
    emails = _pending_inbound(db, org_id, limit)
    if not emails:
        db.commit()
        return {"classified": 0, "failed": 0}
    own_domain = _own_domain(db, org_id)
    reqs = [build_request(e, own_domain) for e in emails]

    threshold = get_settings().classify_batch_threshold
    if allow_batch and llm.supports_batch and len(reqs) >= threshold:
        batch_id = llm.submit_classification_batch(reqs)
        for e in emails:
            e.processing_state = ProcessingState.BATCHING.value
        db.commit()
        return {"batch_id": batch_id, "submitted": len(reqs)}

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        results = list(pool.map(llm.classify, reqs))
    by_id = {str(e.id): e for e in emails}
    ok = sum(apply_result(db, by_id[r.key], r) for r in results)
    db.commit()
    return {"classified": ok, "failed": len(results) - ok}


def apply_batch_results(db: Session, org_id: uuid.UUID, results: list[ClassifyResult]) -> dict:
    ids = [uuid.UUID(r.key) for r in results]
    emails = {str(e.id): e for e in db.execute(
        select(Email).where(Email.organization_id == org_id, Email.id.in_(ids))).scalars()}
    ok = 0
    for r in results:
        email = emails.get(r.key)
        if email is not None and email.processing_state == ProcessingState.BATCHING.value:
            ok += apply_result(db, email, r)
    db.commit()
    return {"classified": ok, "failed": len(results) - ok}
