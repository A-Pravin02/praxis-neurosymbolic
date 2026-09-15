"""Benchmark: does neuro-symbolic reasoning reduce unsupported enterprise decisions?

The research question is whether adding symbolic reasoning and verification reduces
hallucinated or unsupported enterprise decisions compared with LLM and RAG baselines. This
harness answers it by running several systems on the same frozen questions, the same
knowledge and the same users:

  llm_only               a language model with the person's identity and today's date, and
                         no company knowledge at all
  llm_rag                the model with retrieval over every policy document (including
                         superseded versions, as a document index holds them) and over the
                         records the person may see
  llm_rag_rules          the model extracts a goal, a record and stated facts as JSON; a
                         *basic* rule engine applies the same rules - two-valued (unknown is
                         false), newest policy version always, statements accepted as
                         given, no grounding, claim checks or verification - and the model
                         phrases the result
  neurosymbolic          the full system
  neurosymbolic_lexical  ablation: the semantic encoder replaced by lexical hashing
  rag_extractive         retrieval answered by quoting what was retrieved (no generator)

The LLM systems run only when a real model is configured (a hosted provider or the local
open-weights provider); they are reported as not run otherwise, never simulated.

Scoring is by rule from the expectations in the split file, identical for every system.
Nothing about a system's own confidence or self-assessment is used, except that an answer
a system itself marks as failing verification counts as unsupported.
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
    ("decision_accuracy", "Decision accuracy", "higher"),
    ("calculation_accuracy", "Calculation accuracy", "higher"),
    ("policy_compliance", "Policy compliance", "higher"),
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
LLM_SYSTEMS = ("llm_only", "llm_rag", "llm_rag_rules")
OUTCOMES = {"answered": "answered", "needs_more_information": "conditional",
            "ask_which_record": "clarification", "not_covered": "insufficient",
            "not_permitted": "denied"}


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
    raw: str = ""


def neurosymbolic_system(pack: KnowledgePack) -> Callable[[dict], SystemAnswer]:
    from app.neuro.providers.mock import MockLLMProvider

    # The deterministic realizer: this measures the reasoning, not a model's phrasing.
    controller = NeuroSymbolicController(pack, llm=MockLLMProvider("praxis-mock-1"))

    def run(case: dict) -> SystemAnswer:
        started = time.perf_counter()
        answer: AssistantAnswer = controller.answer(
            case["question"], actor_email=case["user"], actor_roles=tuple(case.get("roles", ())))
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


_ANSWER_FORMAT = (
    'Reply with JSON only, with these keys: "answer" (what you tell the person), "outcome" '
    '(one of "answered", "needs_more_information", "ask_which_record", "not_covered", '
    '"not_permitted"), "decision" (true, false or null for a yes/no question), "value" (a '
    'number for an amount or a count of days, else null), and "result" (for a recommended '
    'resolution one of "replacement", "full_refund", "cancel_order", '
    '"return_and_refund_after_inspection", "goodwill_credit", "no_standard_remedy"; for '
    'routing an issue one of "payments_team", "logistics_desk", "returns_desk", '
    '"order_care"; else null).')


def _llm():
    if settings.llm_provider == "mock":
        return None
    from app.neuro.registry import get_llm
    return get_llm()


def _who(pack: KnowledgePack, case: dict) -> str:
    person = pack.graph.person_by_email(case["user"])
    roles = ", ".join(case.get("roles", [])) or (person.type if person else "user")
    name = person.name if person else case["user"]
    return (f"The person asking is {name} ({case['user']}, role: {roles}) at "
            f"{pack.graph.display_name('meridian')}. Today is {pack.graph.as_of.isoformat()}.")


def _ask(llm, system: str, prompt: str, max_tokens: int = 350) -> str:
    from app.neuro.base import CompletionRequest, Message, Role, Task

    return llm.complete(CompletionRequest(task=Task.GROUNDED_ANSWER, system=system,
                                          messages=[Message(Role.USER, prompt)],
                                          max_tokens=max_tokens, temperature=0.0)).text


def _from_json(raw: str, started: float, sources: list[str]) -> SystemAnswer:
    parsed = _parse_json(raw)
    decision = parsed.get("decision")
    value = parsed.get("value")
    result = parsed.get("result")
    return SystemAnswer(
        text=str(parsed.get("answer", raw)),
        outcome=OUTCOMES.get(str(parsed.get("outcome", "answered")), "answered"),
        decision=decision if isinstance(decision, bool) else None,
        value=result if isinstance(result, str) and result else _numeric(value),
        sources=sources, latency_ms=(time.perf_counter() - started) * 1000, raw=raw)


def llm_only_system(pack: KnowledgePack) -> Callable[[dict], SystemAnswer] | None:
    llm = _llm()
    if llm is None:
        return None

    def run(case: dict) -> SystemAnswer:
        started = time.perf_counter()
        raw = _ask(llm, "You are a customer service and employee assistant for an online "
                        "marketplace. " + _ANSWER_FORMAT,
                   f"{_who(pack, case)}\n\nQuestion: {case['question']}")
        return _from_json(raw, started, [])
    return run


def llm_rag_system(pack: KnowledgePack) -> Callable[[dict], SystemAnswer] | None:
    llm = _llm()
    if llm is None:
        return None
    documents = _documents(pack)

    def run(case: dict) -> SystemAnswer:
        started = time.perf_counter()
        context = _retrieve(pack, documents, case, k=6)
        raw = _ask(llm, "You are a customer service and employee assistant. Answer using only "
                        "the context. " + _ANSWER_FORMAT,
                   f"{_who(pack, case)}\n\nContext:\n"
                   + "\n".join(f"[{d['id']}] {d['text']}" for d in context)
                   + f"\n\nQuestion: {case['question']}")
        return _from_json(raw, started, [d["id"] for d in context])
    return run


def llm_rag_rules_system(pack: KnowledgePack) -> Callable[[dict], SystemAnswer] | None:
    """The model reads the question into structure; a basic rule engine decides."""
    from app.symbolic.reasoner import Reasoner

    llm = _llm()
    if llm is None:
        return None
    documents = _documents(pack)
    program = pack.program
    goals = "\n".join(f"- {g.predicate} ({g.subject_type}): {g.description}"
                      for g in program.goals.values())
    inputs = "\n".join(
        f"- {p}: " + ", ".join(str(v.value) if v.value is not None else v.value_type
                               for v in values)
        for p, values in program.inputs.items())

    def run(case: dict) -> SystemAnswer:
        started = time.perf_counter()
        context = _retrieve(pack, documents, case, k=6)
        extraction_prompt = (
            f"{_who(pack, case)}\nQuestion: {case['question']}\n\nContext:\n"
            + "\n".join(f"[{d['id']}] {d['text']}" for d in context)
            + f"\n\nGoals:\n{goals}\n\nFacts a person may state:\n{inputs}\n\n"
            'Reply with JSON only: {"goal": one goal name or null, "record": the record id '
            'the question is about (like ORD-12345 or CLM-3001) or null, "facts": {fact name: '
            'value} for facts the question states}.')
        extraction = _parse_json(_ask(llm, "You convert questions into structured data.",
                                      extraction_prompt, 200))
        goal = extraction.get("goal") if extraction.get("goal") in program.goals else None
        record = str(extraction.get("record") or "")
        rule_text = "no rule was applied"
        decision = value = None
        if goal:
            subject = record if pack.graph.has_entity(record) else f"case:{program.goals[goal].subject_type}"
            stated = {}
            for name, raw_value in (extraction.get("facts") or {}).items():
                if name in program.inputs or name in program.attributes:
                    stated[(subject, name)] = _coerce(raw_value)
            person = pack.graph.person_by_email(case["user"])
            if subject.startswith("case:") and person is not None:
                link = {"order": "placed_by", "claim": "claimant"}.get(subject.split(":")[1])
                if link:
                    stated[(subject, link)] = person.id
            reasoner = Reasoner(program, pack.graph, stated=stated,
                                open_subjects={subject} if subject.startswith("case:") else set(),
                                two_valued=True, latest_versions=True)
            result = reasoner.evaluate(subject, goal)
            shown = format_value(result.value, program.value_type(goal), graph=pack.graph)
            because = result.decided_by.because if result.decided_by else ""
            rule_text = f"{goal}({subject}) = {shown}" + (f" because {because}" if because else "")
            if isinstance(result.value, bool):
                decision = result.value
            elif result.value is not None:
                value = result.value
        raw = _ask(llm, "You are a customer service and employee assistant. A rule engine has "
                        "already decided the answer; explain it. " + _ANSWER_FORMAT,
                   f"{_who(pack, case)}\n\nContext:\n"
                   + "\n".join(f"[{d['id']}] {d['text']}" for d in context)
                   + f"\n\nRule engine result: {rule_text}\n\nQuestion: {case['question']}")
        answer = _from_json(raw, started, [d["id"] for d in context])
        # The rule engine's result is the decision; the model only phrases it.
        if goal:
            answer.goal = goal
            answer.decision = decision if decision is not None else answer.decision
            answer.value = value if value is not None else answer.value
        return answer
    return run


def _documents(pack: KnowledgePack) -> list[dict]:
    """Every policy version plus every record rendered as a sentence, as RAG would index them."""
    docs = [{"id": f"{c.id}@v{c.version}", "text": f"{c.document} (version {c.version}, "
                                                   f"effective {c.effective or 'n/a'}). "
                                                   f"{c.title}. {c.text}", "entity": None}
            for c in pack.policies.all_clauses]
    graph, program = pack.graph, pack.program
    for entity in graph.entities.values():
        if entity.type in ("company", "category", "grade", "policy_version"):
            continue
        facts = ", ".join(
            f"{humanize(p)} {format_value(v, program.value_type(p), graph=graph)}"
            for p, v in graph.facts_about(entity.id).items() if p != "email")
        docs.append({"id": entity.id, "text": f"{entity.type} {entity.id} {entity.name}: {facts}.",
                     "entity": entity.id})
    return docs


def _retrieve(pack: KnowledgePack, documents: list[dict], case: dict, k: int = 4) -> list[dict]:
    """Retrieval restricted to what the person may see - the same authorization Praxis uses."""
    from app.hybrid.grounding import FactGrounding

    actor = FactGrounding(pack).actor(case["user"], tuple(case.get("roles", ())))
    view = pack.graph.view_for(actor.access_kind, actor.id, pack.program)
    visible = [d for d in documents if d["entity"] is None or view.visible(d["entity"])]
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


def _numeric(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip())
    except ValueError:
        return None


def _coerce(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes"):
            return True
        if lowered in ("false", "no"):
            return False
        number = _numeric(value)
        return number if number is not None else value.strip()
    return value


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
    compliant: bool
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
        checks["retrieval"] = set(expect["clauses"]) <= {s.split("@")[0] for s in answer.sources}

    outcome_ok = answer.outcome in _as_list(expect.get("outcome", answer.outcome))
    checks["outcome"] = outcome_ok

    decision_ok: bool | None = None
    if "decision" in expect and expect["decision"] is not None:
        decision_ok = answer.decision is expect["decision"] or answer.value is expect["decision"]
    if "result" in expect:
        produced = answer.value if isinstance(answer.value, str) else answer.decision
        decision_ok = str(produced) == str(expect["result"])
    value_ok: bool | None = None
    if "value" in expect:
        produced = _number(answer.value if answer.value is not None else answer.decision)
        value_ok = produced is not None and abs(produced - float(expect["value"])) < 0.01
    checks["decision"] = decision_ok
    checks["calculation"] = value_ok
    rule_ok = decision_ok if value_ok is None else (value_ok if decision_ok is None
                                                    else decision_ok and value_ok)
    checks["rule_execution"] = rule_ok

    mentions = expect.get("mention", [])
    checks["explanation"] = all(m.lower() in lowered for m in mentions) if mentions else None
    if expect.get("missing"):
        checks["missing"] = all(humanize(m).split(" ")[0] in lowered or m in lowered
                                for m in expect["missing"])

    # Forbidden content is about what is *asserted to the person*, so it is checked in
    # the answer only. The explanation legitimately lists every value the reasoning
    # consulted.
    forbidden = [f for f in expect.get("forbid", []) if f.lower() in answer.text.lower()]
    if forbidden:
        notes.append(f"stated forbidden content: {forbidden}")

    # Hallucination: asserting something the knowledge does not support.
    expected_outcomes = _as_list(expect.get("outcome"))
    asserted = answer.outcome in ("answered", "conditional")
    definite = answer.outcome == "answered" and (
        (answer.decision is not None and not isinstance(answer.decision, list))
        or answer.value is not None)
    wrong_definite = definite and rule_ok is False
    if wrong_definite:
        notes.append("gave a definite answer that the rules do not support")
    answered_unanswerable = definite and \
        set(expected_outcomes) <= {"clarification", "insufficient", "denied"}
    if answered_unanswerable:
        notes.append("answered a question that needed clarification, was not covered or "
                     "was not permitted")
    hallucinated = bool(forbidden) or wrong_definite or answered_unanswerable or (
        answer.verified is False and asserted)

    unsupported = None
    if asserted:
        unsupported = not answer.sources or answer.verified is False

    compliant = not (forbidden or wrong_definite or answered_unanswerable)
    correct = outcome_ok and not forbidden and all(
        v is not False for k, v in checks.items() if k in
        ("rule_execution", "explanation", "entity", "missing", "retrieval"))
    if reasoning_system and checks.get("intent") is False:
        correct = False
    return CaseScore(case["id"], case["category"], checks, hallucinated, unsupported,
                     correct, compliant, answer, notes)


def _rate(values: list[bool | None]) -> float | None:
    scored = [v for v in values if v is not None]
    return round(sum(scored) / len(scored), 3) if scored else None


def summarise(scores: list[CaseScore]) -> dict[str, Any]:
    return {
        "cases": len(scores),
        "final_answer_accuracy": _rate([s.correct for s in scores]),
        "decision_accuracy": _rate([s.checks.get("decision") for s in scores]),
        "calculation_accuracy": _rate([s.checks.get("calculation") for s in scores]),
        "policy_compliance": _rate([s.compliant for s in scores]),
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
    wanted = systems or ["neurosymbolic", "neurosymbolic_lexical", "rag_extractive",
                         *LLM_SYSTEMS]
    semantic_pack = load_pack(pack_dir, "semantic")
    cases = load_cases(semantic_pack.directory, split)[:limit]

    builders: dict[str, Callable[[], Callable[[dict], SystemAnswer] | None]] = {
        "neurosymbolic": lambda: neurosymbolic_system(semantic_pack),
        "neurosymbolic_lexical": lambda: neurosymbolic_system(load_pack(pack_dir, "hashing")),
        "rag_extractive": lambda: rag_extractive_system(semantic_pack),
        "llm_only": lambda: llm_only_system(semantic_pack),
        "llm_rag": lambda: llm_rag_system(semantic_pack),
        "llm_rag_rules": lambda: llm_rag_rules_system(semantic_pack),
    }
    model = ""
    if settings.llm_provider != "mock" and any(s in LLM_SYSTEMS for s in wanted):
        from app.neuro.registry import get_llm
        model = getattr(get_llm(), "model", "")
    report: dict[str, Any] = {
        "pack": semantic_pack.name, "split": split, "cases": len(cases),
        "encoder": semantic_pack.encoder.name, "semantic": semantic_pack.encoder.semantic,
        "llm_provider": settings.llm_provider, "llm_model": model,
        "systems": {}, "not_run": {},
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
                "hallucinated": s.hallucinated, "compliant": s.compliant, "checks": s.checks,
                "notes": s.notes, "outcome": s.answer.outcome, "pipeline": s.answer.pipeline,
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
    model = f" · model `{report['llm_model']}`" if report.get("llm_model") else ""
    lines = [f"# Neuro-symbolic benchmark — {report['pack']} ({report['split']}, "
             f"{report['cases']} cases)", "",
             f"Encoder: `{report['encoder']}` · LLM provider: `{report['llm_provider']}`{model}",
             "", "| Metric | " + " | ".join(systems) + " | better |",
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
    categories = sorted({c for s in systems for c in report["systems"][s]["by_category"]})
    for title, key in (("Final answer accuracy", "final_answer_accuracy"),
                       ("Hallucination rate", "hallucination_rate")):
        lines += ["", f"## {title} by category", "",
                  "| Category | " + " | ".join(systems) + " |",
                  "|---|" + "---|" * len(systems)]
        for category in categories:
            row = []
            for name in systems:
                summary = report["systems"][name]["by_category"].get(category, {})
                value = summary.get(key)
                row.append("n/a" if value is None else
                           (f"{value:.2f} ({summary['cases']})" if key == "final_answer_accuracy"
                            else f"{value:.2f}"))
            lines.append(f"| {category} | " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"
