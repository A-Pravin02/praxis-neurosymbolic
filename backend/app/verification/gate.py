"""The evidence gate.

The decision to answer, hedge, refuse or deny is made here, in ordinary code,
from measurable quantities. It is never delegated to the language model — a model
cannot be trusted to lower its own bar, and asking it "are you sure?" measures
nothing.

A factual claim is presented as established only when all of these hold:

    evidence exists  AND  evidence is relevant  AND  the claim is supported
    AND  no unresolved contradiction touches it

Otherwise the system hedges or abstains. Abstention is a success, not a failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.config import settings
from app.rag.retriever import EvidenceSpan
from app.symbolic.engine import Contradiction
from app.verification.engine import ClaimStatus, VerificationReport, VerificationStatus


class GateDecision(str, Enum):
    ANSWER = "ANSWER"
    ANSWER_WITH_CAVEAT = "ANSWER_WITH_CAVEAT"
    REPORT_CONFLICT = "REPORT_CONFLICT"
    ABSTAIN = "ABSTAIN"
    DENY = "DENY"


@dataclass
class GateResult:
    decision: GateDecision
    confidence: float
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)

    @property
    def may_answer(self) -> bool:
        return self.decision in (GateDecision.ANSWER, GateDecision.ANSWER_WITH_CAVEAT,
                                 GateDecision.REPORT_CONFLICT)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value, "confidence": round(self.confidence, 4),
            "reasons": self.reasons, "signals": self.signals,
        }


def evaluate(
    report: VerificationReport,
    spans: list[EvidenceSpan],
    *,
    require_evidence: bool = True,
    contradictions: list[Contradiction] | None = None,
    symbolic_conclusion: bool = False,
    answer_threshold: float | None = None,
    caveat_threshold: float | None = None,
) -> GateResult:
    answer_threshold = (answer_threshold if answer_threshold is not None
                        else settings.answer_confidence_threshold)
    caveat_threshold = (caveat_threshold if caveat_threshold is not None
                        else settings.caveat_confidence_threshold)
    contradictions = contradictions or report.contradictions

    usable_spans = [s for s in spans if not s.quarantined]
    supported = report.supported_claims
    verified = [c for c in supported if c.status is ClaimStatus.VERIFIED]

    signals: dict[str, Any] = {
        "evidence_count": len(spans),
        "usable_evidence_count": len(usable_spans),
        "quarantined_evidence_count": len(spans) - len(usable_spans),
        "evidence_relevance": round(report.evidence_relevance, 4),
        "claims": len(report.claims),
        "supported_claims": len(supported),
        "verified_claims": len(verified),
        "unsupported_claims": report.unsupported_count,
        "mean_support": round(report.mean_support, 4),
        "contradictions": len(contradictions),
        "symbolic_conclusion": symbolic_conclusion,
    }
    reasons: list[str] = []

    # --- hard stops --------------------------------------------------------
    unresolved = [c for c in contradictions if c.severity == "error"]
    resolved = [c for c in contradictions if c.severity != "error"]
    signals["unresolved_contradictions"] = len(unresolved)
    signals["resolved_contradictions"] = len(resolved)

    if unresolved:
        confidence = min(0.5, 0.2 + 0.1 * len(verified))
        reasons.append(
            "The sources disagree on a value relevant to this question, and the "
            "conflict has not been resolved."
        )
        return GateResult(GateDecision.REPORT_CONFLICT, confidence, reasons, signals,
                          suggestions=["Ask which document version is authoritative."])
    if resolved:
        # A conflict with an ordering signal (a superseding version) does not stop
        # the answer, but it is always reported alongside it.
        reasons.append(
            "A version conflict was found and resolved in favour of the most recent "
            "source; both values are reported."
        )

    if require_evidence and not spans:
        reasons.append("Nothing in the knowledge base matched this question.")
        return GateResult(GateDecision.ABSTAIN, 0.0, reasons, signals, suggestions=[
            "Upload the document that covers this topic",
            "Rephrase using wording that appears in your documents",
        ])

    if require_evidence and report.evidence_relevance < settings.evidence_relevance_threshold:
        reasons.append(
            f"The closest passages are only weakly related to the question "
            f"(relevance {report.evidence_relevance:.2f} < "
            f"{settings.evidence_relevance_threshold:.2f})."
        )
        return GateResult(GateDecision.ABSTAIN, round(report.evidence_relevance, 4),
                          reasons, signals, suggestions=[
                              "Try naming the document or policy explicitly"])

    if require_evidence and not supported:
        reasons.append("No candidate answer was supported by the retrieved evidence.")
        return GateResult(GateDecision.ABSTAIN, 0.0, reasons, signals)

    # --- confidence --------------------------------------------------------
    confidence = _confidence(report, usable_spans, verified, symbolic_conclusion)
    signals["computed_confidence"] = round(confidence, 4)

    if not require_evidence and not supported:
        # A general-knowledge answer: allowed, but never dressed up as grounded.
        reasons.append("Answered from general knowledge; no workspace evidence applies.")
        return GateResult(GateDecision.ANSWER_WITH_CAVEAT, min(confidence, 0.45),
                          reasons, signals)

    if report.status is VerificationStatus.CONTRADICTED:
        reasons.append("Verification found conflicting support.")
        return GateResult(GateDecision.REPORT_CONFLICT, min(confidence, 0.45),
                          reasons, signals)

    if confidence >= answer_threshold:
        reasons.append(
            f"{len(supported)} claim(s) supported by evidence"
            + (f", {len(verified)} deterministically verified" if verified else "")
            + "."
        )
        return GateResult(GateDecision.ANSWER, confidence, reasons, signals)

    if confidence >= caveat_threshold:
        reasons.append(
            f"Support is present but below the confident-answer threshold "
            f"({confidence:.2f} < {answer_threshold:.2f})."
        )
        return GateResult(GateDecision.ANSWER_WITH_CAVEAT, confidence, reasons, signals)

    reasons.append(
        f"Confidence {confidence:.2f} is below the minimum required to answer "
        f"({caveat_threshold:.2f})."
    )
    return GateResult(GateDecision.ABSTAIN, confidence, reasons, signals, suggestions=[
        "Ask about a narrower, more specific point",
        "Upload the source that covers this",
    ])


def _confidence(report: VerificationReport, usable_spans: list[EvidenceSpan],
                verified: list, symbolic_conclusion: bool) -> float:
    """Blend independent signals. Deterministic derivation dominates."""
    supported = report.supported_claims
    if not supported and not verified:
        return 0.0

    support = report.mean_support
    relevance = report.evidence_relevance
    retrieval = max((s.score for s in usable_spans), default=0.0)
    verified_share = len(verified) / max(1, len(supported))
    unsupported_penalty = 0.08 * min(report.unsupported_count, 4)

    confidence = (
        0.34 * support
        + 0.22 * relevance
        + 0.14 * min(1.0, retrieval)
        + 0.30 * verified_share
    )
    if symbolic_conclusion and verified:
        # A conclusion the rule engine derived from verified premises is the
        # strongest signal available; it is still capped below certainty.
        confidence = max(confidence, 0.82)
    confidence -= unsupported_penalty
    return round(max(0.0, min(0.97, confidence)), 4)


def denial_result(reason: str) -> GateResult:
    return GateResult(GateDecision.DENY, 0.0, [reason],
                      {"policy_denied": True})
