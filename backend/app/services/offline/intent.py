"""Rule-based question understanding for offline answers: which report to build, for which company and
date range. Deliberately small - when a question doesn't match, the engine says so and falls back to a
labelled search instead of guessing."""
import re
from dataclasses import dataclass, field

DATE_PHRASES: list[tuple[str, str]] = [
    (r"\btoday\b|\bthis morning\b", "today"),
    (r"\byesterday\b", "yesterday"),
    (r"\bthis week\b", "this_week"),
    (r"\b(?:last|past) (?:7|seven) days\b|\b(?:last|past) week\b", "last_7_days"),
    (r"\bthis month\b", "this_month"),
    (r"\b(?:last|past) (?:30|thirty) days\b|\b(?:last|past) month\b", "last_30_days"),
]

CATEGORY_WORDS: list[tuple[str, str]] = [
    (r"\benquir|\binquir|\bquotation requests?\b|\brfqs?\b|\bleads?\b", "enquiry"),
    (r"(?i:\bpurchase orders?\b|\borders?\b)|\bP\.?O\.?s?\b", "order"),
    (r"\bpayments?\b|\binvoices?\b|\bfinancial\b|\bfinance\b|\bdues?\b|\boutstanding\b|\breceivables?\b", "financial"),
    (r"\bsuppliers?\b|\bprocurement\b|\bvendors?\b|\byarn\b|\bdyes?\b|\bchemicals?\b", "supplier"),
    (r"\bshipments?\b|\blogistics\b|\btransport(?:er)?s?\b|\bconsignments?\b|\bdispatch", "logistics"),
    (r"\binternal\b|\bstaff\b|\bproduction\b", "internal"),
]

SIGNAL_WORDS: list[tuple[str, str]] = [
    (r"\b(?:say|says|said|claim|claims|claimed)\b.{0,30}\bpaid\b|\bpayments? (?:received|claimed|done|made)\b|"
     r"\bpaid\b|\bmade (?:a |the )?payments?\b", "payment_claimed"),
    (r"\breminders?\b|\boverdue\b", "payment_reminder"),
    (r"\bdisputes?\b|\bdebit notes?\b|\bdeductions?\b", "payment_dispute"),
]

GENERIC_NAME_TOKENS = {
    "textiles", "textile", "fabrics", "fabric", "exports", "export", "pvt", "ltd", "limited", "llc", "llp", "co",
    "company", "industries", "mills", "mill", "garments", "apparels", "apparel", "house", "traders", "trading",
    "the", "and", "sons", "accounts", "purchase", "logistics", "roadways", "freight", "forwarders", "spinners",
    "suppliers", "supplier", "group", "india", "enterprises", "fashions", "fashion", "uniforms", "uniform",
    "creations", "sarees", "studio", "designer", "courier", "bank", "alerts", "billing", "division", "yarn",
    "department", "sales", "team", "internal", "office", "cotton", "dyes", "auxiliaries", "associates",
    "chartered", "accountants", "insights", "weekly", "orders", "order", "payments", "emails", "email",
}


@dataclass
class Intent:
    kind: str  # count | unanswered | action_items | shipments | financial | orders | category_list |
    #            company_summary | overview | search
    question: str
    date_preset: str | None = None  # None = not stated in the question
    company: str | None = None
    unknown_company: str | None = None  # named in the question but not found in the mailbox
    categories: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    priorities: list[str] = field(default_factory=list)
    direction: str = "inbound"
    group_by: str = "none"
    keyword: str | None = None


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9&]+", " ", text.lower()).strip() + " "


def resolve_company(question: str, candidates: list[str]) -> str | None:
    """Best candidate whose full name, or all of whose distinctive words, appear in the question."""
    q = _norm(question)
    best: tuple[int, str] | None = None
    for name in candidates:
        n = _norm(name)
        if len(n.strip()) < 3:
            continue
        if n in q:
            score = 100 + len(n)
        else:
            tokens = [t for t in n.split() if t not in GENERIC_NAME_TOKENS and len(t) >= 3]
            if not tokens or not all(f" {t} " in q for t in tokens):
                continue
            score = 10 * len(tokens) + len(n) / 100
        if best is None or score > best[0]:
            best = (score, name)
    return best[1] if best else None


_NAMED_SOURCE = re.compile(r"\b(?:from|by|with|about|regarding|for|of)\s+((?:[A-Z][\w&.'-]*\s?){1,4})")
_FOLLOW_UP = re.compile(r"\b(?:they|them|their|those|these|that company|this company|same company)\b", re.I)


