"""Knowledge representation types.

Three categories of knowledge are kept distinct everywhere in the system:

* ``DIRECT``           - asserted by a source document or a structured record.
* ``INFERRED``         - derived by the symbolic engine from other facts and rules.
* ``MODEL_GENERATED``  - produced by a language model. Never citable as a dataset
                         fact, never usable as a premise for inference.

Mixing them silently is the failure mode this whole platform exists to prevent,
so the distinction is carried in the type, in the database, in the API response
and in the UI.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FactKind(str, Enum):
    DIRECT = "DIRECT"
    INFERRED = "INFERRED"
    MODEL_GENERATED = "MODEL_GENERATED"


class ValueType(str, Enum):
    NUMBER = "number"
    STRING = "string"
    BOOL = "bool"
    DATE = "date"
    DURATION = "duration"


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def typed_value(value: Any, unit: str = "") -> dict[str, Any]:
    """Normalise a Python value into the stored ``{type, value, unit}`` envelope."""
    if isinstance(value, dict) and "type" in value and "value" in value:
        return {"type": value["type"], "value": value["value"],
                "unit": value.get("unit", unit)}
    if isinstance(value, bool):
        return {"type": ValueType.BOOL.value, "value": value, "unit": unit}
    if isinstance(value, (int, float)):
        return {"type": ValueType.NUMBER.value, "value": float(value), "unit": unit}
    if isinstance(value, (dt.date, dt.datetime)):
        return {"type": ValueType.DATE.value, "value": value.isoformat()[:10], "unit": unit}
    text = str(value).strip()
    if _DATE_RE.match(text):
        return {"type": ValueType.DATE.value, "value": text, "unit": unit}
    lowered = text.lower()
    if lowered in {"true", "yes"}:
        return {"type": ValueType.BOOL.value, "value": True, "unit": unit}
    if lowered in {"false", "no"}:
        return {"type": ValueType.BOOL.value, "value": False, "unit": unit}
    try:
        return {"type": ValueType.NUMBER.value, "value": float(text), "unit": unit}
    except ValueError:
        return {"type": ValueType.STRING.value, "value": text, "unit": unit}


def unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def render_value(value: Any) -> str:
    inner = unwrap(value)
    unit = value.get("unit", "") if isinstance(value, dict) else ""
    if isinstance(inner, float) and inner.is_integer():
        inner = int(inner)
    return f"{inner} {unit}".strip()


@dataclass
class Provenance:
    """Where a fact came from, in enough detail to reproduce the conclusion."""
    source_kind: str = "document"          # document | record | user_attribute | rule | model
    document_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None
    document_title: str = ""
    evidence_text: str = ""
    extractor: str = ""
    rule_codes: list[str] = field(default_factory=list)
    premise_fact_ids: list[str] = field(default_factory=list)
    effective_date: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "document_id": str(self.document_id) if self.document_id else None,
            "chunk_id": str(self.chunk_id) if self.chunk_id else None,
            "document_title": self.document_title,
            "evidence_text": self.evidence_text[:600],
            "extractor": self.extractor,
            "rule_codes": list(self.rule_codes),
            "premise_fact_ids": list(self.premise_fact_ids),
            "effective_date": self.effective_date,
        }


@dataclass
class FactDTO:
    """A subject-predicate-object triple with kind, confidence and provenance."""
    subject: str
    predicate: str
    object: dict[str, Any]
    kind: FactKind = FactKind.DIRECT
    confidence: float = 1.0
    provenance: Provenance = field(default_factory=Provenance)
    fact_id: str = ""
    valid_from: str | None = None
    valid_to: str | None = None
    sensitivity: str = "internal"
    required_permission: str | None = None

    def key(self) -> tuple[str, str]:
        return (self.subject, self.predicate)

    @property
    def value(self) -> Any:
        return unwrap(self.object)

    def render(self) -> str:
        return f"{self.subject}.{self.predicate} = {render_value(self.object)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.fact_id or None,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "kind": self.kind.value,
            "confidence": round(self.confidence, 4),
            "provenance": self.provenance.as_dict(),
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }


def make_fact(subject: str, predicate: str, value: Any, *, kind: FactKind = FactKind.DIRECT,
              confidence: float = 1.0, unit: str = "",
              provenance: Provenance | None = None, **kwargs: Any) -> FactDTO:
    return FactDTO(
        subject=subject.strip(), predicate=predicate.strip(),
        object=typed_value(value, unit), kind=kind, confidence=confidence,
        provenance=provenance or Provenance(), **kwargs,
    )
