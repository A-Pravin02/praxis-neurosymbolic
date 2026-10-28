"""NEURAL. Turn an established conclusion into a reply a person reads.

This runs *after* every decision has been made. It receives a structured conclusion -
the decision, the value, the reasons the rules gave, the conditions that are still
unknown - and phrases it. It is never asked what the answer is.

Two realizers share one contract:

* `realize` composes the reply deterministically from the conclusion. It cannot add a
  fact because it only has the conclusion's fields to work with. It is used offline and
  whenever a model's phrasing fails verification.
* `LanguageModelRealizer` asks the configured LLM to rephrase the same conclusion more
  naturally. Its output is then checked: every number it states must appear in the
  conclusion, or the deterministic reply is used instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.logging import get_logger, log_event
from app.hybrid import ablations

logger = get_logger("praxis.neural.generation")


@dataclass
class Conclusion:
    kind: str                     # decision | calculation | explanation | enumeration |
    #                               lookup | retrieval | clarification | insufficient | denied
    status: str = "determined"    # determined | conditional | default | unknown | conflict
    subject: str = ""
    headline: str = ""            # the phrased result, e.g. "your order can be returned"
    decision: bool | None = None
    value: str | None = None
    reasons: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    cases: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    clauses: list[dict] = field(default_factory=list)
    options: list[dict] = field(default_factory=list)
    prompt: str = ""

    def facts_text(self) -> str:
        """Everything the reply is allowed to draw on, as one string."""
        parts = [self.subject, self.headline, self.value or "", *self.reasons,
                 *self.conditions, *[f"{a} {b}" for a, b in self.cases], *self.missing,
                 *self.notes, *self.corrections, *self.items, self.prompt,
                 # The clause id and document are part of what may be printed: without
                 # them the "1" in "(RET-1)" reads as a figure the reply invented.
                 *[" ".join(str(c.get(k, "")) for k in ("id", "title", "document", "text"))
                   for c in self.clauses],
                 *[o.get("label", "") + " " + o.get("id", "") + " " + o.get("detail", "")
                   for o in self.options]]
        return " ".join(p for p in parts if p)


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


def _join(items: list[str], word: str = "and") -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {word} " + items[-1]


def realize(c: Conclusion) -> str:
    lines: list[str] = []

    if c.corrections:
        lines.append(" ".join(_sentence(x) for x in c.corrections))

    if c.kind == "retrieval":
        for clause in c.clauses:
            lines.append(f"{clause['text']} ({clause['id']}, {clause['document']})")
    elif c.kind == "lookup":
        lines.append(_sentence(c.headline))
    elif c.kind == "clarification":
        lines.append(_sentence(c.prompt))
        for option in c.options:
            detail = f" — {option['detail']}" if option.get("detail") else ""
            lines.append(f"• {option['label']}" + (f" ({option['id']})" if option.get("id")
                                                   and option["id"] != option["label"]
                                                   else "") + detail)
    elif c.kind in ("insufficient", "denied"):
        lines.append(_sentence(c.headline))
        lines.extend(_sentence(r) for r in c.reasons)
    elif c.kind == "enumeration":
        if c.items:
            lines.append(_sentence(c.headline))
            lines.extend(f"• {item}" for item in c.items)
        else:
            lines.append(_sentence(c.headline))
        lines.extend(_sentence(r) for r in c.reasons)
    elif c.status == "conditional" and c.cases:
        lines.append(_sentence(c.headline))
        for condition, outcome in c.cases:
            lines.append(f"• If {condition}: {outcome}")
        if c.conditions:
            lines.append(_sentence("In every case this assumes " + _join(c.conditions)))
    elif c.status == "conditional":
        opener = "Provisionally, " if c.kind != "explanation" else ""
        lines.append(_sentence(opener + c.headline + (
            f" — {c.reasons[0]}" if c.reasons else "")))
        if c.conditions:
            lines.append(_sentence("That holds as long as " + _join(c.conditions)))
    else:
        opener = ""
        if c.decision is True:
            opener = "Yes — "
        elif c.decision is False:
            opener = "No — "
        reason = f": {_join(c.reasons, 'and')}" if c.reasons else ""
        lines.append(_sentence(opener + c.headline + reason))

    if c.missing:
        lines.append(_sentence("To give a definite answer I would need to know " +
                               _join(c.missing, "and")))
    if c.notes:
        lines.append(" ".join(_sentence(n) for n in c.notes))
    return "\n".join(line for line in lines if line).strip()


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[float]:
    out = set()
    for token in _NUMBER.findall(text or ""):
        try:
            out.add(round(float(token.replace(",", "")), 2))
        except ValueError:
            continue
    return out


class LanguageModelRealizer:
    """Rephrase a conclusion with the configured model, and distrust the result."""

    def __init__(self, llm) -> None:
        self.llm = llm

    def realize(self, c: Conclusion) -> tuple[str, bool]:
        from app.neuro.base import CompletionRequest, Message, Role, Task

        draft = realize(c)
        if getattr(self.llm, "name", "") == "mock":
            return draft, False
        request = CompletionRequest(
            task=Task.GROUNDED_ANSWER,
            system=("Rewrite the reply below so it reads naturally to a customer or employee. "
                    "Do not add, remove or change any fact, amount, date, condition or policy "
                    "reference. Do not add advice. Keep bullet points if present."),
            messages=[Message(Role.USER, draft)],
        )
        try:
            text = self.llm.complete(request).text.strip()
        except Exception as exc:  # noqa: BLE001 - any provider failure falls back
            log_event(logger, 30, "realizer model failed; using deterministic reply",
                      error=type(exc).__name__)
            return draft, False
        if not text:
            # Robustness, not verification: an empty completion is a provider failure
            # and must fall back whatever the ablation says, or the system returns
            # nothing at all.
            log_event(logger, 30, "realizer model returned nothing; using deterministic reply")
            return draft, False

        allowed = numbers_in(c.facts_text()) | numbers_in(draft)
        if not numbers_in(text) <= allowed:
            # This is a verification check: the model's wording introduced a figure the
            # established conclusion does not support. `no_verification` must remove it,
            # because an ablation that leaves a guard running measures nothing - the
            # arms come out byte-identical and the study reports no effect where it
            # simply failed to remove one.
            if ablations.active("no_verification"):
                log_event(logger, 30, "model phrasing introduced a figure; released "
                                      "anyway under the no_verification ablation")
                return text, True
            log_event(logger, 30, "model phrasing introduced a figure; using deterministic reply")
            return draft, False
        return text, True
