"""HYBRID. When the assistant can't answer, work out *why* - and tell the enterprise.

"I don't know" helps nobody. Each unanswerable question is classified by what is
missing, because each kind is fixed by a different person:

    uncovered_topic          no policy, rule or record is about this at all
                             -> the policy owner writes a policy
    undefined_case           the rules decide this kind of question, but not for the
                             case described ("which team owns credential issues?")
                             -> the policy owner extends the rule
    missing_policy_parameter a rule applies but a value it needs was never recorded
                             (a travel cap for a new grade)
                             -> the knowledge administrator adds the fact
    unclear_request          the question is near several things the assistant covers
                             -> nothing is missing; the person is asked
    verification_failure     an answer was derived but failed verification, so it was
                             withheld -> someone reviews the rule or the record

The classification uses the structures that already exist - the rule base, the goal the
router chose, the conditions the reasoner could not decide - not a model's opinion. Gaps
are recorded in the reasoning trace and aggregated for administrators.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.neural.entity_extraction import description_facets, is_question_clause
from app.symbolic.render import humanize

if TYPE_CHECKING:  # pragma: no cover
    from app.hybrid.knowledge_pack import KnowledgePack
    from app.neural.query_understanding import Interpretation
    from app.symbolic.reasoner import GoalResult

_WORD = re.compile(r"[a-z]+")
_GENERIC = {
    "what", "which", "when", "where", "who", "whom", "why", "how", "does", "will", "would",
    "could", "should", "can", "the", "and", "for", "with", "this", "that", "they", "their",
    "them", "have", "has", "had", "your", "yours", "mine", "company", "customer", "customers",
    "about", "there", "here", "into", "from", "just", "still", "been", "being", "want",
    "wants", "need", "needs", "please", "tell", "know", "get", "give", "take", "make",
    "able", "allowed", "possible", "anything", "someone", "somebody", "issue", "problem",
    "handle", "handles", "handled", "team", "desk", "route", "routed", "escalate", "any",
    "our", "you", "are", "was", "were", "is", "be", "my", "me", "do", "did", "if", "it",
    "whose", "who's", "isn't", "can't", "won't", "don't", "doesn't",
}
# A description the input layer could not classify at its own threshold matches no
# defined value.
UNDEFINED_CASE_THRESHOLD = 0.70


def content_terms(text: str, known: set[str]) -> list[str]:
    words = [w for w in _WORD.findall(text.lower()) if len(w) > 3 and w not in _GENERIC]
    return list(dict.fromkeys(w for w in words if w not in known and w.rstrip("s") not in known))


def uncovered_topic(interpretation: "Interpretation", pack: "KnowledgePack",
                    nearest: Any = None) -> dict[str, Any]:
    known = pack.policies.vocabulary()
    for goal in pack.program.goals.values():
        known |= set(_WORD.findall(goal.description.lower()))
    # F-6 fix: always include entity-type names from the schema so gap summaries
    # are meaningful even before any policy documents are loaded for a tenant.
    for entity_type in getattr(pack.program, "entities", ()):
        known |= set(_WORD.findall(entity_type.lower()))
    terms = content_terms(interpretation.question, known)
    subject = ", ".join(terms[:4]) if terms else "this question"
    detail = "No policy, rule or record in the company knowledge covers it."
    if nearest is not None:
        detail += (f" The closest topic is {nearest.clause.title} ({nearest.clause.id}), "
                   f"which doesn't address it.")
    return {"type": "uncovered_topic", "summary": f"No policy covers: {subject}",
            "detail": detail, "terms": terms, "owner": "policy owner",
            "nearest": nearest.clause.id if nearest is not None else None}


def undefined_case(interpretation: "Interpretation", goal, result: "GoalResult",
                   pack: "KnowledgePack") -> dict[str, Any] | None:
    """The rules decide this kind of question, but none of them for the case described.

    Applies when the goal is a decision table over one input - every rule concluding it
    maps one value of that input to a result - the reasoner still lacks it after the neural-symbolic loop,
    and the question *describes* a case that matches none of the input's declared values.
    """
    program = pack.program
    specs = program.rules_for(goal.name)
    if not specs or result.status not in ("conditional", "unknown"):
        return None
    missing = {m.predicate for m in result.missing if program.is_input(m.predicate)}
    for predicate in sorted(missing):
        # Only a decision table over the input: every rule must have exactly one fact
        # condition that directly tests the key predicate's value. Rules may also have
        # comparison or filter conditions — what matters is that each rule is enumerated
        # over one value of the input, not that the body is a single literal.
        def _is_key_fact(condition, pred=predicate):
            return condition.type == "fact" and condition.predicate == pred

        if not all(sum(1 for c in spec.rule.body if _is_key_fact(c)) == 1
                   for spec in specs):
            continue
        values = [v for v in program.inputs.get(predicate, []) if v.value is not None]
        if len(values) < 2:
            continue
        description = _described_case(interpretation, goal)
        if not description:
            continue
        facets = [f for v in values for f in description_facets(v.description)]
        best = float(pack.encoder.similarity([description], facets).max())
        if best >= UNDEFINED_CASE_THRESHOLD:
            continue
        clauses = sorted({c for spec in specs for c in spec.clauses})
        defined = [humanize(str(v.value)) for v in values]
        return {
            "type": "undefined_case",
            "summary": f"{_goal_phrase(goal)} is not defined for: \"{description}\"",
            "detail": (f"{', '.join(clauses)} {'defines' if len(clauses) == 1 else 'define'} "
                       f"the {humanize(goal.name)} only for "
                       f"{', '.join(defined[:-1])} and {defined[-1]}."),
            "goal": goal.name, "input": predicate, "defined_values": defined,
            "clauses": clauses, "case": description, "similarity": round(best, 3),
            "owner": "policy owner",
        }
    return None


def missing_parameter(result: "GoalResult", pack: "KnowledgePack", graph) -> dict | None:
    from app.hybrid.claims import POLICY_TYPES

    for outcome in result.refutations():
        failed = outcome.failed
        if failed is None or not failed.expression.endswith("is not recorded"):
            continue
        match = re.match(r"^(\w+)\((.+)\) is not recorded$", failed.expression)
        if not match:
            continue
        predicate, subject_name = match.groups()
        attribute = pack.program.attributes.get(predicate)
        if attribute is None or attribute.subject_type not in POLICY_TYPES:
            continue
        return {"type": "missing_policy_parameter",
                "summary": f"{humanize(predicate)} is not recorded for {subject_name}",
                "detail": f"Rule {outcome.spec.code} ({outcome.spec.rule.name}) needs it.",
                "rule": outcome.spec.code, "clauses": outcome.spec.clauses,
                "owner": "knowledge administrator"}
    return None


def unclear_request(interpretation: "Interpretation", alternatives: list) -> dict[str, Any]:
    return {"type": "unclear_request",
            "summary": "Request matched several topics without a clear winner",
            "detail": "Asked the person which they meant: " + "; ".join(
                a.name for a in alternatives[:3]),
            "owner": None}


def verification_failure(failures: list) -> dict[str, Any]:
    return {"type": "verification_failure",
            "summary": "A derived answer failed verification and was withheld",
            "detail": "; ".join(f"{c.name}: {c.detail}" for c in failures),
            "owner": "knowledge administrator"}


def _described_case(interpretation: "Interpretation", goal) -> str:
    """The part of the question that describes the case, if it describes one."""
    goal_words = set(_WORD.findall(goal.description.lower()))
    statements = [c for c in interpretation.clauses if not is_question_clause(c)]
    candidates = statements or interpretation.clauses
    best = ""
    for clause in candidates:
        text = re.sub(r"^\s*(who|which team|what team|where)\b[^,]*?\b(for|with|when|if|about)\b\s*",
                      "", clause, flags=re.IGNORECASE).strip()
        if len(content_terms(text, goal_words)) >= 2 and len(text) > len(best):
            best = text
    return best


def _goal_phrase(goal) -> str:
    text = humanize(goal.name)
    return text[:1].upper() + text[1:]
