from datetime import UTC, datetime
from pathlib import Path

from app.models import MailboxAccount
from app.providers.mail.base import MailProvider

MOCK_DATASETS = {
    "office@suryodaya-fabrics.example": "emails.json",
    "office@decoy-polymers.example": "decoy_emails.json",
}
MOCK_DIR = Path(__file__).resolve().parents[3] / "mock_data"


def get_mail_provider(mailbox: MailboxAccount, now: datetime | None = None) -> MailProvider:
    if mailbox.provider == "mock":
        from app.providers.mail.mock import MockMailProvider, load_dataset

        dataset = load_dataset(MOCK_DIR / MOCK_DATASETS.get(mailbox.email_address, "emails.json"))
        return MockMailProvider(now=now or datetime.now(UTC), dataset=dataset)
    if mailbox.provider == "gmail":
        raise NotImplementedError("Gmail provider arrives in Phase 2")
    raise ValueError(f"unknown mail provider {mailbox.provider}")
