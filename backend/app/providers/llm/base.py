from dataclasses import dataclass
from typing import Protocol

from app.schemas.classification import EmailClassificationOut


@dataclass
class ClassifyRequest:
    key: str  # email id; also the Batch API custom_id
    user_message: str
    sender_is_internal: bool = False  # used only by the offline classifier


@dataclass
class ClassifyResult:
    key: str
    output: EmailClassificationOut | None
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


class LLMProvider(Protocol):
    name: str
    classify_model: str
    supports_batch: bool

    def classify(self, req: ClassifyRequest) -> ClassifyResult: ...

    def submit_classification_batch(self, reqs: list[ClassifyRequest]) -> str: ...

    def poll_classification_batch(self, batch_id: str) -> list[ClassifyResult] | None:
        """None while still processing."""
        ...

    def summarize(self, system: str, prompt: str) -> tuple[str, str]:
        """Returns (text, model)."""
        ...
