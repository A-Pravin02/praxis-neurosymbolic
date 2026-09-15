"""Knowledge administration and decision analytics for the Support Decision Copilot.

Everything here is scoped twice: to the tenant that serves the knowledge pack (other
workspaces get 404), and by permission - reading knowledge needs `rule:read`, changing it
`rule:write` or `fact:write`, analytics and traces `audit:read`. Every change is validated
against the whole pack before it is written, recorded in the audit trail, and reversible.
"""
from __future__ import annotations

import json
import statistics
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core import audit
from app.core.deps import CurrentPrincipal, DbSession, client_ip, require_permissions
from app.core.errors import NotFoundError, ValidationError
from app.db.models.chat import Message
from app.hybrid import knowledge_store as store
from app.hybrid import service as ns_service
from app.hybrid.knowledge_pack import default_pack_dir
from app.tenancy.permissions import (
    AUDIT_READ, FACT_READ, FACT_WRITE, POLICY_READ, ROLE_TEMPLATES, RULE_READ, RULE_WRITE,
)

router = APIRouter(prefix="/copilot", tags=["copilot"])
KNOWLEDGE_CHANGE = "knowledge.change"


def _pack_dir() -> Path:
    return default_pack_dir()


def _require_pack_tenant(db: DbSession, principal: CurrentPrincipal) -> None:
    if not ns_service.serves_tenant(db, principal):
        raise NotFoundError("This workspace has no knowledge pack.")


pack_tenant = Depends(_require_pack_tenant)


# ---------------------------------------------------------------------------
# bodies
# ---------------------------------------------------------------------------

class FileChange(BaseModel):
    content: str = Field(max_length=400_000)
    note: str = Field(default="", max_length=500)


class ValidateRequest(BaseModel):
    folder: str = Field(max_length=20)
    name: str = Field(max_length=90)
    content: str | None = Field(default=None, max_length=400_000)


class EntityChange(BaseModel):
    id: str = Field(max_length=64)
    type: str = Field(max_length=40)
    name: str | None = Field(default=None, max_length=200)
    attributes: dict[str, Any] = Field(default_factory=dict)


class RuleTest(BaseModel):
    goal: str = Field(max_length=80)
    subject: str | None = Field(default=None, max_length=64)
    subject_type: str | None = Field(default=None, max_length=40)
    facts: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------

@router.get("/overview", dependencies=[Depends(require_permissions(RULE_READ)), pack_tenant])
def overview(db: DbSession, principal: CurrentPrincipal) -> dict[str, Any]:
    pack = ns_service._controller().pack
    graph, program = pack.graph, pack.program
    types = Counter(e.type for e in graph.entities.values())
    return {
        "company": graph.display_name("meridian"), "as_of": graph.as_of.isoformat(),
        "revision": store.revision(_pack_dir()),
        "counts": {"policies": len(pack.policies.documents()), "rules": len(program.rules),
                   "goals": len(program.goals), "entities": len(graph.entities),
                   "clauses": len(pack.policies.all_clauses)},
        "entity_types": dict(sorted(types.items())),
        "goals": [{"name": g.predicate, "subject": g.subject_type, "kind": g.kind,
                   "description": g.description} for g in program.goals.values()],
        "evaluation": _latest_evaluation(),
    }


# ---------------------------------------------------------------------------
# policies and rules
# ---------------------------------------------------------------------------

@router.get("/policies", dependencies=[Depends(require_permissions(RULE_READ)), pack_tenant])
def policies() -> dict[str, Any]:
    pack = ns_service._controller().pack
    return {"documents": pack.policies.documents(), "files": store.list_files(_pack_dir())}


@router.get("/files/{folder}/{name}",
            dependencies=[Depends(require_permissions(RULE_READ)), pack_tenant])
def read_file(folder: str, name: str) -> dict[str, Any]:
    try:
        return {"folder": folder, "name": name,
                "content": store.read_file(_pack_dir(), folder, name)}
    except (store.KnowledgeChangeError, FileNotFoundError) as exc:
        raise NotFoundError(str(exc)) from exc


@router.post("/validate", dependencies=[Depends(require_permissions(RULE_READ)), pack_tenant])
def validate(payload: ValidateRequest) -> dict[str, Any]:
    try:
        return store.validate(_pack_dir(), {(payload.folder, payload.name): payload.content}
                              ).as_dict()
    except store.KnowledgeChangeError as exc:
        return {"ok": False, "errors": [str(exc)], "summary": {}}


