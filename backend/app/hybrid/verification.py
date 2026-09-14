"""HYBRID. Check the answer before anyone reads it.

Four checks, each deterministic:

1. **Record consistency.** Where the company stores the outcome a rule derives (a refund
   marked "rejected"), the stored value and the derived decision must agree. If they do
   not, either the record or the policy is wrong, and the answer says so.
2. **Result constraints.** An amount of money or days must be a finite, non-negative
   number. A rule producing -₹40 is a broken rule, not an answer.
3. **Citation integrity.** Every policy clause a fired rule cites must exist in the policy
   text, so a source link never points at nothing.
4. **Answer grounding.** Every figure in the reply must appear in the conclusion the
   reasoning produced. A number that appears only in the reply was invented by whatever
   wrote the reply.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.neural.response_generation import Conclusion, numbers_in
from app.symbolic.reasoner import GoalResult, Symbolic

if TYPE_CHECKING:  # pragma: no cover
    from app.hybrid.knowledge_pack import KnowledgePack


@dataclass
class Check:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return {"check": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class VerificationReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def as_dict(self) -> dict:
        return {"passed": self.passed, "checks": [c.as_dict() for c in self.checks]}


class Verifier:
    def __init__(self, pack: "KnowledgePack") -> None:
        self.pack = pack
        self.program = pack.program
        self.graph = pack.graph

    def record_consistency(self, goal: str, subject: str, result: GoalResult) -> Check | None:
        recorded = self.program.recorded.get(goal)
        if recorded is None or result.status not in ("determined", "default"):
            return None
        holders = [e for (s, p), values in self.graph._facts.items()
                   if p == recorded.relation and subject in map(str, values)
                   for e in [s]]
        if not holders:
            return None
        stored = self.graph.values(holders[0], recorded.predicate)
        if not stored:
            return None
        expected = recorded.true_value if result.value is True else recorded.false_value
        agrees = str(stored[0]).lower() == expected.lower()
        return Check("record consistency", agrees,
                     f"the stored {recorded.predicate} of {holders[0]} is '{stored[0]}', and the "
                     f"policy derives '{expected}'"
                     + ("" if agrees else " — the record and the policy disagree"))

    def constraints(self, goal: str, result: GoalResult) -> Check | None:
        value_type = self.program.value_type(goal)
        if value_type not in ("money", "days", "distance", "years"):
            return None
        value = result.value
        if isinstance(value, Symbolic) or value is None:
            return Check("result constraints", True,
                         "the amount depends on an unstated input and is reported as a formula")
        try:
            number = float(value)
        except (TypeError, ValueError):
            return Check("result constraints", False, f"{goal} is not a number: {value!r}")
        ok = math.isfinite(number) and number >= 0
        return Check("result constraints", ok,
                     f"{goal} = {number:g} is " + ("a valid non-negative amount" if ok
                                                  else "not a valid amount"))

    def citations(self, rule_clauses: list[str]) -> Check | None:
        if not rule_clauses:
            return None
        missing = [c for c in rule_clauses if self.pack.policies.clause(c) is None]
        return Check("citation integrity", not missing,
                     "every rule cites a policy clause that exists" if not missing
                     else f"rules cite clauses missing from the policy text: {', '.join(missing)}")

    def grounding(self, answer: str, conclusion: Conclusion) -> Check:
        allowed = numbers_in(conclusion.facts_text())
        stated = numbers_in(answer)
        extra = sorted(stated - allowed)
        return Check("answer grounding", not extra,
                     "every figure in the answer comes from the reasoning"
                     if not extra else f"figures not produced by the reasoning: {extra}")


def run_checks(*checks: Any) -> VerificationReport:
    return VerificationReport([c for c in checks if c is not None])
