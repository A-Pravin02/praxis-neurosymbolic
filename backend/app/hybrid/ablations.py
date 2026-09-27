"""Switches that remove one mechanism of the system, for the ablation study only.

An ablation is only informative if it removes a mechanism *everywhere* it acts. Version
resolution, for example, is used by the reasoner that decides, by the grounding step that
reads records, and by the verifier that re-derives the answer; switching it off in only
the first would let the verifier quietly repair the damage and the study would report
that version resolution does not matter. So each switch is read at the point of use,
through a context variable the controller sets for the duration of one answer.

The context variable is per thread and per task, so concurrent benchmark workers running
different ablations never see each other's switches. With no switch set - always, in the
deployed service - every mechanism is on.

    latest_version     the newest policy version is applied to every case
    two_valued         an unknown fact is false, as in a classical rule engine
    trust_statements   a fact a person states overrides the company record, and a
                       claimed approval counts as a recorded one
    no_authorization   reasoning runs over every record, whoever is asking
    no_verification    an answer that fails verification is released anyway
    no_number_check    a claim carrying a number is scored on wording alone, so a
                       sentence that reads like the evidence supports it even when
                       the figure in it does not appear there
    no_kind_agreement  the goal nearest the question wins even when it answers a
                       different kind of question than the one asked - an amount
                       where a yes or no was wanted, or the reverse
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

KNOWN = frozenset({"latest_version", "two_valued", "trust_statements", "no_authorization",
                   "no_verification", "no_number_check", "no_kind_agreement"})

_ACTIVE: ContextVar[frozenset[str]] = ContextVar("praxis_ablations", default=frozenset())


def active(name: str) -> bool:
    return name in _ACTIVE.get()


@contextmanager
def applied(names: frozenset[str]) -> Iterator[None]:
    unknown = set(names) - KNOWN
    if unknown:
        raise ValueError(f"unknown ablation: {sorted(unknown)}")
    token = _ACTIVE.set(frozenset(names))
    try:
        yield
    finally:
        _ACTIVE.reset(token)
