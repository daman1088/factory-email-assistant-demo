"""Tenant-scoped email queries shared by the Emails API, the dashboard and the chat tools.

Every function takes `org_id` explicitly and applies it in SQL. Nothing here trusts a client- or
LLM-supplied organisation id.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, case, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import Contact, Email, EmailClassification

PRIORITY_RANK = case({"high": 0, "medium": 1, "low": 2}, value=EmailClassification.priority, else_=3)


@dataclass
class EmailFilters:
    start: datetime | None = None
    end: datetime | None = None
    categories: list[str] = field(default_factory=list)
    priorities: list[str] = field(default_factory=list)
    action_required: bool | None = None
    direction: str | None = "inbound"  # inbound | outbound | None (= both)
    company: str | None = None
    financial_signals: list[str] = field(default_factory=list)
    keyword: str | None = None
    thread_id: uuid.UUID | None = None


def base_query(org_id: uuid.UUID) -> Select:
    return (
        select(Email, EmailClassification)
        .outerjoin(EmailClassification, and_(EmailClassification.email_id == Email.id,
                                             EmailClassification.is_current))
        .where(Email.organization_id == org_id)
    )


def company_condition(db: Session, org_id: uuid.UUID, company: str):
    """Match a company by AI-extracted name, sender display name, or any address we've seen at that
    company (so our outbound replies *to* them are included too)."""
    like = f"%{company.strip()}%"
    addresses = list(db.execute(
        select(Contact.email).where(Contact.organization_id == org_id,
                                    or_(Contact.company_name.ilike(like), Contact.display_name.ilike(like),
                                        Contact.domain.ilike(f"%{company.strip().replace(' ', '')}%")))
    ).scalars())
    conds = [EmailClassification.company_name.ilike(like), Email.from_name.ilike(like)]
    if addresses:
        conds.append(Email.participant_emails.overlap(addresses))
    return or_(*conds)


def apply_filters(db: Session, org_id: uuid.UUID, stmt: Select, f: EmailFilters) -> Select:
    if f.start:
        stmt = stmt.where(Email.sent_at >= f.start)
    if f.end:
        stmt = stmt.where(Email.sent_at < f.end)
    if f.direction:
        stmt = stmt.where(Email.direction == f.direction)
    if f.categories:
        stmt = stmt.where(EmailClassification.primary_category.in_(f.categories))
    if f.priorities:
        stmt = stmt.where(EmailClassification.priority.in_(f.priorities))
    if f.action_required is not None:
        stmt = stmt.where(EmailClassification.action_required.is_(f.action_required))
    if f.financial_signals:
        stmt = stmt.where(EmailClassification.financial_signal.in_(f.financial_signals))
    if f.company:
        stmt = stmt.where(company_condition(db, org_id, f.company))
    if f.keyword:
        like = f"%{f.keyword.strip()}%"
        stmt = stmt.where(or_(Email.subject.ilike(like), Email.body_clean.ilike(like),
                              EmailClassification.summary.ilike(like)))
    if f.thread_id:
        stmt = stmt.where(Email.thread_id == f.thread_id)
    return stmt


def count(db: Session, org_id: uuid.UUID, f: EmailFilters) -> int:
    stmt = apply_filters(db, org_id, select(func.count(Email.id)).select_from(Email).outerjoin(
        EmailClassification, and_(EmailClassification.email_id == Email.id, EmailClassification.is_current)
    ).where(Email.organization_id == org_id), f)
    return db.execute(stmt).scalar_one()


def count_by(db: Session, org_id: uuid.UUID, f: EmailFilters, column) -> list[tuple[str | None, int]]:
    stmt = (select(column, func.count(Email.id)).select_from(Email)
            .outerjoin(EmailClassification, and_(EmailClassification.email_id == Email.id,
                                                 EmailClassification.is_current))
            .where(Email.organization_id == org_id))
    stmt = apply_filters(db, org_id, stmt, f).group_by(column).order_by(func.count(Email.id).desc())
    return [(k, n) for k, n in db.execute(stmt).all()]


def list_emails(db: Session, org_id: uuid.UUID, f: EmailFilters, *, limit: int = 50, offset: int = 0,
                order: str = "newest") -> list[tuple[Email, EmailClassification | None]]:
    stmt = apply_filters(db, org_id, base_query(org_id), f)
    if order == "priority":
        stmt = stmt.order_by(PRIORITY_RANK, EmailClassification.action_required.desc().nulls_last(),
                             Email.sent_at.desc())
    elif order == "oldest":
        stmt = stmt.order_by(Email.sent_at.asc())
    else:
        stmt = stmt.order_by(Email.sent_at.desc())
    return [(e, c) for e, c in db.execute(stmt.limit(limit).offset(offset)).all()]


def get_email(db: Session, org_id: uuid.UUID, email_id: uuid.UUID):
    return db.execute(base_query(org_id).where(Email.id == email_id)).first()


def unanswered(db: Session, org_id: uuid.UUID, f: EmailFilters, *, limit: int = 50):
    """Inbound emails with no later outbound message in the same thread, one row per thread (the latest
    unanswered inbound message)."""
    reply = aliased(Email)
    has_reply = exists().where(reply.organization_id == org_id, reply.thread_id == Email.thread_id,
                               reply.direction == "outbound", reply.sent_at > Email.sent_at)
    f = EmailFilters(**{**f.__dict__, "direction": "inbound"})
    stmt = apply_filters(db, org_id, base_query(org_id), f).where(~has_reply)
    stmt = stmt.order_by(Email.thread_id, Email.sent_at.desc()).distinct(Email.thread_id)
    rows = [(e, c) for e, c in db.execute(stmt).all()]
    rows.sort(key=lambda r: r[0].sent_at, reverse=True)
    return rows[:limit]


def thread_messages(db: Session, org_id: uuid.UUID, thread_id: uuid.UUID):
    stmt = base_query(org_id).where(Email.thread_id == thread_id).order_by(Email.sent_at.asc())
    return [(e, c) for e, c in db.execute(stmt).all()]


def to_local(dt: datetime, tz: str) -> datetime:
    return dt.astimezone(ZoneInfo(tz))
