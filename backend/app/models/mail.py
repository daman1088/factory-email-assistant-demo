import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.domain import ProcessingState
from app.models.common import created_at_col, org_id_col, uuid_pk

EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5. Changing the model to another size needs a migration.


class MailboxAccount(Base):
    __tablename__ = "mailbox_accounts"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_mailbox_org_id"),
        UniqueConstraint("organization_id", "email_address", name="uq_mailbox_org_email"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id_col()
    provider: Mapped[str] = mapped_column(String(20))  # gmail | mock
    email_address: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20), default="connected")  # connected|error|disconnected
    encrypted_refresh_token: Mapped[bytes | None] = mapped_column(LargeBinary)
    encrypted_access_token: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str | None] = mapped_column(Text)
    history_id: Mapped[str | None] = mapped_column(String(40))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "mailbox_account_id"],
                             ["mailbox_accounts.organization_id", "mailbox_accounts.id"], ondelete="CASCADE"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id_col()
    mailbox_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(20))  # initial | incremental | reprocess
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|succeeded|failed
    started_at: Mapped[datetime] = created_at_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    messages_seen: Mapped[int] = mapped_column(Integer, default=0)
    messages_new: Mapped[int] = mapped_column(Integer, default=0)
    cursor: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class EmailThread(Base):
    __tablename__ = "email_threads"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_thread_org_id"),
        UniqueConstraint("mailbox_account_id", "provider_thread_id", name="uq_thread_provider"),
        ForeignKeyConstraint(["organization_id", "mailbox_account_id"],
                             ["mailbox_accounts.organization_id", "mailbox_accounts.id"], ondelete="CASCADE"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id_col()
    mailbox_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    provider_thread_id: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(Text)
    first_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, default=0)


class Email(Base):
    __tablename__ = "emails"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_email_org_id"),
        # Dedup: the same provider message can only be stored once per mailbox.
        UniqueConstraint("mailbox_account_id", "provider_message_id", name="uq_email_provider_msg"),
        ForeignKeyConstraint(["organization_id", "mailbox_account_id"],
                             ["mailbox_accounts.organization_id", "mailbox_accounts.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["organization_id", "thread_id"],
                             ["email_threads.organization_id", "email_threads.id"], ondelete="CASCADE"),
        Index("ix_emails_org_sent", "organization_id", "sent_at"),
        Index("ix_emails_org_dir_sent", "organization_id", "direction", "sent_at"),
        Index("ix_emails_org_state", "organization_id", "processing_state"),
        Index("ix_emails_participants", "participant_emails", postgresql_using="gin"),
        Index("ix_emails_subject_trgm", "subject", postgresql_using="gin",
              postgresql_ops={"subject": "gin_trgm_ops"}),
        Index("ix_emails_body_trgm", "body_clean", postgresql_using="gin",
              postgresql_ops={"body_clean": "gin_trgm_ops"}),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id_col()
    mailbox_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    provider_message_id: Mapped[str] = mapped_column(String(200))
    rfc822_message_id: Mapped[str | None] = mapped_column(String(500))
    in_reply_to: Mapped[str | None] = mapped_column(String(500))
    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound
    from_name: Mapped[str | None] = mapped_column(String(300))
    from_email: Mapped[str] = mapped_column(String(320))
    from_domain: Mapped[str | None] = mapped_column(String(255))
    to_addrs: Mapped[list] = mapped_column(JSONB, default=list)  # [{name, email}]
    cc_addrs: Mapped[list] = mapped_column(JSONB, default=list)
    participant_emails: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # lowercased from/to/cc
    subject: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text, default="")  # full plain text
    body_clean: Mapped[str] = mapped_column(Text, default="")  # quotes/signature stripped; used for AI input
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    attachments: Mapped[list] = mapped_column(JSONB, default=list)  # metadata only: [{filename, mime_type, size}]
    labels: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processing_state: Mapped[str] = mapped_column(String(20), default=ProcessingState.INGESTED.value)
    processing_error: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_col()


class Contact(Base):
    """One row per external address we've exchanged mail with. company_name is filled in from AI
    classification, which lets 'ABC Textiles' resolve to every address at that company."""
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("organization_id", "email", name="uq_contact_org_email"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id_col()
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(300))
    company_name: Mapped[str | None] = mapped_column(String(300))
    domain: Mapped[str | None] = mapped_column(String(255))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailEmbedding(Base):
    __tablename__ = "email_embeddings"
    __table_args__ = (
        UniqueConstraint("email_id", "chunk_index", "model", name="uq_embedding_chunk"),
        ForeignKeyConstraint(["organization_id", "email_id"], ["emails.organization_id", "emails.id"],
                             ondelete="CASCADE"),
        Index("ix_embeddings_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id_col()
    email_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = created_at_col()
