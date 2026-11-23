"""One entry point used by the CLI, the worker and tests: classify then embed whatever is pending."""
import uuid

from sqlalchemy.orm import Session

from app.pipeline.classify import classify_pending
from app.pipeline.embed import embed_pending


def process_org(db: Session, org_id: uuid.UUID, llm, embedder, *, allow_batch: bool = True) -> dict:
    stats = classify_pending(db, org_id, llm, allow_batch=allow_batch)
    stats["embedded"] = embed_pending(db, org_id, embedder)
    return stats
