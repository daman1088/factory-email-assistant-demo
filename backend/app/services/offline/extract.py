"""Deterministic fact extraction for offline answers.

Every fact carries the sentence it was found in (`quote`), copied verbatim from the email apart from
re-joining hard-wrapped lines. Nothing here infers, converts or normalises a value: a quantity is shown
exactly as the email wrote it ("2,600 m"), a date phrase as written ("5th of next month"). That is the
point - an offline answer can only be as good as these rules, so it must never say more than the text.
"""
import re
from dataclasses import dataclass

# ---- sentence handling -------------------------------------------------------------------------------

_ABBREV = re.compile(r"\b(Rs|No|Nos|Pvt|Ltd|Co|approx|Dr|Mr|Mrs|Ms|St|Sr|Jr|vs|Inc|w\.e\.f|e\.g|i\.e)\.", re.I)
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_KEY_VALUE = re.compile(r"^\s*[A-Z][\w /&()-]{1,30}:\s+\S")
_GREETING = re.compile(
    r"^(dear|hi|hello|greetings|namaste|vanakkam|salaam|sir|madam|regards|thanks|thank you|best|warm regards|"
    r"yours|sincerely|kind regards|cheers)\b[\s\w,.!]*$", re.I)


def _paragraphs(text: str) -> list[str]:
    """Re-join hard-wrapped lines; keep list items and 'Key: value' lines as their own units."""
    units: list[str] = []
    current: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if current:
                units.append(" ".join(current))
                current = []
            continue
        if _LIST_ITEM.match(line) or _KEY_VALUE.match(line):
            if current:
                units.append(" ".join(current))
                current = []
            units.append(_LIST_ITEM.sub("", line))
            continue
        current.append(line)
    if current:
        units.append(" ".join(current))
    return units


def sentences(text: str) -> list[str]:
    out = []
    for para in _paragraphs(text):
        protected = _ABBREV.sub(lambda m: m.group(0)[:-1] + "\u2024", para)  # one-dot leader, restored below
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(₹])", protected):
            s = s.replace("\u2024", ".").strip()
            if s and not _GREETING.match(s):
                out.append(s)
    return out


def clip(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


# ---- references --------------------------------------------------------------------------------------

_REF_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("po", re.compile(r"(?:\bP\.?O\.?|(?i:\bpurchase order))\s*(?:No\.?|Number|#)?\s*:?\s*"
                      r"([A-Z0-9]+(?:[/-][A-Z0-9]+)+|\d{2,})\b")),
    ("invoice", re.compile(r"(?i:\binvoice)\s*(?:No\.?|Number|#)?\s*:?\s*([A-Z0-9]+(?:[/-][A-Z0-9]+)+)\b")),
    ("lr", re.compile(r"\bLR\s*(?:No\.?|Number|#)?\s*:?\s*([A-Z]{1,4}-?\d{3,8})\b")),
    ("utr", re.compile(r"\bUTR\s*(?:No\.?|#)?\s*:?\s*([A-Z0-9]{10,22})\b")),
    ("eway", re.compile(r"(?i:\be-?way bill)\s*(?:No\.?|#)?\s*:?\s*(\d{10,12})\b")),
    ("shipment", re.compile(r"(?i:\b(?:shipment|AWB|docket))\s*(?:No\.?|#)?\s*:?\s*([A-Z]{0,3}\d{6,12})\b")),
    ("vehicle", re.compile(r"\b([A-Z]{2}-?\d{1,2}-?[A-Z]{1,3}-?\d{3,4})\b")),
]
REF_LABEL = {"po": "PO", "invoice": "Invoice", "lr": "LR", "utr": "UTR", "eway": "E-way bill",
             "shipment": "Shipment", "vehicle": "Vehicle"}


