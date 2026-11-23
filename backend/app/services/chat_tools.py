"""Read-only tools the chat model can call. The organisation id is bound server-side from the user's
session (ToolContext); tool inputs can only narrow a query inside that tenant, never widen it."""
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.domain import Category, FinancialSignal, Priority
from app.models import Email, EmailClassification, Organization
from app.services import email_query as q
from app.services.dashboard import action_items as list_action_items_svc
from app.services.dates import resolve_range
from app.services.search import hybrid_search

MAX_BODY_CHARS = 4000

DatePreset = Literal["today", "yesterday", "this_week", "last_7_days", "this_month", "last_30_days", "all", "custom"]


class Filters(BaseModel):
    date_preset: DatePreset = "all"
    start_date: date | None = None
    end_date: date | None = None
    categories: list[Category] = []
    priorities: list[Priority] = []
    action_required: bool | None = None
    direction: Literal["inbound", "outbound", "any"] = "inbound"
    company: str | None = Field(default=None, max_length=100)
    financial_signals: list[FinancialSignal] = []
    keyword: str | None = Field(default=None, max_length=100)


class CountInput(Filters):
    group_by: Literal["none", "category", "priority", "company", "day", "financial_signal"] = "none"


class ListInput(Filters):
    limit: int = Field(default=15, ge=1, le=30)
    order: Literal["newest", "oldest", "priority"] = "newest"


class SearchInput(Filters):
    query: str = Field(min_length=2, max_length=300)
    limit: int = Field(default=10, ge=1, le=15)


class EmailIdInput(BaseModel):
    email_id: uuid.UUID


class ThreadIdInput(BaseModel):
    thread_id: uuid.UUID


class UnansweredInput(Filters):
    categories: list[Category] = [Category.ENQUIRY, Category.ORDER]
    limit: int = Field(default=20, ge=1, le=30)


class ActionItemsInput(BaseModel):
    status: Literal["open", "done", "all"] = "open"
    date_preset: DatePreset = "last_30_days"
    start_date: date | None = None
    end_date: date | None = None


@dataclass
class ToolContext:
    db: Session
    org: Organization
    embedder: object
    seen_email_ids: set[str] = field(default_factory=set)

    @property
    def tz(self) -> str:
        return self.org.timezone


def _to_filters(ctx: ToolContext, f: Filters) -> q.EmailFilters:
    rng = resolve_range(f.date_preset, ctx.tz, start_date=f.start_date, end_date=f.end_date)
    return q.EmailFilters(
        start=rng.start, end=rng.end, categories=[c.value for c in f.categories],
        priorities=[p.value for p in f.priorities], action_required=f.action_required,
        direction=None if f.direction == "any" else f.direction, company=f.company,
        financial_signals=[s.value for s in f.financial_signals], keyword=f.keyword,
    )


def _item(ctx: ToolContext, e: Email, c: EmailClassification | None, *, full: bool = False) -> dict:
    ctx.seen_email_ids.add(str(e.id))
    item = {
        "email_id": str(e.id), "thread_id": str(e.thread_id),
        "date": q.to_local(e.sent_at, ctx.tz).strftime("%Y-%m-%d %H:%M %Z"),
        "direction": e.direction, "from": f"{e.from_name or ''} <{e.from_email}>".strip(),
        "subject": e.subject,
    }
    if e.direction == "outbound":
        item["to"] = ", ".join(a.get("name") or a["email"] for a in e.to_addrs or [])
    if c is not None:
        item.update({
            "category": c.primary_category, "priority": c.priority, "action_required": c.action_required,
            "company": c.company_name, "summary": c.summary, "suggested_action": c.suggested_action,
            "financial_signal": c.financial_signal,
        })
        if c.amounts:
            item["amounts"] = c.amounts
        if c.references:
            item["references"] = c.references
        if c.important_dates:
            item["dates"] = c.important_dates
    else:
        item["snippet"] = e.snippet
    if full:
        item["attachments"] = [a.get("filename") for a in e.attachments or []]
        item["body"] = "<untrusted_email_body>\n" + (e.body_clean or e.body_text)[:MAX_BODY_CHARS] + \
                       "\n</untrusted_email_body>"
    return item


def count_emails(ctx: ToolContext, inp: CountInput) -> dict:
    f = _to_filters(ctx, inp)
    out: dict = {"total": q.count(ctx.db, ctx.org.id, f)}
    columns = {
        "category": EmailClassification.primary_category,
        "priority": EmailClassification.priority,
        "financial_signal": EmailClassification.financial_signal,
        "company": func.coalesce(EmailClassification.company_name, Email.from_name),
        "day": func.to_char(func.timezone(ctx.tz, Email.sent_at), "YYYY-MM-DD"),
    }
    if inp.group_by != "none":
        out["groups"] = [{"key": k or "not yet analysed", "count": n}
                         for k, n in q.count_by(ctx.db, ctx.org.id, f, columns[inp.group_by])][:40]
    return out


def list_emails(ctx: ToolContext, inp: ListInput) -> dict:
    f = _to_filters(ctx, inp)
    total = q.count(ctx.db, ctx.org.id, f)
    rows = q.list_emails(ctx.db, ctx.org.id, f, limit=inp.limit, order=inp.order)
    return {"total_matching": total, "returned": len(rows), "emails": [_item(ctx, e, c) for e, c in rows]}


