# API

Base path `/api`. Interactive documentation at `/api/docs` (disabled when
`ENVIRONMENT=production`), OpenAPI at `/api/openapi.json`.

Authentication is `Authorization: Bearer <access_token>`. The tenant is taken
from the token and from nowhere else — a header, body field or path segment
claiming a tenant is ignored.

## Errors

```json
{"error": {"code": "not_found", "message": "The requested resource was not found."}}
```

| Status | Code | Meaning |
|---|---|---|
| 401 | `invalid_credentials` | Wrong email *or* wrong password — deliberately indistinguishable |
| 401 | `invalid_token` | Missing, malformed, expired or wrong-type token |
| 403 | `forbidden` | Authenticated but not permitted |
| 404 | `not_found` | Absent **or** not yours; existence is never disclosed |
| 409 | `conflict` | Duplicate email |
| 413 | `payload_too_large` | Upload over the limit |
| 415 | `unsupported_media_type` | File type not supported |
| 422 | `validation_error` | Invalid payload; field *names* only, never values |
| 429 | `rate_limited` | Too many requests |
| 400 | `invalid_rule` / `invalid_constraint` | Rule or constraint failed validation |
| 502 | `provider_error` | Upstream model provider failed |

## Auth

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/auth/register` | — | Creates a workspace, or joins one with `tenant_slug` |
| POST | `/auth/login` | — | Rate limited per IP and per email |
| POST | `/auth/refresh` | — | Rotates; replay revokes the family |
| POST | `/auth/logout` | authenticated | Revokes the presented token's family |
| GET | `/auth/me` | authenticated | Resolved roles and permissions |
| GET | `/auth/sessions` | authenticated | Active and revoked refresh sessions |
| POST | `/auth/sessions/revoke-all` | authenticated | Sign out everywhere |

```bash
curl -X POST localhost:8000/api/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"alice@acme.test","password":"Praxis-Demo-2026!"}'
```

## Chat

| Method | Path | Permission |
|---|---|---|
| POST | `/chat` | `chat:use` |
| POST | `/chat/stream` | `chat:use` |
| GET/POST | `/conversations` | `conversation:read` / `conversation:write` |
| GET/PATCH/DELETE | `/conversations/{id}` | as above |
| GET | `/conversations/{id}/messages` | `conversation:read` |

```json
{
  "message": "How many annual leave days am I entitled to?",
  "conversation_id": null,
  "pipeline_mode": "neurosymbolic"
}
```

`pipeline_mode` is one of `llm_only`, `llm_rag`, `llm_rag_verify`,
`neurosymbolic` — the same ablations the benchmark uses.

The response is the full audit envelope:

```jsonc
{
  "answer": "Your annual leave entitlement is 20 days. ...",
  "route": "RAG_PLUS_SYMBOLIC",
  "gate_decision": "ANSWER",          // ANSWER | ANSWER_WITH_CAVEAT
                                      // REPORT_CONFLICT | ABSTAIN | DENY
  "verification_status": "PARTIALLY_VERIFIED",
  "confidence": 0.74,
  "citations": [{"index": 1, "document_title": "...", "snippet": "...",
                 "score": 0.62, "quarantined": false}],
  "claims": [{"text": "...", "kind": "INFERRED", "status": "verified",
              "support_score": 0.9, "citation_indexes": [1]}],
  "reasoning": {
    "conclusion": "self.annual_leave_days = 20 days",
    "facts": [{"subject": "self", "predicate": "years_of_service",
               "kind": "DIRECT", "provenance": {"source_kind": "user_attribute"}}],
    "inferences": [{"step": 1, "rule_code": "R12",
                    "sentence": "Your annual leave entitlement is 20 days",
                    "premises": ["self.years_of_service = 4"]}],
    "contradictions": [{"predicate": "annual_leave_days", "values": [20, 18],
                        "resolved": true, "preferred_value": 20,
                        "superseded_values": [18], "blocking": false}]
  },
  "policy_decision": null,
  "warnings": ["..."],
  "stages": [{"name": "retrieve", "ms": 17}],
  "latency_ms": 74
}
```

### Streaming

`POST /chat/stream` returns `text/event-stream`:

```
event: start
data: {"conversation_id": "...", "route": "...", "gate_decision": "..."}

event: delta
data: {"text": "Your annual leave "}

event: done
data: { ...the full envelope above... }
```

The pipeline runs to completion **before** the first token. Streaming a
partially-verified answer would defeat the gate, so what streams is already
gate-approved text, chunked for the reader's benefit. An `error` event is
terminal.

## Documents

| Method | Path | Permission |
|---|---|---|
| GET | `/documents` | `document:read` |
| POST | `/documents/text` | `document:write` |
| POST | `/documents/upload` | `document:write` (multipart) |
| DELETE | `/documents/{id}` | `document:delete` |
| POST | `/documents/search` | `document:read` |

Uploads accept `collection`, `sensitivity`, `required_permission`,
`effective_date`, `version_label`, `title`. `sensitivity` must be one of
`public`, `internal`, `confidential`, `hr_confidential`, and you may only set a
`required_permission` you hold yourself.

## Knowledge, rules and policy

| Method | Path | Permission |
|---|---|---|
| GET | `/facts` | `fact:read` |
| POST | `/facts` | `fact:write` |
| GET | `/rules` | `rule:read` |
| POST | `/rules` | `rule:write` |
| DELETE | `/rules/{code}` | `rule:write` (deactivates) |
| GET | `/policies` | `policy:read` |
| POST | `/policies/evaluate` | authenticated (`user:read` to ask about another user) |
| POST | `/policies/constraints/validate` | `policy:read` |
| GET | `/audit` | `audit:read` |
| POST | `/evaluation/run` | `eval:run` |

Creating a rule compiles it first and then re-validates the whole rule set for
stratification, so an invalid or unstratifiable rule never reaches the database.

## System

`GET /health` — no auth; reports database, LLM provider and embedding provider.

Every response carries `X-Request-ID` (echoing the request's, if supplied),
plus `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and
`Cache-Control: no-store`.

## Rate limits

| Bucket | Default |
|---|---|
| Login (per IP and per email) | 10/min |
| Register (per IP) | 5/min |
| Chat (per user) | 60/min |
| Everything else | 240/min |

In-process, so each API replica keeps its own window (see the gaps section of
`docs/SECURITY.md`).
