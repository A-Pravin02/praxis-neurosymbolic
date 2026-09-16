"""HYBRID. The same question, answered three ways, with the unsupported parts marked.

This is the research question made visible for one question at a time. The person's
question goes to:

    language model alone          the model, the person's identity and today's date
    language model + retrieval    the model with policy clauses and the records this
                                  person may see, retrieved by meaning
    Praxis                        understanding, authorization, symbolic reasoning over
                                  records, verification

and each baseline answer is checked against what Praxis established. There is no gold
answer in a live conversation, so the checks are stated as disagreements, never as
proven errors:

  * the baseline's decision or amount differs from the one the rules derived
  * the baseline gives a definite answer where the rules found the question needs a
    fact nobody gave, is not covered by any policy, or concerns a record the person may
    not see
  * the baseline states a number that appears in no policy parameter, in no record the
    reasoning read, and not in the question

Authorization applies to the baselines exactly as to Praxis: retrieval only returns
records this person may see. The comparison runs only when a real language model is
configured; it is never simulated.
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.config import settings


def available() -> bool:
    return settings.llm_provider != "mock"


def _numbers(text: str) -> list[float]:
    cleaned = re.sub(r"\b[A-Z]{2,6}-?\d+\b|\b\d{4}-\d{2}-\d{2}\b", " ", text or "")
    found = []
    for match in re.finditer(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?(?![\w])", cleaned):
        value = float(match.group(1).replace(",", "") + (f".{match.group(2)}" if match.group(2)
                                                          else ""))
        if value > 2 and not 2020 <= value <= 2035:
            found.append(value)
    return found


def _supported(pack, answer, question: str) -> set[float]:
    numbers: set[float] = set()
    for entity in pack.graph.entities.values():
        if entity.type in ("company", "category", "policy_version", "grade"):
            for value in entity.attributes.values():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numbers.add(float(value))
                    if 0 < float(value) < 1:
                        numbers.add(round(float(value) * 100, 4))
    for fact in answer.facts or []:
        numbers |= set(_numbers(str(fact.get("value", ""))))
    numbers |= set(_numbers(answer.answer))
    numbers |= set(_numbers(question))
    for name in answer.trace.get("reasoning_steps", []):
        numbers |= set(_numbers(str(name)))
    return numbers


def _flags(baseline: dict, praxis, supported: set[float]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    outcome = praxis.outcome
    definite = baseline["outcome"] == "answered" and (
        baseline["decision"] is not None or baseline["value"] is not None)
    if outcome == "answered":
        if isinstance(praxis.decision, bool) and baseline["decision"] is not None \
                and baseline["decision"] is not praxis.decision:
            flags.append({"kind": "contradicts_rules",
                          "detail": f"Says {'yes' if baseline['decision'] else 'no'}; the rules "
                                    f"applied to the records say "
                                    f"{'yes' if praxis.decision else 'no'}."})
        if isinstance(praxis.value, (int, float)) and not isinstance(praxis.value, bool) \
                and isinstance(baseline["value"], (int, float)) \
                and abs(float(baseline["value"]) - float(praxis.value)) > 0.011:
            flags.append({"kind": "contradicts_rules",
                          "detail": f"States {baseline['value']:,.2f}; the rules compute "
                                    f"{float(praxis.value):,.2f}."})
        if isinstance(praxis.value, str) and isinstance(baseline["value"], str) \
                and baseline["value"] != praxis.value:
            flags.append({"kind": "contradicts_rules",
                          "detail": f"Recommends {baseline['value'].replace('_', ' ')}; the "
                                    f"playbook gives {praxis.value.replace('_', ' ')}."})
    elif definite:
        why = {"conditional": "the rules found it depends on a fact the question doesn't give",
               "clarification": "it isn't clear which record or question was meant",
               "insufficient": "no policy or record covers it",
               "denied": "the record belongs to someone this person may not see",
               "needs_review": "the decision failed verification"}.get(outcome, outcome)
        flags.append({"kind": "unsupported_answer",
                      "detail": f"Gives a definite answer, but {why}."})
    unsupported = [n for n in _numbers(baseline["text"])
                   if not any(abs(n - s) <= max(0.011, abs(s) * 1e-4) for s in supported)]
    if unsupported:
        shown = ", ".join(f"{n:,.2f}".rstrip("0").rstrip(".") for n in unsupported[:4])
        flags.append({"kind": "unsupported_number",
                      "detail": f"States {shown}, which no policy parameter, record or "
                                f"part of the question supports."})
    return flags


def compare(question: str, *, email: str, roles: tuple[str, ...], controller) -> dict[str, Any]:
    from app.evaluation import ns_benchmark as nb

    pack = controller.pack
    case = {"question": question, "user": email, "roles": list(roles)}
    started = time.perf_counter()
    praxis = controller.answer(question, actor_email=email, actor_roles=roles)
    praxis_ms = (time.perf_counter() - started) * 1000
    supported = _supported(pack, praxis, question)

    systems = []
    for key, label, build in (("llm_only", "Language model alone", nb.llm_only_system),
                              ("llm_rag", "Language model + retrieval", nb.llm_rag_system)):
        run = build(pack)
        if run is None:
            continue
        answer = run(case)
        row = {"text": answer.text, "outcome": answer.outcome,
               "decision": answer.decision if isinstance(answer.decision, bool) else None,
               "value": answer.value if not isinstance(answer.value, (bool, list)) else None}
        systems.append({"key": key, "label": label, **row,
                        "sources": answer.sources, "latency_ms": round(answer.latency_ms),
                        "flags": _flags(row, praxis, supported)})
    systems.append({
        "key": "praxis", "label": "Praxis", "text": praxis.answer, "outcome": praxis.outcome,
        "decision": praxis.decision if isinstance(praxis.decision, bool) else None,
        "value": praxis.value if not isinstance(praxis.value, (bool, list)) else None,
        "decision_label": praxis.decision_label, "pipeline": praxis.pipeline_label,
        "verification": praxis.verification, "claims": praxis.claims,
        "rules_applied": praxis.rules_applied, "policy_versions": praxis.policy_versions,
        "sources": [s.get("id") for s in praxis.sources], "latency_ms": round(praxis_ms),
        "flags": [],
    })
    return {"question": question, "model": getattr(controller.llm, "model", settings.llm_model),
            "systems": systems}
