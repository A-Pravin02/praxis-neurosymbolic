"""HYBRID. The auditable reasoning trace recorded for every answer.

It records *what the system did*, not what a model thought: the neural interpretation,
the entities found, the knowledge retrieved, the symbolic facts and rules used, the steps
taken, the verification, the pipeline chosen and why, and the confidence. There is no
hidden chain-of-thought to expose, because no step of the decision is a model's
free-text reasoning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReasoningTrace:
    question: str
    neural_interpretation: dict[str, Any] = field(default_factory=dict)
    entities: list[dict[str, Any]] = field(default_factory=list)
    retrieved_knowledge: list[dict[str, Any]] = field(default_factory=list)
    symbolic_facts: list[dict[str, Any]] = field(default_factory=list)
    rules_used: list[dict[str, Any]] = field(default_factory=list)
    rules_considered: list[dict[str, Any]] = field(default_factory=list)
    reasoning_steps: list[str] = field(default_factory=list)
    neural_symbolic_loop: list[dict[str, Any]] = field(default_factory=list)
    grounding: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    selected_pipeline: str = ""
    pipeline_label: str = ""
    pipeline_reason: str = ""
    why_neural: str = ""
    why_symbolic: str = ""
    components: list[dict[str, str]] = field(default_factory=list)
    policy_versions: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    followups: list[dict[str, Any]] = field(default_factory=list)
    authorization: dict[str, Any] = field(default_factory=dict)
    knowledge_gap: dict[str, Any] | None = None
    decision: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    confidence_factors: dict[str, float] = field(default_factory=dict)
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}
