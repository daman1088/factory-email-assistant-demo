import hashlib
import math
import re
from typing import Protocol

from app.models.mail import EMBEDDING_DIM


class Embedder(Protocol):
    model_name: str

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    """Local CPU embeddings via fastembed (ONNX). The model (~130 MB) downloads on first use."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None

    def _get(self):
        if self._model is None:
            from fastembed import TextEmbedding  # heavy import, keep lazy

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._get().passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._get().query_embed(text))).tolist()


class FakeEmbedder:
    """Deterministic hashed bag-of-words vectors. Lexical only; for tests and offline runs."""

    model_name = "fake-hash-v1"

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * EMBEDDING_DIM
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            if len(tok) < 3:
                continue
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        from app.config import get_settings

        s = get_settings()
        _embedder = FastEmbedder(s.embedding_model) if s.embedding_provider == "fastembed" else FakeEmbedder()
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Test hook."""
    global _embedder
    _embedder = embedder
