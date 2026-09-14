"""Turn symbolic values into the words and figures a person reads.

Rendering is typed by the schema, never guessed from the value: a 5 declared as
`days` prints "5", a 5 declared as `money` prints "₹5", and a date is stored as
a day number but always prints as a date.
"""
from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from app.symbolic.dsl import KnowledgeProgram
    from app.symbolic.knowledge_graph import KnowledgeGraph


def humanize(name: str) -> str:
    text = str(name).replace("_", " ").strip()
    if text.endswith(" km"):
        text = text[:-3] + " (km)"
    return text


def format_number(value: float, *, decimals: int = 2) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.{decimals}f}".rstrip("0").rstrip(".")


def format_value(value: Any, value_type: str, *, graph: "KnowledgeGraph | None" = None,
                 program: "KnowledgeProgram | None" = None) -> str:
    from app.symbolic.reasoner import Symbolic, Unknown

    if isinstance(value, Unknown):
        return humanize(value.predicate)
    if isinstance(value, Symbolic):
        return value.render(value_type, graph=graph, program=program)
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value_type == "money" and isinstance(value, (int, float)):
        return f"₹{format_number(float(value))}"
    if value_type == "percent" and isinstance(value, (int, float)):
        return f"{format_number(float(value) * 100)}%"
    if value_type == "years" and isinstance(value, (int, float)):
        return format_number(round(float(value), 1), decimals=1)
    if value_type in ("days", "distance", "number") and isinstance(value, (int, float)):
        return format_number(float(value))
    if value_type == "date" and isinstance(value, (int, float)):
        try:
            return dt.date.fromordinal(int(value)).strftime("%d %b %Y").lstrip("0")
        except (ValueError, OverflowError):
            return str(value)
    if graph is not None and isinstance(value, str) and graph.has_entity(value):
        return graph.display_name(value)
    if isinstance(value, (int, float)):
        return format_number(float(value))
    return humanize(value)


def render_template(template: str, bindings: dict[str, Any], types: dict[str, str], *,
                    graph: "KnowledgeGraph | None" = None,
                    program: "KnowledgeProgram | None" = None) -> str:
    """Fill `{d}` slots in a rule's explanation from its variable bindings."""
    if not template:
        return ""
    out = template
    for name, value in bindings.items():
        slot = "{" + name.lstrip("?") + "}"
        if slot in out:
            out = out.replace(slot, format_value(value, types.get(name, "text"),
                                                 graph=graph, program=program))
    return out
