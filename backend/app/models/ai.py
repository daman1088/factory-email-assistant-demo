import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import created_at_col, org_id_col, uuid_pk


class EmailClassification(Base):
    __tablename__ = "email_classifications"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "email_id"], ["emails.organization_id", "emails.id"],
                             ondelete="CASCADE"),
        # Exactly one current classification per email; older ones are kept for comparison.
        Index("uq_classification_current", "email_id", unique=True, postgresql_where=text("is_current")),
        Index("ix_classification_org_cat", "organization_id", "primary_category",
              postgresql_where=text("is_current")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id_col()
    email_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    primary_category: Mapped[str] = mapped_column(String(20))
    secondary_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    company_name: Mapped[str | None] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10))
    action_required: Mapped[bool] = mapped_column(Boolean, default=False)
    suggested_action: Mapped[str | None] = mapped_column(Text)
    important_dates: Mapped[list] = mapped_column(JSONB, default=list)  # [{date, kind, description}]
    amounts: Mapped[list] = mapped_column(JSONB, default=list)  # [{amount, currency, context}]
    references: Mapped[list] = mapped_column(JSONB, default=list)  # [{type, value}]
    financial_signal: Mapped[str] = mapped_column(String(30), default="none")
    confidence: Mapped[float] = mapped_column(Float)
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(40))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at_col()


class ActionItem(Base):
    __tablename__ = "action_items"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "email_id"], ["emails.organization_id", "emails.id"],
                             ondelete="CASCADE"),
        ForeignKeyConstraint(["organization_id", "thread_id"],
                             ["email_threads.organization_id", "email_threads.id"], ondelete="CASCADE"),
        Index("ix_action_items_org_status", "organization_id", "status"),
        Index("uq_action_item_email_ai", "email_id", unique=True, postgresql_where=text("source = 'ai'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id_col()
    email_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(20), default="other")  # respond | follow_up | other
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | done | dismissed
    source: Mapped[str] = mapped_column(String(10), default="ai")
    created_at: Mapped[datetime] = created_at_col()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailySummary(Base):
    __tablename__ = "daily_summaries"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id_col()
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    summary_text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = created_at_col()
