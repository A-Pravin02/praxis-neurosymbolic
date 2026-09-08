"""Resolve a question onto subjects and predicates that exist in the workspace.

The heuristic predicate vocabulary in `app.neuro.heuristics` is hand-written and
covers the demonstration domain. It cannot cover an arbitrary dataset: nobody is
going to enumerate `weekly_sales_max`, `dc_power_max` and `previous_year_rating_mean`
by hand for seventy tables.

So instead of guessing what a question is about, this module asks the workspace.
It reads the subjects and predicates that actually exist for the tenant and scores
them against the question's terms, with a bonus when a statistical word in the
question ("maximum", "average", "how many") matches the statistical suffix of a
predicate.

That makes attribute lookup work for any ingested dataset without new code, and
keeps the mapping honest: a predicate that does not exist can never be resolved,
so the system cannot invent a plausible-sounding attribute.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.knowledge import Fact
from app.neuro.heuristics import STOPWORDS, content_words, normalize, stem
from app.tenancy.context import Principal

# A statistical word in the question implies a suffix on the predicate.
STAT_WORDS: dict[str, tuple[str, ...]] = {
    "max": ("maximum", "max", "highest", "largest", "peak", "biggest", "greatest"),
    "min": ("minimum", "min", "lowest", "smallest", "least"),
    "mean": ("mean", "average", "avg", "typical"),
    "median": ("median",),
    "distinct_count": ("distinct", "unique", "different"),
    "most_common": ("common", "commonest", "frequent", "modal", "popular", "top"),
    "missing_count": ("missing", "null", "empty", "blank", "absent"),
}

# Whole-predicate aliases for questions that name no column at all.
DIRECT_ALIASES: dict[str, tuple[str, ...]] = {
    "row_count": ("row", "record", "entry", "observation", "sample", "datapoint"),
    "column_count": ("column", "field", "attribute", "variable", "feature"),
    "domain": ("domain", "category", "topic"),
}

MAX_CANDIDATES = 6_000
# Subjects tried before falling back to the whole tenant's predicates.
SUBJECT_CANDIDATES = 4


@dataclass
class Resolution:
    subject: str | None = None
    predicate: str | None = None
    score: float = 0.0
    statistic: str | None = None
    candidates: list[tuple[str, float]] = field(default_factory=list)
    # Subjects the question's name matched equally well. Populated only when the
    # question does not say which of them is meant.
    ambiguous_subjects: list[str] = field(default_factory=list)
    # Whether the question is framed as being about a table at all.
    dataset_scoped: bool = False

    @property
    def resolved(self) -> bool:
        return self.predicate is not None

    def as_dict(self) -> dict:
        return {
            "subject": self.subject, "predicate": self.predicate,
            "score": round(self.score, 4), "statistic": self.statistic,
            "candidates": [{"predicate": p, "score": round(s, 4)}
                           for p, s in self.candidates[:5]],
            "ambiguous_subjects": list(self.ambiguous_subjects),
            "dataset_scoped": self.dataset_scoped,
        }


def _tokens(value: str) -> set[str]:
    """Predicate and subject names split into comparable stems."""
    parts = value.replace(":", " ").replace(".", " ").replace("-", " ").split("_")
    return {stem(token.lower()) for part in parts for token in part.split()
            if len(token) > 1}


def question_tokens_of(question: str) -> set[str]:
    """Question terms, split the same way subject and predicate names are.

    A user writing "the Plant_1_Generation_Data dataset" produces one token under
    ordinary word splitting, which then matches nothing - and the question gets
    answered from whichever table happened to share a word with the attribute.
    Splitting identifiers into their parts is what lets the named dataset win.
    """
    tokens = set(content_words(question))
    for raw in re.split(r"[^A-Za-z0-9]+", question or ""):
        # Stopwords are filtered here too. Without that, "What **is** the mean net
        # promoter score" matches the `is_promoted` column on the word "is", and
        # a promotion rate gets reported as a net promoter score.
        for part in [raw, *re.findall(r"[A-Za-z][a-z0-9]+|\d+", raw)]:
            lowered = part.lower()
            if len(lowered) > 1 and lowered not in STOPWORDS:
                tokens.add(stem(lowered))
    return tokens


# Words that frame a question as being *about a table* rather than about the
# world. A workspace can hold both a company handbook and 70 CSVs, and without
# this the word "policies" in "what are the new policies in the company" matches
# a dataset literally named `policy` - and the question gets answered, or
# refused, as though it were about a spreadsheet.
DATASET_MARKERS = {
    "dataset", "table", "csv", "file", "column", "row", "record", "data",
    "spreadsheet",
}


def _statistic_in(question_tokens: set[str]) -> str | None:
    for suffix, words in STAT_WORDS.items():
        if question_tokens & {stem(w) for w in words}:
            return suffix
    return None


# The subject and predicate vocabulary changes only when facts are written, and
# resolving a question reads it twice. Caching it turns a per-question pair of
# DISTINCT scans over the whole fact table into a dictionary lookup; writes call
# `invalidate` so a freshly ingested dataset is visible immediately.
_VOCAB_CACHE: dict[tuple[str, str | None], tuple[float, list[str]]] = {}
CACHE_TTL_SECONDS = 30.0


def invalidate(tenant_id: uuid.UUID | None = None) -> None:
    if tenant_id is None:
        _VOCAB_CACHE.clear()
        return
    key = str(tenant_id)
    for cached in [k for k in _VOCAB_CACHE if k[0] == key]:
        _VOCAB_CACHE.pop(cached, None)


def _cached(key: tuple[str, str | None], loader) -> list[str]:
    entry = _VOCAB_CACHE.get(key)
    now = time.monotonic()
    if entry is not None and now - entry[0] < CACHE_TTL_SECONDS:
        return entry[1]
    value = loader()
    _VOCAB_CACHE[key] = (now, value)
    return value


def load_subjects(session: Session, tenant_id: uuid.UUID) -> list[str]:
    return _cached((str(tenant_id), None), lambda: list(session.scalars(
        select(Fact.subject).where(Fact.tenant_id == tenant_id, Fact.is_active.is_(True))
        .distinct().limit(MAX_CANDIDATES)
    ).all()))


def load_predicates(session: Session, tenant_id: uuid.UUID,
                    subject: str | None = None) -> list[str]:
    """Predicates for one subject, or for the whole tenant.

    Scoping to the resolved subject matters: asking for the maximum weekly sales
    "in the walmart dataset" otherwise matches `sales_max` from the supermarket
    table, and the resulting subject/predicate pair names no fact that exists.
    """
    def _load() -> list[str]:
        stmt = select(Fact.predicate).where(Fact.tenant_id == tenant_id,
                                            Fact.is_active.is_(True))
        if subject:
            stmt = stmt.where(Fact.subject == subject)
        return list(session.scalars(stmt.distinct().limit(MAX_CANDIDATES)).all())

    return _cached((str(tenant_id), subject or "*"), _load)


def resolve(session: Session, principal: Principal, question: str,
            *, min_score: float = 0.34) -> Resolution:
    """Best (subject, predicate) in this workspace for the question, if any."""
    question_tokens = question_tokens_of(question)
    if not question_tokens:
        return Resolution()

    subjects = load_subjects(session, principal.tenant_id)
    statistic = _statistic_in(question_tokens)
    # Is this asked as a data question at all? Either the wording names a table,
    # or it asks for a computed figure. A question with neither is ordinary
    # prose - it may still match a dataset name by coincidence, but that match
    # must not be allowed to speak for the whole question.
    dataset_scoped = bool(question_tokens & {stem(w) for w in DATASET_MARKERS}
                          ) or statistic is not None

    # --- candidate subjects ----------------------------------------------
    subject_scores: list[tuple[str, float]] = []
    for subject in subjects:
        tokens = _tokens(subject) - {"dataset"}
        if not tokens:
            continue
        overlap = len(tokens & question_tokens) / len(tokens)
        # A long, specific subject name matching fully beats a short generic one.
        score = overlap + 0.05 * len(tokens & question_tokens)
        if overlap > 0:
            subject_scores.append((subject, score))
    subject_scores.sort(key=lambda item: (-item[1], item[0]))
    named = [s for s, score in subject_scores[:SUBJECT_CANDIDATES] if score >= 0.5]
    # A partial name match is still a name match. If the question mentions
    # something that looks like one of our datasets, the answer must come from a
    # dataset - never from the whole tenant, where an unrelated table with a
    # similarly named column would win.
    partial = [s for s, score in subject_scores[:SUBJECT_CANDIDATES] if score > 0]
    # Several candidates are tried, because the best-named subject may not hold the
    # column being asked about: `promotion.csv` and `employee_promotion.csv` both
    # match "promotion" and only one has departments.
    #
    # But if the question names a subject at all, the search stays inside those
    # candidates. Falling back to every predicate in the tenant would answer "the
    # median income in the walmart dataset" from a supermarket table that happens
    # to have an income column - which is the wrong answer, fluently delivered.
    candidate_subjects: list[str | None] = list(named) or list(partial) or [None]

    # Two tables can answer to the same name: `HR Data/train.csv` and
    # `Sales DB/TRAIN.csv` both match "the train dataset" exactly as well. The
    # loop below picks whichever candidate yields a matching column, which is the
    # worst possible tie-break - it guarantees answering from the one table that
    # *can* answer, even when the reader meant the other one, and the reply is
    # fluent and cited. An unequal match is different and must still resolve:
    # "promotion" names `promotion.csv` better than `employee_promotion.csv`, and
    # picking the closer name is correct there.
    #
    # So the trigger is an exact tie at the top, not merely several candidates.
    if candidate_subjects[0] is not None and len(subject_scores) > 1:
        top_score = subject_scores[0][1]
        tied = [s for s, score in subject_scores if score == top_score]
        if len(tied) > 1 and not any(score >= 1.0 for _, score in subject_scores):
            return Resolution(statistic=statistic, ambiguous_subjects=sorted(tied),
                              dataset_scoped=dataset_scoped)

    best: tuple[str | None, str, float] | None = None
    all_scored: list[tuple[str, float]] = []
    for candidate in candidate_subjects:
        # The dataset's own name identifies the table, not the column. Leaving
        # those words in would let "the Plant_1_Generation_Data dataset" match the
        # `plant_id` column and report a plant identifier as a household income.
        column_tokens = question_tokens - (_tokens(candidate) if candidate else set())
        scored = _score_predicates(
            load_predicates(session, principal.tenant_id, candidate),
            column_tokens, statistic)
        if not scored:
            continue
        all_scored = all_scored or scored
        predicate, score = scored[0]
        if best is None or score > best[2]:
            best = (candidate, predicate, score)
            all_scored = scored
        if score >= 1.0:
            break

    fallback_subject = named[0] if named else None
    if best is None:
        return Resolution(subject=fallback_subject, statistic=statistic,
                          dataset_scoped=dataset_scoped)

    best_subject, best_predicate, best_score = best
    best_subject = best_subject or fallback_subject
    if best_score < min_score:
        return Resolution(subject=best_subject or fallback_subject,
                          statistic=statistic, candidates=all_scored[:5],
                          dataset_scoped=dataset_scoped)
    return Resolution(subject=best_subject, predicate=best_predicate, score=best_score,
                      statistic=statistic, candidates=all_scored[:5],
                      dataset_scoped=dataset_scoped)


# Words that describe the shape of a question rather than the attribute it asks
# about. They are removed before checking how much of the question a candidate
# column actually accounts for.
GENERIC_TOKENS = {
    "dataset", "data", "table", "file", "record", "column", "field", "value",
    "figure", "number", "amount", "many", "much", "long",
}
# How much of the question's attribute wording a column must account for. Without
# this floor, "net promoter score" matches `avg_training_score` on the single word
# "score", and a training score is reported as a net promoter score.
MIN_ATTRIBUTE_COVERAGE = 0.5


def _score_predicates(predicates: list[str], question_tokens: set[str],
                      statistic: str | None) -> list[tuple[str, float]]:
    statistic_words = {stem(w) for words in STAT_WORDS.values() for w in words}
    attribute_tokens = question_tokens - statistic_words - GENERIC_TOKENS

    scored: list[tuple[str, float]] = []
    for predicate in predicates:
        tokens = _tokens(predicate)
        if not tokens:
            continue

        suffix_tokens = {"max", "min", "mean", "median", "count", "distinct",
                         "common", "most", "missing"}
        name_tokens = tokens - suffix_tokens
        matched = name_tokens & question_tokens
        alias_hit = any(predicate == alias and question_tokens & {stem(w) for w in words}
                        for alias, words in DIRECT_ALIASES.items())

        # The column has to be named. Without this, "median household income in the
        # walmart dataset" matches `column_0_median` on the statistic bonus alone -
        # walmart's unnamed index column - and reports a number for a quantity the
        # table does not hold.
        if not matched and not alias_hit:
            continue

        # The column must account for most of what was actually asked about, not
        # merely share one common word with it.
        if attribute_tokens and not alias_hit:
            covered = len(attribute_tokens & name_tokens) / len(attribute_tokens)
            if covered < MIN_ATTRIBUTE_COVERAGE:
                continue

        score = len(matched) / len(name_tokens) if name_tokens else 0.0

        # The statistic the question asks for must match the predicate's suffix,
        # otherwise "maximum weekly sales" happily resolves to the mean.
        if statistic:
            if predicate.endswith(f"_{statistic}"):
                score += 0.45
            elif any(predicate.endswith(f"_{other}") for other in STAT_WORDS
                     if other != statistic):
                score -= 0.35
        elif any(predicate.endswith(f"_{other}") for other in STAT_WORDS):
            # No statistic asked for, so a statistic predicate is a weaker fit.
            score -= 0.10

        if alias_hit:
            score += 0.6

        if score > 0:
            scored.append((predicate, score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored
