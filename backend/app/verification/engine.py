"""Verification: does the evidence actually support what we are about to say?

Two checkpoints:

1. **Pre-generation.** Every candidate claim is scored against the evidence set
   and the inference trace. Only claims that pass reach the generator.
2. **Post-generation.** The produced text is split into sentences and each
   assertive sentence is checked back against the approved claims and evidence.
   A sentence that asserts something no approved claim covers is flagged; a
   number that appears in the answer but in no evidence is a hard failure.

Nothing here asks a language model whether it is confident. Confidence is
computed from measurable agreement between text, evidence and derivations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.config import settings
from app.knowledge.schema import FactDTO, FactKind, render_value, unwrap
from app.neuro.heuristics import (
    content_words, coverage, extract_numbers, normalize, phrase_fact, sentences,
    weighted_overlap,
)
from app.rag.retriever import EvidenceSpan
from app.symbolic.engine import Contradiction, InferenceStep


class ClaimStatus(str, Enum):
    VERIFIED = "verified"          # derived deterministically and evidence-backed
    SUPPORTED = "supported"        # evidence supports it above threshold
    WEAK = "weak"                  # some support, below threshold
    UNSUPPORTED = "unsupported"    # no evidence
    CONTRADICTED = "contradicted"  # evidence/facts conflict


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class Claim:
    text: str
    kind: FactKind = FactKind.DIRECT
    fact: FactDTO | None = None
    citation_indexes: list[int] = field(default_factory=list)
    supporting_chunk_ids: list[str] = field(default_factory=list)
    supporting_fact_ids: list[str] = field(default_factory=list)
    support_score: float = 0.0
    # How well this candidate answers the question. Selection and verification are
    # different judgements: a sentence copied verbatim from a document is always
    # "supported" by that document, which says nothing about whether it is an
    # answer. Relevance is what decides that, and the gate uses both.
    relevance: float = 0.0
    status: ClaimStatus = ClaimStatus.UNSUPPORTED
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "kind": self.kind.value, "status": self.status.value,
            "support_score": round(self.support_score, 4),
            "relevance": round(self.relevance, 4),
            "citation_indexes": sorted(set(self.citation_indexes)),
            "fact_ids": self.supporting_fact_ids,
        }


@dataclass
class VerificationReport:
    claims: list[Claim] = field(default_factory=list)
    status: VerificationStatus = VerificationStatus.NOT_APPLICABLE
    evidence_relevance: float = 0.0
    mean_support: float = 0.0
    unsupported_count: int = 0
    contradictions: list[Contradiction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def supported_claims(self) -> list[Claim]:
        return [c for c in self.claims
                if c.status in (ClaimStatus.VERIFIED, ClaimStatus.SUPPORTED)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence_relevance": round(self.evidence_relevance, 4),
            "mean_support": round(self.mean_support, 4),
            "unsupported_count": self.unsupported_count,
            "claims": [c.as_dict() for c in self.claims],
            "contradictions": [c.as_dict() for c in self.contradictions],
            "notes": self.notes,
        }


def _round(value: Any) -> Any:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return value


def _round_set(values: list[float]) -> set[Any]:
    return {_round(v) for v in values}


# ---------------------------------------------------------------------------
# Evidence relevance
# ---------------------------------------------------------------------------

def evidence_relevance(question: str, spans: list[EvidenceSpan]) -> float:
    """How well the retrieved set actually addresses the question.

    Deliberately conservative: it rewards term coverage rather than retriever
    score, because a retriever always returns *something*.
    """
    if not spans:
        return 0.0
    usable = [s for s in spans if not s.quarantined] or spans
    best = max(coverage(question, s.text) for s in usable)
    mean_top = sum(coverage(question, s.text) for s in usable[:3]) / min(3, len(usable))
    return round(0.65 * best + 0.35 * mean_top, 4)


def _numeric_agreement(claim_text: str, span_text: str) -> float | None:
    """If the claim states a number, the evidence must state the same number."""
    claim_numbers = {round(n, 6) for n in extract_numbers(claim_text)}
    if not claim_numbers:
        return None
    span_numbers = {round(n, 6) for n in extract_numbers(span_text)}
    if not span_numbers:
        return 0.0
    return 1.0 if claim_numbers & span_numbers else 0.0


def support_for(claim_text: str, spans: list[EvidenceSpan]) -> tuple[float, list[int]]:
    """Score a claim against evidence spans; return score and the citations used."""
    if not spans:
        return 0.0, []
    scored: list[tuple[float, int]] = []
    for index, span in enumerate(spans, start=1):
        lexical = coverage(claim_text, span.text)
        numeric = _numeric_agreement(claim_text, span.text)
        if numeric is not None:
            # A numeric claim is only supported if the number is present.
            score = 0.35 * lexical + 0.65 * numeric
        else:
            score = lexical
        if span.quarantined:
            # Quarantined text may corroborate but may never be sole support.
            score *= 0.4
        if score > 0:
            scored.append((score, index))
    if not scored:
        return 0.0, []
    scored.sort(reverse=True)
    best = scored[0][0]
    citations = [index for score, index in scored if score >= max(0.25, best * 0.6)][:4]
    return round(best, 4), citations


# ---------------------------------------------------------------------------
# Claim construction
# ---------------------------------------------------------------------------

def claims_from_facts(facts: list[FactDTO], *, label: str = "") -> list[Claim]:
    out: list[Claim] = []
    for fact in facts:
        if fact.kind is FactKind.MODEL_GENERATED:
            continue
        text = phrase_fact(fact.subject, fact.predicate, render_value(fact.object))
        out.append(Claim(
            text=normalize(text), kind=fact.kind, fact=fact,
            supporting_fact_ids=[fact.fact_id] if fact.fact_id else [],
            supporting_chunk_ids=[str(fact.provenance.chunk_id)]
            if fact.provenance.chunk_id else [],
            notes=label,
        ))
    return out


def _looks_like_heading(sentence: str) -> bool:
    """Titles and section headings are labels, not assertions."""
    words = [w for w in sentence.split() if w.isalpha()]
    if len(words) < 3:
        return True
    titled = sum(1 for w in words if w[:1].isupper())
    return titled / len(words) > 0.6


def claims_from_spans(question: str, spans: list[EvidenceSpan],
                      max_claims: int = 6, *, focus_terms: list[str] | None = None,
                      prefer_numeric: bool = False) -> list[Claim]:
    """For a document lookup, the claim *is* the cited sentence.

    `focus_terms` are the words that identify what was asked about (the canonical
    attribute and its synonyms). Without them a question like "what is the return
    window for the Router X1" scores the product's overview paragraph highest,
    because it repeats the product name, while the sentence that actually answers
    the question mentions the product only implicitly.
    """
    focus = {w for term in (focus_terms or []) for w in content_words(term)}
    out: list[Claim] = []
    for index, span in enumerate(spans, start=1):
        if span.quarantined:
            continue
        best_sentence, best_score = "", 0.0
        for sentence in sentences(span.text):
            # A heading or a title is not a claim; require a real sentence.
            if len(sentence) <= 25 or len(content_words(sentence)) < 5:
                continue
            if _looks_like_heading(sentence):
                continue
            # Weighting by term length approximates inverse document frequency,
            # so "annual" separates the annual-leave sentence from any sentence
            # that merely repeats the common words "leave" and "days".
            score = 0.5 * coverage(question, sentence) + 0.5 * weighted_overlap(
                question, sentence)
            if focus:
                words = set(content_words(sentence))
                score += 0.35 * (len(focus & words) / len(focus))
            if prefer_numeric and extract_numbers(sentence):
                score += 0.10
            if score > best_score:
                best_sentence, best_score = sentence, score
        if not best_sentence:
            continue
        out.append(Claim(
            text=normalize(best_sentence)[:600], kind=FactKind.DIRECT,
            citation_indexes=[index], supporting_chunk_ids=[str(span.chunk_id)],
            relevance=round(best_score, 4),
        ))

    if not out:
        return out
    # Keep only sentences that are competitive with the best one; a passage that
    # merely shares common words is evidence, not an answer.
    best = max(c.relevance for c in out)
    cutoff = max(0.35, best * 0.8)
    out = [c for c in out if c.relevance >= cutoff]
    out.sort(key=lambda c: -c.relevance)
    return out[:max_claims]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(
    question: str,
    claims: list[Claim],
    spans: list[EvidenceSpan],
    *,
    inference_steps: list[InferenceStep] | None = None,
    contradictions: list[Contradiction] | None = None,
    require_evidence: bool = True,
) -> VerificationReport:
    report = VerificationReport(claims=claims, contradictions=list(contradictions or []))
    report.evidence_relevance = evidence_relevance(question, spans)

    # A conflict an ordering signal already resolved (a superseding version) does
    # not invalidate the claim that uses the surviving value; it is reported
    # alongside the answer instead.
    unresolved = [c for c in report.contradictions if c.severity == "error"]
    conflicted_keys = {(c.subject, c.predicate) for c in unresolved}
    superseded_values = {
        _round(v) for c in report.contradictions for v in c.superseded_values
    }
    # While a conflict is unresolved, *neither* side may be asserted. Stating one
    # of two disputed values and only then mentioning the disagreement would let
    # the reader take the first number as the answer.
    disputed_values = {_round(v) for c in unresolved for v in c.values}

    for claim in claims:
        fact = claim.fact
        if fact is not None and (fact.subject, fact.predicate) in conflicted_keys:
            claim.status = ClaimStatus.CONTRADICTED
            claim.support_score = 0.0
            claim.notes = "Sources disagree on this value."
            continue

        claim_numbers = _round_set(extract_numbers(claim.text))
        if superseded_values and claim_numbers & superseded_values:
            claim.status = ClaimStatus.CONTRADICTED
            claim.support_score = 0.0
            claim.notes = "This value was superseded by a newer source."
            continue
        if disputed_values and claim_numbers & disputed_values:
            claim.status = ClaimStatus.CONTRADICTED
            claim.support_score = 0.0
            claim.notes = "The sources disagree about this value."
            continue

        score, citations = support_for(claim.text, spans)
        if citations and not claim.citation_indexes:
            claim.citation_indexes = citations
        claim.support_score = max(claim.support_score, score)

        if fact is not None and fact.kind is FactKind.INFERRED:
            # An inferred claim is verified when its derivation is intact, even if
            # the sentence itself never appears verbatim in a document.
            premise_ok = _premises_supported(fact, claims, spans)
            if premise_ok:
                claim.status = ClaimStatus.VERIFIED
                claim.support_score = max(claim.support_score, fact.confidence)
                claim.notes = "Derived by the rule engine from verified facts."
            else:
                claim.status = ClaimStatus.WEAK
                claim.notes = "Derivation premises are not fully evidence-backed."
            continue

        if fact is not None and fact.provenance.source_kind == "user_attribute":
            claim.status = ClaimStatus.VERIFIED
            claim.support_score = max(claim.support_score, fact.confidence)
            claim.notes = "From your workspace profile record."
            continue

        if fact is None:
            # A span-derived claim: it is quoted evidence, so support is not in
            # question; relevance is.
            if claim.relevance >= settings.claim_support_threshold:
                claim.status = ClaimStatus.SUPPORTED
            elif claim.relevance > 0:
                claim.status = ClaimStatus.WEAK
                claim.notes = "Related to the question but not a direct answer."
            else:
                claim.status = ClaimStatus.UNSUPPORTED
            claim.support_score = min(claim.support_score, 1.0)
        elif claim.support_score >= settings.claim_support_threshold:
            claim.status = ClaimStatus.SUPPORTED
        elif claim.support_score > 0:
            claim.status = ClaimStatus.WEAK
        else:
            claim.status = (ClaimStatus.UNSUPPORTED if require_evidence
                            else ClaimStatus.WEAK)

    supported = report.supported_claims
    report.unsupported_count = sum(
        1 for c in claims if c.status in (ClaimStatus.UNSUPPORTED, ClaimStatus.WEAK)
    )
    report.mean_support = (
        round(sum(c.support_score for c in supported) / len(supported), 4)
        if supported else 0.0
    )

    if unresolved:
        report.status = VerificationStatus.CONTRADICTED
    elif not claims:
        report.status = VerificationStatus.INSUFFICIENT_EVIDENCE
    elif not supported:
        report.status = (VerificationStatus.INSUFFICIENT_EVIDENCE if require_evidence
                         else VerificationStatus.UNVERIFIED)
    elif report.unsupported_count == 0 and all(
        c.status is ClaimStatus.VERIFIED for c in supported
    ):
        report.status = VerificationStatus.VERIFIED
    elif report.unsupported_count == 0:
        report.status = VerificationStatus.VERIFIED
    else:
        report.status = VerificationStatus.PARTIALLY_VERIFIED

    if inference_steps:
        report.notes.append(f"{len(inference_steps)} inference step(s) applied.")
    if any(s.quarantined for s in spans):
        report.notes.append(
            "Some retrieved passages were quarantined for instruction-like content."
        )
    return report


def _premises_supported(fact: FactDTO, claims: list[Claim],
                        spans: list[EvidenceSpan]) -> bool:
    premise_ids = set(fact.provenance.premise_fact_ids)
    if not premise_ids:
        # Derived from facts that were not persisted (e.g. profile attributes).
        return fact.confidence >= 0.5
    by_id = {fid: c for c in claims for fid in c.supporting_fact_ids}
    for premise in premise_ids:
        claim = by_id.get(premise)
        if claim is None:
            continue
        if claim.status in (ClaimStatus.UNSUPPORTED, ClaimStatus.CONTRADICTED):
            return False
    return True


# ---------------------------------------------------------------------------
# Post-generation grounding check
# ---------------------------------------------------------------------------

_HEDGE = re.compile(
    r"^(i |we |let me|here|this |that |note that|in summary|to summari|"
    r"you (could|might|may) |treat this|why:|you asked)", re.I)
_CITATION_MARK = re.compile(r"\[(\d{1,2})\]")


@dataclass
class GroundingReport:
    grounded: bool = True
    total_sentences: int = 0
    unsupported_sentences: list[str] = field(default_factory=list)
    unsupported_numbers: list[float] = field(default_factory=list)
    invalid_citations: list[int] = field(default_factory=list)
    score: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "grounded": self.grounded, "score": round(self.score, 4),
            "total_sentences": self.total_sentences,
            "unsupported_sentences": self.unsupported_sentences[:5],
            "unsupported_numbers": self.unsupported_numbers[:8],
            "invalid_citations": self.invalid_citations,
        }


def check_grounding(answer: str, approved_claims: list[Claim],
                    spans: list[EvidenceSpan],
                    extra_context: list[str] | None = None) -> GroundingReport:
    """Verify the produced text says nothing the approved evidence does not.

    `extra_context` carries system-derived provenance the answer is allowed to
    mention - source titles, effective dates, conflict resolutions. Those strings
    come from the platform's own records, not from the model, so checking the
    answer against them is still a check against ground truth.
    """
    report = GroundingReport()
    if not answer.strip():
        return report

    approved_text = " ".join(c.text for c in approved_claims)
    evidence_text = " ".join(s.text for s in spans)
    provenance_text = " ".join(
        part for span in spans
        for part in (span.document_title, span.section, span.effective_date or "",
                     span.version_label)
        if part
    )
    haystack = " ".join(
        [approved_text, evidence_text, provenance_text, " ".join(extra_context or [])]
    )
    haystack_numbers = {round(n, 6) for n in extract_numbers(haystack)}
    haystack_words = set(content_words(haystack))

    valid_indexes = set(range(1, len(spans) + 1))
    for mark in _CITATION_MARK.findall(answer):
        index = int(mark)
        if index not in valid_indexes:
            report.invalid_citations.append(index)

    unsupported = 0
    for sentence in sentences(answer):
        if len(sentence) < 12 or _HEDGE.match(sentence):
            continue
        report.total_sentences += 1
        # Citation markers such as [2] are references, not asserted values.
        bare = _CITATION_MARK.sub(" ", sentence)
        numbers = {round(n, 6) for n in extract_numbers(bare)}
        missing_numbers = numbers - haystack_numbers
        if missing_numbers:
            report.unsupported_numbers.extend(sorted(missing_numbers))
            report.unsupported_sentences.append(sentence[:240])
            unsupported += 1
            continue
        words = set(content_words(bare))
        if words and len(words & haystack_words) / len(words) < 0.34:
            report.unsupported_sentences.append(sentence[:240])
            unsupported += 1

    if report.total_sentences:
        report.score = round(1.0 - unsupported / report.total_sentences, 4)
    report.grounded = (
        unsupported == 0 and not report.invalid_citations
    )
    return report