def parse(question: str, company_candidates: list[str], previous: "Intent | None" = None) -> Intent:
    q = question.strip()
    low = q.lower()
    intent = Intent(kind="search", question=q)

    for pattern, preset in DATE_PHRASES:
        if re.search(pattern, low):
            intent.date_preset = preset
            break

    intent.company = resolve_company(q, company_candidates)
    if intent.company is None:
        named = _NAMED_SOURCE.search(q)
        if named and not re.match(r"(?:the|this|last|today|yesterday|all|my|our|any|PO|LR)\b", named.group(1)):
            intent.unknown_company = named.group(1).strip()
    if intent.company is None and previous is not None and _FOLLOW_UP.search(q):
        intent.company = previous.company
        intent.date_preset = intent.date_preset or previous.date_preset
        intent.unknown_company = None

    for pattern, cat in CATEGORY_WORDS:
        if re.search(pattern, q if cat == "order" else low) and cat not in intent.categories:  # "PO" is case-sensitive
            intent.categories.append(cat)
    for pattern, sig in SIGNAL_WORDS:
        if re.search(pattern, low):
            intent.signals.append(sig)
    if re.search(r"\burgent|\bhigh[- ]priority|\bimportant|\bcritical", low):
        intent.priorities = ["high"]
    if re.search(r"\b(?:we|i) (?:sent|replied)|\bsent (?:by us|emails?|mail)|\bour replies\b", low):
        intent.direction = "outbound"
    if re.search(r"\bcomplain|\bquality (?:issue|problem)|\bdefect|\bshade variation|\brejection", low):
        intent.keyword = "complaint" if "complain" in low else None
        intent.categories = intent.categories or ["order"]

    if re.search(r"\bby (?:category|type)\b|\bper category\b|\beach category\b|\bbreakdown\b", low):
        intent.group_by = "category"
    elif re.search(r"\bby (?:company|customer|sender)\b|\bper (?:company|customer)\b|\bwhich (?:companies|customers)\b",
                   low):
        intent.group_by = "company"
    elif re.search(r"\bper day\b|\bby day\b|\bdaily\b", low):
        intent.group_by = "day"

    if re.search(r"\bhow many\b|\bnumber of\b|\bcount\b", low):
        intent.kind = "count"
    elif re.search(r"unanswered|not (?:been )?(?:replied|answered|responded)|\bno (?:reply|response)\b|"
                   r"haven'?t (?:replied|responded)|awaiting (?:our |a |my )?(?:reply|response)|"
                   r"pending (?:reply|replies|response)|still waiting|without (?:a )?(?:reply|response)", low):
        intent.kind = "unanswered"
    elif re.search(r"\baction items?\b|\bto-?dos?\b|\btasks?\b|\bfollow[- ]?ups?\b|\bpending actions?\b", low):
        intent.kind = "action_items"
    elif re.search(r"\bshipments?\b|\bdispatch|\bdeliver(?:y|ies|ed)\b|\bconsignments?\b|\btransport|\bLR\b|"
                   r"\blogistics\b|\bin transit\b|\bcourier\b|\be-?way\b", q, re.I):
        intent.kind = "shipments"
    elif re.search(r"\bpayments?\b|\bpaid\b|\binvoices?\b|\boutstanding\b|\boverdue\b|\bdues?\b|\breceivables?\b|"
                   r"\bfinancial\b|\bfinance\b|\bmoney\b|\butr\b|\bdebit notes?\b|\bhow much\b|\bbills?\b|\badvance\b",
                   low):
        intent.kind = "financial"
    elif re.search(r"(?i:\bpurchase orders?\b|\borders?\b)|\bP\.?O\.?s?\b", q):
        intent.kind = "orders"
    elif intent.company and not intent.categories:
        intent.kind = "company_summary"
    elif intent.categories or intent.keyword:
        intent.kind = "category_list"
    elif intent.unknown_company and re.search(r"\bsummar|\bemails? from\b|\bmails? from\b|\btell me about\b", low):
        intent.kind = "company_summary"  # named company that isn't in the mailbox -> say so, don't show an overview
    elif re.search(r"\bsummar|\boverview\b|\bbrief\b|\bhighlights?\b|\bwhat(?:'s| is| has) (?:going on|happening|"
                   r"come in|new)\b|\bupdate me\b", low):
        intent.kind = "overview"
    elif intent.company:
        intent.kind = "company_summary"
    return intent
