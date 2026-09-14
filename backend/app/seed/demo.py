"""Seed the demonstration workspaces.

Creates two isolated tenants with users, roles, policies, access constraints,
documents, facts and rules. Idempotent: running it twice does not duplicate
anything.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import passwords
from app.auth.service import provision_tenant_roles, resolve_principal
from app.core.logging import get_logger, log_event
from app.db.models.policy import AccessConstraint, Policy
from app.db.models.rag import Document
from app.db.models.symbolic import ConstraintRule, Rule
from app.db.models.tenancy import (
    Role, Tenant, TenantLookup, User, UserLookup, UserRole,
)
from app.db.provision import ensure_permission_catalogue
from app.db.session import SessionLocal, bind_tenant
from app.knowledge import store as knowledge_store
from app.rag.ingest import IngestOptions, ingest_text
from app.seed.documents import ACME_DOCUMENTS, GLOBEX_DOCUMENTS
from app.tenancy.context import Principal

logger = get_logger("praxis.seed")

DEMO_PASSWORD = "Praxis-Demo-2026!"

ACME_SLUG = "acme"
GLOBEX_SLUG = "globex"


@dataclass
class DemoUser:
    email: str
    display_name: str
    roles: list[str]
    attributes: dict[str, Any]


ACME_USERS = [
    DemoUser("admin@acme.test", "Acme Admin", ["admin"],
             {"years_of_service": 7, "department": "operations", "country": "India",
              "age": 41, "employee_id": "E-1001"}),
    DemoUser("alice@acme.test", "Alice Chen", ["employee"],
             {"years_of_service": 4, "department": "engineering", "country": "India",
              "age": 33, "employee_id": "E-1002"}),
    DemoUser("bob@acme.test", "Bob Ferreira", ["employee"],
             {"years_of_service": 1, "department": "support", "country": "Portugal",
              "age": 26, "employee_id": "E-1003"}),
    DemoUser("hr@acme.test", "Hana Rao", ["hr"],
             {"years_of_service": 6, "department": "people", "country": "India",
              "age": 38, "employee_id": "E-1004"}),
    DemoUser("analyst@acme.test", "Ada Silva", ["analyst"],
             {"years_of_service": 3, "department": "data", "country": "Brazil",
              "age": 30, "employee_id": "E-1005"}),
]

GLOBEX_USERS = [
    DemoUser("admin@globex.test", "Globex Admin", ["admin"],
             {"years_of_service": 9, "department": "operations", "country": "Germany",
              "age": 45, "employee_id": "G-2001"}),
    DemoUser("carol@globex.test", "Carol Novak", ["employee"],
             {"years_of_service": 6, "department": "sales", "country": "Germany",
              "age": 35, "employee_id": "G-2002"}),
]

# --- rules ------------------------------------------------------------------

ACME_RULES = [
    {
        "code": "R12",
        "name": "Annual leave after two years of service",
        "description": "Handbook 2026 section 2: more than 2 years of service gives "
                       "20 days of annual leave.",
        "body": [
            {"type": "fact", "subject": "?e", "predicate": "years_of_service",
             "value": "?y"},
            {"type": "comparison", "left": "?y", "op": "gt", "right": {"const": 2}},
        ],
        "head": {"subject": "?e", "predicate": "annual_leave_days",
                 "value": {"const": 20}, "unit": "days"},
        "priority": 200,
    },
    {
        "code": "R13",
        "name": "Annual leave during the first two years",
        "description": "Handbook 2026 section 2: 2 years of service or less gives "
                       "15 days of annual leave.",
        "body": [
            {"type": "fact", "subject": "?e", "predicate": "years_of_service",
             "value": "?y"},
            {"type": "comparison", "left": "?y", "op": "lte", "right": {"const": 2}},
        ],
        "head": {"subject": "?e", "predicate": "annual_leave_days",
                 "value": {"const": 15}, "unit": "days"},
        "priority": 190,
    },
    {
        "code": "R14",
        "name": "Leave carryover cap",
        "description": "Handbook 2026 section 2: at most 5 days may be carried over.",
        "body": [
            {"type": "fact", "subject": "?e", "predicate": "annual_leave_days",
             "value": "?d"},
        ],
        "head": {"subject": "?e", "predicate": "carryover_days",
                 "value": {"expr": {"op": "min", "args": ["?d", {"const": 5}]}},
                 "unit": "days"},
        "priority": 150,
    },
    {
        "code": "R15",
        "name": "Probation status",
        "description": "An employee with less than 0.5 years of service is on probation.",
        "body": [
            {"type": "fact", "subject": "?e", "predicate": "years_of_service",
             "value": "?y"},
            {"type": "comparison", "left": "?y", "op": "lt", "right": {"const": 0.5}},
        ],
        "head": {"subject": "?e", "predicate": "on_probation", "value": {"const": True}},
        "priority": 140,
    },
    {
        "code": "R16",
        "name": "Notice period after probation",
        "description": "Handbook 2026 section 6: 30 days once probation is complete.",
        "body": [
            {"type": "fact", "subject": "?e", "predicate": "years_of_service",
             "value": "?y"},
            {"type": "comparison", "left": "?y", "op": "gte", "right": {"const": 0.5}},
        ],
        "head": {"subject": "?e", "predicate": "notice_period_days",
                 "value": {"const": 30}, "unit": "days"},
        "priority": 130,
    },
    {
        "code": "R17",
        "name": "Long-service extra training budget",
        "description": "Employees with 5 or more years of service get an extra "
                       "300 USD of training budget on top of the standard 1200.",
        "body": [
            {"type": "fact", "subject": "?e", "predicate": "years_of_service",
             "value": "?y"},
            {"type": "comparison", "left": "?y", "op": "gte", "right": {"const": 5}},
        ],
        "head": {"subject": "?e", "predicate": "training_budget",
                 "value": {"const": 1500}, "unit": "usd"},
        "priority": 120,
    },
]

GLOBEX_RULES = [
    {
        "code": "G01",
        "name": "Globex annual leave",
        "description": "Staff guide: more than 1 year of service gives 25 days.",
        "body": [
            {"type": "fact", "subject": "?e", "predicate": "years_of_service",
             "value": "?y"},
            {"type": "comparison", "left": "?y", "op": "gt", "right": {"const": 1}},
        ],
        "head": {"subject": "?e", "predicate": "annual_leave_days",
                 "value": {"const": 25}, "unit": "days"},
        "priority": 200,
    },
]

ACME_CONSTRAINT_RULES = [
    {"code": "C01", "description": "Employees must be at least 18 years old.",
     "expression": {"predicate": "age", "op": "gte", "value": 18}, "severity": "error"},
    {"code": "C02", "description": "Annual leave may not exceed 40 days.",
     "expression": {"predicate": "annual_leave_days", "op": "lte", "value": 40},
     "severity": "error"},
]

# --- policies ---------------------------------------------------------------

def base_policies(tenant_label: str) -> list[dict[str, Any]]:
    return [
        {
            "code": "P010", "name": "Everyone may read general documents",
            "effect": "ALLOW", "priority": 100,
            "subject_permissions": ["document:read"], "actions": ["READ"],
            "resource_types": ["document", "chunk"],
            "condition": {"op": "eq", "field": "tenant_id", "ref": "subject.tenant_id"},
        },
        {
            "code": "P020", "name": "HR-confidential documents need the HR permission",
            "effect": "DENY", "priority": 900,
            "subject_roles": [], "actions": ["READ"], "resource_types": ["document"],
            "condition": {"op": "and", "args": [
                {"op": "eq", "field": "sensitivity", "value": "hr_confidential"},
                {"op": "not_in", "field": "required_permission",
                 "value": ["document:read_hr"]},
            ]},
            "description": "Belt and braces: an HR-confidential document that is not "
                           "correctly labelled is denied outright.",
        },
        {
            "code": "P030", "name": "Salary records require the salary:read permission",
            "effect": "ALLOW", "priority": 300,
            "subject_permissions": ["salary:read"], "actions": ["READ"],
            "resource_types": ["salary_record"],
            "condition": {"op": "eq", "field": "tenant_id", "ref": "subject.tenant_id"},
        },
        {
            "code": "P040", "name": "Only HR may read HR-confidential documents",
            "effect": "ALLOW", "priority": 400,
            "subject_permissions": ["document:read_hr"], "actions": ["READ"],
            "resource_types": ["document", "chunk"],
        },
        {
            "code": "P050", "name": "Owners may read their own conversations",
            "effect": "ALLOW", "priority": 200,
            "subject_permissions": ["conversation:read"], "actions": ["READ"],
            "resource_types": ["conversation"],
            "condition": {"op": "eq", "field": "user_id", "ref": "subject.user_id"},
        },
        {
            "code": "P060", "name": "Rule authors may write rules",
            "effect": "ALLOW", "priority": 200,
            "subject_permissions": ["rule:write"], "actions": ["WRITE", "READ"],
            "resource_types": ["rule"],
        },
        {
            "code": "P900", "name": f"Default deny outside {tenant_label}",
            "effect": "DENY", "priority": 1000, "actions": ["*"],
            "resource_types": ["*"],
            "condition": {"op": "ne", "field": "tenant_id", "ref": "subject.tenant_id"},
        },
    ]


def base_constraints() -> list[dict[str, Any]]:
    return [
        {
            "code": "AC01",
            "description": "Retrieval is confined to the caller's own tenant.",
            "resource_type": "document",
            "applies_to_roles": [],
            "expression": {"op": "eq", "field": "tenant_id", "ref": "subject.tenant_id"},
        },
        {
            "code": "AC02",
            "description": "Only ready documents are retrievable.",
            "resource_type": "document",
            "applies_to_roles": [],
            "expression": {"op": "in", "field": "status", "value": ["ready"]},
        },
        {
            "code": "AC03",
            "description": "Employees may not retrieve the HR-confidential collection.",
            "resource_type": "document",
            "applies_to_roles": ["employee", "analyst", "manager"],
            "expression": {"op": "ne", "field": "sensitivity", "value": "hr_confidential"},
        },
    ]


# ---------------------------------------------------------------------------

def _get_or_create_tenant(session: Session, slug: str, name: str) -> Tenant:
    bind_tenant(session, None)
    routing = session.get(TenantLookup, slug)
    if routing is not None:
        bind_tenant(session, routing.tenant_id)
        return session.get(Tenant, routing.tenant_id)
    tenant_id = uuid.uuid4()
    bind_tenant(session, tenant_id)
    tenant = Tenant(id=tenant_id, slug=slug, name=name)
    session.add(tenant)
    session.add(TenantLookup(slug=slug, tenant_id=tenant_id, is_active=True))
    session.flush()
    return tenant


def _get_or_create_user(session: Session, tenant: Tenant, spec: DemoUser) -> User:
    user = session.scalar(
        select(User).where(User.tenant_id == tenant.id, User.email == spec.email)
    )
    if user is None:
        user = User(
            id=uuid.uuid4(), tenant_id=tenant.id, email=spec.email,
            display_name=spec.display_name,
            password_hash=passwords.hash_password(DEMO_PASSWORD),
            attributes=spec.attributes,
        )
        session.add(user)
        session.flush()
        session.add(UserLookup(email_ci=spec.email, tenant_id=tenant.id, user_id=user.id))
    else:
        user.display_name = spec.display_name
        user.attributes = spec.attributes
        user.password_hash = passwords.hash_password(DEMO_PASSWORD)
    session.flush()

    roles = session.scalars(
        select(Role).where(Role.tenant_id == tenant.id, Role.name.in_(spec.roles))
    ).all()
    existing = {ur.role_id for ur in session.scalars(
        select(UserRole).where(UserRole.user_id == user.id)).all()}
    for role in roles:
        if role.id not in existing:
            session.add(UserRole(user_id=user.id, role_id=role.id, tenant_id=tenant.id))
    session.flush()
    session.refresh(user)
    return user


def _seed_policies(session: Session, tenant: Tenant, label: str) -> int:
    created = 0
    for spec in base_policies(label):
        row = session.scalar(select(Policy).where(Policy.tenant_id == tenant.id,
                                                  Policy.code == spec["code"]))
        if row is None:
            row = Policy(tenant_id=tenant.id, code=spec["code"])
            session.add(row)
            created += 1
        row.name = spec.get("name", "")
        row.description = spec.get("description", "")
        row.effect = spec["effect"]
        row.priority = spec.get("priority", 100)
        row.subject_roles = spec.get("subject_roles", [])
        row.subject_permissions = spec.get("subject_permissions", [])
        row.actions = spec.get("actions", [])
        row.resource_types = spec.get("resource_types", [])
        row.condition_json = spec.get("condition")
        row.obligations = spec.get("obligations", [])
        row.is_active = True
    session.flush()
    return created


def _seed_constraints(session: Session, tenant: Tenant) -> int:
    created = 0
    for spec in base_constraints():
        row = session.scalar(select(AccessConstraint).where(
            AccessConstraint.tenant_id == tenant.id,
            AccessConstraint.code == spec["code"]))
        if row is None:
            row = AccessConstraint(tenant_id=tenant.id, code=spec["code"])
            session.add(row)
            created += 1
        row.description = spec["description"]
        row.resource_type = spec["resource_type"]
        row.applies_to_roles = spec["applies_to_roles"]
        row.expression_json = spec["expression"]
        row.is_active = True
    session.flush()
    return created


def _seed_rules(session: Session, tenant: Tenant, specs: list[dict]) -> int:
    for spec in specs:
        knowledge_store.save_rule(
            session, tenant.id, code=spec["code"], body=spec["body"], head=spec["head"],
            name=spec.get("name", ""), description=spec.get("description", ""),
            priority=spec.get("priority", 100), confidence=spec.get("confidence", 1.0),
            origin="curated",
        )
    return len(specs)


def _seed_constraint_rules(session: Session, tenant: Tenant,
                           specs: list[dict]) -> int:
    for spec in specs:
        row = session.scalar(select(ConstraintRule).where(
            ConstraintRule.tenant_id == tenant.id, ConstraintRule.code == spec["code"]))
        if row is None:
            row = ConstraintRule(tenant_id=tenant.id, code=spec["code"])
            session.add(row)
        row.description = spec["description"]
        row.expression_json = spec["expression"]
        row.severity = spec.get("severity", "error")
        row.is_active = True
    session.flush()
    return len(specs)


def _seed_documents(session: Session, principal: Principal,
                    specs: list[dict]) -> tuple[int, int]:
    ingested = 0
    facts = 0
    for spec in specs:
        existing = session.scalar(select(Document).where(
            Document.tenant_id == principal.tenant_id, Document.title == spec["title"]))
        if existing is not None:
            continue
        report = ingest_text(
            session, principal, spec["text"], title=spec["title"],
            source_uri=f"demo://{spec['collection']}/{spec['title']}",
            options=IngestOptions(
                collection=spec["collection"], sensitivity=spec["sensitivity"],
                required_permission=spec.get("required_permission"),
                effective_date=spec.get("effective_date"),
                version_label=spec.get("version_label", ""),
                extract_facts=True, fact_subject="policy",
            ),
        )
        ingested += 1
        facts += report.facts
    return ingested, facts


def seed_tenant(session: Session, *, slug: str, name: str, users: list[DemoUser],
                documents: list[dict], rules: list[dict],
                constraint_rules: list[dict]) -> dict[str, Any]:
    tenant = _get_or_create_tenant(session, slug, name)
    provision_tenant_roles(session, tenant.id)
    _seed_policies(session, tenant, name)
    _seed_constraints(session, tenant)
    _seed_rules(session, tenant, rules)
    _seed_constraint_rules(session, tenant, constraint_rules)

    created_users = [_get_or_create_user(session, tenant, spec) for spec in users]
    admin = next((u for u in created_users if "admin" in
                  {r.name for r in u.roles}), created_users[0])
    principal = resolve_principal(session, admin)
    ingested, facts = _seed_documents(session, principal, documents)

    return {"tenant": slug, "tenant_id": str(tenant.id), "users": len(created_users),
            "documents": ingested, "facts": facts, "rules": len(rules)}


# The neuro-symbolic enterprise assistant's workspace. Its knowledge - policies, rules
# and the company graph - lives in the knowledge pack (backend/data/meridian), and these
# accounts are linked to graph entities by email.
MERIDIAN_SLUG = "meridian"
MERIDIAN_USERS = [
    DemoUser("priya@meridian.test", "Priya Nair", ["customer"], {"membership": "plus"}),
    DemoUser("sam@meridian.test", "Sam Mehta", ["customer"], {"membership": "standard"}),
    DemoUser("dev@meridian.test", "Dev Rao", ["employee"], {"grade": "L2"}),
    DemoUser("ana@meridian.test", "Ana Joseph", ["employee"], {"grade": "L1"}),
    DemoUser("support@meridian.test", "Kiran Das", ["support"], {}),
]


def seed_all() -> dict[str, Any]:
    ensure_permission_catalogue()
    summary: dict[str, Any] = {"tenants": []}
    for spec in (
        {"slug": MERIDIAN_SLUG, "name": "Meridian Marketplace", "users": MERIDIAN_USERS,
         "documents": [], "rules": [], "constraint_rules": []},
        {"slug": ACME_SLUG, "name": "Acme Corporation", "users": ACME_USERS,
         "documents": ACME_DOCUMENTS, "rules": ACME_RULES,
         "constraint_rules": ACME_CONSTRAINT_RULES},
        {"slug": GLOBEX_SLUG, "name": "Globex Industries", "users": GLOBEX_USERS,
         "documents": GLOBEX_DOCUMENTS, "rules": GLOBEX_RULES,
         "constraint_rules": []},
    ):
        session = SessionLocal()
        try:
            result = seed_tenant(session, **spec)
            session.commit()
            summary["tenants"].append(result)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    log_event(logger, 20, "demo data seeded", tenants=len(summary["tenants"]))
    return summary
