"""NEURAL. Semantic retrieval over company policy text.

This is the part of the system that *is* standard RAG: find the policy clauses whose
meaning is closest to the question. On its own it can quote a policy but cannot apply
it - it cannot tell you whether *your* order, delivered 25 days ago, is inside a 10-day
window. Where the answer is a decision about a case, retrieval supplies the citation
and the symbolic reasoner supplies the decision.

Clauses are the unit of retrieval, not arbitrary chunks, because rules cite clauses:
a retrieved clause and a fired rule can be matched by id, which is how the answer's
sources and the reasoning trace point at the same policy text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.neural.encoder import Encoder

_HEADING = re.compile(r"^##\s+(?P<id>[A-Z]{2,4}-\d+)\s+(?P<title>.+)$")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "is", "are", "of", "to", "and", "or", "in", "on", "for", "what",
         "does", "do", "i", "my", "can", "it", "be", "with", "how", "company", "s"}


@dataclass(frozen=True)
class Clause:
    id: str
    title: str
    text: str
    document: str

    @property
    def full_text(self) -> str:
        return f"{self.title}. {self.text}"


@dataclass
class RetrievedClause:
    clause: Clause
    score: float
    semantic: float
    lexical: float

    def as_dict(self) -> dict:
        return {"id": self.clause.id, "title": self.clause.title,
                "document": self.clause.document, "text": self.clause.text,
                "score": round(self.score, 4), "semantic": round(self.semantic, 4),
                "lexical": round(self.lexical, 4)}


def parse_policies(directory: Path) -> list[Clause]:
    clauses: list[Clause] = []
    for path in sorted(directory.glob("*.md")):
        document = path.read_text(encoding="utf-8")
        title_line = next((line for line in document.splitlines() if line.startswith("# ")),
                          path.stem)
        doc_title = title_line.lstrip("# ").strip()
        current: dict | None = None
        for line in document.splitlines():
            heading = _HEADING.match(line.strip())
            if heading:
                if current:
                    clauses.append(_finish(current, doc_title))
                current = {"id": heading.group("id"), "title": heading.group("title").strip(),
                           "lines": []}
            elif current is not None and line.strip():
                current["lines"].append(line.strip())
        if current:
            clauses.append(_finish(current, doc_title))
    return clauses


def _finish(current: dict, document: str) -> Clause:
    return Clause(current["id"], current["title"], " ".join(current["lines"]), document)


def _content(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


class PolicyIndex:
    def __init__(self, clauses: list[Clause], encoder: "Encoder") -> None:
        self.clauses = clauses
        self.encoder = encoder
        self._by_id = {c.id: c for c in clauses}

    def clause(self, clause_id: str) -> Clause | None:
        return self._by_id.get(clause_id)

    def search(self, question: str, *, k: int = 4) -> list[RetrievedClause]:
        if not self.clauses:
            return []
        semantic = self.encoder.similarity([question], [c.full_text for c in self.clauses])[0]
        asked = _content(question)
        results = []
        for clause, sem in zip(self.clauses, semantic):
            words = _content(clause.full_text)
            lexical = len(asked & words) / len(asked) if asked else 0.0
            # Meaning dominates; shared words break near-ties between clauses that
            # are about the same topic.
            score = 0.85 * float(sem) + 0.15 * lexical
            results.append(RetrievedClause(clause, score, float(sem), lexical))
        results.sort(key=lambda r: -r.score)
        return results[:k]


@lru_cache(maxsize=4)
def load_policies(directory: str) -> tuple[Clause, ...]:
    return tuple(parse_policies(Path(directory)))
