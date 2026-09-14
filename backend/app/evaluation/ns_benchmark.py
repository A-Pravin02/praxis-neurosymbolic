"""Benchmark: does neuro-symbolic reasoning beat retrieval, and what does each half add?

The research question is whether combining neural language understanding with
symbolic knowledge and rules produces a more accurate, explainable and reliable
enterprise assistant than a conventional LLM/RAG system. This harness answers it on the
frozen test set in data/<pack>/benchmark/test.yaml by running several systems on the
same questions, the same knowledge and the same users:

  neurosymbolic          the full system
  neurosymbolic_lexical  ablation: the semantic encoder replaced by lexical hashing, so
                         the contribution of *neural* understanding is isolated
  rag_extractive         retrieval over the same policy text and the person's own
                         records, answered by quoting what was retrieved - the RAG
                         pipeline with no generator, which is what runs offline
  llm_rag                the same retrieval with the configured LLM writing the answer;
                         runs only when a real model is configured, and is reported as
                         not run otherwise, never simulated

Scoring is by rule from the expectations in the test file, identical for every system.
Nothing about a system's own confidence or self-assessment is used.
"""
from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from app.config import settings
from app.hybrid.controller import AssistantAnswer, NeuroSymbolicController
from app.hybrid.knowledge_pack import KnowledgePack, load_pack
from app.symbolic.render import format_value, humanize

METRICS = [
    ("final_answer_accuracy", "Final answer accuracy", "higher"),
    ("intent_accuracy", "Intent accuracy", "higher"),
    ("entity_accuracy", "Entity / record accuracy", "higher"),
    ("pipeline_accuracy", "Pipeline-selection accuracy", "higher"),
    ("rule_execution_accuracy", "Rule execution accuracy", "higher"),
    ("retrieval_accuracy", "Retrieval accuracy", "higher"),
    ("explanation_correctness", "Explanation correctness", "higher"),
    ("hallucination_rate", "Hallucination rate", "lower"),
    ("unsupported_answer_rate", "Unsupported-answer rate", "lower"),
    ("mean_latency_ms", "Mean latency (ms)", "lower"),
]


# ---------------------------------------------------------------------------
# Systems
# ---------------------------------------------------------------------------

@dataclass
class SystemAnswer:
    text: str
    outcome: str
    goal: str | None = None
    pipeline: str | None = None
    decision: Any = None
    value: Any = None
    subject: str | None = None
    sources: list[str] = field(default_factory=list)
    explanation: str = ""
    verified: bool | None = None
    latency_ms: float = 0.0


def neurosymbolic_system(pack: KnowledgePack) -> Callable[[dict], SystemAnswer]:
    controller = NeuroSymbolicController(pack)

    def run(case: dict) -> SystemAnswer:
        started = time.perf_counter()
        answer: AssistantAnswer = controller.answer(case["question"], actor_email=case["user"])
        latency = (time.perf_counter() - started) * 1000
        trace = answer.trace
        return SystemAnswer(
            text=answer.answer, outcome=answer.outcome, goal=answer.goal,
            pipeline=answer.pipeline, decision=answer.decision, value=answer.value,
            subject=answer.subject,
            sources=[s["id"] for s in answer.sources],
            explanation=" ".join(answer.explanation + trace.get("reasoning_steps", [])),
            verified=trace.get("verification", {}).get("passed"),
            latency_ms=latency,
        )
    return run


def rag_extractive_system(pack: KnowledgePack) -> Callable[[dict], SystemAnswer]:
    """Retrieve policy clauses and the person's records; answer with what was retrieved."""
    documents = _documents(pack)

    def run(case: dict) -> SystemAnswer:
        started = time.perf_counter()
        context = _retrieve(pack, documents, case)
        text = "\n".join(d["text"] for d in context)
        return SystemAnswer(
            text=text, outcome="answered" if context else "insufficient",
            sources=[d["id"] for d in context], explanation="",
            latency_ms=(time.perf_counter() - started) * 1000)
    return run


