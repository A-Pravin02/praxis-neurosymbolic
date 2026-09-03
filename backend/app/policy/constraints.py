"""Access constraint language: validate -> AST -> safe SQL predicate.

Constraints are JSON expressions over a *whitelisted* set of fields per resource
type. They are never SQL, never Python, and never evaluated with `eval`. The
compiler emits SQLAlchemy expressions with bound parameters, so a constraint
cannot inject SQL even if an administrator writes something hostile.

Grammar
-------
    expr := {"op": "and"|"or", "args": [expr, ...]}
          | {"op": "not", "args": [expr]}
          | {"op": <comparison>, "field": <name>, "value": <literal>}
          | {"op": <comparison>, "field": <name>, "ref": <context path>}
          | {"op": "is_null"|"not_null", "field": <name>}
          | {"op": "true"} | {"op": "false"}

    comparison := eq | ne | lt | lte | gt | gte | in | not_in
                | contains | startswith | endswith

    context path := "subject.<attr>" | "env.<attr>"

Missing context is INDETERMINATE, not a silent allow: `resolve` raises and the
PDP turns that into INDETERMINATE (deny by default).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, and_, false, not_, or_, true

from app.core.errors import ConstraintCompileError

MAX_DEPTH = 8
MAX_NODES = 120

LOGICAL_OPS = {"and", "or", "not"}
COMPARISON_OPS = {
    "eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in",
    "contains", "startswith", "endswith",
}
NULLARY_FIELD_OPS = {"is_null", "not_null"}
CONSTANT_OPS = {"true", "false"}
ALL_OPS = LOGICAL_OPS | COMPARISON_OPS | NULLARY_FIELD_OPS | CONSTANT_OPS

# Fields a constraint may mention, per resource type. Anything else is rejected.
RESOURCE_FIELDS: dict[str, dict[str, str]] = {
    "document": {
        "tenant_id": "uuid", "collection": "string", "sensitivity": "string",
        "required_permission": "string", "uploaded_by": "uuid", "status": "string",
        "media_type": "string", "effective_date": "string", "version_label": "string",
        "title": "string",
    },
    "chunk": {
        "tenant_id": "uuid", "document_id": "uuid", "injection_flagged": "bool",
        "section": "string",
    },
    "fact": {
        "tenant_id": "uuid", "subject": "string", "predicate": "string", "kind": "string",
        "sensitivity": "string", "required_permission": "string", "is_active": "bool",
    },
    "conversation": {"tenant_id": "uuid", "user_id": "uuid", "is_archived": "bool"},
    "salary_record": {
        "tenant_id": "uuid", "employee_id": "string", "department": "string",
        "amount": "number",
    },
    "rule": {"tenant_id": "uuid", "code": "string", "is_active": "bool"},
}

CONTEXT_ROOTS = {"subject", "env"}


class MissingContext(Exception):
    """A referenced context value was not supplied."""


@dataclass
class Node:
    op: str
    args: list["Node"] = field(default_factory=list)
    field_name: str | None = None
    value: Any = None
    ref: str | None = None

    def to_json(self) -> dict:
        out: dict[str, Any] = {"op": self.op}
        if self.args:
            out["args"] = [a.to_json() for a in self.args]
        if self.field_name is not None:
            out["field"] = self.field_name
        if self.ref is not None:
            out["ref"] = self.ref
        elif self.value is not None or self.op in COMPARISON_OPS:
            out["value"] = self.value
        return out


# ---------------------------------------------------------------------------
# Parsing / validation
# ---------------------------------------------------------------------------

def parse(expression: Any, resource_type: str, *, _depth: int = 0,
          _budget: list[int] | None = None) -> Node:
    if _budget is None:
        _budget = [MAX_NODES]
    _budget[0] -= 1
    if _budget[0] < 0:
        raise ConstraintCompileError("The constraint expression is too large.")
    if _depth > MAX_DEPTH:
        raise ConstraintCompileError("The constraint expression is nested too deeply.")
    if not isinstance(expression, dict):
        raise ConstraintCompileError("Each constraint node must be an object.")

    op = expression.get("op")
    if not isinstance(op, str) or op not in ALL_OPS:
        raise ConstraintCompileError(f"Unsupported constraint operator: {op!r}.")

    if op in CONSTANT_OPS:
        return Node(op=op)

    if op in LOGICAL_OPS:
        args = expression.get("args")
        if not isinstance(args, list) or not args:
            raise ConstraintCompileError(f"'{op}' requires a non-empty args list.")
        if op == "not" and len(args) != 1:
            raise ConstraintCompileError("'not' takes exactly one argument.")
        return Node(op=op, args=[parse(a, resource_type, _depth=_depth + 1, _budget=_budget)
                                 for a in args])

    fields = RESOURCE_FIELDS.get(resource_type)
    if fields is None:
        raise ConstraintCompileError(f"Unknown resource type: {resource_type!r}.")
    name = expression.get("field")
    if not isinstance(name, str) or name not in fields:
        raise ConstraintCompileError(
            f"Field {name!r} is not permitted for resource type {resource_type!r}."
        )

    if op in NULLARY_FIELD_OPS:
        return Node(op=op, field_name=name)

    has_value = "value" in expression
    has_ref = "ref" in expression
    if has_value == has_ref:
        raise ConstraintCompileError(
            "A comparison needs exactly one of 'value' or 'ref'."
        )
    if has_ref:
        ref = expression["ref"]
        if not isinstance(ref, str) or "." not in ref:
            raise ConstraintCompileError("A 'ref' must look like 'subject.attribute'.")
        root, _, attr = ref.partition(".")
        if root not in CONTEXT_ROOTS or not attr.replace("_", "").replace(".", "").isalnum():
            raise ConstraintCompileError(f"Unsupported context reference: {ref!r}.")
        return Node(op=op, field_name=name, ref=ref)

    value = expression["value"]
    if op in {"in", "not_in"}:
        if not isinstance(value, list) or not value:
            raise ConstraintCompileError(f"'{op}' requires a non-empty list value.")
        if len(value) > 200:
            raise ConstraintCompileError(f"'{op}' accepts at most 200 values.")
        for item in value:
            _check_literal(item)
    else:
        _check_literal(value)
    return Node(op=op, field_name=name, value=value)


def _check_literal(value: Any) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > 512:
            raise ConstraintCompileError("Literal strings are limited to 512 characters.")
        return
    raise ConstraintCompileError("Constraint literals must be scalars.")


def validate(expression: Any, resource_type: str) -> Node:
    """Parse and return the AST, raising ConstraintCompileError on any problem."""
    return parse(expression, resource_type)


# ---------------------------------------------------------------------------
# Context resolution
# ---------------------------------------------------------------------------

def resolve_ref(ref: str, context: dict[str, Any]) -> Any:
    root, _, attr = ref.partition(".")
    bucket = context.get(root)
    if not isinstance(bucket, dict):
        raise MissingContext(ref)
    cursor: Any = bucket
    for part in attr.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            raise MissingContext(ref)
        cursor = cursor[part]
    return cursor


# ---------------------------------------------------------------------------
# In-memory evaluation (single resource)
# ---------------------------------------------------------------------------

def evaluate(node: Node, resource: dict[str, Any], context: dict[str, Any]) -> bool:
    if node.op == "true":
        return True
    if node.op == "false":
        return False
    if node.op == "and":
        return all(evaluate(a, resource, context) for a in node.args)
    if node.op == "or":
        return any(evaluate(a, resource, context) for a in node.args)
    if node.op == "not":
        return not evaluate(node.args[0], resource, context)

    assert node.field_name is not None
    if node.field_name not in resource:
        raise MissingContext(f"resource.{node.field_name}")
    left = resource[node.field_name]

    if node.op == "is_null":
        return left is None
    if node.op == "not_null":
        return left is not None

    right = resolve_ref(node.ref, context) if node.ref else node.value
    return _compare(node.op, left, right)


def _norm(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _compare(op: str, left: Any, right: Any) -> bool:
    left, right = _norm(left), _norm(right)
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "in":
        return left in [_norm(v) for v in (right or [])]
    if op == "not_in":
        return left not in [_norm(v) for v in (right or [])]
    if op == "contains":
        return isinstance(left, str) and isinstance(right, str) and right.lower() in left.lower()
    if op == "startswith":
        return isinstance(left, str) and isinstance(right, str) and left.lower().startswith(right.lower())
    if op == "endswith":
        return isinstance(left, str) and isinstance(right, str) and left.lower().endswith(right.lower())
    if left is None or right is None:
        # Ordering against NULL is unknown, which must not read as permitted.
        return False
    try:
        if op == "lt":
            return left < right
        if op == "lte":
            return left <= right
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
    except TypeError:
        return False
    raise ConstraintCompileError(f"Unsupported operator: {op!r}")


# ---------------------------------------------------------------------------
# SQL compilation
# ---------------------------------------------------------------------------

def compile_sql(node: Node, columns: dict[str, Any],
                context: dict[str, Any]) -> ColumnElement[bool]:
    """Compile an AST to a SQLAlchemy boolean expression.

    `columns` maps whitelisted field names to InstrumentedAttributes. Values are
    always bound parameters — never interpolated into SQL text.
    """
    if node.op == "true":
        return true()
    if node.op == "false":
        return false()
    if node.op == "and":
        return and_(*(compile_sql(a, columns, context) for a in node.args))
    if node.op == "or":
        return or_(*(compile_sql(a, columns, context) for a in node.args))
    if node.op == "not":
        return not_(compile_sql(node.args[0], columns, context))

    assert node.field_name is not None
    column = columns.get(node.field_name)
    if column is None:
        raise ConstraintCompileError(
            f"Field {node.field_name!r} cannot be applied to this query."
        )
    if node.op == "is_null":
        return column.is_(None)
    if node.op == "not_null":
        return column.isnot(None)

    right = resolve_ref(node.ref, context) if node.ref else node.value
    right = _norm(right)
    if node.op == "eq":
        return column == right
    if node.op == "ne":
        return column != right
    if node.op == "lt":
        return column < right
    if node.op == "lte":
        return column <= right
    if node.op == "gt":
        return column > right
    if node.op == "gte":
        return column >= right
    if node.op == "in":
        return column.in_([_norm(v) for v in right])
    if node.op == "not_in":
        return column.notin_([_norm(v) for v in right])
    if node.op == "contains":
        return column.ilike(f"%{_escape_like(str(right))}%", escape="\\")
    if node.op == "startswith":
        return column.ilike(f"{_escape_like(str(right))}%", escape="\\")
    if node.op == "endswith":
        return column.ilike(f"%{_escape_like(str(right))}", escape="\\")
    raise ConstraintCompileError(f"Unsupported operator: {node.op!r}")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def describe(node: Node) -> str:
    """Human-readable rendering for the reasoning trace."""
    if node.op in CONSTANT_OPS:
        return node.op
    if node.op == "and":
        return "(" + " AND ".join(describe(a) for a in node.args) + ")"
    if node.op == "or":
        return "(" + " OR ".join(describe(a) for a in node.args) + ")"
    if node.op == "not":
        return f"NOT {describe(node.args[0])}"
    if node.op in NULLARY_FIELD_OPS:
        return f"{node.field_name} {'IS NULL' if node.op == 'is_null' else 'IS NOT NULL'}"
    symbol = {
        "eq": "==", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=",
        "in": "IN", "not_in": "NOT IN", "contains": "CONTAINS",
        "startswith": "STARTS WITH", "endswith": "ENDS WITH",
    }[node.op]
    right = node.ref if node.ref else repr(node.value)
    return f"{node.field_name} {symbol} {right}"
