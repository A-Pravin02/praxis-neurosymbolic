"""Provider registry. The only place concrete providers are named."""
from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.core.logging import get_logger
from app.neuro.base import LLMProvider
from app.neuro.providers.mock import MockLLMProvider

logger = get_logger("praxis.neuro")

_OVERRIDE: LLMProvider | None = None


def _build(name: str) -> LLMProvider:
    if name == "mock":
        return MockLLMProvider(settings.llm_model)
    if name == "local":
        # Evaluation only: an open-weights model on this machine (needs torch + transformers).
        from app.neuro.providers.local import LocalTransformersProvider
        return LocalTransformersProvider()
    # Imported lazily so an offline deployment never needs the network stack.
    from app.neuro.providers.remote import (
        AnthropicProvider, OllamaProvider, OpenAICompatibleProvider,
    )
    if name == "openai":
        return OpenAICompatibleProvider()
    if name == "anthropic":
        return AnthropicProvider()
    if name == "ollama":
        return OllamaProvider()
    raise ValueError(f"Unknown LLM provider: {name}")


@lru_cache(maxsize=4)
def _cached(name: str) -> LLMProvider:
    return _build(name)


def get_llm() -> LLMProvider:
    if _OVERRIDE is not None:
        return _OVERRIDE
    return _cached(settings.llm_provider)


def get_fallback_llm() -> LLMProvider:
    """Deterministic provider used when a network provider fails."""
    return _cached("mock")


def set_override(provider: LLMProvider | None) -> None:
    """Test seam."""
    global _OVERRIDE
    _OVERRIDE = provider
