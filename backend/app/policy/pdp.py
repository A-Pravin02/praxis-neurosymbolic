"""Policy Decision Point.

Deterministic, explainable authorization. Given a Subject, an Action, a Resource
and a Context, it returns ALLOW, DENY or INDETERMINATE together with the policy
that decided and the evidence used. No language model is involved and none can
influence the outcome.

Combining algorithm: **deny-overrides**, with default-deny.
    1. Tenant boundary is checked first and is not overridable by any policy.
    2. Any matching DENY policy wins.
    3. Otherwise the highest-priority matching ALLOW wins.
    4. If a policy's condition cannot be evaluated (missing context), the result
       is INDETERMINATE, which callers must treat as a denial.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.logging import get_logger
from app.policy import constraints
from app.policy.constraints import MissingContext, Node
from app.tenancy.context import Principal

logger = get_logger("praxis.pdp")


class Decision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class Resource:
    resource_type: str
    resource_id: str = ""
    tenant_id: uuid.UUID | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = dict(self.attributes)
        data.setdefault("tenant_id", str(self.tenant_id) if self.tenant_id else None)
        return data


@dataclass(frozen=True)
class AccessRequest:
    subject: Principal
    action: str
    resource: Resource
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyView:
    """A policy in the form the PDP evaluates. Decoupled from the ORM."""
    code: str
    effect: str
    priority: int = 100
    subject_roles: list[str] = field(default_factory=list)
    subject_permissions: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    resource_types: list[str] = field(default_factory=list)
    condition: dict | None = None
    obligations: list[str] = field(default_factory=list)
    name: str = ""

    def matches_subject(self, principal: Principal) -> bool:
        if self.subject_roles and not (set(self.subject_roles) & set(principal.roles)):
            return False
        if self.subject_permissions and not (
            set(self.subject_permissions) & set(principal.permissions)
        ):
            return False
        return True

    def matches_action(self, action: str) -> bool:
        return not self.actions or "*" in self.actions or action in self.actions

    def matches_resource(self, resource_type: str) -> bool:
        return (not self.resource_types or "*" in self.resource_types
                or resource_type in self.resource_types)


@dataclass
class PolicyDecision:
    decision: Decision
    reason: str
    matched_policy: str | None = None
    obligations: list[str] = field(default_factory=list)
    evaluated: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "matched_policy": self.matched_policy,
            "obligations": list(self.obligations),
            "evaluated": self.evaluated,
            "provenance": self.provenance,
        }


def build_context(principal: Principal, extra: dict[str, Any] | None = None) -> dict:
    return {
        "subject": {
            "user_id": str(principal.user_id),
            "tenant_id": str(principal.tenant_id),
            "email": principal.email,
            "roles": sorted(principal.roles),
            "permissions": sorted(principal.permissions),
            **{k: v for k, v in (principal.attributes or {}).items()},
        },
        "env": dict(extra or {}),
    }


class PolicyDecisionPoint:
    def __init__(self, policies: list[PolicyView]) -> None:
        # Highest priority first; ties broken by code for determinism.
        self.policies = sorted(policies, key=lambda p: (-p.priority, p.code))

    def decide(self, request: AccessRequest) -> PolicyDecision:
        subject = request.subject
        resource = request.resource
        evaluated: list[dict[str, Any]] = []

        # 1. Tenant boundary. Not delegated to policy data: a misconfigured or
        #    hostile policy row must not be able to open a cross-tenant path.
        if resource.tenant_id is not None and resource.tenant_id != subject.tenant_id:
            return PolicyDecision(
                decision=Decision.DENY,
                reason="Resource belongs to a different tenant.",
                matched_policy="builtin:tenant_boundary",
                evaluated=[{"policy": "builtin:tenant_boundary", "result": "DENY"}],
                provenance={"subject_tenant": str(subject.tenant_id),
                            "resource_tenant": str(resource.tenant_id)},
            )

        context = build_context(subject, request.context)
        resource_data = resource.as_dict()

        allows: list[tuple[PolicyView, list[str]]] = []
        indeterminate: list[str] = []

        for policy in self.policies:
            if not (policy.matches_subject(subject) and policy.matches_action(request.action)
                    and policy.matches_resource(resource.resource_type)):
                continue
            condition_ok = True
            note = ""
            if policy.condition:
                try:
                    node: Node = constraints.validate(policy.condition,
                                                      resource.resource_type)
                    condition_ok = constraints.evaluate(node, resource_data, context)
                    note = constraints.describe(node)
                except MissingContext as exc:
                    evaluated.append({"policy": policy.code, "result": "INDETERMINATE",
                                      "missing": str(exc)})
                    indeterminate.append(f"{policy.code}: missing {exc}")
                    continue
                except Exception as exc:
                    evaluated.append({"policy": policy.code, "result": "INDETERMINATE",
                                      "error": type(exc).__name__})
                    indeterminate.append(f"{policy.code}: invalid condition")
                    continue
            evaluated.append({
                "policy": policy.code, "effect": policy.effect,
                "result": "MATCH" if condition_ok else "NO_MATCH",
                "condition": note,
            })
            if not condition_ok:
                continue
            if policy.effect.upper() == "DENY":
                # 2. Deny overrides everything.
                return PolicyDecision(
                    decision=Decision.DENY,
                    reason=policy.name or f"Denied by policy {policy.code}.",
                    matched_policy=policy.code,
                    obligations=list(policy.obligations),
                    evaluated=evaluated,
                    provenance={"action": request.action,
                                "resource_type": resource.resource_type,
                                "resource_id": resource.resource_id},
                )
            allows.append((policy, list(policy.obligations)))

        if allows:
            policy, obligations = allows[0]
            return PolicyDecision(
                decision=Decision.ALLOW,
                reason=policy.name or f"Allowed by policy {policy.code}.",
                matched_policy=policy.code,
                obligations=obligations,
                evaluated=evaluated,
                provenance={"action": request.action,
                            "resource_type": resource.resource_type,
                            "resource_id": resource.resource_id},
            )

        if indeterminate:
            # 4. Unevaluable condition: refuse rather than guess.
            return PolicyDecision(
                decision=Decision.INDETERMINATE,
                reason="A policy condition could not be evaluated with the available context.",
                evaluated=evaluated,
                provenance={"unresolved": indeterminate[:5]},
            )

        # 3. Default deny.
        return PolicyDecision(
            decision=Decision.DENY,
            reason="No policy grants this action.",
            matched_policy=None,
            evaluated=evaluated,
            provenance={"action": request.action,
                        "resource_type": resource.resource_type},
        )
