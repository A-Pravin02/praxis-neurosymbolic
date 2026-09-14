"""HYBRID. The neuro-symbolic controller: one question, end to end.

    question
      -> NEURAL    understand: goal candidates, speech act, entities, stated inputs
      -> HYBRID    route: choose pipeline A-F from the question and the rule base
      -> SYMBOLIC  ground: resolve the record, validate statements against records
      -> SYMBOLIC  reason: three-valued rule evaluation over the knowledge graph
      -> HYBRID    loop: the reasoner names an input it lacks; the neural layer looks
                   again for exactly that input; the reasoner re-evaluates
      -> HYBRID    verify: record consistency, constraints, citations, grounding
      -> NEURAL    generate: phrase the established conclusion
      -> HYBRID    explain: a trace and a plain-language account of how it was decided

Where standard RAG ends and neuro-symbolic reasoning begins
------------------------------------------------------------
Pipeline A *is* retrieval: find the policy clause and quote it. Every other reasoning
pipeline uses retrieval only for citations. The decision - can this order be returned,
how much is this claim worth - is made by rules evaluated on records, and a language
model never sees the question when that decision is made.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.hybrid.grounding import FactGrounding, Grounding
from app.hybrid.knowledge_pack import KnowledgePack, load_pack
from app.hybrid.router import RETRIEVAL_ACCEPT, Pipeline, ReasoningRouter, RouteDecision
from app.hybrid.trace import ReasoningTrace
from app.hybrid.verification import Verifier, run_checks
from app.neural.query_understanding import TARGETED_INPUT_THRESHOLD, Interpretation
from app.neural.response_generation import Conclusion, LanguageModelRealizer, realize
from app.symbolic.reasoner import CONDITIONAL, GoalResult, Reasoner, RuleOutcome, Symbolic
from app.symbolic.render import format_value, humanize

logger = get_logger("praxis.hybrid")

MAX_LOOP_ITERATIONS = 2
MAX_CASES = 8
NOT_SPLIT_TYPES = {"company", "customer", "employee", "grade"}
SUBJECT_NOUNS = {"order": "order", "claim": "trip", "return": "return", "employee": "record"}


@dataclass
class AssistantAnswer:
    answer: str
    pipeline: str
    pipeline_label: str
    outcome: str                  # answered | conditional | clarification | insufficient | denied
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

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class NeuroSymbolicController:
    def __init__(self, pack: KnowledgePack | None = None, *, llm=None) -> None:
        self.pack = pack or load_pack()
        self.program = self.pack.program
        self.graph = self.pack.graph
        self.router = ReasoningRouter(self.pack)
        self.grounder = FactGrounding(self.pack)
        self.verifier = Verifier(self.pack)
        if llm is None:
            from app.neuro.registry import get_llm
            llm = get_llm()
        self.realizer = LanguageModelRealizer(llm)

    # =======================================================================
    def answer(self, question: str, *, actor_email: str, actor_roles: tuple[str, ...] = (),
               context: dict | None = None) -> AssistantAnswer:
        started = time.perf_counter()
        actor = self.grounder.actor(actor_email, tuple(actor_roles))
        interpretation = self.pack.understanding.understand(question)
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
        )

        goal = route.goal
        if route.pipeline is Pipeline.INSUFFICIENT:
            result = self._insufficient(interpretation, route, trace)
        elif route.pipeline is Pipeline.CLARIFICATION and goal is None:
            result = self._clarify_intent(route, trace)
        elif goal is None:
            result = self._retrieval(route, trace)
        elif goal.kind == "lookup":
            result = self._lookup(interpretation, route, actor, context, trace)
        else:
            result = self._reason(interpretation, route, actor, context, trace)

        result.latency_ms = trace.latency_ms = int((time.perf_counter() - started) * 1000)
        result.trace = trace.as_dict()
        return result

    # =======================================================================
    # Pipeline A: knowledge retrieval
    # =======================================================================
    def _retrieval(self, route: RouteDecision, trace: ReasoningTrace) -> AssistantAnswer:
        top = route.clauses[0]
        # An overview question ("what is the leave policy") is answered by every clause
        # on the topic, not only the single closest one.
        chosen = [c for c in route.clauses
                  if c.score >= RETRIEVAL_ACCEPT and c.score >= top.score - 0.08][:3]
        conclusion = Conclusion(kind="retrieval", clauses=[
            {"id": c.clause.id, "title": c.clause.title, "document": c.clause.document,
             "text": c.clause.text} for c in chosen])
        text = realize(conclusion)
        report = run_checks(self.verifier.grounding(text, conclusion))
        trace.verification = report.as_dict()
        trace.reasoning_steps = [
            "Classified the request as a question about what company policy says.",
            f"Retrieved {len(chosen)} policy clause{'s' if len(chosen) != 1 else ''} by "
            f"meaning: " + "; ".join(f"{c.clause.id} {c.clause.title} ({c.score:.2f})"
                                     for c in chosen) + ".",
            "No decision or amount was asked for, so no rule was evaluated.",
        ]
        confidence = _clamp((top.score - 0.45) / 0.35) * (1.0 if report.passed else 0.5)
        trace.confidence = round(confidence, 2)
        trace.confidence_factors = {"retrieval_relevance": round(top.score, 3)}
        return AssistantAnswer(
            answer=text, pipeline=route.pipeline.value, pipeline_label=route.pipeline.label,
            outcome="answered", confidence=round(confidence, 2),
            sources=[{"type": "policy", "id": c.clause.id, "title": c.clause.title,
                      "document": c.clause.document, "text": c.clause.text}
                     for c in chosen],
            explanation=[
                "Understood the question as a request for company information.",
                "Retrieved the matching policy text: "
                + ", ".join(f"{c.clause.id} ({c.clause.title})" for c in chosen) + ".",
                "Answered from that text directly — there was no rule to apply.",
                f"Chose {route.pipeline.label} because no decision about a specific case was "
                f"asked for.",
            ])

    # =======================================================================
    # Pipeline F / E without a goal
    # =======================================================================
    def _insufficient(self, interpretation: Interpretation, route: RouteDecision,
                      trace: ReasoningTrace) -> AssistantAnswer:
        best = route.clauses[0] if route.clauses else None
        reasons = ["Nothing in the company's policies, rules or records covers it, so any "
                   "answer would be invented"]
        if best is not None and best.score >= 0.45:
            reasons.append(f"The closest topic I do have is {best.clause.title} "
                           f"({best.clause.id}), and it doesn't answer this")
        conclusion = Conclusion(kind="insufficient", reasons=reasons,
                                headline=f"I can't answer that from "
                                         f"{self.graph.display_name('meridian')}'s company "
                                         f"knowledge")
        text = realize(conclusion)
        trace.reasoning_steps = [
            "Compared the question with every goal the rule base can decide: none is close.",
            "Searched the policy text: no clause is relevant enough to answer from.",
            "Reported what is missing instead of generating an answer.",
        ]
        trace.verification = run_checks(self.verifier.grounding(text, conclusion)).as_dict()
        return AssistantAnswer(
            answer=text, pipeline=route.pipeline.value, pipeline_label=route.pipeline.label,
            outcome="insufficient", confidence=0.0,
            explanation=["Looked for a matching policy, rule or record.",
                         "Found nothing that covers the question.",
                         "Declined to guess, and said what is missing."])

    def _clarify_intent(self, route: RouteDecision, trace: ReasoningTrace) -> AssistantAnswer:
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
        return AssistantAnswer(
            answer=realize(conclusion), pipeline=route.pipeline.value,
            pipeline_label=route.pipeline.label, outcome="clarification", confidence=0.0,
            clarification={"question": conclusion.prompt, "options": options},
            explanation=["Understood the topic, but not which question was meant.",
                         "Asked for clarification instead of guessing."])

    # =======================================================================
    # Pipeline A over records: lookup
    # =======================================================================
    def _lookup(self, interpretation, route, actor, context, trace) -> AssistantAnswer:
        goal = route.goal
        lookup = self.program.lookups[goal.name]
        grounding = self.grounder.ground(interpretation, goal.name, goal.subject_type, actor,
                                         context)
        trace.grounding = grounding.as_dict()
        early = self._grounding_outcome(grounding, route, trace, goal.subject_type)
        if early is not None:
            return early

        subject = grounding.subjects[0]
        facts = []
        for predicate in lookup.predicates:
            values = self.graph.values(subject, predicate)
            if values:
                facts.append((predicate, format_value(values[0], self.program.value_type(
                    predicate), graph=self.graph)))
        detail = ", ".join(f"{humanize(p)} {v}" for p, v in facts)
        conclusion = Conclusion(
            kind="lookup", subject=self.graph.display_name(subject),
            headline=f"your {self.graph.display_name(subject)} ({subject}): {detail}",
            notes=grounding.assumptions)
        text = realize(conclusion)
        report = run_checks(self.verifier.grounding(text, conclusion))
        trace.verification = report.as_dict()
        trace.symbolic_facts = [{"subject": subject, "predicate": p, "value": v,
                                 "source": "record"} for p, v in facts]
        trace.reasoning_steps = [
            f"Resolved the question to {subject} ({self.graph.display_name(subject)}).",
            f"Read {', '.join(lookup.predicates)} from the knowledge graph.",
        ]
        confidence = round(0.5 + 0.5 * _clamp((route.goal_score - 0.45) / 0.35), 2)
        trace.confidence = confidence
        return AssistantAnswer(
            answer=text, pipeline=route.pipeline.value, pipeline_label=route.pipeline.label,
            outcome="answered", confidence=confidence, goal=goal.name, subject=subject,
            sources=[{"type": "record", "id": subject,
                      "title": self.graph.display_name(subject)}],
            context=self._context(context, goal.subject_type, subject),
            explanation=[f"Understood the question as {lookup.description}.",
                         f"Identified your {self.graph.display_name(subject)} ({subject}).",
                         "Read its current status from the company records.",
                         "No policy needed evaluating, so no rules were run."])

    # =======================================================================
    # Pipelines B, C, D: symbolic reasoning
    # =======================================================================
    def _reason(self, interpretation, route, actor, context, trace) -> AssistantAnswer:
        goal = route.goal
        program, graph = self.program, self.graph
        grounding = self.grounder.ground(interpretation, goal.name, goal.subject_type, actor,
                                         context)
        early = self._grounding_outcome(grounding, route, trace, goal.subject_type)
        if early is not None:
            trace.grounding = grounding.as_dict()
            return early

        if goal.goal_kind == "enumeration":
            trace.grounding = grounding.as_dict()
            return self._enumerate(route, grounding, trace)

        subject = grounding.subjects[0]
        subject_type = goal.subject_type
        needed = program.input_predicates_for(goal.name)

        # -- the neural-symbolic loop -------------------------------------------
        reasoner = Reasoner(program, graph, stated=grounding.stated,
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
            found = self.pack.understanding.detect_inputs(interpretation, wanted,
                                                          TARGETED_INPUT_THRESHOLD)
            accepted = [f for f in found if self.grounder.accept_statement(
                grounding, f, subject, subject_type, needed, reasoner)]
            trace.neural_symbolic_loop.append({
                "iteration": iteration,
                "symbolic_needed": sorted(wanted),
                "neural_found": [f.as_dict() for f in found],
                "accepted": [f.predicate for f in accepted],
            })
            if not accepted:
                break
            reasoner = Reasoner(program, graph, stated=grounding.stated,
                                open_subjects=grounding.open_subjects)
            result = reasoner.evaluate(subject, goal.name)
        trace.grounding = grounding.as_dict()

        cases = []
        if grounding.hypothetical and result.status in (CONDITIONAL, "unknown"):
            cases = self._split_cases(goal.name, subject, grounding)

        # -- verification ---------------------------------------------------------
        used = result.rules_used()
        considered = result.refutations()
        # The sources are the clauses that decided the answer. Rules that were refuted
        # explain a refusal, so their clauses are cited only when the answer is one -
        # otherwise a successful return lists the damaged-goods exception it never used.
        refused = result.status in ("default", "absent") or bool(cases)
        cited = sorted({c for o in used + (considered if refused else []) for c in o.spec.clauses})
        consistency = self.verifier.record_consistency(goal.name, subject, result)
        conclusion = self._conclusion(goal, subject, result, grounding, cases, consistency)
        draft = realize(conclusion)
        text, rephrased = self.realizer.realize(conclusion)
        report = run_checks(consistency, self.verifier.constraints(goal.name, result),
                            self.verifier.citations(cited),
                            self.verifier.grounding(text, conclusion))
        if not report.passed and rephrased:
            text = draft
            report = run_checks(consistency, self.verifier.constraints(goal.name, result),
                                self.verifier.citations(cited),
                                self.verifier.grounding(text, conclusion))

        # -- trace, explanation, confidence ----------------------------------------
        trace.verification = report.as_dict()
        trace.symbolic_facts = [
            {"subject": ref.subject, "predicate": ref.predicate,
             "value": format_value(ref.value, program.value_type(ref.predicate), graph=graph),
             "source": ref.source, "rule": ref.rule}
            for ref in reasoner.touched.values() if ref.source in ("record", "stated", "derived")
        ]
        trace.rules_used = [o.as_dict(program, graph) for o in used]
        trace.rules_considered = [o.as_dict(program, graph) for o in considered]
        trace.retrieved_knowledge = [
            {"id": c, "title": self.pack.policies.clause(c).title,
             "document": self.pack.policies.clause(c).document,
             "text": self.pack.policies.clause(c).text, "linked_by": "rule citation"}
            for c in cited if self.pack.policies.clause(c)] or trace.retrieved_knowledge
        trace.reasoning_steps = self._steps(goal, subject, result, grounding, used, considered,
                                            cases, report)
        confidence, factors = self._confidence(route, interpretation, grounding, result,
                                               report, cases)
        trace.confidence, trace.confidence_factors = confidence, factors

        outcome = "conditional" if (result.status in (CONDITIONAL, "unknown") or cases) \
            else "answered"
        return AssistantAnswer(
            answer=text, pipeline=route.pipeline.value, pipeline_label=route.pipeline.label,
            outcome=outcome, confidence=confidence, goal=goal.name,
            decision=_plain(result.value) if result.status in ("determined", "default") else None,
            value=None if cases else _plain(result.value),
            subject=None if grounding.hypothetical else subject,
            sources=[{"type": "policy", "id": c["id"], "title": c["title"],
                      "document": c["document"], "text": c["text"]}
                     for c in trace.retrieved_knowledge if "linked_by" in c]
            + ([] if grounding.hypothetical else
               [{"type": "record", "id": subject, "title": graph.display_name(subject)}]),
            explanation=self._explanation(goal, subject, result, grounding, used, considered,
                                          cases, report, route),
            context=self._context(context, subject_type,
                                  None if grounding.hypothetical else subject))

    # -- enumeration -------------------------------------------------------------
    def _enumerate(self, route, grounding: Grounding, trace) -> AssistantAnswer:
        goal = route.goal
        reasoner = Reasoner(self.program, self.graph)
        qualifying: list[str] = []
        by_code: dict = {}
        for subject in grounding.subjects:
            result = reasoner.evaluate(subject, goal.name)
            if result.status == "determined" and result.value is True:
                qualifying.append(self.graph.display_name(subject))
                if result.decided_by:
                    by_code[result.decided_by.spec.code] = result.decided_by.spec
        rules = list(by_code.values())
        cited = sorted({c for spec in rules for c in spec.clauses})
        conclusion = Conclusion(
            kind="enumeration", items=qualifying,
            headline=(f"{len(qualifying)} of {len(grounding.subjects)} "
                      f"{goal.subject_type}s qualify" if qualifying
                      else f"none of the {goal.subject_type}s qualify"),
            reasons=[f"Checked against rule {', '.join(sorted(s.code for s in rules))} "
                     f"(policy {', '.join(cited)})"] if rules else [])
        text = realize(conclusion)
        report = run_checks(self.verifier.citations(cited),
                            self.verifier.grounding(text, conclusion))
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
            sources=[{"type": "policy", "id": c, "title": self.pack.policies.clause(c).title,
                      "document": self.pack.policies.clause(c).document,
                      "text": self.pack.policies.clause(c).text}
                     for c in cited if self.pack.policies.clause(c)],
            explanation=[f"Understood the question as {goal.description}.",
                         f"Evaluated the rule for every {goal.subject_type} rather than "
                         f"searching for examples.",
                         f"{len(qualifying)} qualified."])

    # -- cases --------------------------------------------------------------------
    def _split_cases(self, goal: str, subject: str, grounding: Grounding) -> list[dict]:
        """A hypothetical that turns on an unknown category is answered per category."""
        program, graph = self.program, self.graph
        probe = Reasoner(program, graph, stated=grounding.stated,
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
            instances = graph.entities_of_type(attribute.value_type)
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
            result = Reasoner(program, graph, stated=stated,
                              open_subjects=grounding.open_subjects).evaluate(subject, goal)
            cases.append({"predicate": predicate, "value": instance.id,
                          "label": instance.name, "result": result})
        return cases

    # -- conclusion ---------------------------------------------------------------
    def _conclusion(self, goal, subject, result: GoalResult, grounding: Grounding, cases,
                    consistency) -> Conclusion:
        program, graph = self.program, self.graph
        value_type = program.value_type(goal.name)
        subject_label = SUBJECT_NOUNS.get(goal.subject_type, goal.subject_type) \
            if grounding.hypothetical else graph.display_name(subject)
        phrases = program.phrases.get(goal.name, {})
        value_text = format_value(result.value, value_type, graph=graph, program=program)

        def headline_for(value: Any) -> str:
            if isinstance(value, bool):
                template = phrases.get("yes" if value else "no")
                fallback = f"{goal.description}: {'yes' if value else 'no'}"
            else:
                template = phrases.get("value")
                fallback = f"{goal.description}: {format_value(value, value_type, graph=graph)}"
            if not template:
                return fallback
            return template.replace("{subject}", subject_label).replace(
                "{value}", format_value(value, value_type, graph=graph, program=program))

        c = Conclusion(kind=goal.goal_kind, status=result.status, subject=subject_label)
        c.corrections = [f"you mentioned {r['stated']} for the {humanize(r['predicate'])}, but "
                         f"the record shows {r['record']}, so I've used the record"
                         for r in grounding.rejected]
        c.notes = list(grounding.assumptions)
        relied = [a for a in grounding.accepted if a.get("status") == "stated by you"]
        if relied and not grounding.hypothetical:
            c.notes.append("This relies on what you told me: " + ", ".join(
                f"{humanize(a['predicate'])} = {a['value']}" for a in relied))
        if consistency is not None and not consistency.passed:
            c.notes.append("Note: the stored record disagrees with what the policy gives "
                           "for this case — " + consistency.detail)

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
                    verdict = _verdict(outcome.value, value_type, graph, program)
                    c.cases.append((f"it is {case['label']}",
                                    verdict + (f" — {reason}" if reason else "")))
                else:
                    c.cases.append((f"it is {case['label']}", "it depends on further details"))
            c.notes.append(f"Tell me which {subject_label} it is and I can check it exactly")
            return c

        if result.status in ("determined", "default"):
            c.decision = result.value if isinstance(result.value, bool) else None
            c.value = None if isinstance(result.value, bool) else value_text
            c.headline = headline_for(result.value)
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
                    shown = format_value(outcome.value, value_type, graph=graph, program=program)
                    c.cases.append((_join_plain(differing) or outcome.spec.rule.name,
                                    _verdict(outcome.value, value_type, graph, program)
                                    if isinstance(outcome.value, bool) else shown))
                c.conditions = sorted(common)
            else:
                lead = result.decided_by
                c.headline = headline_for(result.value)
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
                                      f"{_verdict(default.value, value_type, graph, program)}: "
                                      f"{failures[0]}")
            return c

        if result.status == "absent" and result.refutations():
            # Every rule that could produce the result was refuted: the answer is that
            # nothing applies, and the refutations say why.
            c.status = "determined"
            c.headline = f"that doesn't apply to your {subject_label}"
            c.reasons = _failures(result)[:2]
            return c

        c.status = "unknown"
        c.headline = f"I can't determine {goal.description} yet"
        c.missing = [_describe_input(program, m.predicate) for m in result.missing
                     if program.is_input(m.predicate)]
        if not c.missing:
            c.reasons = ["the company records don't hold what this depends on"]
        return c

    # -- explanation ----------------------------------------------------------------
    def _steps(self, goal, subject, result, grounding, used, considered, cases, report):
        steps = [f"Goal: {goal.name}({subject}) — {goal.description}."]
        if grounding.hypothetical:
            steps.append("No record was named; evaluated an open-world case linked to the "
                         "person asking.")
        for rejected in grounding.rejected:
            steps.append(f"Rejected the statement {rejected['predicate']} = {rejected['stated']}: "
                         f"the record says {rejected['record']}.")
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
                     route) -> list[str]:
        graph = self.graph
        lines = [f"Understood the question as {goal.description}."]
        if grounding.hypothetical:
            lines.append("No specific record was named, so I treated it as a general case "
                         "using what you said.")
        else:
            lines.append(f"Identified your {graph.display_name(subject)} ({subject}).")
        refused = result.status in ("default", "absent") or bool(cases)
        clauses = sorted({c for o in used + (considered if refused else [])
                          for c in o.spec.clauses})
        if clauses:
            titles = [f"{c} {self.pack.policies.clause(c).title}" for c in clauses
                      if self.pack.policies.clause(c)]
            lines.append("Retrieved the policy: " + "; ".join(titles) + ".")
        facts = [f"{humanize(a['predicate'])} = {a['value']} (you said)"
                 for a in grounding.accepted if a.get("status") == "stated by you"]
        if facts:
            lines.append("Took these details from your question: " + ", ".join(facts) + ".")
        if grounding.rejected:
            lines.append("Set aside a detail that contradicted the company record.")
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
            trace.reasoning_steps = ["The referenced record belongs to another account; "
                                     "access was refused before any reasoning."]
            return AssistantAnswer(answer=realize(conclusion), pipeline=Pipeline.INSUFFICIENT.value,
                                   pipeline_label="Access refused", outcome="denied",
                                   confidence=0.0,
                                   explanation=["Checked who owns the record.",
                                                "It isn't yours, so nothing about it was used."])
        if grounding.clarification:
            clar = grounding.clarification
            conclusion = Conclusion(kind="clarification", prompt=clar.question,
                                    options=clar.options)
            trace.selected_pipeline = Pipeline.CLARIFICATION.value
            trace.pipeline_label = Pipeline.CLARIFICATION.label
            trace.pipeline_reason = (
                f"The goal is clear, but it could apply to {len(clar.options)} of your "
                f"{subject_type}s, and they would get different answers. Asking is more "
                f"reliable than choosing one.")
            trace.reasoning_steps = [trace.pipeline_reason]
            return AssistantAnswer(
                answer=realize(conclusion), pipeline=Pipeline.CLARIFICATION.value,
                pipeline_label=Pipeline.CLARIFICATION.label, outcome="clarification",
                confidence=0.0, goal=route.goal.name if route.goal else None,
                clarification={"question": clar.question, "options": clar.options},
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _plain(value: Any) -> Any:
    if isinstance(value, Symbolic):
        return None
    return value


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
        if note and note not in notes:
            notes.append(note)
    return notes


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


def _verdict(value: Any, value_type: str, graph, program) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return format_value(value, value_type, graph=graph, program=program)


def _describe_input(program, predicate: str) -> str:
    description = program.inputs[predicate][0].description
    return (description.replace("the employee ", "you ").replace("the customer ", "you ")
            .replace(" their ", " your "))
