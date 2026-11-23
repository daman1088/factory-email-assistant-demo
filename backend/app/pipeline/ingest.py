"""Store provider messages idempotently. Re-ingesting the same message is a no-op."""
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain import Direction, ProcessingState
from app.models import ActionItem, Contact, Email, EmailThread, MailboxAccount
from app.pipeline import normalize
from app.providers.mail.base import RawMessage

logger = logging.getLogger(__name__)


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def _thread_id(db: Session, mailbox: MailboxAccount, raw: RawMessage) -> uuid.UUID:
    stmt = (
        insert(EmailThread)
        .values(id=uuid.uuid4(), organization_id=mailbox.organization_id, mailbox_account_id=mailbox.id,
                provider_thread_id=raw.provider_thread_id, subject=raw.subject, message_count=0)
        .on_conflict_do_nothing(constraint="uq_thread_provider")
    )
    db.execute(stmt)
    return db.execute(
        select(EmailThread.id).where(EmailThread.mailbox_account_id == mailbox.id,
                                     EmailThread.provider_thread_id == raw.provider_thread_id)
    ).scalar_one()


def ingest_messages(db: Session, mailbox: MailboxAccount, raws: list[RawMessage]) -> int:
    """Insert new messages, update thread stats and contacts. Returns the number of new emails.
    Caller commits."""
    own = mailbox.email_address.lower()
    own_domain = _domain(own)
    new = 0
    for raw in raws:
        from_email = raw.from_addr.email.lower()
        outbound = "SENT" in raw.labels or from_email == own
        direction = Direction.OUTBOUND if outbound else Direction.INBOUND
        text = normalize.body_to_text(raw.body_text, raw.body_html)
        clean = normalize.strip_quoted(text)
        participants = sorted({a.email.lower() for a in [raw.from_addr, *raw.to, *raw.cc]})
        thread_id = _thread_id(db, mailbox, raw)

        email_id = db.execute(
            insert(Email)
            .values(
                id=uuid.uuid4(), organization_id=mailbox.organization_id, mailbox_account_id=mailbox.id,
                thread_id=thread_id, provider_message_id=raw.provider_message_id,
                rfc822_message_id=raw.rfc822_message_id, in_reply_to=raw.in_reply_to,
                direction=direction.value, from_name=raw.from_addr.name, from_email=from_email,
                from_domain=_domain(from_email),
                to_addrs=[{"name": a.name, "email": a.email.lower()} for a in raw.to],
                cc_addrs=[{"name": a.name, "email": a.email.lower()} for a in raw.cc],
                participant_emails=participants, subject=raw.subject or "(no subject)",
                snippet=normalize.snippet(clean), body_text=text, body_clean=clean,
                has_attachments=bool(raw.attachments), attachments=raw.attachments, labels=raw.labels,
                sent_at=raw.sent_at, processing_state=ProcessingState.INGESTED.value,
                content_hash=normalize.content_hash(raw.subject or "", clean),
            )
            .on_conflict_do_nothing(constraint="uq_email_provider_msg")
            .returning(Email.id)
        ).scalar_one_or_none()
        if email_id is None:
            continue  # duplicate
        new += 1

        _update_thread(db, thread_id, raw.sent_at, direction)
        for addr in [raw.from_addr, *raw.to, *raw.cc]:
            if _domain(addr.email) != own_domain:
                _upsert_contact(db, mailbox.organization_id, addr.email.lower(), addr.name, raw.sent_at)
        if outbound:
            close_answered_items(db, mailbox.organization_id, thread_id, raw.sent_at)
    return new


def _update_thread(db: Session, thread_id: uuid.UUID, sent_at: datetime, direction: Direction) -> None:
    values = {
        "message_count": EmailThread.message_count + 1,
        "first_message_at": func.least(func.coalesce(EmailThread.first_message_at, sent_at), sent_at),
        "last_message_at": func.greatest(func.coalesce(EmailThread.last_message_at, sent_at), sent_at),
    }
    col = EmailThread.last_outbound_at if direction is Direction.OUTBOUND else EmailThread.last_inbound_at
    values[col.key] = func.greatest(func.coalesce(col, sent_at), sent_at)
    db.execute(update(EmailThread).where(EmailThread.id == thread_id).values(**values))


def _upsert_contact(db: Session, org_id: uuid.UUID, email: str, name: str | None, seen: datetime) -> None:
    stmt = insert(Contact).values(id=uuid.uuid4(), organization_id=org_id, email=email, display_name=name,
                                  domain=_domain(email), first_seen_at=seen, last_seen_at=seen)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_contact_org_email",
        set_={
            "display_name": func.coalesce(Contact.display_name, stmt.excluded.display_name),
            "first_seen_at": func.least(Contact.first_seen_at, stmt.excluded.first_seen_at),
            "last_seen_at": func.greatest(Contact.last_seen_at, stmt.excluded.last_seen_at),
        },
    )
    db.execute(stmt)


def close_answered_items(db: Session, org_id: uuid.UUID, thread_id: uuid.UUID, reply_at: datetime) -> None:
    """An outbound reply in the thread resolves 'respond' action items for earlier inbound emails."""
    earlier = select(Email.id).where(Email.thread_id == thread_id, Email.sent_at < reply_at)
    db.execute(
        update(ActionItem)
        .where(ActionItem.organization_id == org_id, ActionItem.thread_id == thread_id,
               ActionItem.kind == "respond", ActionItem.status == "open", ActionItem.email_id.in_(earlier))
        .values(status="done", resolved_at=datetime.now(UTC))
    )
