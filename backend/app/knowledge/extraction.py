"""Turn evidence spans and structured records into DIRECT facts and rule candidates.

Extraction is pattern-based and conservative on purpose: a wrong "fact" is worse
than a missing one, because facts are treated as ground truth by the symbolic
engine. Anything the extractors are not confident about stays as retrievable
text, where the verifier can still use it to support a claim, rather than being
promoted to a fact.

Nothing here calls a language model. Adding domain patterns for a new dataset
means registering an extractor, not changing the pipeline.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from app.knowledge.schema import FactDTO, FactKind, Provenance, make_fact
from app.neuro.heuristics import PREDICATE_HINTS, normalize, sentences

# Units we recognise after a number.
_UNITS = r"(days?|working\s+days?|calendar\s+days?|hours?|weeks?|months?|years?|" \
         r"percent|%|usd|eur|inr|rupees?|dollars?)"

# "<phrase> is/are/of <number> <unit>"  e.g. "annual leave entitlement is 20 days"
_ATTR_IS_VALUE = re.compile(
    rf"(?P<phrase>[A-Za-z][A-Za-z \-]{{2,60}}?)\s*"
    rf"(?:is|are|of|:|=|shall\s+be|will\s+be|amounts?\s+to)\s*"
    rf"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{_UNITS})?\b",
    re.I)

# "<number> <unit> of <phrase>"  e.g. "20 days of annual leave"
_VALUE_OF_ATTR = re.compile(
    rf"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{_UNITS})?\s+of\s+"
    rf"(?P<phrase>[A-Za-z][A-Za-z \-]{{2,60}})",
    re.I)

# "employees receive/are entitled to <number> <unit> ... <phrase>"
_ENTITLEMENT = re.compile(
    rf"(?:receive|are\s+entitled\s+to|is\s+entitled\s+to|get|granted|accrue)\s+"
    rf"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{_UNITS})?\s*"
    rf"(?:of\s+)?(?P<phrase>[A-Za-z][A-Za-z \-]{{2,60}})?",
    re.I)

# Conditional statements that are rule candidates rather than facts.
_RULE_SHAPE = re.compile(
    r"\b(if|when|where|provided that|employees with|staff with|anyone with)\b.{0,160}?"
    r"\b(then|receive|are entitled to|is entitled to|shall|must|may|qualify|get)\b",
    re.I)

_COMPARATOR_WORDS = {
    "more than": "gt", "greater than": "gt", "over": "gt", "above": "gt",
    "at least": "gte", "no less than": "gte", "minimum of": "gte", "or more": "gte",
    "less than": "lt", "under": "lt", "below": "lt",
    "at most": "lte", "no more than": "lte", "maximum of": "lte", "or fewer": "lte",
    "exactly": "eq", "equal to": "eq",
}

_CONDITION_RE = re.compile(
    rf"(?P<subject_phrase>[A-Za-z][A-Za-z \-]{{2,50}}?)\s+"
    rf"(?P<comparator>{'|'.join(sorted(_COMPARATOR_WORDS, key=len, reverse=True))})\s+"
    rf"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{_UNITS})?",
    re.I)

_NOISE_PREFIX = re.compile(
    r"^(the|a|an|this|that|their|its|your|our|each|every|all|any|such|said)\s+", re.I)


def canonical_predicate(phrase: str) -> str | None:
    """Map a natural phrase onto a known predicate, else derive a snake_case one."""
    cleaned = _NOISE_PREFIX.sub("", normalize(phrase).lower()).strip(" .,:;-")
    if not cleaned or len(cleaned) < 3:
        return None
    for predicate, hints in PREDICATE_HINTS.items():
        for hint in hints:
            if hint in cleaned or cleaned in hint:
                return predicate
    words = [w for w in re.split(r"[^a-z0-9]+", cleaned) if w]
    if not words or len(words) > 5:
        return None
    return "_".join(words)[:120]


@dataclass
class RuleCandidate:
    """A conditional statement found in text, proposed for human review."""
    code: str
    text: str
    conditions: list[dict[str, Any]] = field(default_factory=list)
    conclusion: dict[str, Any] | None = None
    confidence: float = 0.0
    document_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "text": self.text, "conditions": self.conditions,
            "conclusion": self.conclusion, "confidence": round(self.confidence, 3),
            "document_id": str(self.document_id) if self.document_id else None,
            "chunk_id": str(self.chunk_id) if self.chunk_id else None,
        }


@dataclass
class ExtractionResult:
    facts: list[FactDTO] = field(default_factory=list)
    rule_candidates: list[RuleCandidate] = field(default_factory=list)
    skipped_sentences: int = 0


def _provenance(chunk_id: uuid.UUID | None, document_id: uuid.UUID | None,
                title: str, sentence: str, extractor: str,
                effective_date: str | None = None) -> Provenance:
    return Provenance(
        source_kind="document", document_id=document_id, chunk_id=chunk_id,
        document_title=title, evidence_text=sentence[:600], extractor=extractor,
        effective_date=effective_date,
    )


def extract_from_text(
    text: str, *, subject: str = "policy", document_id: uuid.UUID | None = None,
    chunk_id: uuid.UUID | None = None, document_title: str = "",
    effective_date: str | None = None, sensitivity: str = "internal",
    required_permission: str | None = None,
) -> ExtractionResult:
    """Extract DIRECT facts and rule candidates from one span of text."""
    result = ExtractionResult()
    seen: set[tuple[str, str, Any]] = set()

    for sentence in sentences(text):
        if len(sentence) > 600:
            result.skipped_sentences += 1
            continue

        if _RULE_SHAPE.search(sentence):
            candidate = _rule_candidate(sentence, document_id, chunk_id)
            if candidate:
                result.rule_candidates.append(candidate)
            # A conditional statement is not an unconditional fact.
            continue

        for extractor_name, pattern in (
            ("attr_is_value", _ATTR_IS_VALUE),
            ("value_of_attr", _VALUE_OF_ATTR),
            ("entitlement", _ENTITLEMENT),
        ):
            for match in pattern.finditer(sentence):
                phrase = match.groupdict().get("phrase") or ""
                predicate = canonical_predicate(phrase)
                if not predicate:
                    continue
                try:
                    value = float(match.group("value"))
                except (TypeError, ValueError):
                    continue
                unit = (match.groupdict().get("unit") or "").lower().strip()
                key = (subject, predicate, value)
                if key in seen:
                    continue
                seen.add(key)
                confidence = 0.9 if extractor_name == "attr_is_value" else 0.8
                result.facts.append(make_fact(
                    subject, predicate, value, unit=unit, confidence=confidence,
                    kind=FactKind.DIRECT,
                    provenance=_provenance(chunk_id, document_id, document_title,
                                           sentence, extractor_name, effective_date),
                    valid_from=effective_date, sensitivity=sensitivity,
                    required_permission=required_permission,
                ))
    return result


def _rule_candidate(sentence: str, document_id: uuid.UUID | None,
                    chunk_id: uuid.UUID | None) -> RuleCandidate | None:
    conditions: list[dict[str, Any]] = []
    for match in _CONDITION_RE.finditer(sentence):
        predicate = canonical_predicate(match.group("subject_phrase"))
        if not predicate:
            continue
        comparator = _COMPARATOR_WORDS[match.group("comparator").lower()]
        conditions.append({
            "type": "comparison", "predicate": predicate, "op": comparator,
            "value": float(match.group("value")),
            "unit": (match.groupdict().get("unit") or "").lower(),
        })
    conclusion = None
    entail = _ENTITLEMENT.search(sentence)
    if entail:
        predicate = canonical_predicate(entail.groupdict().get("phrase") or "")
        if predicate:
            conclusion = {
                "type": "assign", "predicate": predicate,
                "value": float(entail.group("value")),
                "unit": (entail.groupdict().get("unit") or "").lower(),
            }
    if not conditions and not conclusion:
        return None
    confidence = 0.65 if (conditions and conclusion) else 0.3
    return RuleCandidate(
        code=f"CAND-{uuid.uuid4().hex[:8]}", text=normalize(sentence)[:600],
        conditions=conditions, conclusion=conclusion, confidence=confidence,
        document_id=document_id, chunk_id=chunk_id,
    )


# ---------------------------------------------------------------------------
# Structured records
# ---------------------------------------------------------------------------

@dataclass
class RecordMapping:
    """Declarative mapping from arbitrary record columns to knowledge triples.

    This is the seam for a future dataset: supply a mapping, not new code.
    """
    subject_field: str
    subject_prefix: str = ""
    predicate_fields: list[str] = field(default_factory=list)
    exclude_fields: list[str] = field(default_factory=list)
    units: dict[str, str] = field(default_factory=dict)
    entity_type: str = "record"
    rename: dict[str, str] = field(default_factory=dict)
    sensitivity: str = "internal"
    required_permission: str | None = None

    @classmethod
    def infer(cls, records: list[dict[str, Any]]) -> "RecordMapping | None":
        """Guess a mapping when none is supplied: first id-like column is the subject."""
        if not records:
            return None
        columns = list(records[0].keys())
        subject = next(
            (c for c in columns
             if re.fullmatch(r"(id|.*_id|.*id|key|code|name|email)", c.strip().lower())),
            columns[0] if columns else None,
        )
        if subject is None:
            return None
        return cls(subject_field=subject,
                   predicate_fields=[c for c in columns if c != subject])


def extract_from_records(
    records: Iterable[dict[str, Any]], mapping: RecordMapping, *,
    document_id: uuid.UUID | None = None, document_title: str = "",
    effective_date: str | None = None,
) -> ExtractionResult:
    result = ExtractionResult()
    for index, record in enumerate(records):
        raw_subject = record.get(mapping.subject_field)
        if raw_subject in (None, ""):
            result.skipped_sentences += 1
            continue
        subject = f"{mapping.subject_prefix}{normalize(str(raw_subject))}".strip()
        fields = mapping.predicate_fields or [
            k for k in record if k != mapping.subject_field
        ]
        for column in fields:
            if column in mapping.exclude_fields or column == mapping.subject_field:
                continue
            value = record.get(column)
            if value in (None, ""):
                continue
            predicate = mapping.rename.get(column) or canonical_predicate(column) or column
            result.facts.append(make_fact(
                subject, predicate, value, unit=mapping.units.get(column, ""),
                confidence=1.0, kind=FactKind.DIRECT,
                provenance=Provenance(
                    source_kind="record", document_id=document_id,
                    document_title=document_title,
                    evidence_text=f"{mapping.subject_field}={raw_subject}; {column}={value}",
                    extractor="record_mapping", effective_date=effective_date,
                ),
                valid_from=effective_date,
                sensitivity=mapping.sensitivity,
                required_permission=mapping.required_permission,
            ))
    return result


# ---------------------------------------------------------------------------
# Registry so datasets can add domain extractors without touching the pipeline
# ---------------------------------------------------------------------------

TextExtractor = Callable[..., ExtractionResult]
_REGISTRY: dict[str, TextExtractor] = {"default": extract_from_text}


def register_extractor(name: str, extractor: TextExtractor) -> None:
    _REGISTRY[name] = extractor


def get_extractor(name: str = "default") -> TextExtractor:
    return _REGISTRY.get(name, extract_from_text)
