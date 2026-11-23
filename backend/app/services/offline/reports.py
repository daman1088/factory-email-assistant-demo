"""Offline report builders. Each returns markdown built only from:
  * exact SQL results (counts, totals, reply status from thread timestamps), and
  * text copied from the emails (quotes, quantities, amounts, dates - see extract.py).
Every email that contributes a line is cited as [[email:<id>]], and every id is registered in
ctx.seen_email_ids so the citation validator keeps it.
"""
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from app.domain import CATEGORY_LABELS
from app.models import Email, EmailClassification, EmailThread
from app.services import email_query as q
from app.services.chat_tools import ToolContext
from app.services.dashboard import action_items as action_items_svc
from app.services.dates import DateRange, resolve_range
from app.services.offline import extract as x
from app.services.offline.intent import Intent
from app.services.search import hybrid_search

HEADER = "**Offline summary** — assembled by rules from your synced emails, not written by an AI model."
STATED_NOTE = ("Quoted text is copied from the emails. Amounts, quantities, dates, payments and deliveries are "
               "what the emails *say*; they are not verified records.")
COUNT_NOTE = "Counts are exact database results."
CLASSIFIER_NOTE = ("Categories come from the offline rules classifier, which mislabels some emails; "
                   "an AI model classifies more accurately.")


# ---- prepared messages --------------------------------------------------------------------------------

@dataclass
class Msg:
    email: Email
    cls: EmailClassification | None
    tz: str
    body: str = ""
    refs: dict = field(default_factory=dict)
    focus: list[str] = field(default_factory=list)  # set for emails that only *mention* a company
    domain_company: str | None = None

    def __post_init__(self):
        self.body = self.email.body_clean or self.email.body_text or ""
        self.refs = x.references(f"{self.email.subject or ''}\n{self.body}")

    @property
    def text(self) -> str:
        """The part of the email a report should draw facts from: all of it, or only the sentences that
        mention the focus company."""
        if self.focus:
            hits = x.focus_sentences(f"{self.email.subject or ''}.\n\n{self.body}", self.focus)
            if hits:
                return "\n\n".join(hits)
        return self.body

    @property
    def ask_text(self) -> str:
        # Internal notes cover several customers: only a request in the sentences about the focus company counts.
        return self.text if self.category == "internal" else self.body

    @property
    def asks(self) -> bool:
        return x.asks_for_response(self.ask_text)

    @property
    def id(self) -> str:
        return str(self.email.id)

    @property
    def inbound(self) -> bool:
        return self.email.direction == "inbound"

    @property
    def category(self) -> str | None:
        return self.cls.primary_category if self.cls else None

    @property
    def local(self) -> datetime:
        return q.to_local(self.email.sent_at, self.tz)

    @property
    def who(self) -> str:
        if not self.inbound:
            return "You"
        company = self.cls.company_name if self.cls else None
        if company == "Internal":
            company = None
        if (not company or company == self.email.from_name) and self.domain_company:
            return self.domain_company
        return company or self.email.from_name or self.email.from_email

    @property
    def subject(self) -> str:
        return re.sub(r"^(?:(?:re|fwd?|fw)\s*:\s*)+", "", self.email.subject or "(no subject)", flags=re.I)

    @property
    def event(self) -> str:
        return x.event_label(self.email.direction, self.email.subject or "", self.body, self.category)

    @property
    def key(self) -> str:
        if self.focus:
            return x.best_sentence(x.focus_sentences(self.body, self.focus) or x.sentences(self.body),
                                   self.email.subject or "")
        return x.key_sentence(self.email.subject or "", self.body)

    @property
    def payment(self) -> tuple[str, str] | None:
        return x.payment_statement(self.email.subject or "", self.body)

    @property
    def shipment(self) -> tuple[str, str] | None:
        return x.shipment_status(self.email.subject or "", self.body, self.email.direction)


def domain_companies(ctx: ToolContext) -> dict[str, str]:
    """Most common company name the classifier found per sender domain (ignoring person-name fallbacks), so
    'Senthil Kumar' at kaverigarments.example is shown as Kaveri Garments. Cached per question."""
    cached = getattr(ctx, "_domain_companies", None)
    if cached is not None:
        return cached
    rows = ctx.db.execute(text("""
        SELECT DISTINCT ON (e.from_domain) e.from_domain, c.company_name
        FROM emails e JOIN email_classifications c ON c.email_id = e.id AND c.is_current
        WHERE e.organization_id = :org AND e.direction = 'inbound' AND e.from_domain IS NOT NULL
          AND c.company_name IS NOT NULL AND c.company_name <> 'Internal'
          AND c.company_name IS DISTINCT FROM e.from_name
        GROUP BY e.from_domain, c.company_name
        ORDER BY e.from_domain, count(*) DESC"""), {"org": ctx.org.id}).all()
    mapping = {d: name for d, name in rows}
    ctx._domain_companies = mapping
    return mapping


def make(ctx: ToolContext, e: Email, c: EmailClassification | None) -> Msg:
    m = Msg(e, c, ctx.tz)
    m.domain_company = domain_companies(ctx).get(e.from_domain or "")
    return m


def prep(ctx: ToolContext, rows) -> list[Msg]:
    out = []
    for e, c in rows:
        ctx.seen_email_ids.add(str(e.id))
        out.append(make(ctx, e, c))
    return out


def cite(m: Msg) -> str:
    return f"[[email:{m.id}]]"


