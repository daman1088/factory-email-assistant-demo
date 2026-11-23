"""Offline, deterministic stand-in for the LLM (LLM_PROVIDER=fake).

It's a keyword/regex classifier: good enough to demo the product and to test the pipeline without an
API key or any cost, and clearly weaker than the real model. Results are stored with
model="offline-rules-v1" so they're never mistaken for AI output.
"""
import re

from app.domain import Category, FinancialSignal, Priority
from app.providers.llm.base import ClassifyRequest, ClassifyResult
from app.schemas.classification import EmailClassificationOut

MODEL = "offline-rules-v1"

_RULES: list[tuple[Category, list[str]]] = [
    (Category.OTHER, [r"unsubscribe", r"newsletter", r"\bweekly\b", r"webinar", r"pre-approved", r"\bloan\b",
                      r"connection request", r"appeared in \d+ searches", r"register now", r"book a (free )?demo",
                      r"\bseo\b", r"bulk sms", r"google'?s 1st page"]),
    (Category.FINANCIAL, [r"\binvoice\b", r"payment", r"overdue", r"\butr\b", r"credited", r"debited",
                          r"statement of account", r"\bgst(r-?1)?\b", r"debit note", r"electricity bill",
                          r"advance"]),
    (Category.LOGISTICS, [r"\blr\b", r"consignment", r"e-way bill", r"vehicle", r"freight", r"proof of delivery",
                          r"\bpod\b", r"pickup", r"in transit", r"courier", r"shipment", r"bales? (missing|short)",
                          r"short receipt"]),
    (Category.SUPPLIER, [r"\byarn\b", r"\bdyes?\b", r"chemicals?", r"softener", r"spares?\b", r"cartons?",
                         r"hdpe", r"boiler", r"price list", r"revised price", r"gear"]),
    (Category.ORDER, [r"purchase order", r"\bpo\b", r"order confirm", r"cancel", r"amendment", r"expedite",
                      r"dispatch status", r"complaint", r"shade approved", r"sample (is )?approved",
                      r"please (supply|book)"]),
    (Category.ENQUIRY, [r"enquiry", r"quotation required", r"please (quote|share|send).{0,40}(rate|price)",
                        r"\brates?\b", r"requirement", r"swatch", r"samples?", r"can you supply", r"buylead",
                        r"\bmoq\b", r"price"]),
]
_HIGH = [r"urgent", r"overdue", r"complaint", r"cancel", r"breakdown", r"purchase order", r"\bpo\b",
         r"amendment", r"tender", r"expir", r"delay", r"short", r"debit note", r"due on", r"3 days left"]
# (?<![A-Za-z]) so a UTR like "SBINR52025..." is not read as an INR amount.
_AMOUNT = re.compile(r"(?<![A-Za-z])(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)|(?<![A-Za-z])USD\s*([\d,]+(?:\.\d+)?)",
                     re.IGNORECASE)
_PER_UNIT = re.compile(r"\s*(?:\+\s*GST\s*)?(?:per|/)\s*(?:metre|meter|mtr|m|kg|piece|pc|bag|roll|unit)\b", re.I)
_SIGNATURE_COMPANY = re.compile(
    r"([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*)*\s+(?:Apparels|Textiles|Garments|Fabrics|Exports|Uniforms|Creations|"
    r"Fashions|Sarees|Mills|Spinners|Logistics|Roadways|Traders|Studio|Industries|Associates|Suppliers|Auxiliaries|"
    r"Courier|Forwarders|House|Enterprises|Corporation)(?:\s+(?:LLC|Ltd\.?|Limited|Pvt\.? Ltd\.?))?)")
_REFS = [
    ("po", re.compile(r"\b(?:PO\s*)?([A-Z]{2,4}(?:/[A-Z]+)?/(?:PO/)?[\d-]+/\d+|[A-Z]{2,3}-\d{3,4}|PC/\d+)\b")),
    ("invoice", re.compile(r"\b((?:SF|NS|XL/INV|CDA|XYZ/YD)/?[\w/]*\d{3,4})\b")),
    ("lr", re.compile(r"\bLR\s*(?:No:?\s*)?([A-Z]{2}-\d{4,6})\b")),
    ("utr", re.compile(r"\bUTR:?\s*([A-Z0-9]{12,22})\b")),
]


def _field(msg: str, tag: str) -> str:
    match = re.search(rf"<{tag}>\n?(.*?)\n?</{tag}>", msg, re.DOTALL)
    return match.group(1).strip() if match else ""