@router.put("/files/{folder}/{name}", dependencies=[pack_tenant])
def write_file(folder: str, name: str, payload: FileChange, request: Request, db: DbSession,
               principal: CurrentPrincipal) -> dict[str, Any]:
    needed = FACT_WRITE if folder == "knowledge" else RULE_WRITE
    require_permissions(needed)(principal)
    try:
        result = store.apply(_pack_dir(), {(folder, name): payload.content},
                             author=principal.email, note=payload.note)
    except store.KnowledgeChangeError as exc:
        audit.record(db, KNOWLEDGE_CHANGE, tenant_id=principal.tenant_id,
                     actor_user_id=principal.user_id, resource_type="knowledge_file",
                     resource_id=f"{folder}/{name}", outcome="rejected",
                     client_ip=client_ip(request), detail={"reason": str(exc)[:500]})
        db.commit()
        raise ValidationError(str(exc)) from exc
    audit.record(db, KNOWLEDGE_CHANGE, tenant_id=principal.tenant_id,
                 actor_user_id=principal.user_id, resource_type="knowledge_file",
                 resource_id=f"{folder}/{name}", client_ip=client_ip(request),
                 detail={"revision": result["revision"], "note": payload.note})
    return result


@router.get("/history", dependencies=[Depends(require_permissions(RULE_READ)), pack_tenant])
def history() -> list[dict[str, Any]]:
    return store.history(_pack_dir())


@router.post("/history/{snapshot_id}/restore",
             dependencies=[Depends(require_permissions(RULE_WRITE)), pack_tenant])
def restore(snapshot_id: str, request: Request, db: DbSession,
            principal: CurrentPrincipal) -> dict[str, Any]:
    try:
        result = store.restore(_pack_dir(), snapshot_id, author=principal.email)
    except store.KnowledgeChangeError as exc:
        raise ValidationError(str(exc)) from exc
    audit.record(db, KNOWLEDGE_CHANGE, tenant_id=principal.tenant_id,
                 actor_user_id=principal.user_id, resource_type="knowledge_snapshot",
                 resource_id=snapshot_id, client_ip=client_ip(request),
                 detail={"revision": result["revision"], "restore": True})
    return result


@router.get("/rules", dependencies=[Depends(require_permissions(RULE_READ)), pack_tenant])
def rules() -> dict[str, Any]:
    pack = ns_service._controller().pack
    program = pack.program
    return {
        "rules": [{"code": s.code, "name": s.rule.name, "clauses": s.clauses,
                   "concludes": s.rule.head.predicate, "priority": s.rule.priority,
                   "source": s.source, "because": s.because} for s in program.rules],
        "versions": [vars(v) for v in program.versions.values()],
        "files": store.list_files(_pack_dir())["rules"],
    }


@router.post("/rules/test", dependencies=[Depends(require_permissions(RULE_READ)), pack_tenant])
def test_rule(payload: RuleTest) -> dict[str, Any]:
    """Evaluate a goal for a record or a described case, without asking a question."""
    from app.symbolic.reasoner import Reasoner
    from app.symbolic.render import format_value

    pack = ns_service._controller().pack
    program, graph = pack.program, pack.graph
    goal = program.goals.get(payload.goal)
    if goal is None:
        raise ValidationError(f"unknown goal {payload.goal!r}")
    if payload.subject:
        if not graph.has_entity(payload.subject):
            raise ValidationError(f"no record {payload.subject!r}")
        subject, open_subjects = payload.subject, set()
    else:
        subject = f"case:{goal.subject_type}"
        open_subjects = {subject}
    stated = {}
    for name, value in payload.facts.items():
        if name not in program.inputs and name not in program.attributes:
            raise ValidationError(f"unknown fact {name!r}")
        stated[(subject, name)] = value
    reasoner = Reasoner(program, graph, stated=stated, open_subjects=open_subjects)
    result = reasoner.evaluate(subject, goal.predicate)
    value_type = program.value_type(goal.predicate)
    return {
        "goal": goal.predicate, "subject": subject, "status": result.status,
        "value": format_value(result.value, value_type, graph=graph, program=program),
        "label": program.label(goal.predicate, result.value),
        "decided_by": result.decided_by.spec.code if result.decided_by else None,
        "because": result.decided_by.because if result.decided_by else None,
        "missing": sorted({m.predicate for m in result.missing}),
        "policy_versions": list(reasoner.version_resolutions.values()),
        "outcomes": [o.as_dict(program, graph) for o in result.outcomes],
    }


# ---------------------------------------------------------------------------
# the knowledge graph
# ---------------------------------------------------------------------------

