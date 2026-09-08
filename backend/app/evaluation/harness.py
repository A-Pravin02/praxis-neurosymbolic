"""Evaluation harness: run one suite through four architectures and compare.

The four modes share the same language model, the same corpus and the same
questions. Only the architecture around the model changes:

    llm_only        the model answers unaided
    llm_rag         retrieval is added
    llm_rag_verify  retrieval plus claim verification and the evidence gate
    neurosymbolic   the full pipeline: knowledge, rules, verification, gate

Every number in the report comes from an actual run. Nothing is estimated,
extrapolated or hard-coded, and a case the harness cannot judge is counted as
unjudged rather than as a pass.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.core.logging import get_logger, log_event
from app.db.models.evaluation import EvalCaseResult, EvalRun
from app.db.models.tenancy import User, UserLookup
from app.db.session import SessionLocal, bind_tenant
from app.evaluation.cases import EvalCase, get_suite
from app.neuro.heuristics import extract_numbers, normalize
from app.orchestrator.pipeline import AssistantTurn, Orchestrator
from app.verification.gate import GateDecision

logger = get_logger("praxis.eval")

MODES = ["llm_only", "llm_rag", "llm_rag_verify", "neurosymbolic"]

ABSTAIN_MARKERS = (
    "don't have enough information",
    "do not have enough information",
    "insufficient evidence",
    "i don't have a grounded answer",
    "don't have anything in your workspace documents",
    "cannot determine",
    "not enough to go on",
)


@dataclass
class CaseOutcome:
    case_id: str
    mode: str
    question: str
    answer: str
    expected_kind: str
    gate_decision: str
    verification_status: str
    confidence: float
    latency_ms: int
    correct: bool = False
    judged: bool = True
    grounded: bool = False
    hallucinated: bool = False
    abstained: bool = False
    citation_correct: bool = False
    conflict_detected: bool = False
    leaked_forbidden: bool = False
    unsupported_claims: int = 0
    total_claims: int = 0
    retrieval_precision: float = 0.0
    retrieval_recall: float = 0.0
    cited_documents: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["answer"] = self.answer[:1200]
        return data


@dataclass
class ModeMetrics:
    mode: str
    cases: int = 0
    factual_accuracy: float | None = None
    groundedness: float | None = None
    citation_accuracy: float | None = None
    unsupported_claim_rate: float | None = None
    hallucination_rate: float | None = None
    contradiction_detection_rate: float | None = None
    symbolic_accuracy: float | None = None
    abstention_accuracy: float | None = None
    false_abstention_rate: float | None = None
    forbidden_content_rate: float | None = None
    retrieval_precision: float | None = None
    retrieval_recall: float | None = None
    mean_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Judging one case
# ---------------------------------------------------------------------------

def _looks_like_abstention(answer: str, turn: AssistantTurn) -> bool:
    if turn.gate_decision == GateDecision.ABSTAIN.value:
        return True
    lowered = (answer or "").lower()
    return any(marker in lowered for marker in ABSTAIN_MARKERS)


_CITATION_MARK = re.compile(r"\[(\d{1,2})\]")


def _asserted_numbers(text: str) -> set[float]:
    """Numbers the text actually asserts, ignoring citation markers."""
    return {round(n, 6) for n in extract_numbers(_CITATION_MARK.sub(" ", text or ""))}


def _states(answer: str, values: list[float]) -> bool:
    if not values:
        return False
    present = _asserted_numbers(answer)
    return any(round(v, 6) in present for v in values)


def _contains_phrases(answer: str, phrases: list[str]) -> bool:
    lowered = (answer or "").lower()
    return all(p.lower() in lowered for p in phrases) if phrases else False


def _any_phrase(answer: str, phrases: list[str]) -> bool:
    lowered = (answer or "").lower()
    return any(p.lower() in lowered for p in phrases)


def judge(case: EvalCase, turn: AssistantTurn, mode: str) -> CaseOutcome:
    answer = turn.answer or ""
    outcome = CaseOutcome(
        case_id=case.id, mode=mode, question=case.question, answer=answer,
        expected_kind=case.expected_kind, gate_decision=turn.gate_decision,
        verification_status=turn.verification_status, confidence=turn.confidence,
        latency_ms=turn.latency_ms,
    )
    outcome.abstained = _looks_like_abstention(answer, turn)
    outcome.cited_documents = sorted({c["document_title"] for c in turn.citations})
    outcome.conflict_detected = bool(
        turn.reasoning.get("contradictions")
    ) or turn.gate_decision == GateDecision.REPORT_CONFLICT.value

    claims = turn.claims or []
    outcome.total_claims = len(claims)
    outcome.unsupported_claims = sum(
        1 for c in claims if c.get("status") in ("unsupported", "weak", "contradicted")
    )

    # Retrieval quality against the gold document set.
    if case.gold_documents:
        cited = set(outcome.cited_documents)
        gold = set(case.gold_documents)
        if cited:
            outcome.retrieval_precision = len(cited & gold) / len(cited)
        outcome.retrieval_recall = len(cited & gold) / len(gold)
        outcome.citation_correct = bool(cited & gold)

    # Groundedness is judged by the harness, identically for every mode.
    # Reading each mode's own internal grounding report would compare the modes
    # against different yardsticks: only the full pipeline runs that check.
    evidence = " ".join(c.get("snippet", "") for c in turn.citations)
    evidence += " " + " ".join(c.get("text", "") for c in (turn.claims or []))
    evidence_numbers = _asserted_numbers(evidence)
    answer_numbers = _asserted_numbers(answer)
    outcome.grounded = (
        outcome.abstained or not answer_numbers
        or answer_numbers.issubset(evidence_numbers)
    )
    if turn.grounding:
        outcome.notes.append(
            f"pipeline grounding score {turn.grounding.get('score')}"
        )

    outcome.leaked_forbidden = (
        _any_phrase(answer, case.forbidden_phrases) if case.forbidden_phrases else False
    )
    states_forbidden_value = (
        _states(answer, case.forbidden_values) if case.forbidden_values else False
    )

    kind = case.expected_kind
    if kind in ("grounded", "symbolic"):
        stated = _states(answer, case.expected_values) if case.expected_values else None
        phrases_ok = (_contains_phrases(answer, case.expected_phrases)
                      if case.expected_phrases else True)
        if stated is None:
            outcome.correct = (not outcome.abstained) and phrases_ok
        else:
            outcome.correct = bool(stated) and not states_forbidden_value and phrases_ok
        outcome.hallucinated = (
            states_forbidden_value or (not outcome.grounded and not outcome.abstained)
        )
    elif kind == "abstain":
        outcome.correct = outcome.abstained and not states_forbidden_value
        # Asserting a value where the correct behaviour was refusal is the
        # canonical hallucination this platform exists to prevent.
        outcome.hallucinated = (not outcome.abstained) and (
            states_forbidden_value or bool(_asserted_numbers(answer))
            or outcome.leaked_forbidden
        )
    elif kind == "conflict":
        outcome.correct = outcome.conflict_detected and not outcome.abstained
        outcome.hallucinated = (
            not outcome.conflict_detected
            and _states(answer, case.expected_values)
        )
    elif kind == "denied":
        outcome.correct = turn.gate_decision == GateDecision.DENY.value or (
            turn.policy_decision or {}
        ).get("decision") in ("DENY", "INDETERMINATE")
        outcome.hallucinated = False
    elif kind == "general":
        outcome.correct = (
            _contains_phrases(answer, case.expected_phrases)
            if case.expected_phrases else not outcome.abstained
        )
        outcome.hallucinated = False
    else:
        outcome.judged = False

    if outcome.leaked_forbidden:
        outcome.correct = False
        outcome.notes.append("Answer contained forbidden content.")
    return outcome


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

def _principal_for(email: str):
    from app.auth.service import resolve_principal

    session = SessionLocal()
    try:
        bind_tenant(session, None)
        lookup = session.get(UserLookup, email.lower())
        if lookup is None:
            raise LookupError(f"Unknown evaluation user: {email}")
        bind_tenant(session, lookup.tenant_id, lookup.user_id)
        user = session.get(User, lookup.user_id)
        return resolve_principal(session, user)
    finally:
        session.close()


def run_case(case: EvalCase, mode: str) -> CaseOutcome:
    principal = _principal_for(case.user_email)
    session = SessionLocal()
    try:
        bind_tenant(session, principal.tenant_id, principal.user_id)
        turn = Orchestrator(session, principal).answer(case.question,
                                                       pipeline_mode=mode)
        session.rollback()   # evaluation never mutates the workspace
    finally:
        session.close()
    return judge(case, turn, mode)


def aggregate(mode: str, outcomes: list[CaseOutcome]) -> ModeMetrics:
    metrics = ModeMetrics(mode=mode, cases=len(outcomes))
    if not outcomes:
        return metrics

    judged = [o for o in outcomes if o.judged]
    factual = [o for o in judged if o.expected_kind in ("grounded", "symbolic")]
    symbolic = [o for o in judged if o.expected_kind == "symbolic"]
    abstain = [o for o in judged if o.expected_kind == "abstain"]
    conflict = [o for o in judged if o.expected_kind == "conflict"]
    answerable = [o for o in judged
                  if o.expected_kind in ("grounded", "symbolic", "general")]
    retrieval = [o for o in outcomes if o.retrieval_recall or o.retrieval_precision]

    def ratio(numerator: int, denominator: int) -> float | None:
        """None when the suite has no case of this kind.

        Reporting 0.000 for a metric nothing measured reads as total failure. A
        suite of table statistics contains no contradiction cases, and saying so
        is not the same as saying the system detected none.
        """
        return round(numerator / denominator, 4) if denominator else None

    metrics.factual_accuracy = ratio(sum(o.correct for o in factual), len(factual))
    metrics.symbolic_accuracy = ratio(sum(o.correct for o in symbolic), len(symbolic))
    metrics.abstention_accuracy = ratio(sum(o.correct for o in abstain), len(abstain))
    metrics.false_abstention_rate = ratio(
        sum(1 for o in answerable if o.abstained), len(answerable))
    metrics.contradiction_detection_rate = ratio(
        sum(o.conflict_detected for o in conflict), len(conflict))
    metrics.groundedness = ratio(sum(o.grounded for o in judged), len(judged))
    metrics.hallucination_rate = ratio(sum(o.hallucinated for o in judged), len(judged))
    metrics.forbidden_content_rate = ratio(
        sum(o.leaked_forbidden for o in outcomes), len(outcomes))
    cited = [o for o in judged if o.cited_documents]
    metrics.citation_accuracy = ratio(sum(o.citation_correct for o in cited), len(cited))

    total_claims = sum(o.total_claims for o in outcomes)
    metrics.unsupported_claim_rate = ratio(
        sum(o.unsupported_claims for o in outcomes), total_claims)

    if retrieval:
        metrics.retrieval_precision = round(
            sum(o.retrieval_precision for o in retrieval) / len(retrieval), 4)
        metrics.retrieval_recall = round(
            sum(o.retrieval_recall for o in retrieval) / len(retrieval), 4)

    latencies = sorted(o.latency_ms for o in outcomes)
    metrics.mean_latency_ms = round(statistics.fmean(latencies), 2)
    index = max(0, int(round(0.95 * (len(latencies) - 1))))
    metrics.p95_latency_ms = float(latencies[index])
    return metrics


@dataclass
class EvaluationReport:
    suite: str
    generated_at: str
    llm_provider: str
    llm_model: str
    embedding_provider: str
    metrics: dict[str, ModeMetrics]
    outcomes: dict[str, list[CaseOutcome]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite, "generated_at": self.generated_at,
            "llm_provider": self.llm_provider, "llm_model": self.llm_model,
            "embedding_provider": self.embedding_provider,
            "metrics": {k: v.as_dict() for k, v in self.metrics.items()},
            "outcomes": {k: [o.as_dict() for o in v] for k, v in self.outcomes.items()},
        }


def run_suite(suite: str = "demo", modes: list[str] | None = None,
              limit: int | None = None,
              persist_tenant_id: uuid.UUID | None = None) -> EvaluationReport:
    cases = get_suite(suite)
    if limit:
        cases = cases[:limit]
    modes = modes or MODES

    metrics: dict[str, ModeMetrics] = {}
    outcomes: dict[str, list[CaseOutcome]] = {}
    for mode in modes:
        results = [run_case(case, mode) for case in cases]
        outcomes[mode] = results
        metrics[mode] = aggregate(mode, results)
        log_event(logger, 20, "evaluation mode complete", mode=mode,
                  cases=len(results),
                  factual_accuracy=metrics[mode].factual_accuracy,
                  hallucination_rate=metrics[mode].hallucination_rate)

    report = EvaluationReport(
        suite=suite,
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        llm_provider=settings.llm_provider, llm_model=settings.llm_model,
        embedding_provider=settings.embedding_provider,
        metrics=metrics, outcomes=outcomes,
    )
    if persist_tenant_id is not None:
        _persist(report, persist_tenant_id)
    return report


def _persist(report: EvaluationReport, tenant_id: uuid.UUID) -> None:
    session = SessionLocal()
    try:
        bind_tenant(session, tenant_id)
        for mode, metrics in report.metrics.items():
            run = EvalRun(
                tenant_id=tenant_id, suite=report.suite, mode=mode,
                llm_provider=report.llm_provider, llm_model=report.llm_model,
                case_count=metrics.cases, metrics_json=metrics.as_dict(),
            )
            session.add(run)
            session.flush()
            for outcome in report.outcomes[mode]:
                session.add(EvalCaseResult(
                    tenant_id=tenant_id, run_id=run.id, case_id=outcome.case_id,
                    question=outcome.question, answer_text=outcome.answer[:8000],
                    expected_kind=outcome.expected_kind, correct=outcome.correct,
                    grounded=outcome.grounded, hallucinated=outcome.hallucinated,
                    abstained=outcome.abstained,
                    citation_correct=outcome.citation_correct,
                    unsupported_claims=outcome.unsupported_claims,
                    total_claims=outcome.total_claims,
                    retrieval_precision=outcome.retrieval_precision,
                    retrieval_recall=outcome.retrieval_recall,
                    latency_ms=outcome.latency_ms,
                    detail_json={"gate": outcome.gate_decision,
                                 "verification": outcome.verification_status,
                                 "confidence": outcome.confidence,
                                 "notes": outcome.notes},
                ))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_ROWS = [
    ("factual_accuracy", "Factual accuracy", "higher"),
    ("symbolic_accuracy", "Symbolic reasoning accuracy", "higher"),
    ("groundedness", "Groundedness", "higher"),
    ("citation_accuracy", "Citation accuracy", "higher"),
    ("abstention_accuracy", "Abstention accuracy", "higher"),
    ("contradiction_detection_rate", "Contradiction detection", "higher"),
    ("retrieval_precision", "Retrieval precision", "higher"),
    ("retrieval_recall", "Retrieval recall", "higher"),
    ("hallucination_rate", "Hallucination rate", "lower"),
    ("unsupported_claim_rate", "Unsupported claim rate", "lower"),
    ("false_abstention_rate", "False abstention rate", "lower"),
    ("forbidden_content_rate", "Forbidden content rate", "lower"),
    ("mean_latency_ms", "Mean latency (ms)", "lower"),
    ("p95_latency_ms", "p95 latency (ms)", "lower"),
]


def render_markdown(report: EvaluationReport) -> str:
    modes = list(report.metrics.keys())
    lines = [
        f"# Evaluation report - suite `{report.suite}`",
        "",
        f"Generated {report.generated_at} | LLM provider `{report.llm_provider}` "
        f"(`{report.llm_model}`) | embeddings `{report.embedding_provider}`",
        "",
        "All figures are computed from this run. Higher is better except where the",
        "metric name says otherwise.",
        "",
        "| Metric | " + " | ".join(modes) + " | better |",
        "|---|" + "---|" * (len(modes) + 1),
    ]
    for key, label, direction in _ROWS:
        values = [getattr(report.metrics[m], key) for m in modes]
        if all(v is None for v in values):
            continue          # nothing in this suite measures it
        formatted = []
        for value in values:
            if value is None:
                formatted.append("n/a")
            elif isinstance(value, float) and value < 1000:
                formatted.append(f"{value:.3f}")
            else:
                formatted.append(f"{value:.0f}")
        lines.append(f"| {label} | " + " | ".join(formatted) + f" | {direction} |")

    lines += ["", "## Cases where the modes disagree", ""]
    baseline, best = modes[0], modes[-1]
    for index, outcome in enumerate(report.outcomes[best]):
        other = report.outcomes[baseline][index]
        if outcome.correct == other.correct:
            continue
        verdict = "fixed by" if outcome.correct else "regressed under"
        lines.append(
            f"- `{outcome.case_id}` ({outcome.expected_kind}): {verdict} `{best}` "
            f"vs `{baseline}`."
        )
        lines.append(f"  - {baseline}: {other.answer[:180].strip()}")
        lines.append(f"  - {best}: {outcome.answer[:180].strip()}")
    if len(lines) and lines[-1].startswith("## Cases"):
        lines.append("- No differences in correctness between modes on this suite.")

    lines += ["", "## Per-case results (full pipeline)", "",
              "| Case | Expected | Gate | Verification | Correct | Grounded |",
              "|---|---|---|---|---|---|"]
    for outcome in report.outcomes[best]:
        lines.append(
            f"| `{outcome.case_id}` | {outcome.expected_kind} | {outcome.gate_decision} "
            f"| {outcome.verification_status} | {'yes' if outcome.correct else 'no'} "
            f"| {'yes' if outcome.grounded else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def write_report(report: EvaluationReport, directory: str | Path = "eval_reports") -> dict:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.replace(":", "").replace("-", "")
    json_path = path / f"{report.suite}-{stamp}.json"
    md_path = path / f"{report.suite}-{stamp}.md"
    json_path.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    latest = path / "latest.md"
    latest.write_text(render_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path), "latest": str(latest)}
