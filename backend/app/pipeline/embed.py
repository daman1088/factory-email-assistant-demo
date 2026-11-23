"""Embed classified emails (inbound and outbound) for semantic search."""
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain import ProcessingState
from app.models import Email, EmailClassification, EmailEmbedding

CHUNK_CHARS = 1500
MAX_CHUNKS = 4
BATCH = 32


def chunk_text(email: Email, summary: str | None) -> list[str]:
    header = f"Subject: {email.subject}\nFrom: {email.from_name or ''} <{email.from_email}>\n"
    if summary:
        header += f"Summary: {summary}\n"
    body = email.body_clean or email.body_text or ""
    chunks = [header + body[:CHUNK_CHARS]]
    for start in range(CHUNK_CHARS, min(len(body), CHUNK_CHARS * MAX_CHUNKS), CHUNK_CHARS):
        chunks.append(f"Subject: {email.subject}\n" + body[start:start + CHUNK_CHARS])
    return chunks


def embed_pending(db: Session, org_id: uuid.UUID, embedder, *, limit: int = 1000) -> int:
    rows = db.execute(
        select(Email, EmailClassification.summary)
        .outerjoin(EmailClassification, (EmailClassification.email_id == Email.id) & EmailClassification.is_current)
        .where(Email.organization_id == org_id, Email.processing_state == ProcessingState.CLASSIFIED.value)
        .limit(limit)
    ).all()
    done = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        items = [(email, idx, text) for email, summary in batch
                 for idx, text in enumerate(chunk_text(email, summary))]
        vectors = embedder.embed_passages([t for _, _, t in items])
        email_ids = [email.id for email, _ in batch]
        db.execute(delete(EmailEmbedding).where(EmailEmbedding.email_id.in_(email_ids),
                                                EmailEmbedding.model == embedder.model_name))
        for (email, idx, text), vec in zip(items, vectors, strict=True):
            db.add(EmailEmbedding(organization_id=org_id, email_id=email.id, chunk_index=idx, content=text,
                                  embedding=vec, model=embedder.model_name))
        for email, _ in batch:
            email.processing_state = ProcessingState.EMBEDDED.value
        db.commit()
        done += len(batch)
    return done