@router.get("/graph", dependencies=[Depends(require_permissions(FACT_READ)), pack_tenant])
def graph_entities(type: str | None = None) -> dict[str, Any]:  # noqa: A002 - query name
    pack = ns_service._controller().pack
    graph = pack.graph
    types = sorted({e.type for e in graph.entities.values()})
    chosen = [e for e in graph.entities.values() if type is None or e.type == type]
    return {
        "types": types,
        "entities": [{"id": e.id, "type": e.type, "name": e.name,
                      "attributes": {k: _shown(pack, k, v) for k, v in e.attributes.items()},
                      "relations": [{"predicate": p, "target": o, "direction": d}
                                    for d, p, o in graph.neighbours(e.id)]}
                     for e in chosen[:500]],
        "attributes": {p: {"subject": a.subject_type, "type": a.value_type}
                       for p, a in pack.program.attributes.items()},
    }


@router.post("/graph/entities",
             dependencies=[Depends(require_permissions(FACT_WRITE)), pack_tenant])
def upsert_entity(payload: EntityChange, request: Request, db: DbSession,
                  principal: CurrentPrincipal) -> dict[str, Any]:
    try:
        result = store.upsert_entity(_pack_dir(), entity_id=payload.id,
                                     entity_type=payload.type, name=payload.name,
                                     attributes=payload.attributes, author=principal.email)
    except store.KnowledgeChangeError as exc:
        raise ValidationError(str(exc)) from exc
    audit.record(db, KNOWLEDGE_CHANGE, tenant_id=principal.tenant_id,
                 actor_user_id=principal.user_id, resource_type="knowledge_entity",
                 resource_id=payload.id, client_ip=client_ip(request),
                 detail={"revision": result["revision"], "type": payload.type,
                         "attributes": sorted(payload.attributes)})
    return result


@router.get("/access", dependencies=[Depends(require_permissions(POLICY_READ)), pack_tenant])
def access() -> dict[str, Any]:
    program = ns_service._controller().pack.program
    return {"grants": [{"kind": g.kind, "scope": g.scope, "types": sorted(g.entity_types)}
                       for g in program.access],
            "roles": {name: sorted(perms) for name, perms in ROLE_TEMPLATES.items()}}


# ---------------------------------------------------------------------------
# analytics, gaps, review queue, traces
# ---------------------------------------------------------------------------

def _turns(db, principal, limit: int = 2000) -> list[dict[str, Any]]:
    """Assistant turns with the question that prompted each, newest first."""
    rows = db.execute(
        select(Message.id, Message.conversation_id, Message.sequence, Message.role,
               Message.content, Message.trace_json, Message.latency_ms, Message.created_at)
        .where(Message.tenant_id == principal.tenant_id)
        .order_by(Message.created_at.desc()).limit(limit * 2)).all()
    questions = {(r.conversation_id, r.sequence): r.content for r in rows if r.role == "user"}
    turns = []
    for r in rows:
        ns = ((r.trace_json or {}).get("neurosymbolic") or {}) if r.role == "assistant" else {}
        if not ns:
            continue
        turns.append({
            "id": str(r.id), "at": r.created_at.isoformat() if r.created_at else None,
            "question": questions.get((r.conversation_id, r.sequence - 1), ""),
            "answer": r.content[:400], "latency_ms": r.latency_ms, "ns": ns})
    return turns[:limit]


@router.get("/analytics", dependencies=[Depends(require_permissions(AUDIT_READ)), pack_tenant])
def analytics(db: DbSession, principal: CurrentPrincipal) -> dict[str, Any]:
    turns = _turns(db, principal)
    total = len(turns)

    def share(predicate) -> float | None:
        return round(sum(1 for t in turns if predicate(t["ns"])) / total, 3) if total else None

    decided = [t for t in turns if t["ns"].get("outcome") in ("answered", "conditional")]
    latencies = [t["latency_ms"] for t in turns if t["latency_ms"] is not None]
    return {
        "measured_from": "assistant turns recorded in this workspace",
        "turns": total,
        "outcomes": dict(Counter(t["ns"].get("outcome") for t in turns)),
        "pipelines": dict(Counter(t["ns"].get("pipeline_label") for t in turns)),
        "verified_rate": round(sum(1 for t in decided if (t["ns"].get("verification") or {})
                                   .get("passed")) / len(decided), 3) if decided else None,
        "withheld_rate": share(lambda n: n.get("outcome") == "needs_review"),
        "clarification_rate": share(lambda n: n.get("outcome") == "clarification"),
        "not_covered_rate": share(lambda n: n.get("outcome") == "insufficient"),
        "approval_escalation_rate": share(lambda n: any(
            f.get("goal") == "approval_required" for f in (n.get("trace") or {})
            .get("followups", [])) or n.get("goal") == "approval_required"
            and n.get("decision_label") == "Yes"),
        "claims_rejected": sum(1 for t in turns for c in (t["ns"].get("claims") or [])
                               if not c.get("confirmed"))
        + sum(1 for t in turns for r in ((t["ns"].get("trace") or {}).get("grounding") or {})
              .get("rejected_statements", []) if r.get("kind") == "unverified claim"),
        "median_latency_ms": statistics.median(latencies) if latencies else None,
        "mean_confidence": round(statistics.mean(t["ns"].get("confidence") or 0 for t in turns),
                                 3) if turns else None,
        "evaluation": _latest_evaluation(),
    }


