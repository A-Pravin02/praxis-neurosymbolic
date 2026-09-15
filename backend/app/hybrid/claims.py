"""HYBRID. Check what a person claims the policy says against what it does say.

"I know your policy is 30 days, so can I return this after 20 days?" contains a fact about
the case (20 days) and a claim about the policy (30 days). A language model given that
prompt tends to accept the premise it is handed. Here the premise is checked: the
reasoning has already read the actual policy parameters from the knowledge graph - the
15-day damage window of the returns policy version in force - so a number the person
attributes to the policy is compared with those parameters.

* A claimed number equal to a parameter the reasoning used is **confirmed**.
* A different number is **rejected**: the answer says what the policy actually sets, cites
  the version, and the reasoning never used the claimed value.

Only numbers the neural layer did not bind to a case input are candidates, and only when
the clause they appear in talks about the policy ("policy", "window", "limit", "cap"...).
A duration that is part of the case ("after 20 days") is never mistaken for a claim.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.neural.query_understanding import POLICY_CLAIM_CUE, UNIT_OF_INPUT, _clause_around
from app.symbolic.render import format_value, humanize

if TYPE_CHECKING:  # pragma: no cover
    from app.hybrid.grounding import Grounding
    from app.hybrid.knowledge_pack import KnowledgePack
    from app.neural.query_understanding import Interpretation
    from app.symbolic.knowledge_graph import FactRef

# Entity types whose attributes are company policy, not case records.
POLICY_TYPES = {"company", "category", "grade", "policy_version"}


def policy_parameters(facts: list["FactRef"], graph, program) -> list["FactRef"]:
    """The numeric policy parameters a proof read."""
    found: dict[tuple[str, str], "FactRef"] = {}
    for ref in facts:
        entity = graph.entities.get(ref.subject) if hasattr(graph, "entities") else None
        if ref.source != "record" or entity is None or entity.type not in POLICY_TYPES:
            continue
        if UNIT_OF_INPUT.get(program.value_type(ref.predicate)) is None:
            continue
        found.setdefault((ref.subject, ref.predicate), ref)
    return list(found.values())


def check_policy_claims(interpretation: "Interpretation", grounding: "Grounding",
                        facts: list["FactRef"], pack: "KnowledgePack", graph,
                        resolutions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    program = pack.program
    parameters = policy_parameters(facts, graph, program)
    if not parameters:
        return []
    used_evidence = [str(s.get("evidence", "")) for s in grounding.accepted + grounding.rejected]
    claims: list[dict[str, Any]] = []
    for quantity in interpretation.mentions.quantities:
        if any(quantity.text and quantity.text in evidence for evidence in used_evidence):
            continue
        clause = _clause_around(interpretation.question, quantity)
        if not POLICY_CLAIM_CUE.search(clause):
            continue
        same_unit = [p for p in parameters
                     if UNIT_OF_INPUT.get(program.value_type(p.predicate)) == quantity.kind]
        if not same_unit:
            continue
        stated = format_value(quantity.value, quantity.kind)
        if any(_equal(p.value, quantity.value) for p in same_unit):
            match = next(p for p in same_unit if _equal(p.value, quantity.value))
            claims.append(_claim(match, stated, clause, graph, program, resolutions,
                                 confirmed=True))
            continue
        labels = [f"the {humanize(p.predicate)} of {graph.display_name(p.subject)}"
                  for p in same_unit]
        scores = pack.encoder.similarity([clause], labels)[0]
        best = same_unit[int(scores.argmax())]
        claims.append(_claim(best, stated, clause, graph, program, resolutions, confirmed=False))
    return claims


def _claim(parameter: "FactRef", stated: str, clause: str, graph, program,
           resolutions: list[dict[str, Any]], *, confirmed: bool) -> dict[str, Any]:
    value_type = program.value_type(parameter.predicate)
    actual = format_value(parameter.value, value_type, graph=graph)
    version = next((r for r in resolutions if r["version"] == parameter.subject), None)
    source = (f"{version['name']} (in force from {version['effective_from']})" if version
              else f"the {graph.display_name(parameter.subject)} policy")
    return {
        "kind": "policy claim", "predicate": parameter.predicate,
        "subject": parameter.subject, "stated": stated, "record": actual,
        "evidence": clause, "confirmed": confirmed, "source": source,
        "reason": (f"matches {source}" if confirmed else
                   f"{source} sets the {humanize(parameter.predicate)} at {actual}, so the "
                   f"policy as written was applied"),
    }


def _equal(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return False