def search_emails(ctx: ToolContext, inp: SearchInput) -> dict:
    f = _to_filters(ctx, inp)
    rows = hybrid_search(ctx.db, ctx.org.id, inp.query, f, ctx.embedder, limit=inp.limit)
    return {"note": "Ranked by relevance; may include weak matches. Not a complete list - use "
                    "count_emails/list_emails for exhaustive answers.",
            "emails": [{**_item(ctx, e, c), "match": how} for e, c, how in rows]}


def get_email(ctx: ToolContext, inp: EmailIdInput) -> dict:
    row = q.get_email(ctx.db, ctx.org.id, inp.email_id)
    if row is None:
        return {"error": "email not found"}
    return _item(ctx, row[0], row[1], full=True)


def get_thread(ctx: ToolContext, inp: ThreadIdInput) -> dict:
    rows = q.thread_messages(ctx.db, ctx.org.id, inp.thread_id)
    if not rows:
        return {"error": "thread not found"}
    items = [_item(ctx, e, c) for e, c in rows]
    for item, (e, _c) in zip(items, rows, strict=True):
        item["body"] = "<untrusted_email_body>\n" + (e.body_clean or "")[:1500] + "\n</untrusted_email_body>"
    return {"messages": items}


def find_unanswered(ctx: ToolContext, inp: UnansweredInput) -> dict:
    f = _to_filters(ctx, inp)
    rows = q.unanswered(ctx.db, ctx.org.id, f, limit=inp.limit)
    now = q.to_local(datetime.now(UTC), ctx.tz)
    items = []
    for e, c in rows:
        it = _item(ctx, e, c)
        it["waiting_days"] = (now - q.to_local(e.sent_at, ctx.tz)).days
        items.append(it)
    return {"note": "Threads whose latest inbound email has no later reply from this mailbox.",
            "count": len(items), "emails": items}


def list_action_items(ctx: ToolContext, inp: ActionItemsInput) -> dict:
    rng = resolve_range(inp.date_preset, ctx.tz, start_date=inp.start_date, end_date=inp.end_date)
    items = list_action_items_svc(ctx.db, ctx.org.id, rng, ctx.tz, status=inp.status, limit=30)
    for it in items:
        ctx.seen_email_ids.add(it["email"]["id"])
    return {"count": len(items), "items": [{
        "title": it["title"], "priority": it["priority"], "status": it["status"], "due_date": it["due_date"],
        "email_id": it["email"]["id"], "from": it["email"]["from_name"], "subject": it["email"]["subject"],
        "date": it["email"]["sent_at"][:16].replace("T", " "),
    } for it in items]}


def financial_mentions(ctx: ToolContext, inp: ListInput) -> dict:
    if not inp.financial_signals and not inp.categories:
        inp = inp.model_copy(update={"categories": [Category.FINANCIAL]})
    result = list_emails(ctx, inp)
    result["note"] = ("financial_signal describes what each email SAYS. payment_claimed = sender claims "
                      "payment, unverified. Only a bank alert email is evidence of money received.")
    return result


def _schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema with $refs inlined and titles dropped, so tool schemas are self-contained."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


TOOLS: dict[str, tuple[type[BaseModel], callable, str]] = {
    "count_emails": (CountInput, count_emails,
                     "Exact count of emails matching filters, optionally grouped. Use for every 'how many' "
                     "question. direction defaults to inbound (received mail)."),
    "list_emails": (ListInput, list_emails,
                    "List emails matching structured filters (date range, category, priority, company, "
                    "action_required, keyword). Returns AI summaries and extracted amounts/references."),
    "search_emails": (SearchInput, search_emails,
                      "Semantic + keyword search for a topic (e.g. 'cotton fabric', 'shade variation'). Combine "
                      "with filters. Returns the most relevant emails, not an exhaustive list."),
    "get_email": (EmailIdInput, get_email, "Full details and body of one email by email_id."),
    "get_thread": (ThreadIdInput, get_thread,
                   "All messages in a conversation thread (received and sent), oldest first."),
    "find_unanswered": (UnansweredInput, find_unanswered,
                        "Customer emails (default: enquiries and orders) that have no reply from us later in "
                        "the same thread."),
    "list_action_items": (ActionItemsInput, list_action_items,
                          "Outstanding (or done) action items extracted from emails, ordered by priority and "
                          "due date."),
    "financial_mentions": (ListInput, financial_mentions,
                           "Emails about invoices, payments, reminders, claims and disputes with extracted "
                           "amounts and references. Filter with financial_signals and company."),
}

TOOL_DEFINITIONS = [{"name": name, "description": desc, "input_schema": _schema(model)}
                    for name, (model, _fn, desc) in TOOLS.items()]


def run_tool(ctx: ToolContext, name: str, raw_input: dict) -> tuple[str, bool]:
    """Returns (json content, is_error)."""
    if name not in TOOLS:
        return json.dumps({"error": f"unknown tool {name}"}), True
    model, fn, _ = TOOLS[name]
    try:
        inp = model.model_validate(raw_input or {})
    except ValidationError as exc:
        return json.dumps({"error": "invalid input", "details": exc.errors(include_url=False)[:5]},
                          default=str), True
    try:
        result = fn(ctx, inp)
    except ValueError as exc:
        return json.dumps({"error": str(exc)}), True
    return json.dumps(result, ensure_ascii=False, default=str), False