def _company(sender: str, category: Category, body: str = "") -> str:
    """sender looks like 'Vikram Malhotra (ABC Textiles) (abctextiles.example)'. Without a company in the
    sender name, look for one in the signature (last lines), e.g. 'Accounts, Silverline Apparels, Bengaluru'."""
    if category is Category.INTERNAL:
        return "Internal"
    name = re.sub(r"\s*\([^()]*\.[^()]*\)\s*$", "", sender).strip()  # drop the trailing (domain)
    inner = re.search(r"\(([^()]+)\)", name)
    if inner:
        if re.search(r"division|dept|department|accounts|billing|operations|sales|purchase", inner.group(1), re.I):
            return name[:inner.start()].strip() or inner.group(1).strip()  # "XYZ Suppliers (Yarn Division)"
        return inner.group(1).strip()
    tail = [ln.strip() for ln in body.splitlines() if ln.strip()][-5:]
    for line in reversed(tail):
        if len(line) > 70 or line.endswith((".", "?", "!", ":")):
            continue  # a sentence ("...delivered to Kaveri Garments, Tiruppur."), not a signature line
        found = _SIGNATURE_COMPANY.search(line)
        if found:
            return found.group(1).strip()
    return name


def _score(text: str, patterns: list[str]) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))


class FakeLLM:
    name = "fake"
    classify_model = MODEL
    supports_batch = False

    def classify(self, req: ClassifyRequest) -> ClassifyResult:
        subject = _field(req.user_message, "subject")
        body = _field(req.user_message, "body")
        sender = _field(req.user_message, "from")
        text = f"{subject}\n{body}"

        if req.sender_is_internal:
            category = Category.INTERNAL
        else:
            # Subject is weighted double: it usually states the purpose.
            scores = {cat: _score(subject, pats) * 2 + _score(body, pats) for cat, pats in _RULES}
            best = max(scores.values())
            category = next(c for c, _ in _RULES if scores[c] == best) if best else Category.OTHER

        high = _score(text, _HIGH) > 0 and category is not Category.OTHER
        priority = Priority.HIGH if high else (Priority.LOW if category is Category.OTHER else Priority.MEDIUM)
        action = category in (Category.ENQUIRY, Category.ORDER) or bool(
            re.search(r"please|kindly|approve|confirm|request", text, re.IGNORECASE)) and category is not Category.OTHER

        fin = FinancialSignal.NONE
        if re.search(r"debit note|deduct", text, re.IGNORECASE):
            fin = FinancialSignal.PAYMENT_DISPUTE
        elif re.search(r"\butr\b|released|transferred|payment done", text, re.IGNORECASE):
            fin = FinancialSignal.PAYMENT_CLAIMED
        elif re.search(r"reminder|overdue|arrange (the )?payment", text, re.IGNORECASE):
            fin = FinancialSignal.PAYMENT_REMINDER
        elif re.search(r"\binvoice\b|\bbill\b", subject, re.IGNORECASE):
            fin = FinancialSignal.INVOICE_ISSUED

        amounts = []
        for m in _AMOUNT.finditer(body):
            raw = m.group(1) or m.group(2)
            is_rate = bool(_PER_UNIT.match(body, m.end()))
            try:
                amounts.append({"amount": float(raw.replace(",", "")), "currency": "USD" if m.group(2) else "INR",
                                "context": "rate per unit" if is_rate else "mentioned in email"})
            except ValueError:
                continue
        refs = [{"type": kind, "value": v} for kind, rx in _REFS for v in dict.fromkeys(rx.findall(text))]

        company = _company(sender, category, body)
        # Summary = the first substantive lines of the body (the subject is already shown next to it).
        lines = [ln.strip() for ln in body.splitlines()
                 if len(ln.strip()) > 25 and not re.match(r"(dear|hi|hello|greetings|namaste|vanakkam|salaam|sir)",
                                                          ln.strip(), re.IGNORECASE)]
        summary = " ".join(lines[:2]) or subject
        if len(summary) > 240:
            summary = summary[:240].rsplit(" ", 1)[0] + "…"
        out = EmailClassificationOut.lenient({
            "primary_category": category.value,
            "secondary_tags": [],
            "company_name": company,
            "summary": summary,
            "priority": priority.value,
            "action_required": action,
            "suggested_action": f"Review and respond: {subject}" if action else "",
            "important_dates": [],
            "amounts": amounts[:5],
            "references": refs[:6],
            "financial_signal": fin.value,
            "confidence": 0.5,
        })
        return ClassifyResult(key=req.key, output=out, model=MODEL)

    def submit_classification_batch(self, reqs: list[ClassifyRequest]) -> str:
        raise NotImplementedError("offline mode has no batch API")

    def poll_classification_batch(self, batch_id: str):
        raise NotImplementedError

    def summarize(self, system: str, prompt: str) -> tuple[str, str]:
        raise NotImplementedError("offline mode uses the template summary")