def day(dt: datetime) -> str:
    return f"{dt.day} {dt:%b}"


def today_local(ctx: ToolContext) -> datetime:
    return q.to_local(datetime.now(UTC), ctx.tz)


def ago(ctx: ToolContext, m: Msg) -> str:
    days = (today_local(ctx).date() - m.local.date()).days
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"


def quote(s: str) -> str:
    return f"“{s.strip().rstrip('.')}”"


def range_phrase(rng: DateRange) -> str:
    label = rng.label.lower()
    if rng.label == "All time":
        return "in all synced mail"
    if label in ("today", "yesterday") or label.startswith("this "):
        return label
    if label.startswith("last "):
        return f"in the {label}"
    return f"from {rng.label}"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def inr(value: float) -> str:
    whole = int(round(value))
    s = str(abs(whole))
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ("-" if whole < 0 else "") + "₹" + ",".join(groups + [tail])


def fmt_total(currency: str, value: float) -> str:
    return inr(value) if currency == "INR" else f"{currency} {value:,.0f}"


# ---- SQL helpers ---------------------------------------------------------------------------------------

def filters(rng: DateRange, intent: Intent, **over) -> q.EmailFilters:
    base = dict(start=rng.start, end=rng.end, categories=list(intent.categories), priorities=list(intent.priorities),
                direction=intent.direction, company=intent.company, keyword=intent.keyword)
    base.update(over)
    return q.EmailFilters(**base)


def fetch(ctx: ToolContext, f: q.EmailFilters, *, extra=None, limit: int = 200, order: str = "newest"):
    stmt = q.apply_filters(ctx.db, ctx.org.id, q.base_query(ctx.org.id), f)
    if extra is not None:
        stmt = stmt.where(extra)
    stmt = stmt.order_by(Email.sent_at.asc() if order == "oldest" else Email.sent_at.desc()).limit(limit)
    return [(e, c) for e, c in ctx.db.execute(stmt).all()]


ThreadState = dict[uuid.UUID, tuple[datetime | None, datetime | None]]


def thread_state(db: Session, org_id: uuid.UUID, thread_ids) -> ThreadState:
    if not thread_ids:
        return {}
    rows = db.execute(select(EmailThread.id, EmailThread.last_inbound_at, EmailThread.last_outbound_at).where(
        EmailThread.organization_id == org_id, EmailThread.id.in_(list(thread_ids)))).all()
    return {tid: (li, lo) for tid, li, lo in rows}


def awaiting_reply(state: dict, m: Msg) -> bool:
    """The latest inbound email of this thread has no later email from this mailbox."""
    li, lo = state.get(m.email.thread_id, (None, None))
    return li is not None and (lo is None or lo < li)


def needs_reply(state: dict, m: Msg) -> bool:
    """No reply yet AND this is the thread's latest inbound email AND it asks for something."""
    li, _lo = state.get(m.email.thread_id, (None, None))
    automated = re.search(r"no-?reply|do-?not-?reply|alerts?@|notifications?@", m.email.from_email or "", re.I)
    return (m.inbound and not automated and awaiting_reply(state, m) and li is not None
            and m.email.sent_at >= li and m.asks)


def stated_totals(db: Session, org_id: uuid.UUID, ids: list[str]) -> list[tuple[str, int, float]]:
    """SQL sum of the largest non-rate amount stored for each email, per currency."""
    if not ids:
        return []
    rows = db.execute(text("""
        SELECT currency, count(*) AS n, sum(mx) AS total FROM (
            SELECT c.email_id, upper(a->>'currency') AS currency, max((a->>'amount')::numeric) AS mx
            FROM email_classifications c CROSS JOIN LATERAL jsonb_array_elements(c.amounts) a
            WHERE c.organization_id = :org AND c.is_current AND c.email_id = ANY(:ids)
              AND coalesce(a->>'context', '') NOT ILIKE '%rate%'
              AND coalesce(a->>'context', '') NOT ILIKE '%per %'
            GROUP BY c.email_id, upper(a->>'currency')) t
        GROUP BY currency ORDER BY currency"""), {"org": org_id, "ids": [uuid.UUID(i) for i in ids]}).all()
    return [(cur, n, float(total)) for cur, n, total in rows]


def company_candidates(db: Session, org_id: uuid.UUID) -> list[str]:
    from app.models import Contact

    names: set[str] = set()
    names.update(db.execute(select(EmailClassification.company_name).where(
        EmailClassification.organization_id == org_id, EmailClassification.is_current,
        EmailClassification.company_name.is_not(None))).scalars())
    names.update(db.execute(select(Contact.company_name).where(
        Contact.organization_id == org_id, Contact.company_name.is_not(None))).scalars())
    names.update(db.execute(select(Email.from_name).where(
        Email.organization_id == org_id, Email.direction == "inbound", Email.from_name.is_not(None))).scalars())
    return sorted(n for n in names if n and n != "Internal")


# ---- shared rendering ----------------------------------------------------------------------------------