def references(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for kind, rx in _REF_PATTERNS:
        for value in rx.findall(text or ""):
            if any(ch.isdigit() for ch in value) and value not in found.setdefault(kind, []):
                found[kind].append(value)
    # "PO 077" in a subject is shorthand for "PO OE/24-25/077" in the body: keep the full number only.
    for kind, values in found.items():
        found[kind] = [v for v in values if not any(o != v and re.search(rf"[/-]{re.escape(v)}$", o) for o in values)]
    return {k: v for k, v in found.items() if v}


# ---- quantities, money, dates --------------------------------------------------------------------------

_NUMBER = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_QTY = re.compile(rf"(?<![\w/.])({_NUMBER})\s*(m|mtrs?|metres?|meters?|kgs?|bags?|bales?|rolls?|pcs|pieces|"
                  r"cartons?|tonnes?|MT|yards?|containers?)\b(?![/-]\d)", re.I)
_MONEY = re.compile(rf"(?<![A-Za-z])(?:Rs\.?|INR|₹|USD|US\$)\s*(?:{_NUMBER})(?:\s*(?:lakhs?|lacs?|crores?)\b)?"
                    r"(?:\s*/-)?", re.I)
_PER_UNIT = re.compile(r"^\s*(?:\+\s*GST\s*)?(?:per|/)\s*(?:metre|meter|mtr|m|kg|piece|pc|bag|roll|unit)\b", re.I)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_DATE_CORE = (rf"(?:{_DAY}|today|tonight|tomorrow|end of (?:the )?(?:day|week|month)|EOD|next week|this week|"
              rf"(?:the )?\d{{1,2}}(?:st|nd|rd|th)(?:\s+of\s+(?:this|next)\s+month|\s+{_MONTH})?|"
              rf"\d{{1,2}}\s+{_MONTH}(?:\s+\d{{4}})?|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?|"
              r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4})")
_DEADLINE = re.compile(
    rf"\b(?:(?:by|before|on or before|latest by|not later than|till|until|due(?: date)?(?: on| by| is)?|"
    rf"deadline(?: is)?|w\.e\.f\.?|expires?|valid till|delivery(?: date)?:?)\s+{_DATE_CORE}"
    rf"|(?:within|in)\s+\d+\s+(?:working\s+)?days?(?:\s+of\s+[\w ]{{2,20}}?(?=[.,;\n]|$))?"
    rf"|(?:ETA|expected delivery)[^.:]{{0,25}}:\s*(?:in\s+)?\d+\s+days?"
    rf"|{_DATE_CORE}\s+(?:of\s+)?(?:next|this)\s+month)",
    re.I)


def quantities(text: str) -> list[str]:
    return list(dict.fromkeys(f"{n} {u}" for n, u in _QTY.findall(text or "")))


def money(text: str) -> tuple[list[str], list[str]]:
    """Returns (amounts, rates) exactly as written. A rate is an amount followed by 'per metre' etc."""
    amounts, rates = [], []
    for m in _MONEY.finditer(text or ""):
        value = m.group(0).strip()
        tail = text[m.end():m.end() + 30]
        if _PER_UNIT.match(tail):
            unit = _PER_UNIT.match(tail).group(0).strip()
            rates.append(f"{value} {unit}")
        else:
            amounts.append(value)
    return list(dict.fromkeys(amounts)), list(dict.fromkeys(rates))


def deadlines(text: str) -> list[str]:
    joined = "\n".join(_paragraphs(text))
    return list(dict.fromkeys(" ".join(m.group(0).split()) for m in _DEADLINE.finditer(joined)))


# ---- statements --------------------------------------------------------------------------------------

# Order matters: the first matching status wins for a sentence.
SHIPMENT_STATUS: list[tuple[str, re.Pattern]] = [
    ("awaiting_dispatch", re.compile(r"dispatch status|update the dispatch|not received any dispatch|expedite|"
                                     r"prepone|dispatch\b.{0,40}\bby the\b|when will .{0,25}dispatch", re.I)),
    ("problem", re.compile(r"broke(?:n)? down|breakdown|\bdelay(?:ed)?\b|\bheld\b|\bshort\b|missing|damaged|"
                           r"expir(?:es|ing|ed)|\bstuck\b|rejected", re.I)),
    ("delivered", re.compile(r"\bdelivered\b|proof of delivery|\bPOD\b", re.I)),
    ("in_transit", re.compile(r"in transit|picked up|on the way|en route|\bETA\b|expected delivery|expected at\b|"
                              r"\b(?:have|has been|been|we) dispatched\b|dispatched (?:today|on|your)", re.I)),
    ("scheduled", re.compile(r"will be placed|pickup (?:is )?scheduled|scheduled for|for loading|"
                             r"ready for dispatch|will dispatch|will be dispatched", re.I)),
]
SHIPMENT_LABEL = {"awaiting_dispatch": "Customer asking about dispatch", "problem": "Problem reported",
                  "delivered": "Delivered (per email)", "in_transit": "In transit (per email)",
                  "scheduled": "Pickup / dispatch scheduled"}

