"""LLM output contract for email classification.

CLASSIFICATION_JSON_SCHEMA is sent to the API as a structured-output format (so the model can only
produce this shape). EmailClassificationOut then re-validates it server-side and normalises values;
nothing from the model is stored without passing through here.
"""
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain import Category, FinancialSignal, Priority

TAGS = [c.value for c in Category] + [
    "quotation_request", "purchase_order", "order_change", "cancellation", "quality_complaint",
    "payment_claim", "payment_reminder", "invoice", "dispatch", "delay", "sample_request",
    "price_change", "export", "urgent",
]
DATE_KINDS = ["deadline", "delivery", "payment_due", "meeting", "other"]
REF_TYPES = ["po", "invoice", "quotation", "lr", "utr", "debit_note", "other"]


class ExtractedDate(BaseModel):
    date: str  # YYYY-MM-DD
    kind: Literal["deadline", "delivery", "payment_due", "meeting", "other"]
    description: str

    @field_validator("date")
    @classmethod
    def _iso(cls, v: str) -> str:
        date.fromisoformat(v)  # raises -> whole item rejected
        return v


class ExtractedAmount(BaseModel):
    amount: float = Field(ge=0)
    currency: str
    context: str

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        v = v.strip().upper()
        return {"RS": "INR", "RS.": "INR", "₹": "INR", "$": "USD"}.get(v, v)[:3]


class ExtractedReference(BaseModel):
    type: Literal["po", "invoice", "quotation", "lr", "utr", "debit_note", "other"]
    value: str


class EmailClassificationOut(BaseModel):
    primary_category: Category
    secondary_tags: list[str] = []
    company_name: str = ""
    summary: str
    priority: Priority
    action_required: bool
    suggested_action: str = ""
    important_dates: list[ExtractedDate] = []
    amounts: list[ExtractedAmount] = []
    references: list[ExtractedReference] = []
    financial_signal: FinancialSignal = FinancialSignal.NONE
    confidence: float = Field(ge=0, le=1)

    @field_validator("secondary_tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return sorted({t for t in v if t in TAGS})[:5]

    @field_validator("summary", "suggested_action", "company_name")
    @classmethod
    def _clean(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()[:500]

    @classmethod
    def lenient(cls, data: dict) -> "EmailClassificationOut":
        """Validate, dropping individual malformed list items (e.g. an invalid date) instead of
        rejecting the whole classification."""
        data = dict(data)
        for key, model in (("important_dates", ExtractedDate), ("amounts", ExtractedAmount),
                           ("references", ExtractedReference)):
            good = []
            for item in data.get(key) or []:
                try:
                    good.append(model.model_validate(item).model_dump())
                except ValueError:
                    continue
            data[key] = good
        if isinstance(data.get("confidence"), (int, float)):
            data["confidence"] = min(max(float(data["confidence"]), 0.0), 1.0)
        return cls.model_validate(data)


def _enum(values: list[str]) -> dict:
    return {"type": "string", "enum": values}


CLASSIFICATION_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["primary_category", "secondary_tags", "company_name", "summary", "priority",
                 "action_required", "suggested_action", "important_dates", "amounts", "references",
                 "financial_signal", "confidence"],
    "properties": {
        "primary_category": _enum([c.value for c in Category]),
        "secondary_tags": {"type": "array", "items": _enum(TAGS)},
        "company_name": {"type": "string"},
        "summary": {"type": "string"},
        "priority": _enum([p.value for p in Priority]),
        "action_required": {"type": "boolean"},
        "suggested_action": {"type": "string"},
        "important_dates": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["date", "kind", "description"],
            "properties": {"date": {"type": "string"}, "kind": _enum(DATE_KINDS),
                           "description": {"type": "string"}}}},
        "amounts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["amount", "currency", "context"],
            "properties": {"amount": {"type": "number"}, "currency": {"type": "string"},
                           "context": {"type": "string"}}}},
        "references": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["type", "value"],
            "properties": {"type": _enum(REF_TYPES), "value": {"type": "string"}}}},
        "financial_signal": _enum([f.value for f in FinancialSignal]),
        "confidence": {"type": "number"},
    },
}