def facts_line(msgs: list[Msg], *, refs: bool = True) -> str | None:
    """'Stated: ...' - values copied verbatim from the emails, de-duplicated, in order of appearance."""
    qty, amounts, rates, dates, ref_vals = [], [], [], [], []
    for m in msgs:
        qty += x.quantities(m.text)
        a, r = x.money(m.text)
        amounts += a
        rates += r
        dates += x.deadlines(m.text)
        m_refs = x.references(m.text) if m.focus else m.refs
        for kind in ("po", "invoice", "lr", "utr"):
            ref_vals += [f"{x.REF_LABEL[kind]} {v}" for v in m_refs.get(kind, [])]
    rate_numbers = {re.sub(r"\D", "", r.split("per")[0].split("/")[0]) for r in rates}
    amounts = [a for a in amounts if re.sub(r"\D", "", a) not in rate_numbers]  # "Rs. 118 + GST" is the rate
    parts = []
    for label, values in (("quantities", qty), ("rate", rates), ("amounts", amounts), ("dates", dates),
                          ("refs", ref_vals if refs else [])):
        values = list(dict.fromkeys(values))
        # "5th of next month" is redundant next to "Delivery: 5th of next month"
        values = [v for v in values if not any(v != o and v.lower() in o.lower() for o in values)][:5]
        if values:
            parts.append(f"{label}: {', '.join(values)}")
    return "Stated in the emails — " + " · ".join(parts) if parts else None


def timeline(msgs: list[Msg], *, show: int = 4, says=None) -> list[str]:
    msgs = sorted(msgs, key=lambda m: m.email.sent_at)
    lines = []
    if len(msgs) > show:
        earlier = msgs[:-show]
        lines.append(f"- {plural(len(earlier), 'earlier email')}: " + ", ".join(
            f"{day(m.local)} {m.event.lower()} {cite(m)}" for m in earlier))
        msgs = msgs[-show:]
    for m in msgs:
        who = "" if m.who == "You" or m.event.startswith("You") else f"{m.who}: "
        lines.append(f"- {day(m.local)} · {m.event} — {who}{quote(says(m) if says else m.key)} {cite(m)}")
    return lines


def followups(ctx: ToolContext, msgs: list[Msg], state: dict, *, limit: int = 6) -> list[str]:
    """Rule-based suggestions, one per email, each tied to the email that triggered it."""
    out: dict[str, tuple[int, str]] = {}
    seen_refs: set[str] = set()
    for m in sorted(msgs, key=lambda m: m.email.sent_at, reverse=True):
        if not m.inbound or m.category == "other" or m.id in out:
            continue
        waiting = needs_reply(state, m)
        tail = f" — no reply from you since {day(m.local)}." if waiting else "."
        pay, ship = m.payment, m.shipment
        amounts, _ = x.money(m.text)
        amount = amounts[0] if amounts else "the amount"
        line: tuple[int, str] | None = None
        if pay and m.category != "internal" and pay[0] in ("claimed", "promised", "dispute", "reminder"):
            kind = pay[0]
            if kind == "claimed":
                utr = m.refs.get("utr")
                line = (3, f"Check your bank statement for {amount} that {m.who} says was paid"
                           f"{f' (UTR {utr[0]})' if utr else ''}{tail}")
            elif kind == "promised":
                dl = x.deadlines(m.body)
                line = (4, f"Follow up with {m.who} if {amount} isn't received"
                           f"{f' (email says: {dl[0]})' if dl else ''}{tail}")
            elif kind == "dispute":
                line = (2, f"Review the dispute / debit note from {m.who}: {quote(pay[1])}{tail}")
            else:
                inv = m.refs.get("invoice")
                line = (5, f"Review the payment reminder from {m.who}"
                           f"{f' for invoice {inv[0]}' if inv else ''} ({amount}){tail}")
        elif ship and ship[0] in ("problem", "awaiting_dispatch") and m.category != "internal":
            ref = next((f"{x.REF_LABEL[k]} {m.refs[k][0]}" for k in ("lr", "po") if m.refs.get(k)), None)
            if ref is None or ref not in seen_refs:  # newest update per shipment only
                seen_refs.add(ref or m.id)
                line = (1, f"Check {ref or 'the shipment'} with {m.who}: {quote(ship[1])}{tail}")
        elif waiting:
            asked = x.ask_sentence(m.ask_text)
            line = (0 if m.category in ("order", "enquiry") else 2,
                    f"Reply to {m.who} about “{m.subject}”{f' — they asked: {quote(asked)}' if asked else ''}"
                    f" (no reply from you since {day(m.local)}).")
        if line:
            out[m.id] = (line[0], f"{line[1]} {cite(m)}")
    return [text for _rank, text in sorted(out.values(), key=lambda t: t[0])][:limit]


def render(title: str, sections: list[str], follow: list[str] | None = None, notes: list[str] | None = None) -> str:
    parts = [HEADER, "", title]
    for s in sections:
        if s:
            parts += ["", s]
    if follow:
        parts += ["", "**Possible follow-ups** (suggested by rules from the emails above)", ""]
        parts += [f"{i}. {line}" for i, line in enumerate(follow, 1)]
    if notes:
        parts += ["", "_" + " ".join(notes) + "_"]
    return "\n".join(parts)


def scope_phrase(intent: Intent, *, categories: bool = True) -> str:
    bits = []
    if intent.categories and categories:
        bits.append("in " + " / ".join(CATEGORY_LABELS.get(c, c) for c in intent.categories))
    if intent.priorities:
        bits.append("marked high priority")
    if intent.company:
        bits.append(f"involving {intent.company}")
    if intent.keyword:
        bits.append(f"mentioning “{intent.keyword}”")
    return (" " + " ".join(bits)) if bits else ""


def not_found(intent: Intent, rng: DateRange, what: str, *, categories: bool = True) -> str:
    return render(f"I found no {what}{scope_phrase(intent, categories=categories)} {range_phrase(rng)}.", [],
                  notes=["Nothing is listed because nothing matched — offline mode never fills gaps with guesses."])


