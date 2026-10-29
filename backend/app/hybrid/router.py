"""HYBRID. The neuro-symbolic reasoning router: which components does this question need?

Neuro-symbolic does not mean every question goes through a rule engine. "What is the
support phone number?" needs a policy clause, not a proof. "How much will I be
reimbursed for a ₹8,750 trip?" must never be answered from text similarity, because a
language model that produces a number is guessing it.

So the router decides, and records why:

    A  Knowledge retrieval   the answer is a policy clause or a stored record
    B  Policy reasoning      a yes/no decision that rules determine
    C  Multi-hop reasoning   an explanation that follows relations to a failed condition
    D  Calculation           an amount computed by rule arithmetic
    E  Clarification         the question cannot be pinned to one goal or one record
    F  Insufficient knowledge the question is about the company, but nothing covers it
    G  General conversation  the question is not about the company at all

The last two are different failures and deserve different answers. "What is the
escalation window for a damaged laptop?" is a company question the knowledge pack does
not cover, and saying so is the honest answer - guessing would invent policy. "Who won
the 2018 World Cup?" is not a company question at all, and answering it from the
company knowledge base is a category error: there is no policy to invent, so the
language model answers it as an ordinary assistant would, labelled as its own knowledge
rather than as anything this company recorded.

The decision uses two neural signals - which goal the question is closest to, and what
kind of answer is wanted - and one symbolic one: the *structure of the rule base* for
that goal. A goal whose rules compute a value needs calculation; a goal concluded by
rules at all needs symbolic evaluation; a goal with no rules is a lookup. The router
reads that from the rules, not from the question's wording.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from app.hybrid import ablations
from app.neural.retrieval import RetrievedClause
from app.symbolic.schema import Expr

if TYPE_CHECKING:  # pragma: no cover
    from app.hybrid.knowledge_pack import KnowledgePack
    from app.neural.intent_detection import IntentCandidate
    from app.neural.query_understanding import Interpretation

# Calibrated on data/meridian/benchmark/dev.yaml; never on the test set.
GOAL_ACCEPT = 0.60        # a goal this close to the question is accepted on its own
GOAL_SUPPORTED = 0.55     # ...or this close, if stated inputs or mentions corroborate it
GOAL_SUGGEST = 0.45       # close enough to offer as "did you mean", not to act on
RETRIEVAL_ACCEPT = 0.55   # a policy clause this relevant answers an informational question
TIE_WINDOW = 0.03         # goals this close are resolved by the rule graph, not by score
KIND_WINDOW = 0.06        # a goal of the requested kind this close to the top is preferred
# A clause the semantic parser selected is used unless the encoder finds it unrelated.
PARSED_CLAUSE_FLOOR = 0.40
CASE_ACTS = {"decide", "calculate", "explain", "locate", "enumerate", "resolve", "compare"}
# Which goal kinds can answer each kind of request.
COMPATIBLE_KINDS = {
    "calculate": {"calculation"},
    "explain": {"explanation", "decision", "calculation"},
    # A yes/no question often wants an amount ("am I owed anything?"), so it overrides
    # nothing a calculation could answer.
    "decide": {"decision", "explanation", "calculation"},
    "enumerate": {"enumeration"},
    "locate": {"lookup"},
    # "What can I offer?" asks for a course of action, which a recommendation goal gives.
    "resolve": {"recommendation", "decision", "calculation", "explanation"},
}
# The goal kind each speech act asks for in the first place; used to break near-ties.
PRIMARY_KIND = {"decide": "decision", "calculate": "calculation", "explain": "explanation",
                "enumerate": "enumeration", "locate": "lookup", "resolve": "recommendation"}
# Within KIND_WINDOW of the top candidate, this goal kind is preferred for the act.
PREFERRED_KIND = {"resolve": "recommendation"}


class Pipeline(str, Enum):
    RETRIEVAL = "A"
    POLICY = "B"
    MULTI_HOP = "C"
    CALCULATION = "D"
    CLARIFICATION = "E"
    INSUFFICIENT = "F"
    GENERAL = "G"

    @property
    def label(self) -> str:
        return {
            "A": "Knowledge retrieval",
            "B": "Policy reasoning",
            "C": "Multi-hop reasoning",
            "D": "Calculation",
            "E": "Clarification",
            "F": "Insufficient knowledge",
            "G": "General conversation",
        }[self.value]


@dataclass
class RouteDecision:
    pipeline: Pipeline
    reason: str
    goal: "IntentCandidate | None" = None
    goal_score: float = 0.0
    symbolic_required: bool = False
    symbolic_reason: str = ""
    neural_reason: str = ""
    required_knowledge: list[str] = field(default_factory=list)
    stages: list[tuple[str, str]] = field(default_factory=list)  # (layer, stage)
    clauses: list[RetrievedClause] = field(default_factory=list)
    alternatives: list["IntentCandidate"] = field(default_factory=list)
    selected_by_parser: bool = False

    def as_dict(self) -> dict:
        return {
            "pipeline": self.pipeline.value, "label": self.pipeline.label,
            "reason": self.reason,
            "goal": self.goal.as_dict() if self.goal else None,
            "goal_score": round(self.goal_score, 4),
            "symbolic_required": self.symbolic_required,
            "symbolic_reason": self.symbolic_reason,
            "neural_reason": self.neural_reason,
            "required_knowledge": self.required_knowledge,
            "stages": [{"layer": layer, "stage": stage} for layer, stage in self.stages],
            "alternatives": [a.as_dict() for a in self.alternatives],
        }


class ReasoningRouter:
    def __init__(self, pack: "KnowledgePack") -> None:
        self.pack = pack
        self.program = pack.program
        self._vocabulary: set[str] | None = None

    # -- public -------------------------------------------------------------
    def route(self, interpretation: "Interpretation") -> RouteDecision:
        act = interpretation.speech_act.label
        neural_reason = (
            f"Understood by meaning with {interpretation.encoder}: the question is closest "
            f"to \"{interpretation.top.description}\" "
            f"(similarity {interpretation.top.score:.2f}) and asks to "
            f"{_ACT_PHRASE.get(act, act)}."
            if interpretation.top else "No goal in the knowledge base resembles the question."
        )
        if not interpretation.semantic:
            neural_reason += (" Understanding is degraded: the semantic model is unavailable "
                              "and a lexical fallback cannot recognise paraphrases.")

        goal, score, support = self._choose_goal(interpretation)
        clauses = self.pack.policies.search(interpretation.question, k=3)
        top_clause = clauses[0].score if clauses else 0.0

        if interpretation.parsed and goal is None:
            return self._parsed_without_goal(interpretation, neural_reason)

        # An informational question is answered from policy text, even if it is about a
        # topic a goal also covers: "what is the leave policy" asks what the policy
        # says, not how many days this person gets.
        if act == "inform" or (act not in CASE_ACTS):
            if top_clause >= RETRIEVAL_ACCEPT:
                return self._retrieval(clauses, neural_reason)
            if goal is None:
                if self._off_topic(interpretation):
                    return self._general(interpretation, neural_reason)
                return self._insufficient(interpretation, clauses, neural_reason)

        if goal is None:
            # A clearly relevant policy clause beats a vague goal match: "help, I can't get
            # into my work account" is answered by the IT helpdesk clause, not by asking
            # whether the person meant a refund.
            if top_clause >= RETRIEVAL_ACCEPT + 0.03:
                return self._retrieval(clauses, neural_reason)
            # Before offering company goals as "did you mean", check the question is
            # about the company at all. Embedding similarity alone will happily rank
            # order-tracking as the nearest neighbour of "write me a haiku", and
            # answering an ordinary conversational question with a menu of refund
            # goals is worse than useless - it is the assistant refusing to talk.
            if self._off_topic(interpretation):
                return self._general(interpretation, neural_reason)
            suggestions = [c for c in interpretation.candidates if c.score >= GOAL_SUGGEST]
            if suggestions:
                return RouteDecision(
                    Pipeline.CLARIFICATION,
                    "The question is loosely related to what the company knowledge covers, but "
                    "not close enough to one goal to act on it safely. Asking which was meant "
                    "is better than reasoning about the wrong question.",
                    neural_reason=neural_reason, alternatives=suggestions[:3],
                    stages=[("NEURAL", "Understand the question"),
                            ("HYBRID", "Detect that no goal is a confident match"),
                            ("HYBRID", "Ask which goal was meant")],
                )
            if top_clause >= RETRIEVAL_ACCEPT:
                return self._retrieval(clauses, neural_reason)
            return self._insufficient(interpretation, clauses, neural_reason)

        return self._reasoning(goal, score, support, act, interpretation, clauses,
                               neural_reason)

    def _parsed_without_goal(self, interpretation, neural_reason) -> RouteDecision:
        """The semantic parser found no goal: a policy clause, a knowledge gap, or chat.

        The parser's "none" is an answer, not a failure to be rescued by similarity: the
        nearest goal is exactly what it has ruled out. A policy clause it selected is still
        checked - it must exist, be in force, and not be unrelated to the question by the
        encoder's measure - before its text is used.
        """
        from_text = self._text_may_answer(interpretation)
        selected = interpretation.selected_clause
        if selected and from_text:
            ranked = self.pack.policies.search(interpretation.question,
                                               k=len(self.pack.policies.clauses))
            chosen = [c for c in ranked if c.clause.id == selected]
            if chosen and chosen[0].score >= PARSED_CLAUSE_FLOOR:
                decision = self._retrieval(chosen, neural_reason)
                decision.selected_by_parser = True
                return decision
        if self._off_topic(interpretation):
            return self._general(interpretation, neural_reason)
        clauses = self.pack.policies.search(interpretation.question, k=3)
        # The parser ruling out every goal settles that no rule decides this question. It
        # does not settle whether the policies answer it: "what is the returns policy"
        # asks what the text says, and the parser names no clause because the question is
        # about the policy as a whole rather than one clause of it. Refusing there
        # reported a knowledge gap over a corpus that covers the topic well - the clause
        # the question is closest to scored 0.72 while the gap was being declared. The
        # same acceptance threshold the unparsed path already uses applies here, but only
        # for the general question that fallback was added for; `_text_may_answer` says
        # which those are.
        if from_text and clauses and clauses[0].score >= RETRIEVAL_ACCEPT:
            return self._retrieval(clauses, neural_reason)
        return self._insufficient(interpretation, clauses, neural_reason,
                                  declined=not from_text)

    def _text_may_answer(self, interpretation: "Interpretation") -> bool:
        """The parser has ruled out every goal. May policy text still answer the question?

        Only when the answer is the same for everyone. Once no rule decides the question,
        nothing a clause says is about this person's case, so reciting the nearest clause
        at someone asking about their own order states company policy they did not ask
        about as though it settled what they did. That is what the unguarded fallback
        did on the test split: every one of the nine out-of-scope questions it answered
        named an order, and the clause it recited was about something else entirely -
        "can you gift-wrap order ORD-85004 before it ships" was answered out of the
        *cancellation* clause, which scores 0.64 against it. The six it refused are
        exactly the six whose nearest clause fell below the acceptance floor, which is
        to say it refused them by luck of the threshold rather than by judgement.

        What survives is the general question the fallback exists for: "what is the
        returns policy" asks what the text says, names no record, and the parser names
        no clause only because the question is about the policy as a whole.

        Both conditions are the rule `_personal_request` already applies to the parse -
        "inform" means the answer is the same for everyone, so a question about the
        asker's own position is not one. No threshold is introduced or tuned here; the
        test is what kind of question was asked.
        """
        if interpretation.speech_act.label in CASE_ACTS:
            return False
        return not interpretation.mentions.records

    # -- topicality ---------------------------------------------------------
    def _domain_vocabulary(self) -> set[str]:
        """Every word the company's own knowledge uses.

        Drawn from the knowledge pack itself, never from a hand-written list of
        "support words": policy text, goal descriptions, and the schema's predicate
        and entity names. A pack for a different company yields a different
        vocabulary with no code change, which is the whole point of the pack.

        Entity labels are deliberately excluded. Whether a customer name appears in
        the graph is a fact about the records, and topicality is decided before
        authorization has admitted anything - a test that consulted labels would
        answer differently for people allowed to see different records.
        """
        if self._vocabulary is None:
            words = set(self.pack.policies.vocabulary())
            program = self.program
            for name, goal in program.goals.items():
                words |= _content(goal.description)
                words |= _content(name)
            for name, lookup in program.lookups.items():
                words |= _content(lookup.description)
                words |= _content(name)
            for name in list(program.attributes) + list(program.entity_types):
                words |= _content(name)
            self._vocabulary = {w for w in words if len(w) > 2}
        return self._vocabulary

    def _off_topic(self, interpretation: "Interpretation") -> bool:
        """True when nothing in the question connects it to this company's knowledge.

        A strict test, and deliberately so: one shared content word is enough to keep
        a question in the company pipeline, because a paraphrase the encoder should
        handle ("can I send this back?") almost always shares one. It only fires when
        the question's subject matter is absent from the pack entirely, which is when
        a goal match can only be an artefact of embedding distance.
        """
        if interpretation.mentions.records or interpretation.mentions.products:
            return False
        if interpretation.inputs:
            return False
        words = _content(interpretation.question) - _FUNCTION_WORDS
        if not words:
            # No content word at all ("hello", "thanks"). There is nothing to
            # reason about, and small talk is not a knowledge gap.
            return True
        return not (words & self._domain_vocabulary())

    # -- goal selection -----------------------------------------------------
    def _choose_goal(self, interpretation: "Interpretation"):
        candidates = interpretation.candidates
        if not candidates:
            return None, 0.0, []
        stated = {i.predicate for i in interpretation.inputs}
        mention_types = {m.entity_type for m in interpretation.mentions.records}
        if interpretation.mentions.products:
            mention_types |= {"product", "order"}

        def support_for(candidate) -> list[str]:
            evidence = [f"stated {p}" for p in
                        stated & self.program.input_predicates_for(candidate.name)]
            if candidate.subject_type in mention_types:
                evidence.append(f"mentions a {candidate.subject_type}")
            return evidence

        # Symbolic validation of the neural interpretation, part 1: a question with no
        # content word cannot select a goal, however similar its embedding happens to be -
        # "How much?" scored 0.6 against a delivery-charge goal. One is enough: "Can I
        # cancel it?" names its goal and only lacks the record.
        if _content_words(interpretation.question) < 1:
            return None, candidates[0].score, []

        top = candidates[0]
        # Part 2: the goal's kind must fit what was asked. "What's my reimbursement?" and
        # "does my trip qualify?" sit a few hundredths apart in embedding space, but one
        # wants an amount and the other a yes or no - and answering the wrong one is a
        # fluent, cited answer to a question nobody asked. When a goal of the kind the
        # speech act calls for is close to the top, it wins.
        act = interpretation.speech_act.label
        wanted = COMPATIBLE_KINDS.get(act)
        if ablations.active("no_kind_agreement"):
            wanted = None
        if wanted and top.goal_kind not in wanted:
            compatible = [c for c in candidates
                          if c.goal_kind in wanted and top.score - c.score <= KIND_WINDOW]
            if compatible:
                top = compatible[0]
        preferred = None if ablations.active("no_kind_agreement") else PREFERRED_KIND.get(act)
        if preferred and top.goal_kind != preferred:
            better = [c for c in candidates
                      if c.goal_kind == preferred and top.score - c.score <= KIND_WINDOW]
            if better:
                top = better[0]
                # The kind of answer asked for has been decided; ties are broken only
                # among goals that give it.
                wanted = {preferred}
        near = [c for c in candidates if top.score - c.score <= TIE_WINDOW
                and (not wanted or c.goal_kind in wanted or c is top)]
        chosen = top
        if len(near) > 1:
            # Near-ties are resolved by the rule graph, not by a rounding difference in
            # cosine similarity: prefer the goal the question supplies evidence for,
            # then the prerequisite over the goal that depends on it. "Can I return the
            # shoes?" scores return and replacement almost equally; replacement depends on
            # return being allowed and needs a damage report nobody gave.
            primary = PRIMARY_KIND.get(act)
            ranked = sorted(near, key=lambda c: (
                # The kind of answer the question's form asks for comes first: "is that
                # reimbursable?" asks yes or no, however many amounts it mentions.
                0 if c.goal_kind == primary else 1,
                -len(support_for(c)),
                sum(1 for other in near if other.name != c.name
                    and c.name in self.program.dependencies(other.name)) * -1,
                -c.score))
            chosen = ranked[0]

        support = support_for(chosen)
        if chosen.score >= GOAL_ACCEPT or (chosen.score >= GOAL_SUPPORTED and support):
            return chosen, chosen.score, support
        return None, chosen.score, support

    # -- pipelines ----------------------------------------------------------
    def _retrieval(self, clauses, neural_reason) -> RouteDecision:
        return RouteDecision(
            Pipeline.RETRIEVAL,
            "The question asks what company policy or information says in general, not for a "
            "decision about a particular case. Retrieving the relevant clauses answers it "
            "directly; there is no rule to evaluate, so the symbolic engine is not used.",
            symbolic_required=False,
            symbolic_reason="No decision, eligibility or amount is being asked for.",
            neural_reason=neural_reason, clauses=clauses,
            required_knowledge=[f"policy clause {c.clause.id} ({c.clause.title})"
                                for c in clauses[:2]],
            stages=[("NEURAL", "Understand the question"),
                    ("NEURAL", "Semantic retrieval of policy clauses"),
                    ("HYBRID", "Check the retrieved clauses are relevant enough to answer"),
                    ("NEURAL", "Compose the answer from the clauses, with citations")],
        )

    def _general(self, interpretation, neural_reason) -> RouteDecision:
        return RouteDecision(
            Pipeline.GENERAL,
            "The question is not about this company: none of its words appear anywhere in "
            "the company's policies, rules or schema, so there is no policy to apply and "
            "no record to consult. The language model answers it from its own knowledge, "
            "and the answer is labelled as such - it carries no citation and no verification, "
            "because there is nothing here to verify it against.",
            symbolic_required=False,
            symbolic_reason="No company policy, rule or record bears on the question.",
            neural_reason=neural_reason,
            stages=[("NEURAL", "Understand the question"),
                    ("HYBRID", "Establish that the question is outside the company knowledge"),
                    ("NEURAL", "Answer from the model's general knowledge, uncited")],
        )

    def _insufficient(self, interpretation, clauses, neural_reason, *,
                      declined: bool = False) -> RouteDecision:
        best = clauses[0] if clauses else None
        if declined:
            # The parser ruled out every goal and the question is about one person's own
            # case, so a clause cannot answer it however well it scores. Saying so is the
            # point: the reason must not read as "no clause was relevant" when one was
            # relevant-looking and was refused on purpose.
            return RouteDecision(
                Pipeline.INSUFFICIENT,
                "The semantic parser read the question against every goal the rule base "
                "declares and named none of them, and the question asks about one "
                "person's own case rather than what a policy says in general. No rule "
                "decides it, so no policy clause answers it either"
                + (f" - the closest, {best.clause.id}, scores {best.score:.2f} but is "
                   f"about {best.clause.title.lower()}" if best else "")
                + ". Reciting it would state policy the person did not ask about as "
                  "though it settled what they did, so the assistant says what is "
                  "missing instead.",
                neural_reason=neural_reason, clauses=clauses,
                stages=[("NEURAL", "Understand the question"),
                        ("NEURAL", "Parse it against the declared goals; none applies"),
                        ("SYMBOLIC", "Confirm the parser named no declared goal"),
                        ("HYBRID", "Establish the question is about one case, not policy"),
                        ("HYBRID", "Refuse the nearest clause and report what is missing")],
            )
        return RouteDecision(
            Pipeline.INSUFFICIENT,
            "Nothing in the company knowledge covers this: no goal in the rule base resembles "
            "it and no policy clause is relevant"
            + (f" (the closest, {best.clause.id}, scores only {best.score:.2f})" if best else "")
            + ". Answering would mean inventing policy, so the assistant says what is missing "
              "instead.",
            neural_reason=neural_reason, clauses=clauses,
            stages=[("NEURAL", "Understand the question"),
                    ("NEURAL", "Search goals and policy text"),
                    ("HYBRID", "Establish that no knowledge covers it"),
                    ("HYBRID", "Report what is missing rather than guess")],
        )

    def _reasoning(self, goal, score, support, act, interpretation, clauses,
                   neural_reason) -> RouteDecision:
        program = self.program
        specs = program.rules_for(goal.name)
        cited = sorted({c for spec in specs for c in spec.clauses})
        entity_types = self._entity_types(goal.name)
        computes = any(isinstance(spec.rule.head.value, Expr) for spec in specs) or \
            any(isinstance(s.rule.head.value, Expr) for s in program.rules
                if s.rule.head.predicate in program.dependencies(goal.name)
                and program.value_type(s.rule.head.predicate) == "money")
        inputs = sorted(program.input_predicates_for(goal.name))
        required = [f"rules {', '.join(s.code for s in specs)} (policy {', '.join(cited)})"] \
            if specs else []
        required += [f"{goal.subject_type} records linking {' → '.join(entity_types)}"]
        if inputs:
            required.append("case details a person may state: " + ", ".join(inputs))

        if goal.kind == "lookup":
            lookup = program.lookups[goal.name]
            return RouteDecision(
                Pipeline.RETRIEVAL,
                f"The question asks for the current state of a {goal.subject_type} "
                f"({', '.join(lookup.predicates)}). That is a record lookup in the knowledge "
                f"graph; no policy decision is involved, so no rule is evaluated.",
                goal=goal, goal_score=score, symbolic_required=False,
                symbolic_reason="Stored facts answer it; nothing needs to be derived.",
                neural_reason=neural_reason, clauses=clauses,
                required_knowledge=[f"{goal.subject_type} record: {', '.join(lookup.predicates)}"],
                stages=[("NEURAL", "Understand the question"),
                        ("NEURAL", "Extract record references"),
                        ("SYMBOLIC", f"Resolve which {goal.subject_type} is meant"),
                        ("SYMBOLIC", "Read the record from the knowledge graph"),
                        ("NEURAL", "Compose the answer")],
            )

        why_symbolic = (
            f"\"{goal.description}\" is decided by {len(specs)} rule"
            f"{'s' if len(specs) != 1 else ''} citing policy {', '.join(cited)}. A language model "
            f"asked this would predict a plausible answer; the rules determine the actual one, "
            f"and can show which condition decided it."
        )
        base_stages = [("NEURAL", "Understand the question and detect the goal"),
                       ("NEURAL", "Extract records, quantities and stated conditions"),
                       ("SYMBOLIC", f"Resolve the {goal.subject_type} and validate the "
                                    f"extracted facts against company records")]

        if goal.goal_kind == "calculation" or (act == "calculate" and computes):
            return RouteDecision(
                Pipeline.CALCULATION,
                f"The answer is an amount. It is computed by rule arithmetic over company "
                f"records and stated values, because a number a language model produces is a "
                f"prediction, not a calculation. The result is then checked against the rule's "
                f"constraints.",
                goal=goal, goal_score=score, symbolic_required=True,
                symbolic_reason=why_symbolic, neural_reason=neural_reason,
                required_knowledge=required, clauses=clauses,
                stages=base_stages + [
                    ("SYMBOLIC", "Evaluate the rules and compute the amount"),
                    ("HYBRID", "Ask the neural layer again for any input the calculation lacks"),
                    ("SYMBOLIC", "Verify the result against the rule constraints"),
                    ("NEURAL", "State the amount and how it was calculated")],
            )

        if goal.goal_kind == "recommendation":
            return RouteDecision(
                Pipeline.MULTI_HOP if len(entity_types) > 2 else Pipeline.POLICY,
                f"The question asks what should be done. The recommendation is the first "
                f"remedy or route the rules allow for this case, so it composes the underlying "
                f"policy decisions ({', '.join(sorted(program.dependencies(goal.name) & set(program.goals)))}) "
                f"across {' → '.join(entity_types)} rather than guessing a course of action.",
                goal=goal, goal_score=score, symbolic_required=True,
                symbolic_reason=why_symbolic, neural_reason=neural_reason,
                required_knowledge=required, clauses=clauses,
                stages=base_stages + [
                    ("SYMBOLIC", "Evaluate each remedy's rules in the playbook's order"),
                    ("SYMBOLIC", "Resolve the policy version in force for the case"),
                    ("HYBRID", "Check claims and approvals against the records"),
                    ("SYMBOLIC", "Verify the decision"),
                    ("NEURAL", "State the recommendation and the next action")],
            )

        if goal.goal_kind == "enumeration":
            return RouteDecision(
                Pipeline.POLICY,
                f"The question asks which {goal.subject_type}s qualify. Each one is checked "
                f"against the rules in the knowledge graph, so the list is complete and exact "
                f"rather than whatever a search happens to surface.",
                goal=goal, goal_score=score, symbolic_required=True,
                symbolic_reason=why_symbolic, neural_reason=neural_reason,
                required_knowledge=required, clauses=clauses,
                stages=[("NEURAL", "Understand the question and detect the goal"),
                        ("SYMBOLIC", f"Evaluate the rule for every {goal.subject_type}"),
                        ("NEURAL", "List the ones that qualify")],
            )

        if goal.goal_kind == "explanation" or act == "explain":
            return RouteDecision(
                Pipeline.MULTI_HOP,
                f"The question asks why an outcome happened. Explaining it means following the "
                f"records across {' → '.join(entity_types)}, re-evaluating the policy on them, "
                f"and identifying which rule condition was violated - multi-step symbolic "
                f"reasoning, not a summary of policy text.",
                goal=goal, goal_score=score, symbolic_required=True,
                symbolic_reason=why_symbolic, neural_reason=neural_reason,
                required_knowledge=required, clauses=clauses,
                stages=base_stages + [
                    ("SYMBOLIC", "Traverse the knowledge graph to the related records"),
                    ("SYMBOLIC", "Re-evaluate the rules and find the violated condition"),
                    ("HYBRID", "Check the derived outcome against the recorded one"),
                    ("NEURAL", "Explain the decision in plain language")],
            )

        return RouteDecision(
            Pipeline.POLICY,
            f"The question asks whether {goal.description.removeprefix('whether ')}. The answer "
            f"depends on company policy applied to a specific case, so the relevant rules are "
            f"evaluated symbolically rather than inferred from similar-sounding text.",
            goal=goal, goal_score=score, symbolic_required=True,
            symbolic_reason=why_symbolic, neural_reason=neural_reason,
            required_knowledge=required, clauses=clauses,
            stages=base_stages + [
                ("SYMBOLIC", "Evaluate the policy rules"),
                ("HYBRID", "Ask the neural layer again for any input the rules lack"),
                ("SYMBOLIC", "Verify the decision"),
                ("NEURAL", "Explain the decision in plain language")],
        )

    def _entity_types(self, goal: str) -> list[str]:
        """Entity types a proof of the goal can touch, in first-reached order."""
        program = self.program
        start = program.goals[goal].subject_type if goal in program.goals else ""
        seen: list[str] = [start] if start else []
        for predicate in sorted(program.dependencies(goal)):
            attribute = program.attributes.get(predicate)
            if attribute is None:
                continue
            for entity_type in (attribute.subject_type, attribute.value_type):
                if entity_type in program.entity_types and entity_type != "company" \
                        and entity_type not in seen:
                    seen.append(entity_type)
        return seen


_FUNCTION_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "i", "me", "my", "we", "our", "you",
    "your", "it", "this", "that", "these", "those", "what", "which", "who", "why", "how",
    "when", "where", "much", "many", "do", "does", "did", "can", "could", "will", "would",
    "should", "about", "other", "one", "of", "to", "for", "in", "on", "and", "or", "so",
    "get", "got", "any", "some", "there", "here", "am", "if",
}


def _content(text: str) -> set[str]:
    """The distinct content words of a piece of text, function words included.

    Splits on anything non-alphanumeric so `return_allowed` yields both halves and a
    predicate name compares against ordinary prose.
    """
    words = "".join(ch.lower() if ch.isalnum() else " " for ch in text).split()
    return {w for w in words if len(w) > 2}


def _content_words(question: str) -> int:
    return len(_content(question) - _FUNCTION_WORDS)


_ACT_PHRASE = {
    "inform": "know what a policy or the company says",
    "decide": "decide whether something is allowed in their case",
    "calculate": "calculate an amount",
    "explain": "explain why an outcome happened",
    "locate": "find the current status of a record",
    "enumerate": "list what qualifies",
    "compare": "compare things",
    "resolve": "resolve a problem they describe",
}
