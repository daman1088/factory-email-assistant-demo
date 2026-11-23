"""Builds the demo mailbox with the main application's own code - no Docker, no external database.

An embedded PostgreSQL 16 + pgvector (the `pgserver` wheel) runs inside the Streamlit process in a temporary
directory. The backend's models, mock mail provider, offline rules classifier and pipeline load all 123
fictional emails into it on start-up. Nothing is written outside the temp directory, so a Streamlit Cloud
restart simply rebuilds the same data.

Why a real Postgres instead of rewriting the logic for SQLite/pandas: the offline answer engine, dashboard
counts and search rely on Postgres features (JSONB aggregation, DISTINCT ON, full-text search, pgvector).
Running the unchanged, tested code keeps the demo's numbers and answers identical to the main app.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.db import Base  # noqa: E402
from app.models import Email, MailboxAccount, Organization  # noqa: E402
from app.pipeline.process import process_org  # noqa: E402
from app.pipeline.sync import initial_sync  # noqa: E402
from app.providers.embed.base import FakeEmbedder, FastEmbedder  # noqa: E402
from app.providers.llm.fake import FakeLLM  # noqa: E402
from app.providers.mail.factory import get_mail_provider  # noqa: E402
from demo import BACKEND_DIR, REPO_ROOT  # noqa: F401  (sets up the backend path and offline config)

logger = logging.getLogger("fea.demo")

ORG_SLUG = "suryodaya"
ORG_NAME = "Suryodaya Fabrics Pvt. Ltd."
ORG_TZ = "Asia/Kolkata"
MAILBOX = "office@suryodaya-fabrics.example"
# The real app's first Gmail sync covers 30 days; the demo imports the whole sample mailbox (oldest: 40 days).
IMPORT_WINDOW_DAYS = 3650
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

_server = None
_server_lock = threading.Lock()


@dataclass
class DemoData:
    org_id: uuid.UUID
    tz: str
    anchor: datetime  # "now" used to place the relative email dates
    day: date  # local date the data was built for
    session_factory: sessionmaker
    embedder: object
    search_mode: str  # "semantic" (local embeddings) or "keyword" (hashed bag-of-words fallback)
    email_count: int
    classified: int
    build_seconds: float

    def session(self) -> Session:
        return self.session_factory()

    def org(self, db: Session) -> Organization:
        return db.get(Organization, self.org_id)


def _postgres():
    """One embedded server per process; each data build gets its own database inside it."""
    global _server
    with _server_lock:
        if _server is None:
            import pgserver

            pgdata = Path(tempfile.mkdtemp(prefix="fea-demo-pg-"))
            _server = pgserver.get_server(pgdata, cleanup_mode="delete")
    return _server


def _sqlalchemy_url(server, database: str) -> str:
    uri = server.get_uri(database)
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


def _create_schema(engine) -> None:
    """The backend's schema from its SQLAlchemy models. The two trigram indexes only speed up ILIKE on large
    mailboxes and need the pg_trgm extension, which the embedded build lacks; 123 emails don't need them."""
    table = Email.__table__
    for index in list(table.indexes):
        if index.name and index.name.endswith("_trgm"):
            table.indexes.discard(index)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


def make_embedder(mode: str | None = None):
    """Local embeddings (no external API). Falls back to the deterministic keyword embedder if the model
    can't be downloaded or loaded, so the demo always starts."""
    mode = (mode or os.environ.get("DEMO_EMBEDDINGS") or "fastembed").lower()
    if mode == "fastembed":
        try:
            embedder = FastEmbedder(EMBEDDING_MODEL)
            embedder.embed_query("warm up")
            return embedder, "semantic"
        except Exception as exc:  # network blocked, model download failed, onnxruntime missing...
            logger.warning("local embedding model unavailable (%s); using keyword embeddings", type(exc).__name__)
    return FakeEmbedder(), "keyword"


def build(now: datetime | None = None, embeddings: str | None = None) -> DemoData:
    started = time.monotonic()
    now = now or datetime.now(UTC)
    day = now.astimezone(ZoneInfo(ORG_TZ)).date()
    server = _postgres()
    database = f"demo_{day:%Y%m%d}_{uuid.uuid4().hex[:8]}"
    server.psql(f'CREATE DATABASE "{database}";')
    engine = create_engine(_sqlalchemy_url(server, database), pool_pre_ping=True, pool_size=5, max_overflow=10)
    _create_schema(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    embedder, search_mode = make_embedder(embeddings)

    with factory() as db:
        org = Organization(slug=ORG_SLUG, name=ORG_NAME, timezone=ORG_TZ)
        db.add(org)
        db.flush()
        mailbox = MailboxAccount(organization_id=org.id, provider="mock", email_address=MAILBOX, status="connected")
        db.add(mailbox)
        db.commit()
        initial_sync(db, mailbox, get_mail_provider(mailbox, now=now), days=IMPORT_WINDOW_DAYS, now=now)
        stats = process_org(db, org.id, FakeLLM(), embedder, allow_batch=False)
        count = db.scalar(select(func.count()).select_from(Email).where(Email.organization_id == org.id))
        org_id = org.id

    return DemoData(org_id=org_id, tz=ORG_TZ, anchor=now, day=day, session_factory=factory, embedder=embedder,
                    search_mode=search_mode, email_count=count, classified=stats.get("classified", 0),
                    build_seconds=round(time.monotonic() - started, 1))