# ---- reports -------------------------------------------------------------------------------------------

def report_count(ctx: ToolContext, intent: Intent, rng: DateRange) -> str:
    f = filters(rng, intent, financial_signals=list(intent.signals))
    asks_unanswered = re.search(r"unanswered|not (?:been )?(?:replied|answered)|no (?:reply|response)",
                                intent.question, re.I)
    if asks_unanswered:
        uf = filters(rng, intent, categories=intent.categories or ["enquiry", "order"])
        rows = q.unanswered(ctx.db, ctx.org.id, uf, limit=1000)
        msgs = prep(ctx, rows[:5])
        title = (f"**{plural(len(rows), 'conversation')}**{scope_phrase(intent) or ' in Enquiries / Orders'} "
                 f"{range_phrase(rng)} {'is' if len(rows) == 1 else 'are'} waiting for a reply from you.")
        lines = [f"- {m.who}: “{m.subject}” — received {day(m.local)} {cite(m)}" for m in msgs]
        return render(title, ["\n".join(lines)] if lines else [],
                      notes=[COUNT_NOTE, "“Waiting” means no email from this mailbox later in the same thread."])
    total = q.count(ctx.db, ctx.org.id, f)
    noun = "sent emails" if intent.direction == "outbound" else "received emails"
    title = f"**{total}** {noun}{scope_phrase(intent)} {range_phrase(rng)}."
    sections = []
    columns = {"category": EmailClassification.primary_category, "company":
               EmailClassification.company_name, "day": None}
    if intent.group_by in ("category", "company"):
        groups = q.count_by(ctx.db, ctx.org.id, f, columns[intent.group_by])
        label = (lambda k: CATEGORY_LABELS.get(k, k or "Not analysed yet")) if intent.group_by == "category" else (
            lambda k: k or "Unknown")
        sections.append("\n".join(f"- {label(k)}: **{n}**" for k, n in groups[:15]))
    elif intent.group_by == "day":
        from sqlalchemy import func

        groups = q.count_by(ctx.db, ctx.org.id, f, func.to_char(func.timezone(ctx.tz, Email.sent_at), "DD Mon"))
        sections.append("\n".join(f"- {k}: **{n}**" for k, n in sorted(groups)))
    if total:
        recent = prep(ctx, q.list_emails(ctx.db, ctx.org.id, f, limit=5))
        sections.append("Most recent:\n" + "\n".join(
            f"- {day(m.local)} · {m.who}: “{m.subject}” {cite(m)}" for m in recent))
    notes = [COUNT_NOTE] + ([CLASSIFIER_NOTE] if intent.categories or intent.group_by == "category" else [])
    return render(title, sections, notes=notes)


def report_unanswered(ctx: ToolContext, intent: Intent, rng: DateRange) -> str:
    f = filters(rng, intent, categories=intent.categories or ["enquiry", "order"])
    rows = q.unanswered(ctx.db, ctx.org.id, f, limit=1000)
    if not rows:
        return not_found(intent, rng, "conversations waiting for your reply")
    msgs = prep(ctx, rows[:15])
    prio = {"high": 0, "medium": 1, "low": 2}
    msgs.sort(key=lambda m: (prio.get(m.cls.priority if m.cls else "", 3), m.email.sent_at))
    by_company: OrderedDict[str, list[Msg]] = OrderedDict()
    for m in msgs:
        by_company.setdefault(m.who, []).append(m)
    lines = []
    for company, items in by_company.items():
        for m in items:
            asked = x.deadlines(m.body)
            lines.append(f"- **{company}** — “{m.subject}” · received {day(m.local)} ({ago(ctx, m)})"
                         f"{' · ⚠ high priority' if m.cls and m.cls.priority == 'high' else ''}\n"
                         f"  They wrote: {quote(m.key)}{f' · date mentioned: {asked[0]}' if asked else ''} {cite(m)}")
    cats = " / ".join(CATEGORY_LABELS.get(c, c) for c in (intent.categories or ["enquiry", "order"]))
    who = f" involving {intent.company}" if intent.company else ""
    title = (f"**{plural(len(rows), 'conversation')}** in {cats}{who}"
             f" {range_phrase(rng)} {'has' if len(rows) == 1 else 'have'} no reply from you yet"
             f"{' (showing 15)' if len(rows) > 15 else ''}.")
    return render(title, ["\n".join(lines)], notes=[
        COUNT_NOTE, "“No reply” means no email from this mailbox later in the same thread — replies by phone, "
                    "WhatsApp or another mailbox aren't visible here.", CLASSIFIER_NOTE])


def report_action_items(ctx: ToolContext, intent: Intent, rng: DateRange) -> str:
    items = action_items_svc(ctx.db, ctx.org.id, rng, ctx.tz, status="open", limit=40)
    if intent.company:
        items = [i for i in items if intent.company.lower() in
                 f"{i['email']['from_name']} {(i['email'].get('classification') or {}).get('company_name')}".lower()]
    if not items:
        return not_found(intent, rng, "open action items")
    rows = q.base_query(ctx.org.id).where(Email.id.in_([uuid.UUID(i["email"]["id"]) for i in items]))
    msgs = {m.id: m for m in prep(ctx, ctx.db.execute(rows).all())}
    lines = []
    for prio in ("high", "medium", "low"):
        group = [i for i in items if i["priority"] == prio]
        if not group:
            continue
        lines.append(f"**{prio.capitalize()} priority**")
        for i in group:
            m = msgs[i["email"]["id"]]
            asked = x.deadlines(m.body)
            due = f" · due {i['due_date']}" if i["due_date"] else (f" · email says: {asked[0]}" if asked else "")
            lines.append(f"- {i['title']} — {m.who}, {day(m.local)}{due} {cite(m)}")
    title = f"**{plural(len(items), 'open action item')}** from emails {range_phrase(rng)}."
    return render(title, ["\n".join(lines)], notes=[
        COUNT_NOTE, "Action items are created by the classifier and close automatically when you reply in the thread."])


