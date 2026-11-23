"""Mailbox synchronisation: initial (last N days, resumable) and incremental (history-based)."""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.logging_setup import log_ctx
from app.models import MailboxAccount, SyncJob
from app.pipeline.ingest import ingest_messages
from app.providers.mail.base import MailProvider

logger = logging.getLogger(__name__)
FETCH_CHUNK = 50  # Gmail batch requests allow up to 50 sub-requests comfortably


def initial_sync(db: Session, mailbox: MailboxAccount, provider: MailProvider, *, days: int = 30,
                 now: datetime | None = None) -> SyncJob:
    now = now or datetime.now(UTC)
    job = SyncJob(organization_id=mailbox.organization_id, mailbox_account_id=mailbox.id, kind="initial",
                  status="running", cursor={})
    db.add(job)
    db.commit()
    try:
        # Record the history cursor *before* listing, so mail arriving during the sync is picked up by
        # the next incremental run (duplicates are ignored by the unique constraint).
        history_id = provider.current_history_id()
        since = now - timedelta(days=days)
        page_token = None
        while True:
            page = provider.list_message_ids(since, page_token)
            for i in range(0, len(page.message_ids), FETCH_CHUNK):
                raws = provider.get_messages(page.message_ids[i:i + FETCH_CHUNK])
                job.messages_new += ingest_messages(db, mailbox, raws)
                job.messages_seen += len(raws)
            page_token = page.next_page_token
            job.cursor = {"page_token": page_token, "since": since.isoformat()}
            db.commit()  # each page is atomic; a crash resumes from job.cursor
            if not page_token:
                break
        mailbox.history_id = history_id
        mailbox.last_synced_at = datetime.now(UTC)
        mailbox.status, mailbox.last_error = "connected", None
        _finish(job, "succeeded")
        db.commit()
    except Exception as exc:
        db.rollback()
        _fail(db, job, mailbox, exc)
        raise
    logger.info("initial sync done", extra=log_ctx(mailbox_id=str(mailbox.id), seen=job.messages_seen,
                                                    new=job.messages_new))
    return job


def incremental_sync(db: Session, mailbox: MailboxAccount, provider: MailProvider) -> SyncJob:
    job = SyncJob(organization_id=mailbox.organization_id, mailbox_account_id=mailbox.id, kind="incremental",
                  status="running", cursor={"history_id": mailbox.history_id})
    db.add(job)
    db.commit()
    try:
        ids, new_history = provider.list_new_message_ids(mailbox.history_id or provider.current_history_id())
        for i in range(0, len(ids), FETCH_CHUNK):
            raws = provider.get_messages(ids[i:i + FETCH_CHUNK])
            job.messages_new += ingest_messages(db, mailbox, raws)
            job.messages_seen += len(raws)
        mailbox.history_id = new_history  # advanced in the same transaction as the inserts
        mailbox.last_synced_at = datetime.now(UTC)
        mailbox.status, mailbox.last_error = "connected", None
        _finish(job, "succeeded")
        db.commit()
    except Exception as exc:
        db.rollback()
        _fail(db, job, mailbox, exc)
        raise
    return job


def _finish(job: SyncJob, status: str) -> None:
    job.status = status
    job.finished_at = datetime.now(UTC)


def _fail(db: Session, job: SyncJob, mailbox: MailboxAccount, exc: Exception) -> None:
    job = db.merge(job)
    mailbox = db.merge(mailbox)
    _finish(job, "failed")
    job.error = f"{type(exc).__name__}: {str(exc)[:300]}"
    mailbox.last_error = job.error
    db.commit()
    logger.exception("sync failed", extra=log_ctx(mailbox_id=str(mailbox.id), job_id=str(job.id)))
