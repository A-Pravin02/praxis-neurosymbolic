"""NEURAL. Sentence embeddings: the component that understands meaning, not words.

"I don't want this product anymore, can I give it back?" shares no content word with
"whether an item can be sent back to the seller". A lexical system cannot connect
them; a sentence-embedding model places them close together because it learned what
the sentences mean. Every other neural component - intent, inputs, retrieval - is
built on this similarity.

The default model is BAAI/bge-small-en-v1.5 run through ONNX Runtime: free, offline
once cached, about 130 MB, and fast on a CPU. If it cannot be loaded, the encoder
falls back to lexical feature hashing and says so: `semantic` is False, and the
assistant marks its understanding as degraded rather than pretending.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import numpy as np

from app.config import settings
from app.core.logging import get_logger, log_event

logger = get_logger("praxis.neural")

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Encoder:
    name = "encoder"
    semantic = False

    def encode(self, texts: Sequence[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError

    def similarity(self, queries: Sequence[str], documents: Sequence[str]) -> np.ndarray:
        """Cosine similarity matrix, queries x documents."""
        if not queries or not documents:
            return np.zeros((len(queries), len(documents)))
        return self.encode(queries) @ self.encode(documents).T


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class SemanticEncoder(Encoder):
    semantic = True

    def __init__(self, model: str, cache_dir: Path) -> None:
        from fastembed import TextEmbedding  # imported lazily: optional dependency

        cache_dir.mkdir(parents=True, exist_ok=True)
        self._model = TextEmbedding(model_name=model, cache_dir=str(cache_dir))
        self.name = model
        self._lock = threading.Lock()
        self._cache: dict[str, np.ndarray] = {}

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            with self._lock:
                vectors = np.array(list(self._model.embed(missing)), dtype=np.float32)
            for text, vector in zip(missing, _normalise(vectors)):
                # Schema descriptions and policy clauses are encoded once and reused;
                # questions are short, so the cache stays small.
                if len(self._cache) < 20_000:
                    self._cache[text] = vector
                else:  # pragma: no cover - bounded cache
                    return _normalise(np.array(list(self._model.embed(list(texts)))))
        return np.stack([self._cache[t] for t in texts])


class HashingEncoder(Encoder):
    """Lexical fallback. Understands word overlap, not meaning."""

    name = "feature-hashing (lexical fallback)"
    semantic = False

    def __init__(self) -> None:
        from app.rag.embeddings import HashingEmbeddingProvider

        self._provider = HashingEmbeddingProvider(dimension=384)

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        return _normalise(np.array(self._provider.embed(list(texts)), dtype=np.float32))


@lru_cache(maxsize=2)
def get_encoder(kind: str | None = None) -> Encoder:
    kind = kind or settings.neural_encoder
    if kind == "semantic":
        cache = Path(settings.neural_model_cache) if settings.neural_model_cache \
            else BACKEND_ROOT / ".model_cache"
        try:
            return SemanticEncoder(settings.neural_model, cache)
        except Exception as exc:  # noqa: BLE001 - any load failure means fall back
            log_event(logger, 30, "semantic encoder unavailable; using lexical fallback",
                      error=type(exc).__name__)
    return HashingEncoder()