def _group_key(m: Msg) -> str:
    for kind in ("lr", "po", "shipment"):
        if m.refs.get(kind):
            return f"{x.REF_LABEL[kind]} {m.refs[kind][0]}"
    return f"thread:{m.email.thread_id}"


def _groups(msgs: list[Msg]) -> OrderedDict[str, list[Msg]]:
    """Group by LR / PO / shipment number; emails without one join their thread's group."""
    thread_key: dict = {}
    for m in sorted(msgs, key=lambda m: m.email.sent_at):
        k = _group_key(m)
        if not k.startswith("thread:"):
            thread_key.setdefault(m.email.thread_id, k)
    groups: OrderedDict[str, list[Msg]] = OrderedDict()
    for m in sorted(msgs, key=lambda m: m.email.sent_at, reverse=True):
        k = _group_key(m)
        if k.startswith("thread:"):
            k = thread_key.get(m.email.thread_id, k)
        groups.setdefault(k, []).append(m)
    return groups


def _title(key: str, items: list[Msg]) -> str:
    if key.startswith("thread:"):
        return f"“{items[-1].subject}”"
    return key


SHIPMENT_CUES = ["%dispatch%", "%consignment%", "%transit%", "%e-way%", "%expedite%", "%delivered%", "%LR %",
                 "%short receipt%", "%vehicle%"]


def report_shipments(ctx: ToolContext, intent: Intent, rng: DateRange) -> str:
    f = filters(rng, intent, categories=[], direction=None, company=None)
    cues = or_(EmailClassification.primary_category == "logistics",
               *[Email.subject.ilike(c) for c in SHIPMENT_CUES], *[Email.body_clean.ilike(c) for c in SHIPMENT_CUES])
    scope = cues
    if intent.company:
        like = f"%{intent.company}%"
        scope = and_(cues, or_(q.company_condition(ctx.db, ctx.org.id, intent.company),
                               Email.subject.ilike(like), Email.body_clean.ilike(like)))
    not_noise = or_(EmailClassification.primary_category.is_(None),
                    EmailClassification.primary_category.notin_(["internal", "other"]))
    rows = fetch(ctx, f, extra=and_(scope, not_noise), limit=300)
    msgs, others = [], []
    for e, c in rows:
        m = make(ctx, e, c)
        pay = m.payment
        if m.shipment and not (pay and pay[0] in x.STRONG_PAYMENT):
            msgs.append(m)
        elif not m.inbound:
            others.append(m)
    if not msgs:
        return not_found(intent, rng, "shipment or dispatch updates", categories=False)
    tracked = {m.email.thread_id for m in msgs}
    msgs += [m for m in others if m.email.thread_id in tracked]  # our replies, for context
    for m in msgs:
        ctx.seen_email_ids.add(m.id)
    state = thread_state(ctx.db, ctx.org.id, tracked)
    groups = _groups(msgs)

    def says(m: Msg) -> str:
        return m.shipment[1] if m.shipment else m.key

    attention, moving, delivered, latest_attention = [], [], [], []
    for key, items in groups.items():
        with_status = [m for m in items if m.shipment]
        latest = max(with_status, key=lambda m: m.email.sent_at)
        status = latest.shipment[0]
        incoming = all(m.category == "supplier" for m in items if m.inbound)
        senders = list(dict.fromkeys(m.who for m in items if m.inbound)) or ["You"]
        head = (f"**{_title(key, items)}** · {', '.join(senders[:2])}"
                f"{' · incoming supply' if incoming else ''} · latest: {x.SHIPMENT_LABEL[status].lower()}")
        body = timeline(items, show=3, says=says)
        facts = facts_line(items, refs=False)
        if facts:
            body.append(f"- {facts}")
        waiting = [m for m in items if needs_reply(state, m)]
        if waiting and status in ("problem", "awaiting_dispatch"):
            body.append(f"- ⚠ Waiting for your reply since {day(max(waiting, key=lambda m: m.email.sent_at).local)}")
        block = head + "\n" + "\n".join(body)
        if status in ("problem", "awaiting_dispatch"):
            attention.append(block)
            if latest.inbound:
                latest_attention.append(latest)
        elif status == "delivered":
            delivered.append(f"- **{_title(key, items)}** · {senders[0]} — {quote(latest.shipment[1])} {cite(latest)}")
        else:
            moving.append(block)
    pending = len(attention) + len(moving)
    title = (f"**{plural(pending, 'shipment')} not yet confirmed delivered**{scope_phrase(intent, categories=False)} "
             f"{range_phrase(rng)} ({plural(len(msgs), 'shipment-related email')}, grouped by LR / PO number).")
    sections = []
    if attention:
        sections.append("**Needs attention** (problem reported or customer chasing dispatch)\n\n"
                        + "\n\n".join(attention))
    if moving:
        sections.append("**In transit or scheduled**\n\n" + "\n\n".join(moving))
    if delivered:
        sections.append("**Delivered, per the emails**\n" + "\n".join(delivered))
    return render(title, sections, followups(ctx, latest_attention, state), notes=[
        "Shipment status is the latest update found in the emails by keyword rules (no tracking system is "
        "connected).", STATED_NOTE])


