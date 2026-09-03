"""Task router: decide which subsystems a request actually needs.

Running retrieval, knowledge extraction, inference and verification for "hello"
is wasteful and, worse, invites spurious evidence. The router picks a plan; the
orchestrator executes exactly that plan.

Routing is deterministic given the intent frame. The intent frame may come from a
model, but the mapping from intent to plan does not — so a model cannot talk the
system into skipping verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.neuro.intent import IntentFrame


class Route(str, Enum):
    DIRECT_RESPONSE = "DIRECT_RESPONSE"
    RAG_QUERY = "RAG_QUERY"
    SYMBOLIC_REASONING = "SYMBOLIC_REASONING"
    RAG_PLUS_SYMBOLIC = "RAG_PLUS_SYMBOLIC"
    POLICY_DECISION = "POLICY_DECISION"
    CONTRADICTION_ANALYSIS = "CONTRADICTION_ANALYSIS"


@dataclass
class Plan:
    route: Route
    retrieve: bool = False
    extract_knowledge: bool = False
    reason: bool = False
    check_policy: bool = False
    verify: bool = True
    require_evidence: bool = True
    goal_predicate: str | None = None
    goal_subject: str = "self"
    rationale: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "route": self.route.value, "retrieve": self.retrieve,
            "extract_knowledge": self.extract_knowledge, "reason": self.reason,
            "check_policy": self.check_policy, "verify": self.verify,
            "require_evidence": self.require_evidence,
            "goal_predicate": self.goal_predicate, "rationale": self.rationale,
        }


def route(frame: IntentFrame) -> Plan:
    intent = frame.intent

    if intent == "smalltalk":
        return Plan(route=Route.DIRECT_RESPONSE, verify=False, require_evidence=False,
                    rationale="Conversational turn with no factual claim to ground.")

    if intent == "definition":
        return Plan(
            route=Route.DIRECT_RESPONSE, retrieve=True, verify=True,
            require_evidence=False,
            rationale="General-knowledge definition. The knowledge base is still "
                      "searched; if it has something relevant the answer is grounded "
                      "in it, otherwise the answer is labelled as general knowledge.",
        )

    if intent == "policy_decision":
        return Plan(route=Route.POLICY_DECISION, check_policy=True, retrieve=False,
                    verify=False, require_evidence=False,
                    rationale="Authorization question: answered by the policy engine, "
                              "not by retrieval or generation.")

    if intent == "contradiction_analysis":
        return Plan(route=Route.CONTRADICTION_ANALYSIS, retrieve=True,
                    extract_knowledge=True, reason=True, verify=True,
                    rationale="Conflict analysis needs the facts from every source that "
                              "speaks to the value.")

    if intent == "eligibility_check":
        return Plan(
            route=Route.RAG_PLUS_SYMBOLIC, retrieve=True, extract_knowledge=True,
            reason=True, verify=True, goal_predicate=frame.target_attribute,
            rationale="Eligibility requires rules applied to facts, with the rules "
                      "themselves grounded in documents.",
        )

    if intent == "attribute_lookup":
        return Plan(
            route=Route.RAG_PLUS_SYMBOLIC, retrieve=True, extract_knowledge=True,
            reason=True, verify=True, goal_predicate=frame.target_attribute,
            rationale="A value question: look it up, and derive it if a rule applies.",
        )

    if intent == "document_lookup":
        # Knowledge extraction still runs: without it the system cannot notice that
        # a retrieved passage was superseded by a newer document.
        return Plan(route=Route.RAG_QUERY, retrieve=True, extract_knowledge=True,
                    reason=True, verify=True,
                    rationale="Direct document question: retrieve, cite, and check "
                              "whether a newer source replaces what was found.")

    # open_question and anything unrecognised
    return Plan(
        route=Route.RAG_QUERY, retrieve=True, extract_knowledge=True, reason=True,
        verify=True, require_evidence=False,
        rationale="Open question: search the knowledge base first; fall back to a "
                  "clearly-labelled general answer if nothing relevant is found.",
    )


def route_for_mode(frame: IntentFrame, mode: str) -> Plan:
    """Constrain a plan to an evaluation pipeline mode.

    Used by the benchmark harness so the four modes differ only in architecture.
    """
    plan = route(frame)
    if mode == "llm_only":
        return Plan(route=Route.DIRECT_RESPONSE, retrieve=False, extract_knowledge=False,
                    reason=False, check_policy=False, verify=False,
                    require_evidence=False,
                    rationale="Ablation: language model alone.")
    if mode == "llm_rag":
        return Plan(route=Route.RAG_QUERY, retrieve=True, extract_knowledge=False,
                    reason=False, check_policy=False, verify=False,
                    require_evidence=False,
                    rationale="Ablation: retrieval, no verification.")
    if mode == "llm_rag_verify":
        return Plan(route=Route.RAG_QUERY, retrieve=True, extract_knowledge=False,
                    reason=False, check_policy=False, verify=True,
                    require_evidence=plan.require_evidence,
                    rationale="Ablation: retrieval plus verification, no symbolic layer.")
    return plan