def llm_rag_system(pack: KnowledgePack) -> Callable[[dict], SystemAnswer] | None:
    if settings.llm_provider == "mock":
        return None
    from app.neuro.base import CompletionRequest, Message, Role, Task
    from app.neuro.registry import get_llm

    llm = get_llm()
    documents = _documents(pack)

    def run(case: dict) -> SystemAnswer:
        started = time.perf_counter()
        context = _retrieve(pack, documents, case)
        prompt = (
            "Answer the customer or employee question using only the context. Reply as JSON "
            'with keys "answer" (text), "decision" (true, false or null) and "value" (a number '
            "or null).\n\nContext:\n" + "\n".join(f"[{d['id']}] {d['text']}" for d in context)
            + f"\n\nQuestion from {case['user']}: {case['question']}")
        raw = llm.complete(CompletionRequest(task=Task.GROUNDED_ANSWER,
                                             messages=[Message(Role.USER, prompt)])).text
        parsed = _parse_json(raw)
        return SystemAnswer(
            text=str(parsed.get("answer", raw)), outcome="answered",
            decision=parsed.get("decision"), value=parsed.get("value"),
            sources=[d["id"] for d in context],
            latency_ms=(time.perf_counter() - started) * 1000)
    return run


def _documents(pack: KnowledgePack) -> list[dict]:
    """Policy clauses plus every record rendered as a sentence, as RAG would index them."""
    docs = [{"id": c.id, "text": f"{c.title}. {c.text}", "owner": None}
            for c in pack.policies.clauses]
    graph, program = pack.graph, pack.program
    for entity in graph.entities.values():
        if entity.type in ("company", "category", "grade"):
            continue
        facts = ", ".join(
            f"{humanize(p)} {format_value(v, program.value_type(p), graph=graph)}"
            for p, v in graph.facts_about(entity.id).items() if p != "email")
        docs.append({"id": entity.id, "text": f"{entity.type} {entity.id} {entity.name}: {facts}.",
                     "owner": graph.owner_of(entity.id)})
    return docs


def _retrieve(pack: KnowledgePack, documents: list[dict], case: dict, k: int = 4) -> list[dict]:
    person = pack.graph.person_by_email(case["user"])
    visible = [d for d in documents
               if d["owner"] is None or (person is not None and d["owner"] == person.id)]
    scores = pack.encoder.similarity([case["question"]], [d["text"] for d in visible])[0]
    ranked = sorted(zip(visible, scores), key=lambda pair: -float(pair[1]))
    return [d for d, score in ranked[:k] if float(score) >= 0.45]


def _parse_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _as_list(value: Any) -> list:
    if value is None:
        return [None]
    return value if isinstance(value, list) else [value]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class CaseScore:
    case_id: str
    category: str
    checks: dict[str, bool | None]
    hallucinated: bool
    unsupported: bool | None
    correct: bool
    answer: SystemAnswer
    notes: list[str] = field(default_factory=list)