PAY_SECTIONS = [
    ("claimed", "Payments customers say they have made", "Claims by the sender — check against your bank statement."),
    ("promised", "Payment promises", None),
    ("bank_credit", "Bank credit alerts", "Bank notification emails; they don't always name the payer."),
    ("bank_debit", "Bank debit alerts", None),
    ("dispute", "Disputes and deductions", None),
    ("reminder", "Reminders and dues", "Includes bills and dues you may need to pay."),
    ("invoice", "Invoices and statements", None),
]
SIGNAL_TO_KIND = {"payment_claimed": ["claimed"], "payment_reminder": ["reminder"], "payment_dispute": ["dispute"]}


def report_financial(ctx: ToolContext, intent: Intent, rng: DateRange) -> str:
    f = filters(rng, intent, categories=[])
    finance = or_(EmailClassification.primary_category.in_(["financial", "internal"]),
                  EmailClassification.financial_signal.notin_(["none"]))
    rows = fetch(ctx, f, extra=finance, limit=300)
    wanted = [k for s in intent.signals for k in SIGNAL_TO_KIND.get(s, [])]
    sections_msgs: OrderedDict[str, list[Msg]] = OrderedDict((k, []) for k, _, _ in PAY_SECTIONS)
    internal: list[Msg] = []
    for e, c in rows:
        m = make(ctx, e, c)
        pay = m.payment
        if not pay:
            continue
        if m.category == "internal":
            if not wanted:
                internal.append(m)
            continue
        if wanted and pay[0] not in wanted:
            continue
        sections_msgs[pay[0]].append(m)
    total_msgs = sum(len(v) for v in sections_msgs.values()) + len(internal)
    if total_msgs == 0:
        return not_found(intent, rng, "payment or invoice emails", categories=False)
    sections = []
    all_msgs = []
    for kind, label, note in PAY_SECTIONS:
        items = sections_msgs[kind]
        if not items:
            continue
        for m in items:
            ctx.seen_email_ids.add(m.id)
        all_msgs += items
        lines = [f"**{label}** ({len(items)})" + (f" — _{note}_" if note else "")]
        for m in items:
            amounts, _ = x.money(m.body)
            refs = [f"{x.REF_LABEL[k]} {m.refs[k][0]}" for k in ("invoice", "po", "utr") if m.refs.get(k)]
            extras = " · ".join(([f"amount stated: {', '.join(amounts[:2])}"] if amounts else []) + refs)
            lines.append(f"- {day(m.local)} · **{m.who}** — {quote(m.payment[1])}"
                         f"{' · ' + extras if extras else ''} {cite(m)}")
        if kind in ("claimed", "promised", "reminder", "dispute"):
            totals = stated_totals(ctx.db, ctx.org.id, [m.id for m in items])
            if totals:
                lines.append("  Total stated: " + ", ".join(
                    f"**{fmt_total(cur, tot)}** across {plural(n, 'email')}" for cur, n, tot in totals)
                    + " (database sum of the largest amount in each email; not verified).")
        sections.append("\n".join(lines))
    if internal:
        for m in internal:
            ctx.seen_email_ids.add(m.id)
        sections.append(f"**Internal accounts notes** ({len(internal)})\n" + "\n".join(
            f"- {day(m.local)} · {m.email.from_name or m.email.from_email} — “{m.subject}”: {quote(m.key)} {cite(m)}"
            for m in internal))
    state = thread_state(ctx.db, ctx.org.id, {m.email.thread_id for m in all_msgs})
    title = (f"**{plural(total_msgs, 'payment-related email')}**{scope_phrase(intent, categories=False)} "
             f"{range_phrase(rng)}, "
             f"grouped by what each email says.")
    return render(title, sections, followups(ctx, all_msgs, state), notes=[
        "Payment status here is only what the emails state — the app has no access to your bank or accounting "
        "records.", STATED_NOTE])


def report_orders(ctx: ToolContext, intent: Intent, rng: DateRange) -> str:
    f = filters(rng, intent, categories=["order"])
    total = q.count(ctx.db, ctx.org.id, f)
    if total == 0:
        return not_found(intent, rng, "order emails", categories=False)
    seed = prep(ctx, fetch(ctx, f, limit=60))
    groups = _groups(seed)
    # Complete each PO's story with every email that mentions the PO number (any date, either direction).
    for key, items in groups.items():
        if key.startswith("PO "):
            have = {m.id for m in items}
            more = fetch(ctx, q.EmailFilters(direction=None, keyword=key[3:]), limit=12)
            items += [m for m in prep(ctx, more) if m.id not in have]
    threads = {m.email.thread_id for items in groups.values() for m in items}
    state = thread_state(ctx.db, ctx.org.id, threads)
    blocks = []
    ordered = sorted(groups.items(), key=lambda kv: (
        not any(m.inbound and awaiting_reply(state, m) for m in kv[1]),
        -max(m.email.sent_at for m in kv[1]).timestamp()))
    for key, items in ordered[:10]:
        senders = list(dict.fromkeys(m.who for m in items if m.inbound and m.category != "internal"))
        latest = max(items, key=lambda m: m.email.sent_at)
        head = f"**{_title(key, items)}** · {', '.join(senders[:2]) or 'You'} · latest: {latest.event.lower()}"
        body = timeline(items)
        facts = facts_line(items, refs=False)
        if facts:
            body.append(f"- {facts}")
        waiting = [m for m in items if needs_reply(state, m) and m.category != "internal"]
        if waiting:
            body.append(f"- ⚠ Waiting for your reply since {day(max(waiting, key=lambda m: m.email.sent_at).local)}")
        blocks.append(head + "\n" + "\n".join(body))
    title = (f"**{plural(total, 'order email')}** received{scope_phrase(intent, categories=False)} "
             f"{range_phrase(rng)}, grouped into "
             f"{plural(len(groups), 'purchase order or conversation', 'purchase orders or conversations')}"
             f"{' (showing 10)' if len(groups) > 10 else ''}.")
    all_msgs = [m for items in groups.values() for m in items]
    return render(title, ["\n\n".join(blocks)], followups(ctx, all_msgs, state),
                  notes=[COUNT_NOTE, STATED_NOTE, CLASSIFIER_NOTE])


