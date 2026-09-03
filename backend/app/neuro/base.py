"""LLM provider abstraction.

Nothing outside app/neuro/providers may import a concrete provider. Callers build
a CompletionRequest and hand it to whatever provider the registry returns.

`task` is a hint describing what the caller wants (intent parsing, grounded answer,
title, direct answer). Network providers use it only to pick a prompt preset; the
offline deterministic provider uses it to choose a strategy.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Task(str, Enum):
    INTENT = "intent"
    GROUNDED_ANSWER = "grounded_answer"
    DIRECT_ANSWER = "direct_answer"
    TITLE = "title"
    CLAIM_SPLIT = "claim_split"
    EXPLAIN_INFERENCE = "explain_inference"
    ABSTAIN = "abstain"


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Message:
    role: Role
    content: str


@dataclass
class CompletionRequest:
    messages: list[Message]
    task: Task = Task.DIRECT_ANSWER
    system: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    # Structured payload the deterministic provider can use directly. Network
    # providers never see this; it is already rendered into the prompt text.
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, request: CompletionRequest) -> Completion: ...

    def stream(self, request: CompletionRequest) -> Iterator[str]: ...


class BaseProvider:
    """Default streaming implementation: chunk a full completion.

    Providers with real token streaming override `stream`.
    """

    name = "base"
    model = ""

    def complete(self, request: CompletionRequest) -> Completion:  # pragma: no cover
        raise NotImplementedError

    def stream(self, request: CompletionRequest) -> Iterator[str]:
        text = self.complete(request).text
        buf: list[str] = []
        for token in _tokenize_for_stream(text):
            buf.append(token)
            if len(buf) >= 3:
                yield "".join(buf)
                buf = []
        if buf:
            yield "".join(buf)


def _tokenize_for_stream(text: str) -> list[str]:
    out: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if ch in " \n":
            out.append(current)
            current = ""
    if current:
        out.append(current)
    return out


def estimate_tokens(text: str) -> int:
    """Cheap, provider-independent token estimate (~4 chars per token)."""
    return max(1, (len(text) + 3) // 4)
