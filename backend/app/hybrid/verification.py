"""HYBRID. Check the answer before anyone reads it - and withhold it if it fails.

Every check is deterministic. None asks a model whether the answer looks right.

 1. **Fact validity.** Every company fact the proof used is in the records with that
    value; every fact a person supplied is one a person may state.
 2. **Authorization.** Every record the proof touched is one the person asking may see.
 3. **Policy applicability.** Every cited clause belongs to the policy version that
    governs the case (or is in force, for unversioned policies).
 4. **Policy version.** The version applied was in force on the case's date.
 5. **Symbolic provenance.** The decision was produced by a rule or a declared default,
    not by the text that phrases it.
 6. **Calculation.** An amount re-derives to the same value from scratch, and is a valid,
    non-negative quantity.
 7. **Supported numbers.** Every figure in the reply appears in the conclusion.
 8. **Contradictions.** The rules did not conflict, the stored outcome agrees with the
    derived one, and no rejected statement was used.
 9. **Answer agreement.** The reply says what the symbolic result says: a "no" is not
    phrased as a "yes", an amount is the amount.
10. **Unsupported claims.** A model's rephrasing adds no sentence the conclusion does not
    support.

When a check fails, the controller does not release the answer: a model's phrasing falls
back to the deterministic one, and a failure in the reasoning itself becomes "needs
review" rather than a confident reply.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.neural.response_generation import Conclusion, numbers_in
from app.symbolic.engine import _normalise
from app.symbolic.reasoner import GoalResult, Reasoner, Symbolic

if TYPE_CHECKING:  # pragma: no cover
    from app.hybrid.knowledge_pack import KnowledgePack

# Checks whose failure means the *reasoning* can't be trusted, not just its phrasing.
REASONING_CHECKS = {"fact validity", "authorization", "policy applicability", "policy version",
                    "symbolic provenance", "calculation", "contradictions", "record consistency"}
UNSUPPORTED_SENTENCE_THRESHOLD = 0.55
_NEGATION = re.compile(r"\b(no|not|never|isn't|aren't|can't|cannot|won't|doesn't|don't|"
                       r"didn't|wasn't|shouldn't|couldn't|n't)\b|n't\b", re.IGNORECASE)


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

    @property
    def reasoning_failed(self) -> bool:
        return any(c.name in REASONING_CHECKS for c in self.failures)

    def as_dict(self) -> dict:
        return {"passed": self.passed, "checks": [c.as_dict() for c in self.checks]}


class Verifier:
    def __init__(self, pack: "KnowledgePack", graph=None) -> None:
        self.pack = pack
        self.program = pack.program
        self.graph = graph if graph is not None else pack.graph

    # 1 -----------------------------------------------------------------------------
    def fact_validity(self, reasoner: Reasoner) -> Check:
        problems = []
        base = getattr(self.graph, "base", self.graph)
        for ref in reasoner.touched.values():
            if ref.source == "record":
                if not any(_normalise(v) == _normalise(ref.value)
                           for v in base.values(ref.subject, ref.predicate)):
                    problems.append(f"{ref.predicate}({ref.subject}) = {ref.value!r} is not in "
                                    f"the records")
            elif ref.source == "stated":
                if ref.subject.startswith("case:"):
                    continue
                if not self.program.is_input(ref.predicate) or \
                        self.program.is_verified_input(ref.predicate):
                    problems.append(f"{ref.predicate}({ref.subject}) was taken from a statement "
                                    f"but only a record can establish it")
        return Check("fact validity", not problems,
                     "every record fact used is in the company records, and every stated fact "
                     "is one a person may state" if not problems else "; ".join(problems))

    # 2 -----------------------------------------------------------------------------
    def authorization(self, reasoner: Reasoner) -> Check | None:
        visible = getattr(self.graph, "visible", None)
        if visible is None:
            return None
        subjects = {ref.subject for ref in reasoner.touched.values()
                    if not ref.subject.startswith("case:")}
        leaked = sorted(s for s in subjects if s in self.graph.base.entities and not visible(s))
        return Check("authorization", not leaked,
                     f"all {len(subjects)} records used are visible to the person asking "
                     f"({self.graph.kind} access)" if not leaked
                     else f"records outside the person's access were used: {', '.join(leaked)}")

    # 3 -----------------------------------------------------------------------------
    def policy_applicability(self, cited: list[dict], resolutions: list[dict]) -> Check | None:
        if not cited:
            return None
        by_policy = {r["policy"]: r for r in resolutions}
        problems = []
        for source in cited:
            clause = self.pack.policies.clause(source["id"], source.get("version"))
            if clause is None:
                continue
            resolution = by_policy.get(clause.policy)
            if resolution is not None:
                if resolution.get("number") is not None and \
                        int(resolution["number"]) != clause.version:
                    problems.append(f"{clause.id} is cited from version {clause.version}, but "
                                    f"version {resolution['number']} governs the case")
            elif not clause.in_force:
                problems.append(f"{clause.id} (version {clause.version}) was superseded on "
                                f"{clause.superseded_on}")
        return Check("policy applicability", not problems,
                     "every cited clause is from the policy version that governs the case"
                     if not problems else "; ".join(problems))

    # 4 -----------------------------------------------------------------------------
    def policy_version(self, resolutions: list[dict]) -> Check | None:
        if not resolutions:
            return None
        problems, notes = [], []
        for r in resolutions:
            starts, ends, when = r["effective_ordinal"], r["superseded_ordinal"], \
                r["reference_ordinal"]
            ok = starts <= when and (ends is None or when < ends)
            window = f"in force from {r['effective_from']}" + (
                f" until {r['superseded_on']}" if r["superseded_on"] else "")
            line = f"{r['name']} ({window}) applied for {r['reference_date']}" + (
                " (no case date, so the current version)" if r["assumed"] else "")
            (notes if ok else problems).append(line)
        return Check("policy version", not problems,
                     "; ".join(notes) if not problems else "; ".join(problems))

    # 5 -----------------------------------------------------------------------------
    def provenance(self, goal: str, result: GoalResult, conclusion: Conclusion,
                   cases: list) -> Check:
        if cases:
            decided = all(c["result"].decided_by is not None or c["result"].source == "default"
                          or c["result"].status in ("conditional", "unknown")
                          for c in cases)
            return Check("symbolic provenance", decided,
                         f"each of the {len(cases)} cases was evaluated by the rules")
        if result.status in ("determined", "conditional"):
            rule = result.decided_by.spec.code if result.decided_by else None
            ok = rule is not None or result.source in ("record", "derived")
            detail = (f"the result was derived by rule {rule}" if rule else
                      f"the result was read from the {result.source}")
        elif result.status == "default":
            ok, detail = True, (f"no rule concluded {goal}, so the policy default applies, with "
                                f"{len(result.refutations())} rule(s) refuted")
        elif result.status in ("absent", "unknown"):
            ok, detail = True, "no decision was asserted"
        else:
            ok, detail = False, f"the reasoning ended in status '{result.status}'"
        if ok and conclusion.decision is not None and result.status in ("determined", "default"):
            ok = conclusion.decision is result.value
            if not ok:
                detail = "the stated decision differs from the derived one"
        return Check("symbolic provenance", ok, detail)

    # 6 -----------------------------------------------------------------------------
    def calculation(self, goal: str, subject: str, result: GoalResult, stated: dict,
                    open_subjects: set[str]) -> Check | None:
        value_type = self.program.value_type(goal)
        if value_type not in ("money", "days", "distance", "years", "percent"):
            return None
        value = result.value
        if isinstance(value, Symbolic) or value is None:
            return Check("calculation", True,
                         "the amount depends on an unstated input and is reported as a formula")
        try:
            number = float(value)
        except (TypeError, ValueError):
            return Check("calculation", False, f"{goal} is not a number: {value!r}")
        if not (math.isfinite(number) and number >= 0):
            return Check("calculation", False, f"{goal} = {number:g} is not a valid amount")
        again = Reasoner(self.program, self.graph, stated=stated,
                         open_subjects=open_subjects).evaluate(subject, goal)
        same = _normalise(again.value) == _normalise(value)
        return Check("calculation", same,
                     f"{goal} = {number:g} re-derives to the same value from scratch"
                     if same else f"{goal} re-derived to {again.value!r}, not {value!r}")

    # 7 -----------------------------------------------------------------------------
    def grounding(self, answer: str, conclusion: Conclusion) -> Check:
        allowed = numbers_in(conclusion.facts_text())
        stated = numbers_in(answer)
        extra = sorted(stated - allowed)
        return Check("supported numbers", not extra,
                     "every figure in the answer comes from the reasoning"
                     if not extra else f"figures not produced by the reasoning: {extra}")

    # 8 -----------------------------------------------------------------------------
    def record_consistency(self, goal: str, subject: str, result: GoalResult) -> Check | None:
        recorded = self.program.recorded.get(goal)
        if recorded is None or result.status not in ("determined", "default"):
            return None
        holders = self.graph.holders_of(recorded.relation, subject)
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

    def contradictions(self, result: GoalResult, grounding, cases: list) -> Check:
        problems = []
        results = [c["result"] for c in cases] if cases else [result]
        if any(r.status == "conflict" for r in results):
            problems.append("rules of equal priority derived different values")
        rejected = {r["predicate"] for r in grounding.rejected}
        used = {p for (_, p) in grounding.stated} & rejected
        if used:
            problems.append(f"a rejected statement was used: {', '.join(sorted(used))}")
        return Check("contradictions", not problems,
                     "no conflicting rules, and no rejected statement was used"
                     if not problems else "; ".join(problems))

    # 9 -----------------------------------------------------------------------------
    def agreement(self, answer: str, conclusion: Conclusion, expected_headline: str,
                  opposite_headline: str | None, value_text: str | None) -> Check | None:
        if conclusion.status != "determined" or conclusion.cases:
            return None
        body = answer
        for correction in conclusion.corrections:
            body = body.replace(correction, "").replace(
                correction[:1].upper() + correction[1:], "")
        if conclusion.decision is not None and opposite_headline:
            wants_negative = bool(_NEGATION.search(expected_headline))
            contains_opposite = opposite_headline.lower() in body.lower()
            if expected_headline.lower() in body.lower():
                ok = not contains_opposite
            else:
                # A rephrasing: judge the polarity of the decision itself, not of the
                # reasons after it ("yes - it hasn't shipped yet" is still a yes).
                lead = re.split(r"[:;—]| because | as | since ", _first_sentence(body), 1)[0]
                says_negative = bool(_NEGATION.search(lead))
                ok = says_negative == wants_negative and not contains_opposite
            return Check("answer agreement", ok,
                         f"the reply states the derived decision ({'no' if wants_negative else 'yes'})"
                         if ok else "the reply's decision differs from the symbolic result")
        if value_text:
            ok = value_text.lower() in answer.lower()
            return Check("answer agreement", ok,
                         f"the reply states the derived value {value_text}" if ok
                         else f"the reply does not state the derived value {value_text}")
        return None

    # 10 ----------------------------------------------------------------------------
    def unsupported_claims(self, answer: str, draft: str, rephrased: bool) -> Check:
        if not rephrased:
            return Check("unsupported claims", True,
                         "the reply was composed only from the reasoning's conclusion")
        sentences = [s for s in _sentences(answer) if len(s.split()) > 3]
        supported = [s for s in _sentences(draft) if s]
        if not sentences or not supported:
            return Check("unsupported claims", True, "nothing to compare")
        scores = self.pack.encoder.similarity(sentences, supported)
        weak = [s for s, row in zip(sentences, scores)
                if float(row.max()) < UNSUPPORTED_SENTENCE_THRESHOLD]
        return Check("unsupported claims", not weak,
                     "every sentence of the model's phrasing is supported by the conclusion"
                     if not weak else f"sentences not supported by the conclusion: {weak[:2]}")

    # -- retrieval-only answers --------------------------------------------------------
    def citations(self, rule_clauses: list[str]) -> Check | None:
        if not rule_clauses:
            return None
        missing = [c for c in rule_clauses if self.pack.policies.clause(c) is None]
        return Check("citation integrity", not missing,
                     "every rule cites a policy clause that exists" if not missing
                     else f"rules cite clauses missing from the policy text: {', '.join(missing)}")


def run_checks(*checks: Any) -> VerificationReport:
    return VerificationReport([c for c in checks if c is not None])


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|\s+•\s+|^•\s+", text or "")
    return [p.strip(" •") for p in parts if p and p.strip(" •")]


def _first_sentence(text: str) -> str:
    sentences = _sentences(text)
    return sentences[0] if sentences else ""
