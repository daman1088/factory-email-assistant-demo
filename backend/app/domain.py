"""Domain vocabulary shared by models, schemas, prompts and the UI API."""
from enum import StrEnum


class Category(StrEnum):
    ENQUIRY = "enquiry"
    ORDER = "order"
    FINANCIAL = "financial"
    SUPPLIER = "supplier"
    LOGISTICS = "logistics"
    INTERNAL = "internal"
    OTHER = "other"


CATEGORY_LABELS = {
    Category.ENQUIRY: "Enquiries",
    Category.ORDER: "Orders",
    Category.FINANCIAL: "Financial",
    Category.SUPPLIER: "Suppliers & Procurement",
    Category.LOGISTICS: "Logistics & Dispatch",
    Category.INTERNAL: "Internal Operations",
    Category.OTHER: "Other",
}


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FinancialSignal(StrEnum):
    """What an email *says* about money. Never a verified payment status."""
    NONE = "none"
    INVOICE_ISSUED = "invoice_issued"
    PAYMENT_REMINDER = "payment_reminder"
    PAYMENT_CLAIMED = "payment_claimed"  # sender says they paid - unverified
    PAYMENT_DISPUTE = "payment_dispute"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ProcessingState(StrEnum):
    INGESTED = "ingested"
    BATCHING = "batching"  # submitted to the Message Batches API, waiting for results
    CLASSIFIED = "classified"
    EMBEDDED = "embedded"  # terminal success state
    FAILED = "failed"
