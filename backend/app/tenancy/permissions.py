"""The permission catalogue. Permissions are verbs; roles are bundles of verbs."""
from __future__ import annotations

# Chat / conversations
CHAT_USE = "chat:use"
CONVERSATION_READ = "conversation:read"
CONVERSATION_WRITE = "conversation:write"

# Documents & retrieval
DOCUMENT_READ = "document:read"
DOCUMENT_WRITE = "document:write"
DOCUMENT_DELETE = "document:delete"
DOCUMENT_READ_HR = "document:read_hr"          # HR-confidential corpus
DOCUMENT_READ_FINANCE = "document:read_finance"

# Knowledge & reasoning
FACT_READ = "fact:read"
FACT_WRITE = "fact:write"
RULE_READ = "rule:read"
RULE_WRITE = "rule:write"

# Policy administration
POLICY_READ = "policy:read"
POLICY_WRITE = "policy:write"

# Sensitive records used by the PDP demo
SALARY_READ = "salary:read"

# Administration
USER_READ = "user:read"
USER_WRITE = "user:write"
AUDIT_READ = "audit:read"
EVAL_RUN = "eval:run"

ALL_PERMISSIONS: dict[str, str] = {
    CHAT_USE: "Use the assistant",
    CONVERSATION_READ: "Read own conversations",
    CONVERSATION_WRITE: "Create and modify own conversations",
    DOCUMENT_READ: "Read general documents",
    DOCUMENT_WRITE: "Upload and ingest documents",
    DOCUMENT_DELETE: "Delete documents",
    DOCUMENT_READ_HR: "Read HR-confidential documents",
    DOCUMENT_READ_FINANCE: "Read finance-confidential documents",
    FACT_READ: "Read structured facts",
    FACT_WRITE: "Create or modify structured facts",
    RULE_READ: "Read symbolic rules",
    RULE_WRITE: "Create or modify symbolic rules",
    POLICY_READ: "Read authorization policies",
    POLICY_WRITE: "Create or modify authorization policies",
    SALARY_READ: "Read salary records",
    USER_READ: "Read users in the tenant",
    USER_WRITE: "Create or modify users in the tenant",
    AUDIT_READ: "Read the audit trail",
    EVAL_RUN: "Run the evaluation harness",
}

# Role name -> permissions. Seeded per tenant.
ROLE_TEMPLATES: dict[str, list[str]] = {
    "employee": [
        CHAT_USE, CONVERSATION_READ, CONVERSATION_WRITE, DOCUMENT_READ,
        FACT_READ, RULE_READ,
    ],
    "manager": [
        CHAT_USE, CONVERSATION_READ, CONVERSATION_WRITE, DOCUMENT_READ, DOCUMENT_WRITE,
        FACT_READ, RULE_READ, USER_READ,
    ],
    "hr": [
        CHAT_USE, CONVERSATION_READ, CONVERSATION_WRITE, DOCUMENT_READ, DOCUMENT_WRITE,
        DOCUMENT_READ_HR, FACT_READ, FACT_WRITE, RULE_READ, SALARY_READ, USER_READ,
    ],
    "analyst": [
        CHAT_USE, CONVERSATION_READ, CONVERSATION_WRITE, DOCUMENT_READ, DOCUMENT_WRITE,
        FACT_READ, RULE_READ, EVAL_RUN,
    ],
    "admin": sorted(ALL_PERMISSIONS.keys()),
}

DEFAULT_ROLE = "employee"
