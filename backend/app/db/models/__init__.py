from app.db.base import Base
from app.db.models.chat import Conversation, Message
from app.db.models.evaluation import EvalCaseResult, EvalRun
from app.db.models.knowledge import Answer, Claim, Entity, Fact, ProvenanceRecord
from app.db.models.policy import AccessConstraint, Policy
from app.db.models.rag import Chunk, Document
from app.db.models.symbolic import ConstraintRule, Rule
from app.db.models.tenancy import (
    AuditEvent,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    Tenant,
    TenantLookup,
    User,
    UserLookup,
    UserRole,
)

__all__ = [
    "Base", "Tenant", "User", "Role", "Permission", "UserRole", "RolePermission",
    "RefreshToken", "AuditEvent", "UserLookup", "TenantLookup", "Conversation", "Message", "Document", "Chunk",
    "Entity", "Fact", "Claim", "Answer", "ProvenanceRecord", "Rule", "ConstraintRule",
    "Policy", "AccessConstraint", "EvalRun", "EvalCaseResult",
]

TENANT_SCOPED_TABLES = [
    "users", "roles", "user_roles", "conversations", "messages", "documents", "chunks",
    "entities", "facts", "claims", "answers", "provenance_records", "rules",
    "constraint_rules", "policies", "access_constraints", "eval_runs", "eval_case_results",
    "refresh_tokens",
]