PAYMENT_KINDS: list[tuple[str, re.Pattern]] = [
    ("bank_credit", re.compile(r"\bcredited with\b|\bcredit alert\b", re.I)),
    ("bank_debit", re.compile(r"\bdebited with\b|\bdebit alert\b", re.I)),
    ("dispute", re.compile(r"debit note|\bdeduct|short payment|short quantity", re.I)),
    ("claimed", re.compile(r"\b(?:released|transferred|remitted|paid)\b|payment (?:has been )?(?:made|done)|"
                           r"\bUTR\b", re.I)),
    ("promised", re.compile(r"\bwill (?:clear|pay|release|transfer|remit)\b|"
                            r"\bwill be (?:made|paid|released|cleared|transferred)\b", re.I)),
    ("reminder", re.compile(r"overdue|reminder|outstanding|pending payment|\bdue (?:date|on|by|amount)\b|"
                            r"\b(?:payment|amount|bill|invoice|dues|contributions?)\b.{0,40}\bdue\b(?!\s+to\b)|"
                            r"(?:kindly|please) (?:arrange|release|clear)(?: the)? (?:payment|dues)", re.I)),
]
STRONG_PAYMENT = {"bank_credit", "bank_debit", "dispute", "claimed", "promised", "reminder"}
_INVOICE_SUBJECT = re.compile(r"\binvoice\b|(?<!way )\bbill\b|statement of account|\bGSTR?-?\d?\b", re.I)


@dataclass
class Highlight:
    sentence: str
    score: int


def _score(sentence: str) -> int:
    score = 0
    if references(sentence):
        score += 3
    if _QTY.search(sentence):
        score += 2
    if _MONEY.search(sentence):
        score += 2
    if _DEADLINE.search(sentence):
        score += 2
    if any(rx.search(sentence) for _, rx in SHIPMENT_STATUS) or any(rx.search(sentence) for _, rx in PAYMENT_KINDS):
        score += 2
    if re.search(r"\b(please|kindly|request|confirm|approve|urgent)", sentence, re.I):
        score += 1
    if len(sentence) < 25:
        score -= 2
    return score


def key_sentence(subject: str, body: str) -> str:
    """The single most informative sentence of the body (verbatim), or the subject if nothing scores."""
    return best_sentence(sentences(body), subject)


_ASKS = re.compile(
    r"\b(?:please|kindly|pls)\s+(?:\w+\s+){0,2}?(?:confirm|reply|revert|update|share|send|advise|acknowledge|check|"
    r"extend|quote|approve|let us know|inform us|arrange|clarify)\b|\blet (?:us|me) know\b|\bcan you\b|"
    r"\bcould you\b|\bneed (?:your )?(?:approval|confirmation)\b|\?", re.I)
_NO_REPLY = re.compile(r"\b(?:do not|don't|please don't) reply\b|\bauto-?generated\b|\bno-?reply\b", re.I)


def asks_for_response(text: str) -> bool:
    """Does the email ask the reader to do or answer something? (requests, questions, approvals)"""
    return bool(_ASKS.search(text or "")) and not _NO_REPLY.search(text or "")


def ask_sentence(text: str) -> str | None:
    """The first sentence that asks for something, verbatim."""
    return next((clip(s) for s in sentences(text) if _ASKS.search(s)), None)