def _topic_key(m: Msg) -> tuple[str, str]:
    if m.category == "internal":
        return "internal", "Internal emails that mention them"
    if m.refs.get("po"):
        return f"po:{m.refs['po'][0]}", f"Purchase order {m.refs['po'][0]}"
    if m.refs.get("lr"):
        return f"lr:{m.refs['lr'][0]}", f"Shipment LR {m.refs['lr'][0]}"
    pay = m.payment
    if pay and pay[0] in x.STRONG_PAYMENT | {"invoice"}:
        return "payments", "Payments and invoices"
    return f"thread:{m.email.thread_id}", f"“{m.subject}”"


def report_company(ctx: ToolContext, intent: Intent, rng: DateRange) -> str:
    name = intent.company or intent.unknown_company or ""
    base = q.EmailFilters(start=rng.start, end=rng.end, direction=None)
    own = fetch(ctx, q.EmailFilters(**{**base.__dict__, "company": name}), limit=150) if intent.company else []
    own_ids = {e.id for e, _ in own}
    mentions = [(e, c) for e, c in fetch(ctx, q.EmailFilters(**{**base.__dict__, "keyword": name}), limit=60)
                if e.id not in own_ids]
    if not own and not mentions:
        return render(f"I found no emails from, to or mentioning **{name}** {range_phrase(rng)}.", [], notes=[
            "Check the spelling, or ask about a company that appears in your inbox. Offline mode never fills "
            "gaps with guesses."])
    own_msgs, mention_msgs = prep(ctx, own), prep(ctx, mentions)
    from app.services.offline.intent import GENERIC_NAME_TOKENS

    focus = [t for t in re.findall(r"[a-z0-9&]+", name.lower()) if t not in GENERIC_NAME_TOKENS and len(t) >= 3]
    for m in mention_msgs:
        m.focus = focus or [name.lower()]
    msgs = own_msgs + mention_msgs
    state = thread_state(ctx.db, ctx.org.id, {m.email.thread_id for m in msgs})

    # Group into topics; a thread joins the topic of its first referenced email (PO / LR).
    thread_topic: dict = {}
    for m in sorted(msgs, key=lambda m: m.email.sent_at):
        key, label = _topic_key(m)
        if not key.startswith("thread:"):
            thread_topic.setdefault(m.email.thread_id, (key, label))
    topics: OrderedDict[str, tuple[str, list[Msg]]] = OrderedDict()
    for m in msgs:
        key, label = _topic_key(m)
        if key.startswith("thread:") and m.email.thread_id in thread_topic:
            key, label = thread_topic[m.email.thread_id]
        topics.setdefault(key, (label, []))[1].append(m)

    blocks = []
    for _key, (label, items) in sorted(topics.items(), key=lambda kv: -max(
            m.email.sent_at for m in kv[1][1]).timestamp()):
        mention_only = all(m in mention_msgs for m in items)
        tag = " · mentioned by " + ", ".join(dict.fromkeys(m.who for m in items)) if mention_only else ""
        body = timeline(items)
        # Internal notes mention several customers at once; their reference numbers may belong to someone else.
        facts = facts_line(items, refs=_key != "internal")
        if facts:
            body.append(f"- {facts}")
        ship = [m for m in items if m.shipment]
        if ship:
            latest = max(ship, key=lambda m: m.email.sent_at)
            if latest.shipment[0] != "delivered":
                body.append(f"- Latest shipment update ({day(latest.local)}): "
                            f"{x.SHIPMENT_LABEL[latest.shipment[0]].lower()} — no delivery confirmation in email")
        waiting = [m for m in items if needs_reply(state, m) and m.category != "internal"]
        if waiting:
            body.append(f"- ⚠ Waiting for your reply since {day(max(waiting, key=lambda m: m.email.sent_at).local)}")
        blocks.append(f"**{label}**{tag}\n" + "\n".join(body))

    n_in = sum(1 for m in own_msgs if m.inbound)
    convs = len({m.email.thread_id for m in own_msgs})
    first, last = (min(m.local for m in msgs), max(m.local for m in msgs))
    display = intent.company or name
    title = (f"**{display}** · {plural(len(own_msgs), 'email')} ({n_in} received, {len(own_msgs) - n_in} sent by you) "
             f"in {plural(convs, 'conversation')}, {day(first)} – {day(last)} {first:%Y}."
             + (f" Also mentioned in {plural(len(mention_msgs), 'other email')}." if mention_msgs else ""))
    return render(title, ["\n\n".join(blocks)], followups(ctx, msgs, state), notes=[COUNT_NOTE, STATED_NOTE])


