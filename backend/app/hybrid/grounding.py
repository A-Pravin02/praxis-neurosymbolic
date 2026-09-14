"""HYBRID. Ground neural hypotheses in company records before any reasoning happens.

The neural layer produces hypotheses: "this question is about order 12346", "the user
said it arrived damaged", "they spent ₹8,750". None of those is a fact yet.

Grounding decides, symbolically:

* **Which record.** An explicit id is validated against the knowledge graph and against
  ownership - a customer cannot reason over someone else's order. "My order" with several
  candidates is narrowed by the goal's declared relevance, and if it is still ambiguous
  the assistant asks instead of choosing.
* **Whether a statement is a fact.** Something the company records (a delivery date) is
  taken from the record. A user saying otherwise is *misleading information*: it is
  reported and set aside, never used. Something the company does not record (why the
  item is being returned) is accepted as the user's statement, and the answer says it
  relied on it.
* **Hypothetical cases.** "If I spent ₹8,750" is about no record at all. It becomes an
  open-world case linked to the person asking, so their grade and membership still apply
  and every unstated condition is unknown rather than false.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.symbolic.reasoner import Reasoner, VIOLATED
from app.symbolic.render import format_value, humanize

if TYPE_CHECKING:  # pragma: no cover
    from app.hybrid.knowledge_pack import KnowledgePack
    from app.neural.query_understanding import Interpretation, StatedInput
    from app.symbolic.knowledge_graph import Entity

# How a hypothetical case of each type is attached to the person asking.
CASE_LINKS = {"order": "placed_by", "claim": "claimant"}
STAFF_ROLES = {"support", "admin"}


@dataclass
class Actor:
    email: str
    entity: "Entity | None" = None
    roles: tuple[str, ...] = ()

    @property
    def id(self) -> str | None:
        return self.entity.id if self.entity else None

    @property
    def kind(self) -> str:
        if STAFF_ROLES & set(self.roles):
            return "staff"
        return self.entity.type if self.entity else "unknown"

    @property
    def name(self) -> str:
        return self.entity.name if self.entity else self.email


@dataclass
class Clarification:
    question: str
    options: list[dict[str, str]]


@dataclass
class Grounding:
    subjects: list[str] = field(default_factory=list)
    hypothetical: bool = False
    stated: dict[tuple[str, str], Any] = field(default_factory=dict)
    open_subjects: set[str] = field(default_factory=set)
    accepted: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    clarification: Clarification | None = None
    denied: str | None = None
    missing_subject: str | None = None

    def as_dict(self) -> dict:
        return {
            "subjects": self.subjects, "hypothetical": self.hypothetical,
            "stated_facts": [{"subject": s, "predicate": p, "value": v}
                             for (s, p), v in self.stated.items()],
            "accepted_statements": self.accepted, "rejected_statements": self.rejected,
            "assumptions": self.assumptions,
            "clarification": vars(self.clarification) if self.clarification else None,
            "denied": self.denied, "missing_subject": self.missing_subject,
        }


class FactGrounding:
    def __init__(self, pack: "KnowledgePack") -> None:
        self.pack = pack
        self.program = pack.program
        self.graph = pack.graph

    def actor(self, email: str, roles: tuple[str, ...] = ()) -> Actor:
        return Actor(email=email, entity=self.graph.person_by_email(email), roles=roles)

    # -- public -------------------------------------------------------------
    def ground(self, interpretation: "Interpretation", goal_name: str, subject_type: str,
               actor: Actor, context: dict | None = None) -> Grounding:
        grounding = Grounding()
        context = context or {}

        if goal_name in self.program.goals and \
                self.program.goals[goal_name].kind == "enumeration":
            grounding.subjects = [e.id for e in self.graph.entities_of_type(subject_type)]
            return grounding

        if subject_type in ("employee", "customer"):
            self._ground_person(grounding, actor, subject_type)
        else:
            self._resolve_record(grounding, interpretation, goal_name, subject_type, actor,
                                 context)

        if grounding.subjects and not grounding.clarification and not grounding.denied:
            self._ground_statements(grounding, interpretation, goal_name)
        return grounding

    # -- the person asking --------------------------------------------------
    def _ground_person(self, grounding: Grounding, actor: Actor, subject_type: str) -> None:
        if actor.entity is not None and actor.entity.type == subject_type:
            grounding.subjects = [actor.entity.id]
            return
        grounding.missing_subject = (
            f"This depends on a {subject_type} record, and {actor.email} is not linked to "
            f"one in the company knowledge.")

    # -- which record -------------------------------------------------------
    def _resolve_record(self, grounding, interpretation, goal_name, subject_type, actor,
                        context) -> None:
        mentions = interpretation.mentions

        explicit = [m.entity_id for m in mentions.records if m.entity_type == subject_type]
        # A mention of a related record points at the one asked about: an order id in a
        # question about a refund, a return id in a question about an order.
        for mention in mentions.records:
            if mention.entity_type != subject_type:
                explicit += self.graph.related(mention.entity_id, subject_type, max_hops=2)
        explicit = list(dict.fromkeys(explicit))

        if mentions.unresolved_ids and not explicit:
            grounding.missing_subject = (
                f"There is no record matching {', '.join(mentions.unresolved_ids)} in the "
                f"company knowledge.")
            return

        if explicit:
            allowed = [e for e in explicit if self._may_access(actor, e)]
            if not allowed:
                grounding.denied = (
                    "That record belongs to a different account, so I can't look at it or "
                    "reason about it for you.")
                return
            if len(allowed) == 1:
                grounding.subjects = allowed
                return
            grounding.clarification = self._clarify(allowed, subject_type)
            return

        # "If" does not by itself mean hypothetical: "what should I do if my payment was
        # deducted but the order failed" describes a real order. Whether a question is
        # about an existing record is decided by consistency with that record below -
        # a description that contradicts every candidate is a new case, and a
        # contradiction is only "misleading" when the person named the record.
        own = self._candidates(actor, subject_type, interpretation)
        products = [m.entity_id for m in mentions.products]
        if products and subject_type == "order" and not own and actor.entity is not None:
            # A product the person never ordered: the question is about that product in
            # general ("can I return a gift card that arrived damaged?").
            self._make_case(grounding, subject_type, actor)
            grounding.stated[(grounding.subjects[0], "contains")] = products[0]
            return
        relevant = [e for e in own if self._relevant(goal_name, e)]
        previous = context.get("subjects", {}).get(subject_type)
        states_details = any(
            i.predicate in self.program.inputs
            and self.program.inputs[i.predicate][0].subject_type == subject_type
            for i in interpretation.inputs)

        if previous and previous in own and (mentions.deictic or not mentions.products):
            grounding.subjects = [previous]
            grounding.assumptions.append(
                f"Taken to mean {self.graph.display_name(previous)} from earlier in the "
                f"conversation.")
            return

        # What the person said narrows which record they mean: "I ordered this two weeks
        # ago" is the order delivered 14 days ago, not any delivered order. A candidate
        # the statements contradict is not the one being described.
        consistent = [e for e in relevant
                      if self._consistent(e, interpretation, goal_name, subject_type)]
        if len(consistent) == 1:
            grounding.subjects = consistent
            if len(own) > 1:
                why = "the one that matches what you described" if len(relevant) > 1 \
                    else "the only one this question can apply to"
                grounding.assumptions.append(
                    f"Taken to mean your {self.graph.display_name(consistent[0])} "
                    f"({consistent[0]}), {why}.")
            return
        if len(consistent) > 1:
            grounding.clarification = self._clarify(consistent, subject_type)
            return
        if subject_type in CASE_LINKS and actor.entity is not None and \
                (relevant or (own and states_details)):
            # Every candidate contradicts the description, or none of the person's records
            # is the kind the question can apply to while they describe a case: "can I
            # return shoes 40 days after delivery" from someone whose shoes haven't
            # shipped is a question about the policy, not about that order.
            self._make_case(grounding, subject_type, actor)
            if products and subject_type == "order":
                grounding.stated[(grounding.subjects[0], "contains")] = products[0]
            return
        if not own and subject_type in CASE_LINKS and actor.entity is not None:
            self._make_case(grounding, subject_type, actor)
            return
        if len(own) > 1:
            grounding.clarification = self._clarify(own, subject_type)
            return
        if len(own) == 1:
            grounding.subjects = own
            return
        grounding.missing_subject = (
            f"I couldn't find a {subject_type} on your account that this could be about. "
            f"If you share its number, I can check it.")

    def _consistent(self, entity_id: str, interpretation, goal_name: str,
                    subject_type: str) -> bool:
        """No statement in the question contradicts this record."""
        needed = self.program.input_predicates_for(goal_name)
        reasoner = Reasoner(self.program, self.graph)
        for statement in interpretation.inputs:
            declared = self.program.inputs.get(statement.predicate)
            if not declared or declared[0].subject_type != subject_type or \
                    statement.predicate not in needed:
                continue
            known = reasoner.evaluate(entity_id, statement.predicate)
            if known.status == "determined" and known.source in ("record", "derived") and \
                    not _same(known.value, statement.value, tolerance=_tolerance(
                        self.program.value_type(statement.predicate))):
                return False
        return True

    def _candidates(self, actor: Actor, subject_type: str, interpretation) -> list[str]:
        if actor.entity is None:
            return []
        records = [e.id for e in self.graph.records_of(actor.entity.id, subject_type)]
        products = {m.entity_id for m in interpretation.mentions.products}
        if products and subject_type == "order":
            # A named product narrows to the orders containing it - and if there are
            # none, to nothing, rather than silently back to every order.
            return [r for r in records
                    if set(map(str, self.graph.values(r, "contains"))) & products]
        if products and subject_type == "return":
            return [r for r in records
                    if set(map(str, self.graph.values(self.graph.values(r, "return_for")[0],
                                                      "contains"))) & products]
        return records

    def _relevant(self, goal_name: str, entity_id: str) -> bool:
        relevance = self.program.relevance.get(goal_name)
        if relevance is None:
            return True
        from app.symbolic.schema import parse_condition

        reasoner = Reasoner(self.program, self.graph)
        bindings: dict[str, Any] = {}
        types: dict[str, str] = {}
        for index, raw in enumerate(relevance.conditions):
            condition = parse_condition(_substitute(raw, entity_id))
            if condition.type == "fact":
                result = reasoner._fact_condition(condition, bindings, types, [], index)
            elif condition.type == "comparison":
                result = reasoner._comparison(condition, bindings, types, index)
            else:
                result = reasoner._absent(condition, bindings, [], index)
            if result.status == VIOLATED:
                return False
        return True

    def _hypothetical(self, interpretation, subject_type: str) -> bool:
        if subject_type not in CASE_LINKS:
            return False
        m = interpretation.mentions
        if m.records or m.products:
            return False
        stated = [i for i in interpretation.inputs
                  if i.predicate in self.program.inputs
                  and self.program.inputs[i.predicate][0].subject_type == subject_type]
        return bool(stated) or m.hypothetical or not m.personal

    def _make_case(self, grounding: Grounding, subject_type: str, actor: Actor) -> None:
        case = f"case:{subject_type}"
        grounding.subjects = [case]
        grounding.hypothetical = True
        grounding.open_subjects.add(case)
        link = CASE_LINKS.get(subject_type)
        if link and actor.entity is not None:
            grounding.stated[(case, link)] = actor.entity.id
        grounding.assumptions.append(
            f"No specific {subject_type} was named, so this is evaluated as a general case "
            f"using what you said and the policies that apply to you.")

    def _clarify(self, entity_ids: list[str], subject_type: str) -> Clarification:
        options = []
        for entity_id in entity_ids[:6]:
            facts = self.graph.facts_about(entity_id)
            detail = facts.get("order_status") or facts.get("refund_status") or ""
            options.append({"id": entity_id, "label": self.graph.display_name(entity_id),
                            "detail": humanize(detail) if detail else ""})
        return Clarification(
            question=f"Which {subject_type} do you mean?", options=options)

    def _may_access(self, actor: Actor, entity_id: str) -> bool:
        if actor.kind == "staff":
            return True
        owner = self.graph.owner_of(entity_id)
        return owner is None or (actor.entity is not None and owner == actor.entity.id)

    # -- statements ---------------------------------------------------------
    def _ground_statements(self, grounding: Grounding, interpretation, goal_name: str) -> None:
        subject = grounding.subjects[0]
        subject_type = subject.split(":", 1)[1] if subject.startswith("case:") \
            else self.graph.entities[subject].type
        needed = self.program.input_predicates_for(goal_name)
        baseline = Reasoner(self.program, self.graph, open_subjects=grounding.open_subjects,
                            stated=grounding.stated)

        for statement in interpretation.inputs:
            self.accept_statement(grounding, statement, subject, subject_type, needed, baseline)

    def accept_statement(self, grounding: Grounding, statement: "StatedInput", subject: str,
                         subject_type: str, needed: set[str], baseline: Reasoner) -> bool:
        declared = self.program.inputs.get(statement.predicate)
        if not declared or declared[0].subject_type != subject_type:
            return False
        if statement.predicate not in needed:
            return False
        value_type = self.program.value_type(statement.predicate)
        shown = format_value(statement.value, value_type, graph=self.graph)

        if subject not in grounding.open_subjects:
            known = baseline.evaluate(subject, statement.predicate)
            if known.status == "determined" and known.source in ("record", "derived"):
                if _same(known.value, statement.value, tolerance=_tolerance(value_type)):
                    grounding.accepted.append({"predicate": statement.predicate,
                                               "value": shown, "status": "confirmed by record",
                                               "evidence": statement.evidence})
                    return True
                grounding.rejected.append({
                    "predicate": statement.predicate, "stated": shown,
                    "record": format_value(known.value, value_type, graph=self.graph),
                    "evidence": statement.evidence,
                    "reason": "contradicts the company record, which is used instead"})
                return False

        grounding.stated[(subject, statement.predicate)] = statement.value
        grounding.accepted.append({"predicate": statement.predicate, "value": shown,
                                   "status": "stated by you", "evidence": statement.evidence,
                                   "confidence": round(statement.confidence, 3)})
        return True


def _tolerance(value_type: str) -> float:
    # People round durations: "two weeks ago" for 13 or 15 days is not a contradiction.
    return 2.0 if value_type == "days" else 0.5


def _same(a: Any, b: Any, *, tolerance: float = 0.5) -> bool:
    try:
        return abs(float(a) - float(b)) <= tolerance
    except (TypeError, ValueError):
        return str(a).lower() == str(b).lower()


def _substitute(raw: Any, entity_id: str) -> Any:
    if isinstance(raw, dict):
        if raw.get("var") == "?x":
            return {"const": entity_id}
        return {k: _substitute(v, entity_id) for k, v in raw.items()}
    if isinstance(raw, list):
        return [_substitute(v, entity_id) for v in raw]
    return raw