def focus_sentences(text: str, tokens: list[str]) -> list[str]:
    """Sentences that mention any of the given (lower-case) words - used for emails that only mention a company."""
    return [s for s in sentences(text) if any(re.search(rf"\b{re.escape(t)}\b", s, re.I) for t in tokens)]


def best_sentence(candidates: list[str], fallback: str) -> str:
    best = max((Highlight(s, _score(s)) for s in candidates), key=lambda h: h.score, default=None)
    return clip(best.sentence) if best and best.score > 0 else clip(fallback)


def first_match(text: str, patterns: list[tuple[str, re.Pattern]]) -> tuple[str, str] | None:
    """(kind, verbatim sentence) for the first sentence matching any pattern, in pattern priority order."""
    sents = sentences(text)
    for kind, rx in patterns:
        for s in sents:
            if rx.search(s):
                return kind, clip(s)
    return None


def shipment_status(subject: str, body: str, direction: str = "inbound") -> tuple[str, str] | None:
    if direction == "outbound":
        # Our own reply: "we will dispatch by the 22nd" is a commitment, not a customer chasing us; and the
        # subject is the customer's ("Re: Please expedite ..."), so only the body counts.
        return first_match(body, [(k, rx) for k, rx in SHIPMENT_STATUS if k != "awaiting_dispatch"])
    # Subject first ("Delivered: Shipment ...", "Delay in delivery - ..."), then the body.
    for kind, rx in SHIPMENT_STATUS:
        if rx.search(subject or ""):
            quote = next((s for s in sentences(body) if rx.search(s)), None)
            return kind, clip(quote or subject)
    return first_match(body, SHIPMENT_STATUS)


def payment_statement(subject: str, body: str) -> tuple[str, str] | None:
    """(kind, verbatim sentence) - body first, subject as fallback. 'invoice' only when the subject says the
    email is an invoice/bill/statement (a passing mention of GST or an invoice number is not enough).
    Amounts are reported separately via money()."""
    hit = first_match(body, PAYMENT_KINDS) or first_match(subject, PAYMENT_KINDS)
    if hit:
        return hit
    if _INVOICE_SUBJECT.search(subject or ""):
        return "invoice", clip(subject)
    return None


def event_label(direction: str, subject: str, body: str, category: str | None) -> str:
    """Short description of what an email is, from keywords in the email itself."""
    text = f"{subject}\n{body}"
    if direction == "outbound":
        if re.search(r"\bconfirm", text, re.I):
            return "You confirmed"
        if re.search(r"per metre|quotation|\brate\b|offer", text, re.I):
            return "You quoted"
        return "You replied"
    if category in ("internal", "other"):
        # Internal notes and newsletters: keyword labels like "Approval" or "Payment reminder" would mislead.
        return "Internal note" if category == "internal" else "Other"
    s = subject or ""
    rules = [
        (r"cancel", "Cancellation"),
        (r"amend|increase the quantity|revis(?:e|ed) (?:the )?(?:quantity|po)", "Amendment requested"),
        (r"\bapproved\b", "Approval"),
        (r"complaint|shade variation|defect|rejected", "Complaint"),
        (r"expedite|prepone", "Expedite request"),
        (r"dispatch status", "Dispatch status asked"),
        (r"^(?:re:\s*)?(?:PO|P\.O\.|purchase order)\b", "PO received"),
    ]
    for pattern, label in rules:
        if re.search(pattern, s, re.I):
            return label
    pay = payment_statement(subject, body)
    ship = shipment_status(subject, body)
    if ship and not (pay and pay[0] in STRONG_PAYMENT):
        return SHIPMENT_LABEL[ship[0]]
    if pay:
        return {"claimed": "Payment reported", "promised": "Payment promised", "reminder": "Payment reminder",
                "dispute": "Debit note / deduction", "bank_credit": "Bank credit alert",
                "bank_debit": "Bank debit alert", "invoice": "Invoice / statement"}[pay[0]]
    return {"enquiry": "Enquiry", "order": "Order", "financial": "Financial", "supplier": "Supplier",
            "logistics": "Logistics", "internal": "Internal", "other": "Other"}.get(category or "", "Email")
