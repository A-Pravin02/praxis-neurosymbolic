"""HYBRID. The neuro-symbolic controller: one question, end to end.

    question
      -> SECURITY  authorize: the person's view of the knowledge graph, before anything else
      -> NEURAL    understand: goal candidates, speech act, entities, stated inputs
      -> HYBRID    route: choose pipeline A-G from the question and the rule base
      -> SYMBOLIC  ground: resolve the record, validate statements against records
      -> SYMBOLIC  reason: three-valued rule evaluation over the authorized graph, with the
                   policy version in force on the case's date
      -> HYBRID    loop: the reasoner names an input it lacks; the neural layer looks
                   again for exactly that input; the reasoner re-evaluates
      -> HYBRID    check claims: numbers attributed to the policy against the policy
      -> HYBRID    verify: ten deterministic checks; an answer that fails is withheld
      -> NEURAL    generate: phrase the established conclusion
      -> HYBRID    explain: decision, recommended action, evidence, rules, verification,
                   and a knowledge gap when the knowledge could not answer

Where standard RAG ends and neuro-symbolic reasoning begins
------------------------------------------------------------
Pipeline A *is* retrieval: find the policy clause and quote it. Every other reasoning
pipeline uses retrieval only for citations. The decision - can this order be returned,
how much is this claim worth, what may an agent offer - is made by rules evaluated on
records, and a language model never sees the question when that decision is made.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger, log_event
from app.hybrid import ablations, gaps
from app.hybrid.claims import check_policy_claims
from app.hybrid.grounding import Actor, FactGrounding, Grounding
from app.hybrid.knowledge_pack import KnowledgePack, load_pack
from app.hybrid.router import RETRIEVAL_ACCEPT, Pipeline, ReasoningRouter, RouteDecision
from app.hybrid.trace import ReasoningTrace
from app.hybrid.verification import VerificationReport, Verifier, run_checks
from app.neural.query_understanding import TARGETED_INPUT_THRESHOLD, Interpretation
from app.neural.response_generation import Conclusion, LanguageModelRealizer, realize
from app.symbolic.reasoner import CONDITIONAL, GoalResult, Reasoner, RuleOutcome, Symbolic
from app.symbolic.render import format_value, humanize

logger = get_logger("praxis.hybrid")

MAX_LOOP_ITERATIONS = 2
MAX_CASES = 8
NOT_SPLIT_TYPES = {"company", "customer", "employee", "grade", "policy_version"}
SUBJECT_NOUNS = {"order": "order", "claim": "trip", "return": "return", "employee": "record",
                 "ticket": "issue"}
CUSTOMER_RECORD_TYPES = {"order", "return", "refund", "payment", "ticket"}


@dataclass
class AssistantAnswer:
    answer: str
    pipeline: str
    pipeline_label: str
    outcome: str        # answered | conditional | clarification | insufficient | denied |
    #                    needs_review | general
    confidence: float
    sources: list[dict[str, Any]] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    clarification: dict[str, Any] | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    goal: str | None = None
    decision: Any = None          # the result, only when it is determined
    value: Any = None             # the result even when conditional; None when symbolic
    subject: str | None = None    # the record the reasoning resolved to
    latency_ms: int = 0
    # -- the decision-support view --------------------------------------------------------
    decision_label: str = ""      # "Yes", "No", "₹8,000", "Offer a replacement", ...
    decision_kind: str = ""       # yes_no | amount | recommendation | list | record |
    #                               information | needs_information | clarification |
    #                               not_covered | access_refused | needs_review
    recommended_action: str | None = None
    verification: dict[str, Any] = field(default_factory=dict)
    knowledge_gap: dict[str, Any] | None = None
    policy_versions: list[dict[str, Any]] = field(default_factory=list)
    rules_applied: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class _Request:
    """Everything that is specific to the person asking. Never stored on the controller."""

    actor: Actor
    view: Any
    grounder: FactGrounding
    verifier: Verifier

    @property
    def staff(self) -> bool:
        return self.actor.kind == "staff"


class NeuroSymbolicController:
    def __init__(self, pack: KnowledgePack | None = None, *, llm=None,
                 ablations: frozenset[str] = frozenset(), understanding: str = "encoder") -> None:
        # Mechanisms switched off for the ablation study (app/hybrid/ablations.py). Always
        # empty in the deployed service.
        self.ablations = frozenset(ablations)
        self.pack = pack or load_pack()
        self.program = self.pack.program
        self.graph = self.pack.graph
        self.router = ReasoningRouter(self.pack)
        if llm is None:
            from app.neuro.registry import get_llm
            llm = get_llm()
        self.llm = llm
        self.realizer = LanguageModelRealizer(llm)
        # How language becomes structure: by embedding similarity (offline, the default),
        # or by the language model as a semantic parser into the pack's closed schema.
        # Everything after understanding is identical either way.
        self.understanding = self.pack.understanding
        if understanding == "llm":
            from app.neural.semantic_parser import LLMSemanticParser
            self.understanding = LLMSemanticParser(self.program, self.graph,
                                                   self.pack.policies, llm,
                                                   self.pack.understanding)

    # =======================================================================
    def answer(self, question: str, *, actor_email: str, actor_roles: tuple[str, ...] = (),
               context: dict | None = None) -> AssistantAnswer:
        with ablations.applied(self.ablations):
            return self._answer(question, actor_email=actor_email, actor_roles=actor_roles,
                                context=context)

    def _answer(self, question: str, *, actor_email: str, actor_roles: tuple[str, ...],
                context: dict | None) -> AssistantAnswer:
        started = time.perf_counter()
        if ablations.active("no_authorization"):
            # Ablation only: the person keeps their identity but every record is visible.
            actor_roles = tuple(actor_roles) + ("admin",)
        actor = FactGrounding(self.pack).actor(actor_email, tuple(actor_roles))
        # Authorization before reasoning: every later step sees only this view.
        view = self.graph.view_for(actor.access_kind, actor.id, self.program)
        req = _Request(actor, view, FactGrounding(self.pack, view), Verifier(self.pack, view))

        interpretation = self.understanding.understand(question)
        route = self.router.route(interpretation)

        trace = ReasoningTrace(
            question=question,
            neural_interpretation=interpretation.as_dict(),
            entities=_entities(interpretation),
            selected_pipeline=route.pipeline.value,
            pipeline_label=route.pipeline.label,
            pipeline_reason=route.reason,
            why_neural=route.neural_reason,
            why_symbolic=route.symbolic_reason,
            components=[{"layer": layer, "stage": stage} for layer, stage in route.stages],
            retrieved_knowledge=[c.as_dict() for c in route.clauses],
            authorization={"access": actor.access_kind, "person": actor.id,
                           "visible_records": len(view.entities),
                           "withheld_records": view.hidden_count()},
        )

        goal = route.goal
        if route.pipeline is Pipeline.GENERAL:
            result = self._general(question, route, trace, context)
        elif route.pipeline is Pipeline.INSUFFICIENT:
            result = self._insufficient(interpretation, route, trace, req)
        elif route.pipeline is Pipeline.CLARIFICATION and goal is None:
            result = self._clarify_intent(interpretation, route, trace)
        elif goal is None:
            result = self._retrieval(route, trace, req)
        elif goal.kind == "lookup":
            result = self._lookup(interpretation, route, req, context, trace)
        else:
            result = self._reason(interpretation, route, req, context, trace)

        self._finish(result, trace)
        result.latency_ms = trace.latency_ms = int((time.perf_counter() - started) * 1000)
        result.trace = trace.as_dict()
        return result

    # =======================================================================
    # Pipeline A: knowledge retrieval
    # =======================================================================
    def _retrieval(self, route: RouteDecision, trace: ReasoningTrace, req) -> AssistantAnswer:
        top = route.clauses[0]
        # An overview question ("what is the leave policy") is answered by every clause
        # on the topic, not only the single closest one. A clause the semantic parser
        # selected has already been checked by the router and is used as chosen.
        chosen = route.clauses[:1] if route.selected_by_parser else [
            c for c in route.clauses
            if c.score >= RETRIEVAL_ACCEPT and c.score >= top.score - 0.08][:3]
        conclusion = Conclusion(kind="retrieval", clauses=[
            {"id": c.clause.id, "title": c.clause.title, "document": c.clause.document,
             "text": c.clause.text} for c in chosen])
        text = realize(conclusion)
        sources = [_clause_source(c.clause) for c in chosen]
        report = run_checks(req.verifier.policy_applicability(sources, []),
                            req.verifier.grounding(text, conclusion))
        trace.verification = report.as_dict()
        trace.reasoning_steps = [
            "Classified the request as a question about what company policy says.",
            f"Retrieved {len(chosen)} policy clause{'s' if len(chosen) != 1 else ''} in force, "
            f"by meaning: " + "; ".join(f"{c.clause.id} {c.clause.title} ({c.score:.2f})"
                                        for c in chosen) + ".",
            "No decision or amount was asked for, so no rule was evaluated.",
        ]
        confidence = _clamp((top.score - 0.45) / 0.35) * (1.0 if report.passed else 0.5)
        trace.confidence = round(confidence, 2)
        trace.confidence_factors = {"retrieval_relevance": round(top.score, 3)}
        return AssistantAnswer(
            answer=text, pipeline=route.pipeline.value, pipeline_label=route.pipeline.label,
            outcome="answered", confidence=round(confidence, 2), sources=sources,
            decision_label="Policy information", decision_kind="information",
            verification=_verification(report),
            explanation=[
                "Understood the question as a request for company information.",
                "Retrieved the matching policy text in force: "
                + ", ".join(f"{c.clause.id} ({c.clause.title})" for c in chosen) + ".",
                "Answered from that text directly — there was no rule to apply.",
                f"Chose {route.pipeline.label} because no decision about a specific case was "
                f"asked for.",
            ])

    # =======================================================================
    # Pipeline G: general conversation
    # =======================================================================
    def _general(self, question: str, route: RouteDecision, trace: ReasoningTrace,
                 context: dict | None) -> AssistantAnswer:
        """Answer an ordinary question from the model's own knowledge.

        Nothing here is verified, and the answer says so rather than borrowing the
        credibility of the verified path: no citation, no rule, no confidence meter
        derived from evidence that does not exist. The reason for answering at all is
        that refusing is not honest either - the assistant has no company knowledge to
        protect on this question, only a conversation to hold.

        The symbolic layer is untouched. A question that reaches here has already been
        established to share no vocabulary with the company's policies, rules or
        schema, so there is no conclusion for a model to contradict.
        """
        text, model_answered = self._general_text(question)
        trace.reasoning_steps = [
            "Compared the question's words with the company's policies, rules and schema: "
            "none of them appear there.",
            "Concluded the question is not about the company, so no policy applies and no "
            "record is relevant.",
            ("Answered from the language model's general knowledge." if model_answered
             else "The configured provider had no general answer to give."),
            "Did not cite or verify: there is no company evidence this answer could rest on.",
        ]
        # Deliberately no verification report. The checks compare an answer against
        # company facts, and reporting "passed" for an answer with no facts behind it
        # would make an unverified answer look verified in every consumer of the API.
        trace.verification = {}
        trace.confidence = 0.0
        return AssistantAnswer(
            answer=text, pipeline=route.pipeline.value, pipeline_label=route.pipeline.label,
            outcome="general", confidence=0.0,
            decision_label="General knowledge", decision_kind="information",
            context=self._context(context, "", ""),
            explanation=[
                "Recognised the question as general conversation, not a question about "
                f"{self.graph.display_name(self.graph.company_id)}.",
                "Used no policy, rule or record, because none applies.",
                "Answered from the language model's own knowledge, which is not verified "
                "and carries no citation.",
            ])

    def _general_text(self, question: str) -> tuple[str, bool]:
        from app.neuro import prompts
        from app.neuro.base import CompletionRequest, Message, Role, Task

        unavailable = (
            "That one isn't about "
            f"{self.graph.display_name(self.graph.company_id)}, so there's no policy or record for me "
            "to answer it from — and this deployment is running the offline deterministic "
            "provider, which has no general knowledge of its own. Configure a language "
            "model (LLM_PROVIDER) and I can answer questions like this conversationally. "
            "Questions about policies, orders and entitlements work either way."
        )
        request = CompletionRequest(
            task=Task.DIRECT_ANSWER, system=prompts.DIRECT_SYSTEM,
            messages=[Message(Role.USER, question)],
        )
        try:
            completion = self.llm.complete(request)
        except Exception as exc:  # noqa: BLE001 - any provider failure degrades to saying so
            log_event(logger, 30, "general-conversation provider failed",
                      error=type(exc).__name__)
            return unavailable, False
        if completion.finish_reason == "no_answer" or not completion.text.strip():
            return unavailable, False
        return completion.text.strip(), True

    # =======================================================================
    # Pipeline F / E without a goal
    # =======================================================================
    def _insufficient(self, interpretation: Interpretation, route: RouteDecision,
                      trace: ReasoningTrace, req) -> AssistantAnswer:
        best = route.clauses[0] if route.clauses else None
        nearest = best if best is not None and best.score >= 0.45 else None
        gap = gaps.uncovered_topic(interpretation, self.pack, nearest)
        covered = f" about {', '.join(gap['terms'][:3])}" if gap["terms"] else ""
        reasons = [f"Nothing in the company's policies, rules or records says anything{covered}, "
                   f"so any answer would be invented"]
        if nearest is not None:
            reasons.append(f"The closest topic I do have is {nearest.clause.title} "
                           f"({nearest.clause.id}), and it doesn't answer this")
        conclusion = Conclusion(kind="insufficient", reasons=reasons,
                                headline=f"I can't answer that from "
                                         f"{self.graph.display_name(self.graph.company_id)}'s company "
                                         f"knowledge")
        text = realize(conclusion)
        trace.reasoning_steps = [
            "Compared the question with every goal the rule base can decide: none is close.",
            "Searched the policy text: no clause is relevant enough to answer from.",
            f"Recorded a knowledge gap: {gap['summary']}.",
            "Reported what is missing instead of generating an answer.",
        ]
        trace.knowledge_gap = gap
        trace.verification = run_checks(req.verifier.grounding(text, conclusion)).as_dict()
        return AssistantAnswer(
            answer=text, pipeline=route.pipeline.value, pipeline_label=route.pipeline.label,
            outcome="insufficient", confidence=0.0, knowledge_gap=gap,
            recommended_action=_gap_action(req),
            explanation=["Looked for a matching policy, rule or record.",
                         "Found nothing that covers the question.",
                         "Declined to guess, said what is missing, and logged the gap."])

    def _clarify_intent(self, interpretation, route: RouteDecision,
                        trace: ReasoningTrace) -> AssistantAnswer:
        options = [{"id": a.name, "label": _sentence_case(a.description), "detail": ""}
                   for a in route.alternatives]
        conclusion = Conclusion(kind="clarification", options=options,
                                prompt="I want to be sure I answer the right question. "
                                       "Did you mean one of these?")
        trace.reasoning_steps = [
            "The question is related to what the company knowledge covers, but no single goal "
            "is a confident match.",
            "Asked which one was meant rather than reasoning about the wrong question.",
        ]
        trace.knowledge_gap = gaps.unclear_request(interpretation, route.alternatives)
        return AssistantAnswer(
            answer=realize(conclusion), pipeline=route.pipeline.value,
            pipeline_label=route.pipeline.label, outcome="clarification", confidence=0.0,
            clarification={"question": conclusion.prompt, "options": options},
            knowledge_gap=trace.knowledge_gap,
            recommended_action="Choose the question you meant.",
            explanation=["Understood the topic, but not which question was meant.",
                         "Asked for clarification instead of guessing."])

    # =======================================================================
    # Pipeline A over records: lookup
    # =======================================================================
    def _lookup(self, interpretation, route, req: _Request, context, trace) -> AssistantAnswer:
        goal = route.goal
        lookup = self.program.lookups[goal.name]
        grounding = req.grounder.ground(interpretation, goal.name, goal.subject_type, req.actor,
                                        context)
        trace.grounding = grounding.as_dict()
        early = self._grounding_outcome(grounding, route, trace, goal.subject_type)
        if early is not None:
            return early

        subject = grounding.subjects[0]
        view = req.view
        facts = []
        for predicate in lookup.predicates:
            values = view.values(subject, predicate)
            if values:
                facts.append((predicate, format_value(values[0], self.program.value_type(
                    predicate), graph=view)))
        detail = ", ".join(f"{humanize(p)} {v}" for p, v in facts)
        owner = "the customer's" if req.staff else "your"
        conclusion = Conclusion(
            kind="lookup", subject=view.display_name(subject),
            headline=f"{owner} {view.display_name(subject)} ({subject}): {detail}",
            notes=grounding.assumptions)
        text = realize(conclusion)
        report = run_checks(req.verifier.grounding(text, conclusion))
        trace.verification = report.as_dict()
        trace.symbolic_facts = [{"subject": subject, "predicate": p, "value": v,
                                 "source": "record"} for p, v in facts]
        trace.reasoning_steps = [
            f"Resolved the question to {subject} ({view.display_name(subject)}).",
            f"Read {', '.join(lookup.predicates)} from the knowledge graph.",
        ]
        confidence = round(0.5 + 0.5 * _clamp((route.goal_score - 0.45) / 0.35), 2)
        trace.confidence = confidence
        return AssistantAnswer(
            answer=text, pipeline=route.pipeline.value, pipeline_label=route.pipeline.label,
            outcome="answered", confidence=confidence, goal=goal.name, subject=subject,
            decision_label="Record status", decision_kind="record",
            verification=_verification(report),
            facts=trace.symbolic_facts,
            sources=[{"type": "record", "id": subject, "title": view.display_name(subject)}],
            context=self._context(context, goal.subject_type, subject),
            explanation=[f"Understood the question as {lookup.description}.",
                         f"Identified the {view.display_name(subject)} ({subject}).",
                         "Read its current status from the company records.",
                         "No policy needed evaluating, so no rules were run."])

    # =======================================================================
    # Pipelines B, C, D: symbolic reasoning
    # =======================================================================
    def _reason(self, interpretation, route, req: _Request, context, trace) -> AssistantAnswer:
        goal = route.goal
        program, view = self.program, req.view
        grounding = req.grounder.ground(interpretation, goal.name, goal.subject_type, req.actor,
                                        context)
        early = self._grounding_outcome(grounding, route, trace, goal.subject_type)
        if early is not None:
            trace.grounding = grounding.as_dict()
            return early

        if goal.goal_kind == "enumeration":
            trace.grounding = grounding.as_dict()
            return self._enumerate(route, grounding, trace, req)

        subject = grounding.subjects[0]
        subject_type = goal.subject_type
        needed = program.input_predicates_for(goal.name)

        # -- the neural-symbolic loop -------------------------------------------
        reasoner = Reasoner(program, view, stated=grounding.stated,
                            open_subjects=grounding.open_subjects)
        result = reasoner.evaluate(subject, goal.name)
        asked: set[str] = set()
        for iteration in range(1, MAX_LOOP_ITERATIONS + 1):
            wanted = {m.predicate for m in result.missing
                      if m.subject == subject and program.is_input(m.predicate)}
            wanted -= asked | {p for (s, p) in grounding.stated if s == subject}
            if result.status not in (CONDITIONAL, "unknown") or not wanted:
                break
            asked |= wanted
            found = self.understanding.detect_inputs(interpretation, wanted,
                                                          TARGETED_INPUT_THRESHOLD)
            accepted = [f for f in found if req.grounder.accept_statement(
                grounding, f, subject, subject_type, needed, reasoner)]
            trace.neural_symbolic_loop.append({
                "iteration": iteration,
                "symbolic_needed": sorted(wanted),
                "neural_found": [f.as_dict() for f in found],
                "accepted": [f.predicate for f in accepted],
            })
            if not accepted:
                break
            reasoner = Reasoner(program, view, stated=grounding.stated,
                                open_subjects=grounding.open_subjects)
            result = reasoner.evaluate(subject, goal.name)

        # -- a question the rules decide, but not for the case described -----------------
        if result.status in (CONDITIONAL, "unknown"):
            gap = gaps.undefined_case(interpretation, goal, result, self.pack)
            if gap is not None:
                trace.grounding = grounding.as_dict()
                return self._undefined_case(gap, goal, route, trace, req)

        cases = []
        if grounding.hypothetical and result.status in (CONDITIONAL, "unknown"):
            cases = self._split_cases(goal.name, subject, grounding, view)

        # -- follow-up goals that qualify the recommendation ------------------------------
        followups: list[tuple[str, GoalResult]] = []
        if not cases and result.status in ("determined", "default"):
            for name in program.followups.get(goal.name, []):
                extra = reasoner.evaluate(subject, name)
                if extra.status == "determined" and extra.decided_by is not None and \
                        extra.value not in (False, None, 0):
                    followups.append((name, extra))

        # -- claims about the policy, checked against the parameters the proof read -------
        resolutions = _resolutions(reasoner, cases)
        touched = list(reasoner.touched.values()) + [
            ref for case in cases for ref in case["reasoner"].touched.values()]
        claims = check_policy_claims(interpretation, grounding, touched, self.pack, view,
                                     resolutions)
        grounding.rejected.extend(c for c in claims if not c["confirmed"])
        trace.grounding = grounding.as_dict()
        trace.claims = claims

        # -- conclusion and citations -------------------------------------------------------
        used = result.rules_used()
        for _, extra in followups:
            used += [o for o in extra.rules_used() if o.spec.code not in {u.spec.code for u in used}]
        considered = result.refutations()
        # The sources are the clauses that decided the answer. Rules that were refuted
        # explain a refusal, so their clauses are cited only when the answer is one -
        # otherwise a successful return lists the damaged-goods exception it never used.
        refused = result.status in ("default", "absent") or bool(cases)
        codes = sorted({c for o in used + (considered if refused else []) for c in o.spec.clauses})
        cited = self._cite(codes, resolutions)

        audience = _Audience(req, subject_type)
        consistency = req.verifier.record_consistency(goal.name, subject, result)
        conclusion = self._conclusion(goal, subject, result, grounding, cases, consistency,
                                      followups, audience, view, resolutions)
        draft = realize(conclusion)
        text, rephrased = self.realizer.realize(conclusion)

        expected, opposite, value_text = self._agreement_targets(goal, result, audience, subject,
                                                                 grounding, view)

        def verify(reply: str, model_phrased: bool) -> VerificationReport:
            return run_checks(
                req.verifier.fact_validity(reasoner),
                req.verifier.authorization(reasoner),
                req.verifier.policy_applicability(cited, resolutions),
                req.verifier.policy_version(resolutions),
                req.verifier.provenance(goal.name, result, conclusion, cases),
                req.verifier.calculation(goal.name, subject, result, grounding.stated,
                                         grounding.open_subjects) if not cases else None,
                req.verifier.grounding(reply, conclusion),
                consistency,
                req.verifier.contradictions(result, grounding, cases),
                req.verifier.agreement(reply, conclusion, expected, opposite, value_text),
                req.verifier.unsupported_claims(reply, draft, model_phrased),
            )

        report = verify(text, rephrased)
        if not report.passed and rephrased:
            # The model's phrasing is not released; the deterministic one is re-verified.
            text, rephrased = draft, False
            report = verify(text, False)

        # -- trace, explanation, confidence ----------------------------------------
        trace.verification = report.as_dict()
        trace.policy_versions = resolutions
        trace.symbolic_facts = [
            {"subject": ref.subject, "predicate": ref.predicate,
             "value": format_value(ref.value, program.value_type(ref.predicate), graph=view),
             "source": ref.source, "rule": ref.rule}
            for ref in reasoner.touched.values() if ref.source in ("record", "stated", "derived")
        ]
        trace.rules_used = [o.as_dict(program, view) for o in used]
        trace.rules_considered = [o.as_dict(program, view) for o in considered]
        trace.retrieved_knowledge = [{**c, "linked_by": "rule citation"} for c in cited] \
            or trace.retrieved_knowledge
        trace.followups = [{"goal": name, "value": _plain(extra.value),
                            "rule": extra.decided_by.spec.code} for name, extra in followups]
        trace.reasoning_steps = self._steps(goal, subject, result, grounding, used, considered,
                                            cases, report, resolutions)

        if not report.passed and not ablations.active("no_verification"):
            # Not released: a decision that fails verification is not a decision.
            return self._withheld(report, goal, route, trace, grounding, cited, resolutions)

        confidence, factors = self._confidence(route, interpretation, grounding, result,
                                               report, cases)
        trace.confidence, trace.confidence_factors = confidence, factors
        if result.status in ("default", "absent") and not cases:
            trace.knowledge_gap = gaps.missing_parameter(result, self.pack, view)

        outcome = "conditional" if (result.status in (CONDITIONAL, "unknown") or cases) \
            else "answered"
        value_type = program.value_type(goal.name)
        return AssistantAnswer(
            answer=text, pipeline=route.pipeline.value, pipeline_label=route.pipeline.label,
            outcome=outcome, confidence=confidence, goal=goal.name,
            decision=_plain(result.value) if result.status in ("determined", "default") else None,
            value=None if cases else _plain(result.value),
            subject=None if grounding.hypothetical else subject,
            sources=cited + ([] if grounding.hypothetical else
                             [{"type": "record", "id": subject,
                               "title": view.display_name(subject)}]),
            explanation=self._explanation(goal, subject, result, grounding, used, considered,
                                          cases, report, route, view),
            context=self._context(context, subject_type,
                                  None if grounding.hypothetical else subject),
            decision_label=_decision_label(outcome, goal, result, value_type, view, program),
            decision_kind=_decision_kind(outcome, goal),
            recommended_action=self._action(goal, result, cases, followups, conclusion,
                                            audience, view),
            verification=_verification(report),
            policy_versions=resolutions,
            rules_applied=[_rule_summary(o, program, view) for o in used],
            facts=trace.symbolic_facts,
            knowledge_gap=trace.knowledge_gap,
            claims=claims)

    # -- a case the rules don't define -----------------------------------------------
    def _undefined_case(self, gap, goal, route, trace, req) -> AssistantAnswer:
        conclusion = Conclusion(
            kind="insufficient",
            headline=f"I understand this is about {goal.description}, but the company "
                     f"knowledge doesn't define it for this case",
            reasons=[gap["detail"],
                     f"Nothing says what applies to: \"{gap['case']}\", so I won't guess"])
        text = realize(conclusion)
        trace.knowledge_gap = gap
        trace.reasoning_steps = [
            f"Goal: {goal.name} — {goal.description}.",
            f"Every rule for {goal.name} depends on {gap['input']}, and the description matches "
            f"none of its defined values (best similarity {gap['similarity']:.2f}).",
            f"Recorded a knowledge gap: {gap['summary']}.",
        ]
        trace.verification = run_checks(req.verifier.grounding(text, conclusion)).as_dict()
        return AssistantAnswer(
            answer=text, pipeline=Pipeline.INSUFFICIENT.value,
            pipeline_label=Pipeline.INSUFFICIENT.label, outcome="insufficient",
            confidence=0.0, goal=goal.name, knowledge_gap=gap,
            decision_label="Not defined", decision_kind="not_covered",
            recommended_action=_gap_action(req),
            sources=[_clause_source(self.pack.policies.clause(c)) for c in gap["clauses"]
                     if self.pack.policies.clause(c)],
            explanation=[f"Understood the question as {goal.description}.",
                         f"Found the rules that decide it ({', '.join(gap['clauses'])}).",
                         "None of them covers the case described, so no answer was derived.",
                         "Logged the gap for the policy owner."])

    # -- a derived answer that failed verification ------------------------------------------
    def _withheld(self, report, goal, route, trace, grounding, cited, resolutions):
        failures = report.failures
        gap = gaps.verification_failure(failures)
        trace.knowledge_gap = gap
        conclusion = Conclusion(
            kind="insufficient",
            headline="I can't release a decision for this case because it didn't pass "
                     "verification",
            reasons=["Failed check" + ("s" if len(failures) > 1 else "") + ": "
                     + ", ".join(c.name for c in failures)])
        return AssistantAnswer(
            answer=realize(conclusion), pipeline=route.pipeline.value,
            pipeline_label=route.pipeline.label, outcome="needs_review", confidence=0.0,
            goal=goal.name, knowledge_gap=gap, sources=cited, policy_versions=resolutions,
            decision_label="Needs review", decision_kind="needs_review",
            recommended_action="Escalate to a team lead for manual review; do not act on an "
                               "unverified decision.",
            verification=_verification(report),
            explanation=[f"Understood the question as {goal.description}.",
                         "Derived an answer, then verified it.",
                         "Verification failed, so the answer was withheld: "
                         + "; ".join(f"{c.name} — {c.detail}" for c in failures) + "."])

    # -- enumeration -------------------------------------------------------------
    def _enumerate(self, route, grounding: Grounding, trace, req) -> AssistantAnswer:
        goal = route.goal
        view = req.view
        reasoner = Reasoner(self.program, view)
        qualifying: list[str] = []
        by_code: dict = {}
        for subject in grounding.subjects:
            result = reasoner.evaluate(subject, goal.name)
            if result.status == "determined" and result.value is True:
                qualifying.append(view.display_name(subject))
                if result.decided_by:
                    by_code[result.decided_by.spec.code] = result.decided_by.spec
        rules = list(by_code.values())
        cited = self._cite(sorted({c for spec in rules for c in spec.clauses}), [])
        conclusion = Conclusion(
            kind="enumeration", items=qualifying,
            headline=(f"{len(qualifying)} of {len(grounding.subjects)} "
                      f"{goal.subject_type}s qualify" if qualifying
                      else f"none of the {goal.subject_type}s qualify"),
            reasons=[f"Checked against rule {', '.join(sorted(s.code for s in rules))} "
                     f"(policy {', '.join(c['id'] for c in cited)})"] if rules else [])
        text = realize(conclusion)
        report = run_checks(req.verifier.citations([c["id"] for c in cited]),
                            req.verifier.policy_applicability(cited, []),
                            req.verifier.authorization(reasoner),
                            req.verifier.grounding(text, conclusion))
        trace.verification = report.as_dict()
        trace.rules_used = [{"rule": s.code, "name": s.rule.name, "clauses": s.clauses}
                            for s in rules]
        trace.reasoning_steps = [
            f"Evaluated {goal.name} for all {len(grounding.subjects)} {goal.subject_type}s in "
            f"the knowledge graph.",
            f"{len(qualifying)} satisfied the rule.",
        ]
        confidence = round(0.55 + 0.45 * _clamp((route.goal_score - 0.45) / 0.35), 2)
        trace.confidence = confidence
        return AssistantAnswer(
            answer=text, pipeline=route.pipeline.value, pipeline_label=route.pipeline.label,
            outcome="answered", confidence=confidence, goal=goal.name, decision=qualifying,
            sources=cited, decision_label=f"{len(qualifying)} qualify", decision_kind="list",
            verification=_verification(report),
            rules_applied=[{"rule": s.code, "name": s.rule.name, "clauses": s.clauses,
                            "because": s.because, "formula": ""} for s in rules],
            explanation=[f"Understood the question as {goal.description}.",
                         f"Evaluated the rule for every {goal.subject_type} rather than "
                         f"searching for examples.",
                         f"{len(qualifying)} qualified."])

    # -- cases --------------------------------------------------------------------
    def _split_cases(self, goal: str, subject: str, grounding: Grounding, view) -> list[dict]:
        """A hypothetical that turns on an unknown category is answered per category."""
        program = self.program
        probe = Reasoner(program, view, stated=grounding.stated,
                         open_subjects=grounding.open_subjects)
        options = []
        subject_type = program.goals[goal].subject_type
        for predicate in sorted(program.dependencies(goal)):
            attribute = program.attributes.get(predicate)
            if attribute is None or attribute.value_type in NOT_SPLIT_TYPES or \
                    attribute.value_type not in program.entity_types:
                continue
            # Only an attribute of the case itself can be assumed for it. Assuming a
            # product's category on an order changes nothing the rules read.
            if attribute.subject_type != subject_type:
                continue
            instances = view.entities_of_type(attribute.value_type)
            if not 1 < len(instances) <= MAX_CASES:
                continue
            if probe.evaluate(subject, predicate).status in (CONDITIONAL, "unknown"):
                options.append((len(instances), predicate, instances))
        if not options:
            return []
        _, predicate, instances = min(options, key=lambda o: o[0])
        cases = []
        for instance in instances:
            stated = dict(grounding.stated)
            stated[(subject, predicate)] = instance.id
            case_reasoner = Reasoner(program, view, stated=stated,
                                     open_subjects=grounding.open_subjects)
            result = case_reasoner.evaluate(subject, goal)
            cases.append({"predicate": predicate, "value": instance.id,
                          "label": instance.name, "result": result, "reasoner": case_reasoner})
        return cases

    # -- citations -------------------------------------------------------------------
    def _cite(self, codes: list[str], resolutions: list[dict]) -> list[dict]:
        """Each cited clause in the version that governed the case."""
        by_policy = {r["policy"]: r for r in resolutions}
        sources = []
        for code in codes:
            current = self.pack.policies.clause(code)
            if current is None:
                continue
            resolution = by_policy.get(current.policy)
            clause = current
            if resolution is not None and resolution.get("number") is not None:
                clause = self.pack.policies.clause(code, int(resolution["number"])) or current
            sources.append(_clause_source(clause))
        return sources

    # -- conclusion ---------------------------------------------------------------
    def _conclusion(self, goal, subject, result: GoalResult, grounding: Grounding, cases,
                    consistency, followups, audience: "_Audience", view,
                    resolutions: list[dict]) -> Conclusion:
        program = self.program
        value_type = program.value_type(goal.name)
        subject_label = SUBJECT_NOUNS.get(goal.subject_type, goal.subject_type) \
            if grounding.hypothetical else view.display_name(subject)

        def headline_for(name: str, value: Any) -> str:
            return self._headline(name, value, subject_label, audience, view)

        c = Conclusion(kind=goal.goal_kind, status=result.status, subject=subject_label)
        c.corrections = [_correction(r, program) for r in grounding.rejected]
        c.notes = list(grounding.assumptions)
        relied = [a for a in grounding.accepted if a.get("status") == "stated by you"]
        if relied and not grounding.hypothetical:
            c.notes.append(("This relies on what the customer said: " if audience.staff
                            else "This relies on what you told me: ") + ", ".join(
                f"{humanize(a['predicate'])} = {a['value']}" for a in relied))
        if consistency is not None and not consistency.passed:
            c.notes.append("Note: the stored record disagrees with what the policy gives "
                           "for this case — " + consistency.detail)
        for name, extra in followups:
            because = extra.decided_by.because if extra.decided_by else ""
            c.notes.append(headline_for(name, extra.value) + (f": {because}" if because else ""))
        for r in resolutions:
            # Worth saying only when the policy has changed: which version decided it.
            if not r["assumed"] and len(r["versions_available"]) > 1:
                c.notes.append(f"Applied {r['name']}, in force on {r['reference_date']}")

        if cases:
            c.status = CONDITIONAL
            predicate = cases[0]["predicate"]
            c.headline = (f"it depends on the {humanize(predicate)}, which I don't know for "
                          f"this {subject_label}")
            for case in cases:
                outcome = case["result"]
                if outcome.status in ("determined", "default"):
                    reason = outcome.decided_by.because if outcome.decided_by else \
                        _first_failure(outcome)
                    verdict = _verdict(outcome.value, value_type, view, program, goal.name)
                    c.cases.append((f"it is {case['label']}",
                                    verdict + (f" — {reason}" if reason else "")))
                else:
                    c.cases.append((f"it is {case['label']}", "it depends on further details"))
            c.notes.append(f"Tell me which {subject_label} it is and I can check it exactly")
            return c

        if result.status in ("determined", "default"):
            c.decision = result.value if isinstance(result.value, bool) else None
            c.value = None if isinstance(result.value, bool) else \
                _verdict(result.value, value_type, view, program, goal.name)
            c.headline = headline_for(goal.name, result.value)
            if result.decided_by is not None:
                c.reasons = [result.decided_by.because] if result.decided_by.because else []
            else:
                c.reasons = _failures(result)[:2]
            return c

        if result.status == CONDITIONAL:
            outcomes = result.conditionals()
            distinct = {repr(o.value) for o in outcomes}
            if len(outcomes) > 1 and len(distinct) > 1:
                common = set.intersection(*[set(_assumptions(o)) for o in outcomes])
                c.headline = "the answer depends on details I don't have yet"
                for outcome in sorted(outcomes, key=lambda o: -o.priority):
                    differing = [a for a in _assumptions(outcome) if a not in common]
                    c.cases.append((_join_plain(differing) or outcome.spec.rule.name,
                                    _verdict(outcome.value, value_type, view, program,
                                             goal.name)))
                c.conditions = sorted(common)
            else:
                lead = result.decided_by
                c.headline = headline_for(goal.name, result.value)
                # Not the rule's `because`: it states every condition as met, and here
                # some of them are only assumed. The requirements are the honest reason.
                c.reasons = []
                c.conditions = _assumptions(lead) if lead else []
                # When the conditional path is an exception, what happens without it is
                # half the answer: "only if it arrived damaged - grocery items can't be
                # returned for change of mind" rather than a bare "provisionally yes".
                default = program.defaults.get(goal.name)
                failures = _failures(result)
                if failures and default is not None and \
                        repr(default.value) != repr(result.value):
                    c.notes.insert(0, f"Otherwise the answer is "
                                      f"{_verdict(default.value, value_type, view, program, goal.name)}: "
                                      f"{failures[0]}")
            return c

        if result.status == "absent" and result.refutations():
            # Every rule that could produce the result was refuted: the answer is that
            # nothing applies, and the refutations say why.
            c.status = "determined"
            c.headline = f"that doesn't apply to {audience.possessive} {subject_label}"
            c.reasons = _failures(result)[:2]
            return c

        c.status = "unknown"
        c.headline = f"I can't determine {goal.description} yet"
        c.missing = [_describe_input(program, m.predicate) for m in result.missing
                     if program.is_input(m.predicate)]
        if not c.missing:
            c.reasons = ["the company records don't hold what this depends on"]
        return c

    def _headline(self, name: str, value: Any, subject_label: str, audience: "_Audience",
                  view) -> str:
        program = self.program
        phrases = program.phrases.get(name, {})
        value_type = program.value_type(name)
        goal = program.goals.get(name)
        description = goal.description if goal else humanize(name)
        shown = _verdict(value, value_type, view, program, name)
        if isinstance(value, bool):
            template = phrases.get("yes" if value else "no")
            fallback = f"{description}: {'yes' if value else 'no'}"
        else:
            template = phrases.get("value")
            fallback = f"{description}: {shown}"
        if not template:
            return fallback
        return audience.adapt(template).replace("{subject}", subject_label).replace(
            "{value}", shown)

    def _agreement_targets(self, goal, result, audience, subject, grounding, view):
        if result.status not in ("determined", "default"):
            return "", None, None
        label = SUBJECT_NOUNS.get(goal.subject_type, goal.subject_type) \
            if grounding.hypothetical else view.display_name(subject)
        expected = self._headline(goal.name, result.value, label, audience, view)
        if isinstance(result.value, bool):
            return expected, self._headline(goal.name, not result.value, label, audience,
                                             view), None
        return expected, None, _verdict(result.value, self.program.value_type(goal.name), view,
                                        self.program, goal.name)

    # -- recommended action --------------------------------------------------------
    def _action(self, goal, result, cases, followups, conclusion, audience, view) -> str | None:
        program = self.program
        if cases or result.status in (CONDITIONAL, "unknown"):
            needed = conclusion.conditions or conclusion.missing or \
                [a for a, _ in conclusion.cases][:3]
            if not needed:
                return None
            who = "Confirm with the customer" if audience.staff else "Tell me"
            return f"{who} whether {_join_plain(needed[:3])}, to get a definite decision."
        steps = []
        main = _action_text(program, goal.name, result.value, view)
        if main:
            steps.append(main)
        # A follow-up that gates the remedy (an approval) comes before it.
        for name, extra in followups:
            extra_text = _action_text(program, name, extra.value, view)
            if extra_text:
                steps.insert(0, extra_text)
        return " ".join(steps) or None

    # -- explanation ----------------------------------------------------------------
    def _steps(self, goal, subject, result, grounding, used, considered, cases, report,
               resolutions):
        steps = [f"Goal: {goal.name}({subject}) — {goal.description}."]
        if grounding.hypothetical:
            steps.append("No record was named; evaluated an open-world case"
                         + (" linked to the person asking." if any(
                             p in ("placed_by", "claimant") for (_, p) in grounding.stated)
                            else "."))
        for resolution in resolutions:
            steps.append(f"Resolved the {resolution['policy']} policy version: "
                         f"{resolution['name']} — {resolution['basis']}.")
        for rejected in grounding.rejected:
            steps.append(f"Rejected the {rejected.get('kind', 'statement')} "
                         f"{rejected['predicate']} = {rejected['stated']}: "
                         f"{rejected.get('reason') or 'the record says ' + str(rejected['record'])}.")
        for outcome in used:
            steps.append(f"Rule {outcome.spec.code} ({outcome.spec.rule.name}) {outcome.status}"
                         + (f": {outcome.because}" if outcome.because else "."))
        for outcome in considered:
            if outcome.failed is not None:
                steps.append(f"Rule {outcome.spec.code} ({outcome.spec.rule.name}) refuted at "
                             f"`{outcome.failed.expression}`"
                             + (f": {outcome.failed.note}" if outcome.failed.note else "."))
        if cases:
            steps.append(f"Split into {len(cases)} cases on the unknown "
                         f"{humanize(cases[0]['predicate'])}.")
        steps.append(f"Result: {result.status}" + (
            f", value {format_value(result.value, self.program.value_type(goal.name), graph=self.graph)}"
            if result.value is not None else "") + ".")
        steps.append("Verification " + ("passed" if report.passed else "found problems") + ": "
                     + "; ".join(c.detail for c in report.checks) + ".")
        return steps

    def _explanation(self, goal, subject, result, grounding, used, considered, cases, report,
                     route, view) -> list[str]:
        lines = [f"Understood the question as {goal.description}."]
        if grounding.hypothetical:
            lines.append("No specific record was named, so I treated it as a general case "
                         "using what was described.")
        else:
            lines.append(f"Identified the {view.display_name(subject)} ({subject}).")
        refused = result.status in ("default", "absent") or bool(cases)
        clauses = sorted({c for o in used + (considered if refused else [])
                          for c in o.spec.clauses})
        if clauses:
            titles = [f"{c} {self.pack.policies.clause(c).title}" for c in clauses
                      if self.pack.policies.clause(c)]
            lines.append("Retrieved the policy: " + "; ".join(titles) + ".")
        facts = [f"{humanize(a['predicate'])} = {a['value']} (as stated)"
                 for a in grounding.accepted if a.get("status") == "stated by you"]
        if facts:
            lines.append("Took these details from the question: " + ", ".join(facts) + ".")
        if grounding.rejected:
            lines.append("Set aside a statement that the company records or policy contradict.")
        if used:
            lines.append("Applied the rules: " + ", ".join(
                f"{o.spec.code} ({o.spec.rule.name})" for o in used) + ".")
        failed = [o for o in considered if o.failed is not None and o.failed.note]
        if failed and result.status == "default":
            lines.append("Found the condition that decided it: " + failed[0].failed.note + ".")
        if cases:
            lines.append(f"The result depends on the {humanize(cases[0]['predicate'])}, so I "
                         f"evaluated each possibility.")
        lines.append("Verified the result: " + ("all checks passed." if report.passed else
                                                 "a check failed — see the trace."))
        lines.append(f"Chose {route.pipeline.label}: {route.reason.split('. ')[0]}.")
        return lines

    # -- confidence -------------------------------------------------------------
    def _confidence(self, route, interpretation, grounding, result, report, cases):
        understanding = _clamp((route.goal_score - 0.45) / 0.35)
        semantic_inputs = [a.get("confidence", 1.0) for a in grounding.accepted
                           if a.get("status") == "stated by you"]
        inputs = _clamp((min(semantic_inputs) - 0.55) / 0.35) if semantic_inputs else 1.0
        symbolic = {"determined": 1.0, "default": 0.95, CONDITIONAL: 0.7, "unknown": 0.3,
                    "absent": 0.3, "conflict": 0.2}.get(result.status, 0.3)
        if cases:
            symbolic = 0.65
        support = 0.85 if grounding.hypothetical else (0.92 if semantic_inputs else 1.0)
        verified = 1.0 if report.passed else 0.4
        factors = {"understanding": round(understanding, 3), "stated_inputs": round(inputs, 3),
                   "symbolic_determinacy": symbolic, "fact_support": support,
                   "verification": verified}
        score = (0.25 * understanding + 0.10 * inputs + 0.35 * symbolic + 0.15 * support
                 + 0.15 * verified)
        if not interpretation.semantic:
            score *= 0.8
        return round(_clamp(score), 2), factors

    # -- grounding outcomes ---------------------------------------------------------
    def _grounding_outcome(self, grounding: Grounding, route, trace, subject_type):
        if grounding.denied:
            conclusion = Conclusion(kind="denied", headline=grounding.denied)
            trace.reasoning_steps = ["The referenced record is outside the person's access; "
                                     "access was refused before any reasoning."]
            return AssistantAnswer(answer=realize(conclusion), pipeline=Pipeline.INSUFFICIENT.value,
                                   pipeline_label="Access refused", outcome="denied",
                                   confidence=0.0, decision_label="Access refused",
                                   decision_kind="access_refused",
                                   explanation=["Checked who may see the record.",
                                                "It isn't accessible to you, so nothing about it "
                                                "was used."])
        if grounding.clarification:
            clar = grounding.clarification
            conclusion = Conclusion(kind="clarification", prompt=clar.question,
                                    options=clar.options)
            trace.selected_pipeline = Pipeline.CLARIFICATION.value
            trace.pipeline_label = Pipeline.CLARIFICATION.label
            trace.pipeline_reason = (
                f"The goal is clear, but it could apply to {len(clar.options)} "
                f"{subject_type}s, and they would get different answers. Asking is more "
                f"reliable than choosing one.")
            trace.reasoning_steps = [trace.pipeline_reason]
            return AssistantAnswer(
                answer=realize(conclusion), pipeline=Pipeline.CLARIFICATION.value,
                pipeline_label=Pipeline.CLARIFICATION.label, outcome="clarification",
                confidence=0.0, goal=route.goal.name if route.goal else None,
                clarification={"question": clar.question, "options": clar.options},
                decision_label="Clarification needed", decision_kind="clarification",
                recommended_action=f"Choose which {subject_type} this is about.",
                explanation=[f"Understood the question as {route.goal.description}.",
                             f"Found {len(clar.options)} {subject_type}s it could be about.",
                             "Asked which one, because the answer differs between them."])
        if grounding.missing_subject:
            conclusion = Conclusion(kind="insufficient", headline=grounding.missing_subject)
            trace.reasoning_steps = [grounding.missing_subject]
            return AssistantAnswer(
                answer=realize(conclusion), pipeline=Pipeline.INSUFFICIENT.value,
                pipeline_label=Pipeline.INSUFFICIENT.label, outcome="insufficient",
                confidence=0.0, goal=route.goal.name if route.goal else None,
                decision_label="Record not found", decision_kind="needs_information",
                recommended_action=f"Share the {subject_type} number.",
                explanation=[f"Understood the question as {route.goal.description}.",
                             "Could not find the record it depends on.",
                             "Said what is missing instead of guessing."])
        return None

    def _context(self, context, subject_type, subject) -> dict:
        merged = dict(context or {})
        subjects = dict(merged.get("subjects", {}))
        if subject:
            subjects[subject_type] = subject
        merged["subjects"] = subjects
        return merged

    def _finish(self, result: AssistantAnswer, trace: ReasoningTrace) -> None:
        if not result.verification:
            result.verification = {"status": "not_applicable", "passed": None,
                                   "checks": (trace.verification or {}).get("checks", [])}
        if result.knowledge_gap is None and trace.knowledge_gap:
            result.knowledge_gap = trace.knowledge_gap
        trace.decision = {"label": result.decision_label, "kind": result.decision_kind,
                          "recommended_action": result.recommended_action,
                          "outcome": result.outcome}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _Audience:
    """Phrase for the person asking: "your order" to a customer, "the customer's order"
    to a support agent handling it."""

    def __init__(self, req: _Request, subject_type: str) -> None:
        self.staff = req.staff and subject_type in CUSTOMER_RECORD_TYPES
        self.possessive = "the customer's" if self.staff else "your"

    def adapt(self, template: str) -> str:
        if not self.staff:
            return template
        return template.replace("your ", "the customer's ").replace("you're ", "the customer is ")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _plain(value: Any) -> Any:
    if isinstance(value, Symbolic):
        return None
    return value


def _clause_source(clause) -> dict[str, Any]:
    return {"type": "policy", "id": clause.id, "title": clause.title,
            "document": clause.document, "text": clause.text, "policy": clause.policy,
            "version": clause.version, "effective": clause.effective,
            "in_force": clause.in_force}


def _verification(report: VerificationReport) -> dict[str, Any]:
    return {"status": "passed" if report.passed else "needs_review", "passed": report.passed,
            "checks": [c.as_dict() for c in report.checks]}


def _resolutions(reasoner: Reasoner, cases: list[dict]) -> list[dict]:
    found: dict[tuple[str, str], dict] = dict(reasoner.version_resolutions)
    for case in cases:
        for key, value in case["reasoner"].version_resolutions.items():
            found.setdefault(key, value)
    return list(found.values())


def _gap_action(req: _Request) -> str:
    if req.staff:
        return ("Escalate to a team lead; don't improvise a policy. The gap has been logged for "
                "the knowledge owner.")
    return "Contact customer support for help with this; the gap has been logged."


def _entities(interpretation: Interpretation) -> list[dict]:
    m = interpretation.mentions
    out = [{"type": r.entity_type, "id": r.entity_id, "text": r.text, "method": r.method}
           for r in m.records]
    out += [{"type": "product", "id": p.entity_id, "text": p.text, "method": p.method}
            for p in m.products]
    out += [{"type": "category", "id": c.entity_id, "text": c.text, "method": c.method}
            for c in m.categories]
    out += [{"type": q.kind, "value": q.value, "text": q.text, "method": "pattern"}
            for q in m.quantities]
    return out


def _correction(rejected: dict, program) -> str:
    kind = rejected.get("kind")
    if kind == "policy claim":
        return (f"you said the policy allows {rejected['stated']}, but {rejected['source']} "
                f"sets the {humanize(rejected['predicate'])} at {rejected['record']}, so I've "
                f"applied the policy as written")
    if kind == "unverified claim":
        description = program.inputs[rejected["predicate"]][0].description
        return (f"you mentioned that {description}, but {rejected['reason'].split(', and ')[-1]}, "
                f"so it hasn't been treated as approved")
    return (f"you mentioned {rejected['stated']} for the {humanize(rejected['predicate'])}, but "
            f"the record shows {rejected['record']}, so I've used the record")


def _decision_label(outcome, goal, result, value_type, view, program) -> str:
    if outcome == "conditional":
        return "Depends on details"
    value = result.value
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return _sentence_case(_verdict(value, value_type, view, program, goal.name))


def _decision_kind(outcome, goal) -> str:
    if outcome == "conditional":
        return "needs_information"
    return {"calculation": "amount", "recommendation": "recommendation",
            "enumeration": "list"}.get(goal.goal_kind, "yes_no")


def _action_text(program, goal: str, value: Any, view) -> str | None:
    actions = program.actions.get(goal, {})
    if isinstance(value, bool):
        return actions.get("yes" if value else "no")
    text = actions.get(str(value)) or actions.get("value")
    if text:
        shown = _verdict(value, program.value_type(goal), view, program, goal)
        return text.replace("{value}", shown)
    return None


def _rule_summary(outcome: RuleOutcome, program, graph) -> dict[str, Any]:
    import re

    def plain(expression: str) -> str:
        return re.sub(r"(\w+)\(([^()]*)\)", lambda m: humanize(m.group(1)), expression)

    conditions = [plain(c.expression) for c in outcome.conditions
                  if not c.expression.endswith("= ?")]
    head = outcome.spec.rule.head
    shown = format_value(outcome.value, program.value_type(head.predicate), graph=graph,
                         program=program) if outcome.value is not None else "?"
    formula = " AND ".join(conditions) + f" → {humanize(head.predicate)} = {shown}"
    return {"rule": outcome.spec.code, "name": outcome.spec.rule.name,
            "clauses": outcome.spec.clauses, "status": outcome.status,
            "because": outcome.because, "formula": formula}


def _failures(result: GoalResult) -> list[str]:
    # The most useful "why not" comes from a rule that genuinely applied and failed, not
    # from an exception whose own conditions are unknown: an electronics item past its
    # 10-day window is explained by the window, not by the 7-day rule for damaged
    # goods nobody said it was. So rank by how few conditions were undecided before
    # the failure, then by how far the rule got, then by priority.
    ordered = sorted(result.refutations(), key=lambda o: (
        sum(1 for c in o.conditions if c.status == "unknown"),
        -(o.failed.index if o.failed else 0),
        -o.priority))
    notes: list[str] = []
    for outcome in ordered:
        note = outcome.failed.note if outcome.failed else ""
        deeper = _premise_failure(outcome)
        if note and deeper and deeper not in note:
            # "not eligible for return" says what failed; the premise says why.
            note = f"{note}, because {deeper}"
        if note and note not in notes:
            notes.append(note)
    return notes


def _premise_failure(outcome: RuleOutcome) -> str:
    """When a rule failed on a derived premise, the premise's own reason."""
    failed = outcome.failed
    if failed is None:
        return ""
    for premise in outcome.premises:
        head = f"{premise.predicate}({premise.subject})"
        if failed.expression.startswith(head) and premise.status in ("default", "absent") \
                and premise.refutations():
            inner = _failures(premise)
            return inner[0] if inner else ""
    return ""


def _first_failure(result: GoalResult) -> str:
    failures = _failures(result)
    return failures[0] if failures else ""


def _assumptions(outcome: RuleOutcome | None) -> list[str]:
    if outcome is None:
        return []
    shown = []
    for condition in outcome.assumptions:
        if condition.requirement:
            shown.append(condition.requirement)
            continue
        expression = condition.expression
        if expression.endswith("= ?"):
            continue
        for leaf in condition.missing:
            expression = expression.replace(f"?{leaf.predicate}", humanize(leaf.predicate))
        shown.append(expression)
    for premise in outcome.premises:
        if premise.status == CONDITIONAL and premise.decided_by is not None:
            shown.extend(_assumptions(premise.decided_by))
    return list(dict.fromkeys(shown))


def _join_plain(items: list[str]) -> str:
    return " and ".join(items)


def _verdict(value: Any, value_type: str, graph, program, predicate: str | None = None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if predicate is not None:
        label = program.label(predicate, value)
        if label:
            return label
    return format_value(value, value_type, graph=graph, program=program)


def _describe_input(program, predicate: str) -> str:
    description = program.inputs[predicate][0].description
    return (description.replace("the employee ", "you ").replace("the customer ", "you ")
            .replace(" their ", " your "))