def report_category_list(ctx: ToolContext, intent: Intent, rng: DateRange) -> str:
    f = filters(rng, intent)
    total = q.count(ctx.db, ctx.org.id, f)
    if total == 0:
        return not_found(intent, rng, "emails")
    msgs = prep(ctx, q.list_emails(ctx.db, ctx.org.id, f, limit=20, order="priority"))
    state = thread_state(ctx.db, ctx.org.id, {m.email.thread_id for m in msgs})
    by_company: OrderedDict[str, list[Msg]] = OrderedDict()
    for m in msgs:
        by_company.setdefault(m.who, []).append(m)
    blocks = []
    for company, items in by_company.items():
        lines = [f"**{company}**"]
        for m in items:
            flag = " · ⚠ no reply yet" if m.inbound and awaiting_reply(state, m) else ""
            lines.append(f"- {day(m.local)} · {m.event} — “{m.subject}”: {quote(m.key)}{flag} {cite(m)}")
        blocks.append("\n".join(lines))
    title = (f"**{plural(total, 'received email')}**{scope_phrase(intent)} {range_phrase(rng)}"
             f"{' (showing 20, most important first)' if total > 20 else ''}, grouped by company.")
    return render(title, ["\n\n".join(blocks)], followups(ctx, msgs, state),
                  notes=[COUNT_NOTE, STATED_NOTE, CLASSIFIER_NOTE])


def report_overview(ctx: ToolContext, intent: Intent, rng: DateRange) -> str:
    f = filters(rng, intent, categories=[])
    total = q.count(ctx.db, ctx.org.id, f)
    if total == 0:
        return not_found(intent, rng, "received emails")
    by_cat = q.count_by(ctx.db, ctx.org.id, f, EmailClassification.primary_category)
    cats = ", ".join(f"{CATEGORY_LABELS.get(k, k or 'not analysed')} {n}" for k, n in by_cat)
    sent = q.count(ctx.db, ctx.org.id, filters(rng, intent, categories=[], direction="outbound"))
    waiting = q.unanswered(ctx.db, ctx.org.id, filters(rng, intent, categories=["enquiry", "order"]), limit=1000)
    important = prep(ctx, q.list_emails(ctx.db, ctx.org.id, filters(rng, intent, categories=[], priorities=["high"]),
                                        limit=6, order="priority"))
    state = thread_state(ctx.db, ctx.org.id, {m.email.thread_id for m in important})
    title = (f"**{plural(total, 'email')} received {range_phrase(rng)}** and {sent} sent. "
             f"By category: {cats}. {plural(len(waiting), 'customer conversation')} (enquiries / orders) "
             f"{'is' if len(waiting) == 1 else 'are'} waiting for your reply.")
    lines = [f"- {day(m.local)} · **{m.who}** · {m.event} — {quote(m.key)} {cite(m)}" for m in important]
    sections = ["**High-priority emails**\n" + "\n".join(lines)] if lines else []
    return render(title, sections, followups(ctx, important, state), notes=[COUNT_NOTE, STATED_NOTE, CLASSIFIER_NOTE])


SUPPORTED = ("Offline mode can build these reports: a summary of one company's emails · exact counts "
             "(“how many enquiries this week?”) · unanswered enquiries and orders · purchase orders · payments and "
             "invoices · pending shipments · open action items · an overview of today.")


def report_search(ctx: ToolContext, intent: Intent, rng: DateRange) -> str:
    f = filters(rng, intent, categories=[], company=None)
    rows = hybrid_search(ctx.db, ctx.org.id, intent.question[:300], f, ctx.embedder, limit=8)
    if not rows:
        return render(f"I couldn't find emails matching your question {range_phrase(rng)}.", [], notes=[SUPPORTED])
    msgs = prep(ctx, [(e, c) for e, c, _how in rows])
    by_company: OrderedDict[str, list[Msg]] = OrderedDict()
    for m in msgs:
        by_company.setdefault(m.who, []).append(m)
    blocks = []
    for company, items in by_company.items():
        blocks.append(f"**{company}**\n" + "\n".join(
            f"- {day(m.local)} · {m.event} — “{m.subject}”: {quote(m.key)} {cite(m)}" for m in items))
    title = ("I can't answer this question directly without an AI model, so here are the emails that best match it "
             f"(keyword + meaning search, {range_phrase(rng)}), grouped by company.")
    return render(title, ["\n\n".join(blocks)], notes=[SUPPORTED, STATED_NOTE])


REPORTS = {
    "count": (report_count, "all"),
    "unanswered": (report_unanswered, "last_30_days"),
    "action_items": (report_action_items, "last_30_days"),
    "shipments": (report_shipments, "last_30_days"),
    "financial": (report_financial, "last_30_days"),
    "orders": (report_orders, "last_30_days"),
    "company_summary": (report_company, "all"),
    "category_list": (report_category_list, "last_30_days"),
    "overview": (report_overview, "today"),
    "search": (report_search, "all"),
}


def build(ctx: ToolContext, intent: Intent) -> str:
    fn, default_range = REPORTS[intent.kind]
    rng = resolve_range(intent.date_preset or default_range, ctx.tz)
    return fn(ctx, intent, rng)
