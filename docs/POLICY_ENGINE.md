# Policy engine

## Model

```
Subject     the authenticated Principal: user, tenant, roles, permissions, attributes
Action      READ | WRITE | DELETE | ...
Resource    a type, an id, a tenant, and attributes
Context     subject.* and env.* values a condition may reference
Policy      (roles|permissions) x actions x resource types x condition -> effect
Constraint  a row-level predicate, compiled into the query
Decision    ALLOW | DENY | INDETERMINATE, with the policy that decided and why
```

## Combining algorithm: deny-overrides, default deny

1. **Tenant boundary.** If the resource belongs to another tenant, DENY. This is
   checked in code before any policy row is read, so a misconfigured or hostile
   policy cannot open a cross-tenant path.
2. **Any matching DENY wins**, regardless of priority.
3. Otherwise the **highest-priority matching ALLOW** wins.
4. A condition that cannot be evaluated — a missing context value, a malformed
   expression — yields **INDETERMINATE**, which callers treat as a denial.
5. Nothing matched: **DENY**.

Every decision carries its evaluation trail: which policies were considered, what
their conditions rendered to, and which one decided. That is what makes an
authorization answer explainable to a user and auditable afterwards.

## Access constraints

A small JSON expression language over a whitelist of fields per resource type:

```json
{"op": "and", "args": [
  {"op": "eq", "field": "tenant_id", "ref": "subject.tenant_id"},
  {"op": "ne", "field": "sensitivity", "value": "hr_confidential"}
]}
```

```
expr := {"op": "and"|"or", "args": [expr, ...]}
      | {"op": "not", "args": [expr]}
      | {"op": <cmp>, "field": <whitelisted>, "value": <scalar>}
      | {"op": <cmp>, "field": <whitelisted>, "ref": "subject.x" | "env.x"}
      | {"op": "is_null"|"not_null", "field": <whitelisted>}
      | {"op": "true"} | {"op": "false"}

cmp := eq | ne | lt | lte | gt | gte | in | not_in | contains | startswith | endswith
```

### Compilation

```
JSON -> validate (fields, operators, depth, node count, literal types)
     -> AST
     -> SQLAlchemy expression with bound parameters
```

There is no `eval`, no string interpolation into SQL, and no way to name a field
outside the per-resource whitelist — `password_hash` is in no whitelist and
cannot be reached. `LIKE` patterns escape `%`, `_` and backslash. Depth is capped
at 8 and node count at 120.

The same AST also evaluates in memory against a single resource, which is how the
PDP checks a condition without issuing a query.

### Failing closed

A constraint that fails to compile is not skipped: the query is reduced to
`false`, returning nothing, and the failure is logged. A broken restriction must
never become an absent restriction.

### Ordering against NULL

`amount >= 10` where `amount` is NULL is *unknown*, and unknown must not read as
permitted. In-memory evaluation returns `False`; in SQL, three-valued logic
excludes the row from a positive predicate. Both fail closed.

## Seeded policy set

| Code | Effect | Priority | Meaning |
|---|---|---|---|
| `P900` | DENY | 1000 | Anything outside the caller's tenant |
| `P020` | DENY | 900 | An HR-confidential document that is not correctly labelled |
| `P040` | ALLOW | 400 | `document:read_hr` may read HR-confidential documents |
| `P030` | ALLOW | 300 | `salary:read` may read salary records |
| `P050` | ALLOW | 200 | Owners may read their own conversations |
| `P060` | ALLOW | 200 | `rule:write` may read and write rules |
| `P010` | ALLOW | 100 | `document:read` may read general documents in-tenant |

Access constraints applied to retrieval:

| Code | Applies to | Predicate |
|---|---|---|
| `AC01` | everyone | `tenant_id == subject.tenant_id` |
| `AC02` | everyone | `status IN ('ready')` |
| `AC03` | employee, analyst, manager | `sensitivity != 'hr_confidential'` |

## Roles

| Role | Permissions |
|---|---|
| `employee` | chat, own conversations, read documents, read facts and rules |
| `manager` | employee + upload documents, read users |
| `hr` | manager-level + read HR-confidential documents, write facts, read salary |
| `analyst` | manager-level + run the evaluation harness |
| `admin` | everything, including policy and rule authoring and the audit trail |

Permissions live in a global catalogue the application role cannot write to, so a
role can only be granted a permission that was explicitly declared.

## Two layers, deliberately

Route guards (`require_permissions`) are coarse and cheap: they reject a caller
who could not possibly be allowed, before any work happens. The PDP is
fine-grained and explainable, and decides per resource. A route guard is not a
substitute for the PDP, and the PDP is not a substitute for row level security.

## Limits

- Policies are evaluated per request with no caching; on a large policy set this
  becomes the hot path.
- There is no policy simulator beyond `POST /api/policies/evaluate`, which does
  let an administrator ask what the PDP would decide for another user without
  granting that user anything.
- Obligations are carried in the decision but nothing consumes them yet.
- No delegation, no time-bounded grants, no break-glass path.
