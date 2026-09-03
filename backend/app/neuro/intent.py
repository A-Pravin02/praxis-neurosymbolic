"""Neural understanding: natural language -> structured IntentFrame.

The model proposes; deterministic heuristics dispose. Whatever the LLM returns is
validated against a schema and reconciled with rule-based signals, so a
hallucinated or malformed intent degrades to the heuristic answer instead of
steering the pipeline somewhere strange.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger, log_event
from app.neuro import prompts
from app.neuro.base import CompletionRequest, Message, Role, Task
from app.neuro.heuristics import definition_subject, question_shape
from app.neuro.providers.mock import build_intent_frame
from app.neuro.registry import get_llm

logger = get_logger("praxis.intent")

VALID_INTENTS = {
    "smalltalk", "definition", "attribute_lookup", "eligibility_check",
    "document_lookup", "contradiction_analysis", "policy_decision", "open_question",
}


@dataclass
class IntentFrame:
    intent: str = "open_question"
    shape: str = "open"
    entity: str | None = None
    target_attribute: str | None = None
    desired_value: float | None = None
    requires_evidence: bool = True
    keywords: list[str] = field(default_factory=list)
    source: str = "heuristic"

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent, "shape": self.shape, "entity": self.entity,
            "target_attribute": self.target_attribute,
            "desired_value": self.desired_value,
            "requires_evidence": self.requires_evidence,
            "keywords": self.keywords[:12], "source": self.source,
        }


def _from_dict(data: dict[str, Any], fallback: IntentFrame) -> IntentFrame:
    intent = data.get("intent")
    if intent not in VALID_INTENTS:
        intent = fallback.intent
    desired = data.get("desired_value")
    if isinstance(desired, str):
        try:
            desired = float(desired)
        except ValueError:
            desired = None
    if not isinstance(desired, (int, float)):
        desired = fallback.desired_value
    keywords = data.get("keywords")
    if not isinstance(keywords, list):
        keywords = fallback.keywords
    return IntentFrame(
        intent=intent,
        shape=fallback.shape,
        entity=_clean(data.get("entity")) or fallback.entity,
        target_attribute=_clean(data.get("target_attribute")) or fallback.target_attribute,
        desired_value=float(desired) if isinstance(desired, (int, float)) else None,
        requires_evidence=bool(data.get("requires_evidence", fallback.requires_evidence)),
        keywords=[str(k)[:40] for k in keywords][:20],
    )


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:120] if value else None


def parse_intent(question: str, history: list[dict[str, str]] | None = None) -> IntentFrame:
    heuristic = IntentFrame(**{**build_intent_frame(question), "source": "heuristic"})

    provider = get_llm()
    if provider.name == "mock":
        # The deterministic provider *is* the heuristic; no round trip needed.
        return heuristic

    try:
        completion = provider.complete(CompletionRequest(
            messages=[Message(role=Role.USER, content=question)],
            task=Task.INTENT, system=prompts.INTENT_SYSTEM, temperature=0.0,
        ))
        data = json.loads(_extract_json(completion.text))
        if not isinstance(data, dict):
            raise ValueError("intent payload is not an object")
        frame = _from_dict(data, heuristic)
        frame.source = f"llm:{provider.name}"
        # A model must not turn an evidence-requiring question into a free-form one.
        if heuristic.requires_evidence:
            frame.requires_evidence = True
        if heuristic.shape == "definition" and frame.entity is None:
            frame.entity = definition_subject(question)
        frame.shape = question_shape(question)
        return frame
    except Exception as exc:
        log_event(logger, 30, "intent parsing fell back to heuristics",
                  error=type(exc).__name__)
        return heuristic


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text or "{}"
