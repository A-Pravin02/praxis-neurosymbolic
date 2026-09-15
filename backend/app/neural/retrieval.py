"""NEURAL. Semantic retrieval over company policy text.

This is the part of the system that *is* standard RAG: find the policy clauses whose
meaning is closest to the question. On its own it can quote a policy but cannot apply
it - it cannot tell you whether *your* order, delivered 25 days ago, is inside a 10-day
window. Where the answer is a decision about a case, retrieval supplies the citation
and the symbolic reasoner supplies the decision.

Clauses are the unit of retrieval, not arbitrary chunks, because rules cite clauses:
a retrieved clause and a fired rule can be matched by id, which is how the answer's
sources and the reasoning trace point at the same policy text.

Policies are versioned. A document declares `Policy: returns. Version: 2. Effective:
2026-09-01.` Every version stays citeable - a case governed by version 1 cites version 1
- but only clauses in force are *retrieved*, so a question about what the policy says
never gets an answer quoted from a superseded version.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.neural.encoder import Encoder

_HEADING = re.compile(r"^##\s+(?P<id>[A-Z]{2,4}-\d+)\s+(?P<title>.+)$")
_POLICY = re.compile(r"\bPolicy:\s*(?P<policy>[\w\-]+)", re.I)
_VERSION = re.compile(r"\bVersion:\s*(?P<version>\d+)", re.I)
_EFFECTIVE = re.compile(r"\bEffective:?\s*(?P<date>\d{4}-\d{2}-\d{2})", re.I)
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "is", "are", "of", "to", "and", "or", "in", "on", "for", "what",
         "does", "do", "i", "my", "can", "it", "be", "with", "how", "company", "s"}


@dataclass(frozen=True)
class Clause:
    id: str
    title: str
    text: str
    document: str
    policy: str = ""
    version: int = 1
    effective: str = ""          # ISO date the document took effect
    superseded_on: str = ""      # ISO date a later version replaced it, if any
    source_file: str = ""

    @property
    def full_text(self) -> str:
        return f"{self.title}. {self.text}"

    @property
    def in_force(self) -> bool:
        return not self.superseded_on

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "document": self.document,
                "text": self.text, "policy": self.policy, "version": self.version,
                "effective": self.effective, "superseded_on": self.superseded_on or None,
                "in_force": self.in_force}


@dataclass
class RetrievedClause:
    clause: Clause
    score: float
    semantic: float
    lexical: float

    def as_dict(self) -> dict:
        return {**self.clause.as_dict(), "score": round(self.score, 4),
                "semantic": round(self.semantic, 4), "lexical": round(self.lexical, 4)}


@dataclass(frozen=True)
class PolicyDocument:
    file: str
    title: str
    policy: str
    version: int
    effective: str
    clause_ids: tuple[str, ...]


def parse_document(text: str, name: str) -> tuple[PolicyDocument, list[Clause]]:
    """One policy document: its version header and its numbered clauses."""
    lines = text.splitlines()
    title_line = next((line for line in lines if line.startswith("# ")), name)
    doc_title = title_line.lstrip("# ").strip()
    preamble = "\n".join(line for line in lines[:12] if not line.startswith("##"))
    policy = _POLICY.search(preamble)
    version = _VERSION.search(preamble)
    effective = _EFFECTIVE.search(preamble)
    stem = Path(name).stem
    meta = {
        "policy": policy.group("policy").lower() if policy else re.sub(r"_v\d+$", "", stem),
        "version": int(version.group("version")) if version else 1,
        "effective": effective.group("date") if effective else "",
    }
    clauses: list[Clause] = []
    current: dict | None = None
    for line in lines:
        heading = _HEADING.match(line.strip())
        if heading:
            if current:
                clauses.append(_finish(current, doc_title, meta, name))
            current = {"id": heading.group("id"), "title": heading.group("title").strip(),
                       "lines": []}
        elif current is not None and line.strip():
            current["lines"].append(line.strip())
    if current:
        clauses.append(_finish(current, doc_title, meta, name))
    document = PolicyDocument(name, doc_title, meta["policy"], meta["version"],
                              meta["effective"], tuple(c.id for c in clauses))
    return document, clauses


def parse_policies(directory: Path, *, as_of: dt.date | None = None) -> list[Clause]:
    """Every clause of every version, each marked with when it was superseded.

    A version is superseded on the date a later version of the same policy took effect,
    provided that date is not after `as_of`. A version scheduled for the future does not
    yet replace the one in force.
    """
    clauses: list[Clause] = []
    for path in sorted(directory.glob("*.md")):
        _, found = parse_document(path.read_text(encoding="utf-8"), path.name)
        clauses.extend(found)
    today = (as_of or dt.date.today()).isoformat()
    by_policy: dict[str, list[tuple[int, str]]] = {}
    for clause in clauses:
        by_policy.setdefault(clause.policy, [])
        if (clause.version, clause.effective) not in by_policy[clause.policy]:
            by_policy[clause.policy].append((clause.version, clause.effective))
    marked: list[Clause] = []
    for clause in clauses:
        later = sorted(e for v, e in by_policy[clause.policy]
                       if v > clause.version and e and e <= today)
        marked.append(replace(clause, superseded_on=later[0]) if later else clause)
    return marked


def _finish(current: dict, document: str, meta: dict, source_file: str) -> Clause:
    return Clause(current["id"], current["title"], " ".join(current["lines"]), document,
                  meta["policy"], meta["version"], meta["effective"], "", source_file)


def _content(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


class PolicyIndex:
    def __init__(self, clauses: list[Clause], encoder: "Encoder") -> None:
        self.all_clauses = clauses
        # Only what is in force is retrieved; every version remains citeable.
        self.clauses = [c for c in clauses if c.in_force]
        self.encoder = encoder
        self._current = {c.id: c for c in self.clauses}
        self._versions: dict[str, dict[int, Clause]] = {}
        for clause in clauses:
            self._versions.setdefault(clause.id, {})[clause.version] = clause

    def clause(self, clause_id: str, version: int | None = None) -> Clause | None:
        if version is not None:
            return self._versions.get(clause_id, {}).get(int(version))
        return self._current.get(clause_id) or next(
            iter(sorted(self._versions.get(clause_id, {}).values(),
                        key=lambda c: -c.version)), None)

    def versions_of(self, clause_id: str) -> list[Clause]:
        return sorted(self._versions.get(clause_id, {}).values(), key=lambda c: c.version)

    def documents(self) -> list[dict]:
        seen: dict[tuple[str, int], dict] = {}
        for clause in self.all_clauses:
            key = (clause.policy, clause.version)
            entry = seen.setdefault(key, {
                "policy": clause.policy, "version": clause.version, "title": clause.document,
                "effective": clause.effective, "superseded_on": clause.superseded_on or None,
                "in_force": clause.in_force, "file": clause.source_file, "clauses": []})
            entry["clauses"].append({"id": clause.id, "title": clause.title})
        return sorted(seen.values(), key=lambda d: (d["policy"], d["version"]))

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

    def vocabulary(self) -> set[str]:
        """Every content word any policy in force uses - for naming what is not covered."""
        words: set[str] = set()
        for clause in self.clauses:
            words |= _content(clause.full_text)
        return words