def score_case(case: dict, answer: SystemAnswer, *, reasoning_system: bool) -> CaseScore:
    expect = case.get("expect", {})
    checks: dict[str, bool | None] = {}
    notes: list[str] = []
    text = f"{answer.text}\n{answer.explanation}"
    lowered = text.lower()

    if "goal" in expect:
        checks["intent"] = answer.goal in _as_list(expect["goal"]) if reasoning_system else None
    if "pipeline" in expect:
        checks["pipeline"] = answer.pipeline in _as_list(expect["pipeline"]) \
            if reasoning_system else None
    if "subject" in expect:
        checks["entity"] = answer.subject == expect["subject"] if reasoning_system else None
    if "clauses" in expect:
        checks["retrieval"] = set(expect["clauses"]) <= set(answer.sources)

    outcome_ok = answer.outcome in _as_list(expect.get("outcome", answer.outcome))
    checks["outcome"] = outcome_ok

    rule_ok: bool | None = None
    if "decision" in expect and expect["decision"] is not None:
        rule_ok = answer.decision is expect["decision"] or answer.value is expect["decision"]
    if "value" in expect:
        produced = _number(answer.value if answer.value is not None else answer.decision)
        value_ok = produced is not None and abs(produced - float(expect["value"])) < 0.01
        rule_ok = value_ok if rule_ok is None else (rule_ok and value_ok)
    checks["rule_execution"] = rule_ok

    mentions = expect.get("mention", [])
    mention_ok = all(m.lower() in lowered for m in mentions) if mentions else None
    checks["explanation"] = mention_ok
    missing_ok = None
    if expect.get("missing"):
        missing_ok = all(humanize(m).split(" ")[0] in lowered or m in lowered
                         for m in expect["missing"])
        checks["missing"] = missing_ok

    # Forbidden content is about what is *asserted to the person*, so it is checked in
    # the answer only. The explanation legitimately lists every value the reasoning
    # consulted - a grade cap of ₹8,000 appears there even when the answer is ₹3,000.
    forbidden = [f for f in expect.get("forbid", []) if f.lower() in answer.text.lower()]
    if forbidden:
        notes.append(f"stated forbidden content: {forbidden}")

    # Hallucination: asserting something the knowledge does not support.
    wrong_definite = False
    expected_outcomes = _as_list(expect.get("outcome"))
    definite = answer.decision is not None and not isinstance(answer.decision, list)
    if definite and rule_ok is False and answer.outcome == "answered":
        wrong_definite = True
        notes.append("gave a definite answer that the rules do not support")
    answered_unanswerable = (
        answer.outcome == "answered" and definite
        and set(expected_outcomes) <= {"clarification", "insufficient", "denied"})
    if answered_unanswerable:
        notes.append("answered a question that needed clarification or was not covered")
    hallucinated = bool(forbidden) or wrong_definite or answered_unanswerable or (
        answer.verified is False and answer.outcome in ("answered", "conditional"))

    unsupported = None
    if answer.outcome in ("answered", "conditional"):
        unsupported = not answer.sources or answer.verified is False

    correct = outcome_ok and not forbidden and all(
        v is not False for k, v in checks.items() if k in
        ("rule_execution", "explanation", "entity", "missing", "retrieval"))
    if reasoning_system and checks.get("intent") is False:
        correct = False
    return CaseScore(case["id"], case["category"], checks, hallucinated, unsupported,
                     correct, answer, notes)


def _rate(values: list[bool | None]) -> float | None:
    scored = [v for v in values if v is not None]
    return round(sum(scored) / len(scored), 3) if scored else None


def summarise(scores: list[CaseScore]) -> dict[str, Any]:
    return {
        "cases": len(scores),
        "final_answer_accuracy": _rate([s.correct for s in scores]),
        "intent_accuracy": _rate([s.checks.get("intent") for s in scores]),
        "entity_accuracy": _rate([s.checks.get("entity") for s in scores]),
        "pipeline_accuracy": _rate([s.checks.get("pipeline") for s in scores]),
        "rule_execution_accuracy": _rate([s.checks.get("rule_execution") for s in scores]),
        "retrieval_accuracy": _rate([s.checks.get("retrieval") for s in scores]),
        "explanation_correctness": _rate([s.checks.get("explanation") for s in scores]),
        "hallucination_rate": _rate([s.hallucinated for s in scores]),
        "unsupported_answer_rate": _rate([s.unsupported for s in scores]),
        "mean_latency_ms": round(statistics.mean(s.answer.latency_ms for s in scores), 1)
        if scores else None,
    }


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

def load_cases(pack_dir: Path, split: str = "test") -> list[dict]:
    return yaml.safe_load((pack_dir / "benchmark" / f"{split}.yaml").read_text(
        encoding="utf-8"))["cases"]


