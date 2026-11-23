from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class Address:
    name: str | None
    email: str


@dataclass
class RawMessage:
    """Provider-neutral message, as close to what Gmail gives us as is useful."""
    provider_message_id: str
    provider_thread_id: str
    sent_at: datetime
    from_addr: Address
    to: list[Address]
    cc: list[Address]
    subject: str
    body_text: str | None
    body_html: str | None
    labels: list[str]
    rfc822_message_id: str | None = None
    in_reply_to: str | None = None
    attachments: list[dict] = field(default_factory=list)  # [{filename, mime_type, size}]


@dataclass
class ListPage:
    message_ids: list[str]
    next_page_token: str | None


class MailProvider(Protocol):
    """Read-only mailbox access. Implementations: MockMailProvider (dev), GmailProvider (Phase 2)."""

    def mailbox_address(self) -> str: ...

    def current_history_id(self) -> str: ...

    def list_message_ids(self, since: datetime, page_token: str | None = None) -> ListPage: ...

    def list_new_message_ids(self, history_id: str) -> tuple[list[str], str]:
        """Return ids added since `history_id` and the new history id."""
        ...

    def get_messages(self, message_ids: list[str]) -> list[RawMessage]: ...
