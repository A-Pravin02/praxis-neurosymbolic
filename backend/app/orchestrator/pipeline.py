"""The neuro-symbolic orchestrator.

Executes the plan the router chose:

    understand -> retrieve -> extract -> reason -> verify -> gate -> present

The language model appears exactly twice: once to understand the question, and
once to present conclusions that have already been established. Between those two
points the system is deterministic, and the model has no way to add a fact or to
raise its own confidence.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import audit
from app.core.errors import ProviderError
from app.core.logging import get_logger, log_event
from app.db.models.knowledge import Fact
from app.knowledge import store as knowledge_store
from app.knowledge.schema import FactDTO, FactKind, render_value, unwrap
from app.neuro.heuristics import extract_numbers as _numbers
from app.neuro.heuristics import phrase_fact
from app.neuro import prompts
from app.neuro.base import CompletionRequest, Message, Role, Task
from app.neuro.intent import IntentFrame, parse_intent
from app.neuro.registry import get_fallback_llm, get_llm
from app.orchestrator.router import Plan, Route, route_for_mode
from app.policy import service as policy_service
from app.policy.pdp import Decision, Resource
from app.rag.retriever import EvidenceSpan, HybridRetriever, RetrievalQuery
from app.symbolic.engine import (
    Contradiction, InferenceResult, InferenceStep, SymbolicEngine, check_constraints,
)
from app.tenancy.context import Principal
from app.verification import gate as gate_module
from app.verification.engine import (
    Claim, ClaimStatus, VerificationReport, VerificationStatus, check_grounding,
    claims_from_facts, claims_from_spans, verify,
)

logger = get_logger("praxis.orchestrator")

ABSTAIN_TEXT = (
    "I don't have enough information in the available knowledge base to answer that "
    "reliably."
)


@dataclass
class StageTiming:
    name: str
    ms: int
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ms": self.ms, "detail": self.detail}


@dataclass
class AssistantTurn:
    answer: str = ""
    route: str = Route.DIRECT_RESPONSE.value
    gate_decision: str = gate_module.GateDecision.ABSTAIN.value
    verification_status: str = VerificationStatus.NOT_APPLICABLE.value
    confidence: float = 0.0
    citations: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    reasoning: dict[str, Any] = field(default_factory=dict)
    policy_decision: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    stages: list[StageTiming] = field(default_factory=list)
    latency_ms: int = 0
    pipeline_mode: str = "neurosymbolic"
    intent: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    grounding: dict[str, Any] = field(default_factory=dict)
    gate_signals: dict[str, Any] = field(default_factory=dict)

    def trace(self) -> dict[str, Any]:
        return {
            "intent": self.intent, "plan": self.plan, "reasoning": self.reasoning,
            "citations": self.citations, "claims": self.claims,
            "gate": self.gate_signals, "grounding": self.grounding,
            "policy_decision": self.policy_decision, "warnings": self.warnings,
            "stages": [s.as_dict() for s in self.stages],
            "verification_status": self.verification_status,
            "confidence": self.confidence, "route": self.route,
        }


class _Timer:
    def __init__(self, turn: AssistantTurn, name: str, **detail: Any) -> None:
        self.turn, self.name, self.detail = turn, name, detail

    def __enter__(self) -> "_Timer":
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.turn.stages.append(StageTiming(
            name=self.name, ms=int((time.perf_counter() - self.started) * 1000),
            detail=self.detail,
        ))


# ---------------------------------------------------------------------------

class Orchestrator:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.llm = get_llm()

    # -- public ------------------------------------------------------------
    def answer(self, question: str, *, history: list[dict[str, str]] | None = None,
               pipeline_mode: str = "neurosymbolic") -> AssistantTurn:
        started = time.perf_counter()
        turn = AssistantTurn(pipeline_mode=pipeline_mode)
        history = history or []

        with _Timer(turn, "understand"):
            frame = parse_intent(question, history)
            plan = route_for_mode(frame, pipeline_mode)
        turn.intent = frame.as_dict()
        turn.plan = plan.as_dict()
        turn.route = plan.route.value

        if plan.check_policy:
            self._policy_path(turn, question, frame)
            turn.latency_ms = int((time.perf_counter() - started) * 1000)
            return turn

        spans: list[EvidenceSpan] = []
        if plan.retrieve:
            with _Timer(turn, "retrieve") as timer:
                spans = self._retrieve(question, frame)
                timer.detail = {"spans": len(spans),
                                "quarantined": sum(1 for s in spans if s.quarantined)}
        citations = _citations(spans)
        turn.citations = citations

        facts: list[FactDTO] = []
        inference = InferenceResult()
        if plan.extract_knowledge:
            with _Timer(turn, "knowledge") as timer:
                facts = self._collect_facts(spans, frame)
                timer.detail = {"facts": len(facts)}

        if plan.reason and facts:
            with _Timer(turn, "reason") as timer:
                inference = self._reason(facts)
                timer.detail = {"derived": len(inference.derived),
                                "steps": len(inference.steps),
                                "contradictions": len(inference.contradictions)}
        elif facts:
            inference.facts = facts

        conclusion_fact = self._conclusion_fact(plan, frame, inference)
        blocking = self._relevant_contradictions(inference, frame, conclusion_fact)
        aside = self._non_blocking_contradictions(inference, blocking)
        claims = self._build_claims(question, plan, frame, spans, inference,
                                    conclusion_fact, inference.contradictions)

        with _Timer(turn, "verify") as timer:
            if plan.verify:
                report = verify(
                    question, claims, spans,
                    inference_steps=inference.steps,
                    contradictions=blocking,
                    require_evidence=plan.require_evidence,
                )
            else:
                # Ablation modes: no verification. Claims pass through unchecked.
                report = VerificationReport(claims=claims,
                                            status=VerificationStatus.NOT_APPLICABLE)
                for claim in claims:
                    claim.status = ClaimStatus.SUPPORTED
            timer.detail = {"status": report.status.value,
                            "supported": len(report.supported_claims)}
        turn.verification_status = report.status.value
        turn.claims = [c.as_dict() for c in report.claims]

        with _Timer(turn, "gate"):
            if plan.verify:
                gate = gate_module.evaluate(
                    report, spans, require_evidence=plan.require_evidence,
                    contradictions=blocking,
                    symbolic_conclusion=conclusion_fact is not None
                    and conclusion_fact.kind is FactKind.INFERRED,
                )
            else:
                gate = gate_module.GateResult(
                    gate_module.GateDecision.ANSWER, 0.5,
                    ["Verification disabled for this pipeline mode."], {},
                )
        turn.gate_decision = gate.decision.value
        turn.confidence = gate.confidence
        turn.gate_signals = gate.as_dict()

        turn.reasoning = self._reasoning_trace(frame, inference, conclusion_fact,
                                               spans, plan, blocking, aside)
        for contradiction in aside:
            if contradiction.is_resolved:
                turn.warnings.append(
                    f"Version conflict on {contradiction.predicate}: "
                    f"{contradiction.resolution}"
                )
            else:
                turn.warnings.append(
                    f"The knowledge base also holds conflicting values for "
                    f"{contradiction.predicate} of {contradiction.subject}; that "
                    f"conflict does not affect this answer."
                )

        with _Timer(turn, "generate"):
            self._generate(turn, question, history, plan, report, gate, spans,
                           conclusion_fact, inference, blocking)

        if plan.verify and gate.may_answer:
            with _Timer(turn, "grounding_check"):
                self._post_check(turn, report, spans, [
                    c.resolution for c in list(blocking) + list(aside) if c.resolution
                ] + [s.rule_text for s in inference.steps])

        turn.latency_ms = int((time.perf_counter() - started) * 1000)
        log_event(logger, 20, "turn complete", route=turn.route,
                  gate=turn.gate_decision, verification=turn.verification_status,
                  confidence=turn.confidence, latency_ms=turn.latency_ms,
                  citations=len(turn.citations))
        if gate.decision is gate_module.GateDecision.ABSTAIN:
            audit.record(self.session, audit.ANSWER_ABSTAINED,
                         tenant_id=self.principal.tenant_id,
                         actor_user_id=self.principal.user_id, outcome="abstained",
                         detail={"reasons": gate.reasons[:3], "route": turn.route})
        return turn

    # -- stages ------------------------------------------------------------

    def _retrieve(self, question: str, frame: IntentFrame) -> list[EvidenceSpan]:
        retriever = HybridRetriever(self.session, self.principal)
        query = question
        if frame.target_attribute:
            # Widen the query with the canonical attribute name so a question
            # phrased differently from the document still retrieves it.
            query = f"{question} {frame.target_attribute.replace('_', ' ')}"
        result = retriever.retrieve(RetrievalQuery(text=query,
                                                   top_k=settings.rerank_top_k))
        return result.spans

    def _collect_facts(self, spans: list[EvidenceSpan],
                       frame: IntentFrame) -> list[FactDTO]:
        """Facts the caller may see, restricted to what this question is about."""
        facts: list[FactDTO] = []

        # 1. The caller's own profile attributes, as DIRECT facts about "self".
        user = self.session.get(_user_model(), self.principal.user_id)
        if user is not None:
            facts.extend(knowledge_store.subject_facts_for_user(
                user.id, user.attributes or {}, user.email))

        # 2. Facts extracted from the passages that were actually retrieved.
        chunk_ids = [s.chunk_id for s in spans if not s.quarantined]
        if chunk_ids:
            rows = self.session.scalars(
                select(Fact).where(
                    policy_service.fact_filter(self.session, self.principal),
                    Fact.source_chunk_id.in_(chunk_ids),
                )
            ).all()
            facts.extend(knowledge_store.fact_to_dto(r) for r in rows)

        # 3. Facts about the target attribute from any permitted source, so a
        #    contradiction between two documents is visible even when retrieval
        #    surfaced only one of them.
        if frame.target_attribute:
            rows = self.session.scalars(
                select(Fact).where(
                    policy_service.fact_filter(self.session, self.principal),
                    Fact.predicate == frame.target_attribute,
                ).limit(50)
            ).all()
            facts.extend(knowledge_store.fact_to_dto(r) for r in rows)

        # De-duplicate by identity, preserving order.
        seen: set[str] = set()
        unique: list[FactDTO] = []
        for fact in facts:
            key = fact.fact_id or f"{fact.subject}|{fact.predicate}|{unwrap(fact.object)}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(fact)
        return unique

    def _reason(self, facts: list[FactDTO]) -> InferenceResult:
        rules, problems = knowledge_store.load_rules(self.session,
                                                     self.principal.tenant_id)
        engine = SymbolicEngine(rules)
        result = engine.forward(facts)
        if problems:
            result.rules_considered.extend(f"invalid:{p}" for p in problems)
        constraint_defs = knowledge_store.load_constraint_rules(
            self.session, self.principal.tenant_id)
        violations = check_constraints(result.facts, constraint_defs)
        if violations:
            for violation in violations:
                result.contradictions.append(Contradiction(
                    subject=violation.subject, predicate=violation.predicate,
                    values=[violation.value], severity=violation.severity,
                    kind="constraint_violation",
                    resolution=violation.description,
                ))
        return result

    def _conclusion_fact(self, plan: Plan, frame: IntentFrame,
                         inference: InferenceResult) -> FactDTO | None:
        # Only routes that asked for a value produce a conclusion. "What does the
        # handbook say" must report the sources, not the caller's own entitlement.
        predicate = plan.goal_predicate
        if not predicate:
            return None
        for subject in ("self", plan.goal_subject, "policy"):
            fact = inference.value_of(subject, predicate)
            if fact is not None:
                return fact
        # Any subject as a last resort (single-entity corpora).
        candidates = [f for f in inference.facts if f.predicate == predicate]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _relevant_contradictions(
        self, inference: InferenceResult, frame: IntentFrame,
        conclusion_fact: FactDTO | None = None,
    ) -> list[Contradiction]:
        """Conflicts that actually bear on this answer.

        A conflict blocks an answer only when the answer depends on the disputed
        value: same subject and predicate as the conclusion, or a conflict on one
        of the conclusion's premises. A conflict elsewhere in the knowledge base
        is reported as a warning but does not suppress a conclusion that does not
        rest on it — suppressing everything would make the system useless, and
        pretending the conflict does not exist would be dishonest.
        """
        contradictions = inference.contradictions
        if frame.intent == "contradiction_analysis":
            return contradictions
        blocking: list[Contradiction] = []
        premise_ids = set(conclusion_fact.provenance.premise_fact_ids) if conclusion_fact \
            else set()
        for contradiction in contradictions:
            if contradiction.kind == "constraint_violation":
                blocking.append(contradiction)
                continue
            if conclusion_fact is not None:
                if (contradiction.subject == conclusion_fact.subject
                        and contradiction.predicate == conclusion_fact.predicate):
                    blocking.append(contradiction)
                elif premise_ids & set(contradiction.fact_ids):
                    blocking.append(contradiction)
            elif frame.target_attribute and contradiction.predicate == frame.target_attribute:
                blocking.append(contradiction)
        return blocking

    @staticmethod
    def _non_blocking_contradictions(
        inference: InferenceResult, blocking: list[Contradiction],
    ) -> list[Contradiction]:
        blocking_keys = {(c.subject, c.predicate) for c in blocking}
        return [c for c in inference.contradictions
                if (c.subject, c.predicate) not in blocking_keys]

    def _build_claims(self, question: str, plan: Plan, frame: IntentFrame,
                      spans: list[EvidenceSpan], inference: InferenceResult,
                      conclusion_fact: FactDTO | None,
                      contradictions: list[Contradiction] | None = None) -> list[Claim]:
        """Assemble the candidate claims, dropping anything a newer source replaced."""
        contradictions = contradictions or []
        superseded_ids = {fid for c in contradictions for fid in c.superseded_fact_ids}
        superseded_values = {
            _round(v) for c in contradictions for v in c.superseded_values
        }

        claims: list[Claim] = []
        if conclusion_fact is not None:
            claims.extend(claims_from_facts([conclusion_fact], label="conclusion"))
            # Include the premises so the user can see what the conclusion rests on.
            premise_ids = set(conclusion_fact.provenance.premise_fact_ids)
            premises = [f for f in inference.facts
                        if f.fact_id and f.fact_id in premise_ids
                        and f.fact_id not in superseded_ids]
            claims.extend(claims_from_facts(premises, label="premise"))

        if plan.route in (Route.RAG_QUERY, Route.RAG_PLUS_SYMBOLIC,
                          Route.CONTRADICTION_ANALYSIS, Route.DIRECT_RESPONSE):
            existing = {c.text.lower() for c in claims}
            span_claims: list[Claim] = []
            focus = _focus_terms(frame, conclusion_fact)
            conclusion_value = (
                _round(conclusion_fact.value) if conclusion_fact is not None else None
            )
            for claim in claims_from_spans(
                question, spans, focus_terms=focus,
                prefer_numeric=frame.intent in ("attribute_lookup", "eligibility_check",
                                                "document_lookup"),
            ):
                if claim.text.lower() in existing:
                    continue
                # A sentence whose value a newer document replaced must not be
                # presented as current. It is still visible as evidence.
                numbers = {_round(n) for n in _numbers(claim.text)}
                if frame.intent != "contradiction_analysis" and superseded_values:
                    if numbers & superseded_values:
                        continue
                # Never stand a competing number next to a derived conclusion: a
                # correct "15 days" answer beside a generic "20 days" sentence
                # reads as a contradiction to the user even when it is not one.
                if conclusion_value is not None and numbers and                         conclusion_value not in numbers:
                    continue
                span_claims.append(claim)
            span_claims.sort(key=lambda c: -c.relevance)
            limit = 2 if conclusion_fact is not None else 4
            claims.extend(span_claims[:limit])
        return claims[:10]

    def _reasoning_trace(self, frame: IntentFrame, inference: InferenceResult,
                         conclusion_fact: FactDTO | None, spans: list[EvidenceSpan],
                         plan: Plan, blocking: list[Contradiction],
                         aside: list[Contradiction]) -> dict[str, Any]:
        chunk_to_index = {str(s.chunk_id): i for i, s in enumerate(spans, start=1)}
        facts_out = []
        for fact in inference.facts[:40]:
            entry = fact.as_dict()
            entry["citation_index"] = chunk_to_index.get(
                str(fact.provenance.chunk_id)) if fact.provenance.chunk_id else None
            facts_out.append(entry)
        contradictions = []
        for contradiction in list(blocking) + list(aside):
            item = contradiction.as_dict()
            item["blocking"] = contradiction in blocking
            item["citation_indexes"] = sorted({
                chunk_to_index[cid] for cid in contradiction.chunk_ids
                if cid in chunk_to_index
            })
            contradictions.append(item)
        return {
            "goal": (f"{plan.goal_subject}.{plan.goal_predicate}"
                     if plan.goal_predicate else ""),
            "facts": facts_out,
            "inferences": [s.as_dict() for s in inference.steps],
            "rules_considered": inference.rules_considered[:40],
            "rules_fired": inference.rules_fired,
            "contradictions": contradictions,
            "conclusion": conclusion_fact.render() if conclusion_fact else "",
            "iterations": inference.iterations,
        }

    # -- policy path -------------------------------------------------------

    def _policy_path(self, turn: AssistantTurn, question: str,
                     frame: IntentFrame) -> None:
        resource_type, action = _guess_resource_action(question, frame)
        with _Timer(turn, "policy"):
            decision = policy_service.decide(
                self.session, self.principal, action,
                Resource(resource_type=resource_type,
                         tenant_id=self.principal.tenant_id,
                         attributes={"tenant_id": str(self.principal.tenant_id),
                                     "sensitivity": "confidential"}),
            )
        turn.policy_decision = decision.as_dict()
        turn.verification_status = VerificationStatus.VERIFIED.value
        allowed = decision.decision is Decision.ALLOW
        turn.gate_decision = (gate_module.GateDecision.ANSWER.value if allowed
                              else gate_module.GateDecision.DENY.value)
        turn.confidence = 1.0
        verdict = {
            Decision.ALLOW: "Yes - that access is permitted.",
            Decision.DENY: "No - that access is denied.",
            Decision.INDETERMINATE: "I cannot determine that with the information "
                                    "available, so it is treated as denied.",
        }[decision.decision]
        turn.answer = (
            f"{verdict}\n\n"
            f"Decision: **{decision.decision.value}** for action `{action}` on "
            f"`{resource_type}`.\n"
            f"Reason: {decision.reason}\n"
            + (f"Policy: `{decision.matched_policy}`\n" if decision.matched_policy else "")
            + "\nThis decision comes from the policy engine, not from a language "
              "model, and is reproducible."
        )
        turn.reasoning = {
            "goal": f"{action} {resource_type}", "facts": [], "inferences": [],
            "rules_considered": [e.get("policy", "") for e in decision.evaluated],
            "contradictions": [], "conclusion": decision.decision.value,
        }

    # -- generation --------------------------------------------------------

    @staticmethod
    def _conclusion_disputed(conclusion_fact: FactDTO | None,
                             blocking: list[Contradiction]) -> bool:
        """A value the sources still disagree about is not a conclusion."""
        if conclusion_fact is None:
            return False
        return any(
            c.severity == "error"
            and c.predicate == conclusion_fact.predicate
            and c.subject in (conclusion_fact.subject, "policy")
            for c in blocking
        )

    def _generate(self, turn: AssistantTurn, question: str,
                  history: list[dict[str, str]], plan: Plan,
                  report: VerificationReport, gate: "gate_module.GateResult",
                  spans: list[EvidenceSpan], conclusion_fact: FactDTO | None,
                  inference: InferenceResult,
                  blocking: list[Contradiction] | None = None) -> None:
        blocking = blocking or []
        if gate.decision is gate_module.GateDecision.ABSTAIN:
            turn.answer = self._compose(Task.ABSTAIN, question, history, context={
                "reasons": gate.reasons, "suggestions": gate.suggestions,
            }, system=prompts.ABSTAIN_SYSTEM)
            turn.warnings.extend(gate.reasons)
            return

        supported = [c.as_dict() for c in report.supported_claims]
        context = {
            "question": question,
            "claims": supported,
            "citations": turn.citations,
            "conclusion": (
                "" if self._conclusion_disputed(conclusion_fact, blocking)
                else self._conclusion_sentence(conclusion_fact, plan)
            ),
            # Only the derivation behind the stated conclusion belongs in the prose.
            # Everything the engine derived is still in the reasoning trace.
            "inferences": (
                self._readable_inferences(
                    inference, self._derivation_steps(inference, conclusion_fact))[:6]
                if conclusion_fact is not None else []
            ),
            # Only conflicts that bear on this answer are narrated. Everything
            # else stays in the warnings and the reasoning trace, so an unrelated
            # disagreement elsewhere in the corpus cannot contaminate the answer.
            "contradictions": [c for c in turn.reasoning.get("contradictions", [])
                               if c.get("blocking")],
            "caveat": gate.decision is gate_module.GateDecision.ANSWER_WITH_CAVEAT,
        }

        if not supported and not context["conclusion"]:
            turn.answer = self._compose(Task.DIRECT_ANSWER, question, history,
                                        context=context,
                                        system=prompts.DIRECT_SYSTEM, grounded=False)
            turn.warnings.append(
                "Answered from general model knowledge, not from your workspace "
                "documents. It is not evidence-backed."
            )
            return

        turn.answer = self._compose(Task.GROUNDED_ANSWER, question, history,
                                    context=context, system=prompts.GROUNDED_SYSTEM,
                                    grounded=True)
        if gate.decision is gate_module.GateDecision.REPORT_CONFLICT:
            turn.warnings.append(
                "Conflicting sources were found; the conflict is reported rather than "
                "resolved."
            )

    @staticmethod
    def _derivation_steps(inference: InferenceResult,
                          conclusion_fact: FactDTO | None) -> list[Any]:
        """The steps that actually produced the conclusion, in order.

        Forward chaining derives everything derivable; only the part of the trace
        the answer rests on belongs in the answer. The full trace stays in the
        reasoning panel.
        """
        if conclusion_fact is None:
            return list(inference.steps)
        by_conclusion = {step.conclusion: step for step in inference.steps}
        by_render = {f.render(): f for f in inference.facts}
        wanted: list[Any] = []
        seen: set[str] = set()
        frontier = [conclusion_fact.render()]
        while frontier:
            target = frontier.pop()
            if target in seen:
                continue
            seen.add(target)
            step = by_conclusion.get(target)
            if step is None:
                continue
            wanted.append(step)
            for premise in step.premises:
                frontier.append(premise)
                fact = by_render.get(premise)
                if fact is not None and fact.provenance.premise_fact_ids:
                    for other in inference.facts:
                        if other.fact_id in fact.provenance.premise_fact_ids:
                            frontier.append(other.render())
        wanted.sort(key=lambda s: s.step)
        return wanted

    @staticmethod
    def _readable_inferences(inference: InferenceResult,
                             steps: list[Any] | None = None) -> list[dict[str, Any]]:
        """Inference steps rendered for prose: no internal identifiers in the text."""
        by_render = {f.render(): f for f in inference.facts}
        out: list[dict[str, Any]] = []
        for step in (steps if steps is not None else inference.steps):
            entry = step.as_dict()
            fact = by_render.get(step.conclusion)
            entry["sentence"] = (
                phrase_fact(fact.subject, fact.predicate, render_value(fact.object))
                if fact is not None else step.conclusion
            )
            entry["premise_sentences"] = [
                phrase_fact(p.subject, p.predicate, render_value(p.object))
                for premise in step.premises
                for p in [by_render.get(premise)] if p is not None
            ]
            out.append(entry)
        return out

    def _conclusion_sentence(self, fact: FactDTO | None, plan: Plan) -> str:
        if fact is None:
            return ""
        sentence = phrase_fact(fact.subject, fact.predicate, render_value(fact.object))
        if fact.kind is FactKind.INFERRED:
            return (f"{sentence}. That was derived by the rule engine from the "
                    f"recorded facts, not generated")
        return sentence

    def _compose(self, task: Task, question: str, history: list[dict[str, str]],
                 *, context: dict[str, Any], system: str,
                 grounded: bool = True) -> str:
        if grounded:
            system_prompt, messages = prompts.build_grounded_messages(
                question, history, context)
        else:
            system_prompt, messages = prompts.build_direct_messages(question, history)
        request = CompletionRequest(messages=messages, task=task,
                                    system=system or system_prompt, context=context)
        try:
            return self.llm.complete(request).text.strip()
        except ProviderError:
            # Never fabricate: fall back to the deterministic composer, which can
            # only restate approved material.
            log_event(logger, 30, "llm provider failed; using deterministic composer")
            return get_fallback_llm().complete(request).text.strip()

    def _post_check(self, turn: AssistantTurn, report: VerificationReport,
                    spans: list[EvidenceSpan],
                    extra_context: list[str] | None = None) -> None:
        grounding = check_grounding(turn.answer, report.supported_claims, spans,
                                    extra_context)
        turn.grounding = grounding.as_dict()
        if grounding.grounded:
            return
        if grounding.unsupported_numbers:
            # A number that appears in no evidence is the classic hallucination.
            # Refuse rather than publish it.
            turn.answer = (
                f"{ABSTAIN_TEXT}\n\nA draft answer was produced but it contained "
                f"values that do not appear in any retrieved evidence, so it was "
                f"discarded."
            )
            turn.gate_decision = gate_module.GateDecision.ABSTAIN.value
            turn.verification_status = VerificationStatus.INSUFFICIENT_EVIDENCE.value
            turn.confidence = 0.0
            turn.warnings.append(
                "The generated answer failed the post-generation grounding check "
                "(unsupported numeric values) and was withheld."
            )
            audit.record(self.session, audit.ANSWER_ABSTAINED,
                         tenant_id=self.principal.tenant_id,
                         actor_user_id=self.principal.user_id,
                         outcome="withheld",
                         detail={"reason": "ungrounded_numbers",
                                 "values": grounding.unsupported_numbers[:5]})
            return
        turn.warnings.append(
            "Part of the generated wording could not be traced to the evidence; "
            "treat the untraced parts as unverified."
        )
        if turn.verification_status == VerificationStatus.VERIFIED.value:
            turn.verification_status = VerificationStatus.PARTIALLY_VERIFIED.value
        turn.confidence = round(turn.confidence * max(0.4, grounding.score), 4)


# ---------------------------------------------------------------------------

def _focus_terms(frame: IntentFrame, conclusion_fact: FactDTO | None) -> list[str]:
    """The words that identify what the question is about."""
    from app.neuro.heuristics import PREDICATE_HINTS, label_for

    predicate = (conclusion_fact.predicate if conclusion_fact is not None
                 else frame.target_attribute)
    if not predicate:
        return []
    terms = [predicate.replace("_", " "), label_for(predicate)]
    terms.extend(PREDICATE_HINTS.get(predicate, ()))
    return terms


def _round(value: Any) -> Any:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return value


def _citations(spans: list[EvidenceSpan]) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "document_id": str(span.document_id),
            "document_title": span.document_title,
            "title": span.document_title,
            "chunk_id": str(span.chunk_id),
            "section": span.section,
            "page": span.page,
            "snippet": span.text[:1200],
            "score": round(span.score, 4),
            "quarantined": span.quarantined,
        }
        for index, span in enumerate(spans, start=1)
    ]


def _guess_resource_action(question: str, frame: IntentFrame) -> tuple[str, str]:
    lowered = question.lower()
    if "salary" in lowered or "compensation" in lowered or "pay" in lowered:
        return "salary_record", "READ"
    if "rule" in lowered:
        return "rule", "READ"
    if "conversation" in lowered:
        return "conversation", "READ"
    return "document", "READ"


def _user_model():
    from app.db.models.tenancy import User
    return User