@router.get("/gaps", dependencies=[Depends(require_permissions(AUDIT_READ)), pack_tenant])
def gaps(db: DbSession, principal: CurrentPrincipal) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for turn in _turns(db, principal):
        gap = turn["ns"].get("knowledge_gap")
        if not gap or gap.get("type") == "unclear_request":
            continue
        key = (gap.get("type", ""), gap.get("summary", ""))
        entry = grouped.setdefault(key, {**gap, "count": 0, "examples": [],
                                         "last_seen": turn["at"]})
        entry["count"] += 1
        if len(entry["examples"]) < 3 and turn["question"]:
            entry["examples"].append({"question": turn["question"], "message_id": turn["id"]})
    return sorted(grouped.values(), key=lambda g: -g["count"])


@router.get("/review", dependencies=[Depends(require_permissions(AUDIT_READ)), pack_tenant])
def review(db: DbSession, principal: CurrentPrincipal) -> list[dict[str, Any]]:
    """Turns a person should look at: withheld, not covered, unclear, or unverified."""
    queue = []
    for turn in _turns(db, principal, limit=500):
        ns = turn["ns"]
        reasons = []
        if ns.get("outcome") == "needs_review":
            reasons.append("withheld: failed verification")
        if ns.get("outcome") == "insufficient":
            reasons.append("not covered by company knowledge")
        if ns.get("outcome") == "clarification":
            reasons.append("needed clarification")
        verification = ns.get("verification") or {}
        if verification.get("passed") is False and ns.get("outcome") != "needs_review":
            reasons.append("verification failed")
        if (ns.get("confidence") or 0) < 0.6 and ns.get("outcome") in ("answered", "conditional"):
            reasons.append("low confidence")
        if reasons:
            queue.append({"message_id": turn["id"], "at": turn["at"],
                          "question": turn["question"], "answer": turn["answer"],
                          "outcome": ns.get("outcome"), "pipeline": ns.get("pipeline_label"),
                          "decision": ns.get("decision_label"), "reasons": reasons,
                          "gap": (ns.get("knowledge_gap") or {}).get("summary")})
    return queue[:200]


@router.get("/traces/{message_id}",
            dependencies=[Depends(require_permissions(AUDIT_READ)), pack_tenant])
def trace(message_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal) -> dict[str, Any]:
    row = db.scalar(select(Message).where(Message.tenant_id == principal.tenant_id,
                                          Message.id == message_id))
    if row is None or not (row.trace_json or {}).get("neurosymbolic"):
        raise NotFoundError("No reasoning trace for that message.")
    return {"message_id": str(row.id), "answer": row.content,
            "neurosymbolic": row.trace_json["neurosymbolic"]}


@router.get("/evaluation", dependencies=[Depends(require_permissions(AUDIT_READ)), pack_tenant])
def evaluation() -> dict[str, Any]:
    return {"runs": _evaluation_runs()}


def _evaluation_runs() -> list[dict[str, Any]]:
    folder = _pack_dir() / "benchmark" / "results"
    runs = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        runs.append({
            "file": path.name, "split": data.get("split"), "cases": data.get("cases"),
            "llm_model": data.get("llm_model"), "label": data.get("label", path.stem),
            "frozen": data.get("frozen"), "note": data.get("note"),
            "systems": {name: s["overall"] for name, s in data.get("systems", {}).items()},
            "by_category": {name: {c: v.get("final_answer_accuracy")
                                   for c, v in s.get("by_category", {}).items()}
                            for name, s in data.get("systems", {}).items()},
            "not_run": data.get("not_run", {}),
        })
    return runs


def _latest_evaluation() -> dict[str, Any] | None:
    runs = [r for r in _evaluation_runs() if r.get("split") == "copilot"]
    return runs[-1] if runs else None


def _shown(pack, predicate: str, value: Any) -> Any:
    from app.symbolic.render import format_value

    value_type = pack.program.value_type(predicate)
    return format_value(value, value_type, graph=pack.graph) if value_type == "date" else value
