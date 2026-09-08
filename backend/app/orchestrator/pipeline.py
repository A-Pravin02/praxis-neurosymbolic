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

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import audit
from app.core.errors import ProviderError
from app.core.logging import get_logger, log_event
from app.db.models.knowledge import Fact
from app.knowledge import store as knowledge_store
from app.knowledge.resolver import Resolution, question_tokens_of, resolve
from app.knowledge.schema import FactDTO, FactKind, render_value, unwrap
from app.neuro.heuristics import extract_numbers as _numbers
from app.neuro.heuristics import is_exploratory
from app.neuro.heuristics import phrase_fact
from app.neuro import prompts
from app.neuro.base import CompletionRequest, Message, Role, Task
from app.neuro.intent import IntentFrame, parse_intent
from app.neuro.registry import get_fallback_llm, get_llm
from app.orchestrator.router import Plan, Route, route_for_mode
from app.policy import service as policy_service
from app.policy.pdp import Decision, Resource
from app.db.models.rag import Chunk, Document
from app.neuro.heuristics import coverage
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

def _terms_of(value: str) -> set[str]:
    """Subject or predicate name split into the same stems a question produces."""
    return question_tokens_of((value or "").replace(":", " ").replace("_", " "))


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
        self.resolution = Resolution()
        self.last_finish_reason = "stop"

    # -- public ------------------------------------------------------------
    def answer(self, question: str, *, history: list[dict[str, str]] | None = None,
               pipeline_mode: str = "neurosymbolic") -> AssistantTurn:
        started = time.perf_counter()
        turn = AssistantTurn(pipeline_mode=pipeline_mode)
        history = history or []

        with _Timer(turn, "understand"):
            frame = parse_intent(question, history)
            plan = route_for_mode(frame, pipeline_mode)
            resolution = self._resolve_target(frame, plan, question, pipeline_mode)
        turn.intent = {**frame.as_dict(), "resolution": resolution.as_dict()}
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
                facts = self._collect_facts(spans, frame, plan.goal_subject)
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

        if conclusion_fact is not None and plan.retrieve:
            with _Timer(turn, "evidence_backfill") as timer:
                added = self._backfill_evidence(spans, conclusion_fact)
                timer.detail = {"added": len(added)}
            if added:
                spans = spans + added
                turn.citations = citations = _citations(spans)

        blocking = self._relevant_contradictions(inference, frame, conclusion_fact)
        aside = self._non_blocking_contradictions(inference, blocking, question)
        claims = self._build_claims(question, plan, frame, spans, inference,
                                    conclusion_fact, inference.contradictions)

        with _Timer(turn, "verify") as timer:
            if plan.verify:
                report = verify(
                    question, claims, spans,
                    inference_steps=inference.steps,
                    contradictions=blocking,
                    require_evidence=plan.require_evidence,
                    exploratory=is_exploratory(question),
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
                    # A computed statistic is as deterministic as a derived one:
                    # both are values the system produced, not text it read.
                    symbolic_conclusion=conclusion_fact is not None and (
                        conclusion_fact.kind is FactKind.INFERRED
                        or conclusion_fact.provenance.source_kind == "record"
                    ),
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

    def _resolve_target(self, frame: IntentFrame, plan: Plan, question: str,
                        pipeline_mode: str) -> "Resolution":
        """Bind the question to a subject and predicate this workspace really has.

        The hand-written predicate vocabulary cannot cover an arbitrary dataset,
        so when it fails the workspace itself is consulted. A hit upgrades the
        plan to the symbolic route, because a question that names a stored
        attribute is a value question however it happens to be phrased.

        Ablation modes are left alone: their whole purpose is to run without this.
        """
        blank = Resolution()
        if pipeline_mode != "neurosymbolic" or plan.check_policy:
            return blank
        if frame.intent in ("smalltalk", "contradiction_analysis"):
            return blank

        resolution = resolve(self.session, self.principal, question)
        self.resolution = resolution
        if resolution.subject:
            # Even a subject-only match matters. "The median income in the walmart
            # dataset" names a table that has no income column; binding the subject
            # confines the answer to that table's own sources, so the question is
            # refused rather than answered from a supermarket table that happens to
            # have one.
            plan.goal_subject = resolution.subject
        if not resolution.resolved:
            return resolution

        if frame.intent == "document_lookup":
            # "What does the handbook say about X" asks what a source states, not
            # what the stored value is. The subject is already bound above for
            # scoping; deriving a conclusion here would answer a different question.
            return resolution

        frame.target_attribute = frame.target_attribute or resolution.predicate
        plan.goal_predicate = plan.goal_predicate or resolution.predicate
        if plan.route in (Route.DIRECT_RESPONSE, Route.RAG_QUERY):
            plan.route = Route.RAG_PLUS_SYMBOLIC
            plan.retrieve = True
            plan.extract_knowledge = True
            plan.reason = True
            plan.require_evidence = True
            plan.rationale = (
                "The question names an attribute this workspace stores, so it is "
                "answered from the recorded value rather than from prose."
            )
        return resolution

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

    def _collect_facts(self, spans: list[EvidenceSpan], frame: IntentFrame,
                       goal_subject: str | None = None) -> list[FactDTO]:
        """Facts the caller may see, restricted to what this question is about."""
        facts: list[FactDTO] = []

        # 1. The caller's own profile attributes, as DIRECT facts about "self".
        user = self.session.get(_user_model(), self.principal.user_id)
        if user is not None:
            facts.extend(knowledge_store.subject_facts_for_user(
                user.id, user.attributes or {}, user.email))

        # 2. Facts from the passages that were retrieved, and from the documents
        #    those passages belong to. Facts computed at ingest time (statistics
        #    over a table, for instance) attach to the document rather than to any
        #    one chunk, so a chunk-only lookup finds none of them.
        chunk_ids = [s.chunk_id for s in spans if not s.quarantined]
        document_ids = {s.document_id for s in spans if not s.quarantined}
        if chunk_ids or document_ids:
            conditions = []
            if chunk_ids:
                conditions.append(Fact.source_chunk_id.in_(chunk_ids))
            if document_ids:
                conditions.append(and_(Fact.source_chunk_id.is_(None),
                                       Fact.source_document_id.in_(document_ids)))
            rows = self.session.scalars(
                select(Fact).where(
                    policy_service.fact_filter(self.session, self.principal),
                    or_(*conditions),
                ).limit(4000)
            ).all()
            facts.extend(knowledge_store.fact_to_dto(r) for r in rows)

        # 3. Facts about the target attribute from any permitted source, so a
        #    contradiction between two documents is visible even when retrieval
        #    surfaced only one of them.
        if frame.target_attribute:
            stmt = select(Fact).where(
                policy_service.fact_filter(self.session, self.principal),
                Fact.predicate == frame.target_attribute,
            )
            if goal_subject:
                stmt = stmt.where(Fact.subject == goal_subject)
            facts.extend(knowledge_store.fact_to_dto(r)
                         for r in self.session.scalars(stmt.limit(50)).all())

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

    def _backfill_evidence(self, spans: list[EvidenceSpan],
                           conclusion_fact: FactDTO) -> list[EvidenceSpan]:
        """Put the conclusion's own source passage into the evidence set.

        A fact computed at ingest time is attached to the document that describes
        it, but retrieval ranks passages by wording and can easily miss that
        document while surfacing a longer, chattier one. The result is an
        abstention on a value the system can prove, which is a false refusal, not
        a safe one.

        This is not manufacturing support: it fetches the passage the fact
        actually came from, so the user can check it. If that passage does not
        contain the value, verification will still fail - as it should.
        """
        document_id = conclusion_fact.provenance.document_id
        if document_id is None or any(s.document_id == document_id for s in spans):
            return []

        rows = self.session.execute(
            select(Chunk.id, Chunk.document_id, Chunk.text, Chunk.section, Chunk.page,
                   Chunk.injection_flagged, Chunk.injection_patterns, Document.title,
                   Document.collection, Document.sensitivity, Document.effective_date,
                   Document.version_label)
            .join(Document, Document.id == Chunk.document_id)
            .where(policy_service.retrieval_filter(self.session, self.principal),
                   Chunk.document_id == document_id)
            .limit(60)
        ).all()
        if not rows:
            return []

        # Choose the passage that actually states the value.
        target = conclusion_fact.render()
        wanted = {round(n, 6) for n in _numbers(render_value(conclusion_fact.object))}
        best_row, best_score = None, 0.0
        for row in rows:
            score = coverage(target, row.text)
            if wanted and wanted & {round(n, 6) for n in _numbers(row.text)}:
                score += 1.0
            if score > best_score:
                best_row, best_score = row, score
        if best_row is None or best_score <= 0:
            return []
        return [HybridRetriever._to_span(best_row, lexical_score=0.5)]

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
        for subject in (plan.goal_subject, "self", "policy"):
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
        question: str = "",
    ) -> list[Contradiction]:
        """Conflicts worth mentioning beside this answer.

        Every disagreement anywhere in the loaded fact set used to be surfaced,
        so a question about who to call about a password warned about a version
        conflict on annual leave and another on warranty months. That is noise,
        and noise beside an abstention reads as though the two were connected.

        The full set stays in the reasoning trace either way; this only decides
        what is worth saying out loud, which is the conflicts touching what was
        actually asked about.
        """
        blocking_keys = {(c.subject, c.predicate) for c in blocking}
        aside = [c for c in inference.contradictions
                 if (c.subject, c.predicate) not in blocking_keys]
        if not question:
            return aside
        asked = question_tokens_of(question)
        related = []
        for contradiction in aside:
            terms = _terms_of(contradiction.predicate) | _terms_of(contradiction.subject)
            if terms & asked:
                related.append(contradiction)
        return related

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
            # When the question named a specific subject, only that subject's own
            # sources may answer it. Otherwise "the median income in the walmart
            # dataset" gets answered from a supermarket table that happens to have
            # an income column - fluent, cited, and about the wrong thing.
            allowed_documents = self._subject_documents(plan.goal_subject, inference)
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
                exploratory=is_exploratory(question),
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
                if allowed_documents is not None:
                    chunk_documents = {
                        str(span.document_id) for span in spans
                        if str(span.chunk_id) in claim.supporting_chunk_ids
                    }
                    if chunk_documents and not (chunk_documents & allowed_documents):
                        continue
                span_claims.append(claim)
            span_claims.sort(key=lambda c: -c.relevance)
            limit = 2 if conclusion_fact is not None else 4
            claims.extend(span_claims[:limit])
        return claims[:10]

    def _subject_documents(self, goal_subject: str | None,
                           inference: InferenceResult) -> set[str] | None:
        """Documents that the resolved subject's facts come from.

        Returns None when no subject was identified, which leaves claim selection
        unrestricted - the ordinary case for a general document question.
        """
        if not goal_subject or goal_subject == "self":
            return None
        documents = {
            str(fact.provenance.document_id) for fact in inference.facts
            if fact.subject == goal_subject and fact.provenance.document_id
        }
        if documents:
            return documents
        # The subject's facts may not have been loaded, because retrieval never
        # surfaced their document. Ask the store directly rather than silently
        # dropping the restriction.
        rows = self.session.scalars(
            select(Fact.source_document_id).where(
                policy_service.fact_filter(self.session, self.principal),
                Fact.subject == goal_subject,
                Fact.source_document_id.isnot(None),
            ).distinct().limit(20)
        ).all()
        return {str(r) for r in rows} or None

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
            "inferences": self._readable_inferences(inference),
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

        ambiguous = self._ambiguous_sources(report, spans, conclusion_fact)
        if ambiguous:
            listing = "\n".join(f"- {title}: {value}"
                                for title, value in ambiguous[:4])
            turn.answer = (
                "Several datasets report a different value for that, so naming one "
                "would be a guess:\n\n"
                f"{listing}\n\n"
                "Tell me which dataset you mean and I will answer from it."
            )
            turn.gate_decision = gate_module.GateDecision.ABSTAIN.value
            turn.verification_status = VerificationStatus.INSUFFICIENT_EVIDENCE.value
            turn.confidence = 0.0
            turn.warnings.append(
                f"{len(ambiguous)} datasets report different values for this "
                f"attribute; the question does not say which is meant."
            )
            audit.record(self.session, audit.ANSWER_ABSTAINED,
                         tenant_id=self.principal.tenant_id,
                         actor_user_id=self.principal.user_id, outcome="abstained",
                         detail={"reason": "ambiguous_across_sources",
                                 "sources": [t for t, _ in ambiguous][:5]})
            return
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

        reporting_conflict = gate.decision is gate_module.GateDecision.REPORT_CONFLICT
        named_subject = self.resolution.subject

        # Deliberately not gated on `not supported`: when the name is ambiguous,
        # a span that happens to match is not a rescue, it is the leak. "The train
        # dataset" once fell through to a course description about training a
        # recommendation model, which is supported, cited, and nonsense.
        if not reporting_conflict and self.resolution.ambiguous_subjects:
            # Several datasets answer to the name the question used, and it does
            # not say which. Picking the one that happens to hold a matching column
            # would produce a fluent, cited answer about the wrong table.
            labels = [s.split(":", 1)[-1] for s in self.resolution.ambiguous_subjects]
            listed = ", ".join(f"`{label}`" for label in labels)
            turn.answer = (
                f"That name matches more than one dataset here - {listed} - and the "
                f"question doesn't say which one you mean. They hold different "
                f"columns, so answering from whichever happens to have a matching "
                f"one would be guessing. Which did you mean?"
            )
            turn.warnings.append(f"Ambiguous dataset name; candidates: {listed}.")
            turn.gate_decision = gate_module.GateDecision.ABSTAIN.value
            turn.verification_status = VerificationStatus.INSUFFICIENT_EVIDENCE.value
            turn.confidence = 0.0
            audit.record(self.session, audit.ANSWER_ABSTAINED,
                         tenant_id=self.principal.tenant_id,
                         actor_user_id=self.principal.user_id, outcome="abstained",
                         detail={"reason": "ambiguous_subject_name",
                                 "candidates": list(self.resolution.ambiguous_subjects)})
            return
        if (not supported and not context["conclusion"] and not reporting_conflict
                and named_subject and not self.resolution.resolved
                and self.resolution.dataset_scoped):
            # The question named a dataset this workspace holds, but nothing in it
            # matches the attribute asked for. Saying so is more useful than a
            # generic refusal, and far better than answering from a table that
            # happens to have a similarly named column.
            label = named_subject.split(":", 1)[-1]
            turn.answer = (
                f"The `{label}` dataset does not record that. I can see its columns "
                f"and computed statistics, and none of them match what you asked "
                f"for, so I would be answering from a different dataset if I "
                f"answered at all."
            )
            turn.warnings.append(
                f"Question scoped to `{label}`; no matching attribute exists there."
            )
            # This is a refusal, and the envelope has to say so. Leaving the gate
            # decision as an answer makes a refusal look like a confident reply to
            # every consumer of the API - including the evaluation harness.
            turn.gate_decision = gate_module.GateDecision.ABSTAIN.value
            turn.verification_status = VerificationStatus.INSUFFICIENT_EVIDENCE.value
            turn.confidence = 0.0
            audit.record(self.session, audit.ANSWER_ABSTAINED,
                         tenant_id=self.principal.tenant_id,
                         actor_user_id=self.principal.user_id, outcome="abstained",
                         detail={"reason": "attribute_absent_from_named_subject",
                                 "subject": named_subject})
            return

        if not supported and not context["conclusion"] and not reporting_conflict:
            turn.answer = self._compose(Task.DIRECT_ANSWER, question, history,
                                        context=context,
                                        system=prompts.DIRECT_SYSTEM, grounded=False)
            if self.last_finish_reason == "no_answer":
                # Say why there is no answer. The provider's own wording blames
                # the model ("configure a cloud provider"), which is misleading
                # when the real reason is that the corpus does not cover the
                # topic - the user goes looking for a settings problem instead of
                # adding the document that would answer them.
                if spans:
                    turn.answer = (
                        "I don't have anything in your workspace documents that "
                        "answers that. I searched and found the closest passages, "
                        "but none of them cover it - so rather than guess, I would "
                        "rather tell you it isn't there. If it should be, the "
                        "document holding it hasn't been added yet."
                    )
                # The model declined. Reporting that as ANSWER_WITH_CAVEAT is the
                # same defect as the dataset refusal that called itself an answer:
                # the prose says "I don't have a grounded answer" while the
                # machine-readable decision says otherwise, and every consumer
                # trusts the decision.
                turn.gate_decision = gate_module.GateDecision.ABSTAIN.value
                turn.verification_status = VerificationStatus.INSUFFICIENT_EVIDENCE.value
                turn.confidence = 0.0
                audit.record(self.session, audit.ANSWER_ABSTAINED,
                             tenant_id=self.principal.tenant_id,
                             actor_user_id=self.principal.user_id,
                             outcome="abstained",
                             detail={"reason": "model_declined_general_answer"})
                return
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

    def _ambiguous_sources(self, report: VerificationReport, spans: list[EvidenceSpan],
                           conclusion_fact: FactDTO | None) -> list[tuple[str, str]]:
        """Different documents offering different numbers for the same question.

        Retrieval happily returns an income column from three unrelated tables. A
        single "mean income" answer assembled from them is fluent, cited, and
        meaningless. When a value question draws competing numbers from separate
        sources and nothing identifies which source is meant, that is ambiguity -
        and the honest response is to say so and ask.

        A derived conclusion settles the question, so this does not apply to it.
        """
        if conclusion_fact is not None:
            return []
        by_document: dict[str, tuple[str, float]] = {}
        titles = {str(span.chunk_id): (str(span.document_id), span.document_title)
                  for span in spans}
        for claim in report.supported_claims:
            values = _numbers(claim.text)
            if not values:
                continue
            for chunk_id in claim.supporting_chunk_ids:
                entry = titles.get(chunk_id)
                if entry is None:
                    continue
                document_id, title = entry
                by_document.setdefault(document_id, (title, values[0]))
        if len(by_document) < 2:
            return []
        distinct = {round(value, 6) for _, value in by_document.values()}
        if len(distinct) < 2:
            return []
        return [(title, f"{value:g}") for title, value in by_document.values()]

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
            completion = self.llm.complete(request)
        except ProviderError:
            # Never fabricate: fall back to the deterministic composer, which can
            # only restate approved material.
            log_event(logger, 30, "llm provider failed; using deterministic composer")
            completion = get_fallback_llm().complete(request)
        # A provider that declines says so here. Kept so the caller can label the
        # turn a refusal without having to recognise the refusal's wording.
        self.last_finish_reason = completion.finish_reason
        return completion.text.strip()

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