def run_benchmark(*, pack_dir: str | None = None, systems: list[str] | None = None,
                  limit: int | None = None, split: str = "test") -> dict[str, Any]:
    wanted = systems or ["neurosymbolic", "neurosymbolic_lexical", "rag_extractive", "llm_rag"]
    semantic_pack = load_pack(pack_dir, "semantic")
    cases = load_cases(semantic_pack.directory, split)[:limit]

    builders: dict[str, Callable[[], Callable[[dict], SystemAnswer] | None]] = {
        "neurosymbolic": lambda: neurosymbolic_system(semantic_pack),
        "neurosymbolic_lexical": lambda: neurosymbolic_system(load_pack(pack_dir, "hashing")),
        "rag_extractive": lambda: rag_extractive_system(semantic_pack),
        "llm_rag": lambda: llm_rag_system(semantic_pack),
    }
    report: dict[str, Any] = {
        "pack": semantic_pack.name, "split": split, "cases": len(cases),
        "encoder": semantic_pack.encoder.name, "semantic": semantic_pack.encoder.semantic,
        "llm_provider": settings.llm_provider, "systems": {}, "not_run": {},
    }
    for name in wanted:
        system = builders[name]()
        if system is None:
            report["not_run"][name] = ("no real language model is configured "
                                       "(LLM_PROVIDER=mock); not simulated")
            continue
        reasoning = name.startswith("neurosymbolic")
        scores = [score_case(case, system(case), reasoning_system=reasoning) for case in cases]
        categories = sorted({s.category for s in scores})
        report["systems"][name] = {
            "overall": summarise(scores),
            "by_category": {c: summarise([s for s in scores if s.category == c])
                            for c in categories},
            "cases": [{
                "id": s.case_id, "category": s.category, "correct": s.correct,
                "hallucinated": s.hallucinated, "checks": s.checks, "notes": s.notes,
                "outcome": s.answer.outcome, "pipeline": s.answer.pipeline,
                "goal": s.answer.goal, "subject": s.answer.subject,
                "decision": s.answer.decision if not isinstance(s.answer.decision, list)
                else len(s.answer.decision),
                "value": s.answer.value if not isinstance(s.answer.value, list) else None,
                "answer": s.answer.text[:600],
            } for s in scores],
        }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    systems = list(report["systems"])
    lines = [f"# Neuro-symbolic benchmark — {report['pack']} ({report['split']}, "
             f"{report['cases']} cases)", "",
             f"Encoder: `{report['encoder']}` · LLM provider: `{report['llm_provider']}`", "",
             "| Metric | " + " | ".join(systems) + " | better |",
             "|---|" + "---|" * len(systems) + "---|"]
    for key, label, better in METRICS:
        row = []
        for name in systems:
            value = report["systems"][name]["overall"].get(key)
            row.append("n/a" if value is None else (f"{value:.3f}" if isinstance(value, float)
                                                    and key != "mean_latency_ms"
                                                    else str(value)))
        lines.append(f"| {label} | " + " | ".join(row) + f" | {better} |")
    for name, reason in report.get("not_run", {}).items():
        lines.append("")
        lines.append(f"`{name}` not run: {reason}.")
    lines += ["", "## Final answer accuracy by category", "",
              "| Category | " + " | ".join(systems) + " |",
              "|---|" + "---|" * len(systems)]
    categories = sorted({c for s in systems for c in report["systems"][s]["by_category"]})
    for category in categories:
        row = []
        for name in systems:
            summary = report["systems"][name]["by_category"].get(category, {})
            value = summary.get("final_answer_accuracy")
            row.append("n/a" if value is None else f"{value:.2f} ({summary['cases']})")
        lines.append(f"| {category} | " + " | ".join(row) + " |")
    lines += ["", "## Hallucination rate by category", "",
              "| Category | " + " | ".join(systems) + " |",
              "|---|" + "---|" * len(systems)]
    for category in categories:
        row = []
        for name in systems:
            value = report["systems"][name]["by_category"].get(category, {}).get(
                "hallucination_rate")
            row.append("n/a" if value is None else f"{value:.2f}")
        lines.append(f"| {category} | " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"
