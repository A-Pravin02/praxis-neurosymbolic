"""Embedding provider abstraction and the default offline implementation.

The default is a deterministic feature-hashing embedder: word unigrams, word
bigrams and character 4-grams are hashed into a fixed-width signed vector with
sublinear term weighting, then L2-normalised. It needs no model download, no
network and no GPU, and it produces stable vectors across processes and
platforms (BLAKE2b, not Python's randomised `hash`).

It is a lexical-semantic hybrid, not a sentence transformer: it captures term
overlap and some morphology, not paraphrase. That is why retrieval combines it
with an explicit lexical score and a reranker. Swap in a real embedding model by
setting EMBEDDING_PROVIDER.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol, runtime_checkable

import httpx

from app.config import settings
from app.core.errors import ProviderError
from app.neuro.heuristics import lower_words


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_one(self, text: str) -> list[float]: ...


def _bucket(feature: str, dimension: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % dimension
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return index, sign


def _char_ngrams(word: str, n: int = 4) -> list[str]:
    if len(word) <= n:
        return [f"#{word}#"]
    padded = f"#{word}#"
    return [padded[i:i + n] for i in range(len(padded) - n + 1)]


class HashingEmbeddingProvider:
    name = "hashing"

    def __init__(self, dimension: int | None = None, model: str | None = None) -> None:
        self.dimension = dimension or settings.embedding_dimension
        self.model = model or settings.embedding_model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        words = lower_words(text)
        features: Counter[str] = Counter()
        for word in words:
            features[f"w:{word}"] += 1
            for gram in _char_ngrams(word):
                features[f"c:{gram}"] += 1
        for a, b in zip(words, words[1:]):
            features[f"b:{a}_{b}"] += 1

        vector = [0.0] * self.dimension
        for feature, count in features.items():
            index, sign = _bucket(feature, self.dimension)
            # Sublinear term weighting; char n-grams contribute less than words.
            weight = (1.0 + math.log(count)) * (0.35 if feature.startswith("c:") else 1.0)
            vector[index] += sign * weight

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            # An empty or purely-symbolic string: return a stable non-zero unit vector
            # so cosine distance stays defined.
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]


class OpenAIEmbeddingProvider:
    name = "openai"

    def __init__(self) -> None:
        self.model = settings.embedding_model
        self.dimension = settings.embedding_dimension
        self._base_url = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                response = client.post(
                    f"{self._base_url}/embeddings",
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    json={"model": self.model, "input": list(texts),
                          "dimensions": self.dimension},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError("The embedding provider is unavailable.") from exc
        ordered = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        return [item["embedding"] for item in ordered]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class OllamaEmbeddingProvider:
    name = "ollama"

    def __init__(self) -> None:
        self.model = settings.embedding_model
        self.dimension = settings.embedding_dimension
        self._base_url = (settings.llm_base_url or "http://localhost:11434").rstrip("/")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                for text in texts:
                    response = client.post(f"{self._base_url}/api/embeddings",
                                           json={"model": self.model, "prompt": text})
                    response.raise_for_status()
                    out.append(response.json()["embedding"])
        except (httpx.HTTPError, KeyError) as exc:
            raise ProviderError("The embedding provider is unavailable.") from exc
        return out

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


_OVERRIDE: EmbeddingProvider | None = None


@lru_cache(maxsize=4)
def _cached(name: str) -> EmbeddingProvider:
    if name == "hashing":
        return HashingEmbeddingProvider()
    if name == "openai":
        return OpenAIEmbeddingProvider()
    if name == "ollama":
        return OllamaEmbeddingProvider()
    raise ValueError(f"Unknown embedding provider: {name}")


def get_embedder() -> EmbeddingProvider:
    if _OVERRIDE is not None:
        return _OVERRIDE
    return _cached(settings.embedding_provider)


def set_override(provider: EmbeddingProvider | None) -> None:
    """Test seam."""
    global _OVERRIDE
    _OVERRIDE = provider


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
