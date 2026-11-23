import uuid

from sqlalchemy import and_, case, select
from sqlalchemy.orm import Session

from app.domain import CATEGORY_LABELS, Category
from app.models import ActionItem, Email, EmailClassification
from app.services import email_query as q
from app.services.dates import DateRange
from app.services.serializers import email_row


def kpis(db: Session, org_id: uuid.UUID, rng: DateRange) -> dict:
    f = q.EmailFilters(start=rng.start, end=rng.end, direction="inbound")
    by_cat = dict(q.count_by(db, org_id, f, EmailClassification.primary_category))
    total = q.count(db, org_id, f)
    return {
        "total_received": total,
        "enquiries": by_cat.get(Category.ENQUIRY.value, 0),
        "orders": by_cat.get(Category.ORDER.value, 0),
        "financial": by_cat.get(Category.FINANCIAL.value, 0),
        "suppliers": by_cat.get(Category.SUPPLIER.value, 0),
        "logistics": by_cat.get(Category.LOGISTICS.value, 0),
        "high_priority": q.count(db, org_id, q.EmailFilters(start=rng.start, end=rng.end, priorities=["high"])),
        "action_required": q.count(db, org_id, q.EmailFilters(start=rng.start, end=rng.end, action_required=True)),
        "pending_analysis": by_cat.get(None, 0),
        "sent": q.count(db, org_id, q.EmailFilters(start=rng.start, end=rng.end, direction="outbound")),
    }


def category_breakdown(db: Session, org_id: uuid.UUID, rng: DateRange) -> list[dict]:
    f = q.EmailFilters(start=rng.start, end=rng.end, direction="inbound")
    counts = dict(q.count_by(db, org_id, f, EmailClassification.primary_category))
    return [{"category": c.value, "label": CATEGORY_LABELS[c], "count": counts.get(c.value, 0)} for c in Category]


def priority_inbox(db: Session, org_id: uuid.UUID, rng: DateRange, tz: str, limit: int = 8) -> list[dict]:
    f = q.EmailFilters(start=rng.start, end=rng.end, priorities=["high", "medium"])
    return [email_row(e, c, tz) for e, c in q.list_emails(db, org_id, f, limit=limit, order="priority")]


def action_items(db: Session, org_id: uuid.UUID, rng: DateRange, tz: str, *, status: str = "open",
                 limit: int = 50) -> list[dict]:
    prio = case({"high": 0, "medium": 1, "low": 2}, value=ActionItem.priority, else_=3)
    stmt = (
        select(ActionItem, Email, EmailClassification)
        .join(Email, and_(Email.id == ActionItem.email_id, Email.organization_id == org_id))
        .outerjoin(EmailClassification, and_(EmailClassification.email_id == Email.id,
                                             EmailClassification.is_current))
        .where(ActionItem.organization_id == org_id, Email.sent_at >= rng.start, Email.sent_at < rng.end)
        .order_by(prio, ActionItem.due_date.asc().nulls_last(), Email.sent_at.desc())
        .limit(limit)
    )
    if status != "all":
        stmt = stmt.where(ActionItem.status == status)
    return [{
        "id": str(item.id), "title": item.title, "priority": item.priority, "status": item.status,
        "kind": item.kind, "due_date": item.due_date.isoformat() if item.due_date else None,
        "email": email_row(email, cls, tz),
    } for item, email, cls in db.execute(stmt).all()]
