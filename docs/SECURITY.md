# Security

## Threat model

The platform is multi-tenant, ingests untrusted documents, and puts a language
model in the middle. That combination produces the threats below. Each one has a
control and a permanent regression test in `backend/tests/test_security.py`.

| # | Threat | Control | Test |
|---|---|---|---|
| 1 | Tenant escape (A reads B's data) | RLS + query predicate + PDP, layered | `test_cross_tenant_*`, `test_tenant_sees_only_its_own_rows` |
| 2 | IDOR by guessing ids | Ownership check returns **404**, never 403 | `test_conversation_idor_returns_404_not_403` |
| 3 | Privilege escalation | Permissions come only from the verified token; body fields ignored | `test_claimed_permissions_in_the_request_body_are_ignored` |
| 4 | Direct prompt injection | Structural separation; the gate is code, not a prompt | `test_injected_document_cannot_disable_abstention` |
| 5 | Indirect prompt injection | Escaped, fenced, quarantined; never in the system role | `test_indirect_injection_does_not_change_behaviour` |
| 6 | Malicious documents | Injection screening at ingest; quarantined spans cannot become facts | `test_quarantined_passage_never_becomes_a_fact` |
| 7 | RAG poisoning | Quarantined spans down-weighted 65% in rerank, capped as support | `test_reranker_demotes_quarantined_spans` |
| 8 | Unauthorized retrieval | `required_permission` + access constraints inside the SQL query | `test_search_endpoint_respects_document_permissions` |
| 9 | SQL injection | No user string ever reaches SQL text; constraints compile to bound params | `test_search_query_cannot_inject_sql` |
| 10 | Token theft | 15-minute access tokens; refresh rotates; `typ` claim checked | `test_refresh_token_cannot_be_used_as_access_token` |
| 11 | Refresh replay | Family rotation with reuse detection: replay burns the family | `test_replay_revokes_the_whole_family` |
| 12 | Account enumeration | Identical response and timing for unknown email and wrong password | `test_login_response_identical_for_unknown_email_and_wrong_password` |
| 13 | Data leakage in errors | Typed errors; no internals, no submitted values | `test_error_responses_do_not_leak_internals` |
| 14 | Memory leakage across users | History is per conversation, per user, RLS-scoped | `test_conversation_history_does_not_cross_users` |
| 15 | Policy bypass | Deny-overrides PDP, default deny, tenant boundary not policy-controlled | `test_tenant_boundary_is_not_overridable_by_policy` |
| 16 | Tool abuse | There are no model-invoked tools; the model only *presents* | architecture |
| 17 | Symbolic rule injection | Rules are validated JSON, never code; `rule:write` required | `test_rule_body_cannot_carry_executable_content` |
| 18 | Accidental RLS bypass | Tests assert the app role is not owner/superuser/BYPASSRLS | `test_application_role_is_not_a_superuser_and_cannot_bypass_rls` |
| 19 | Unsupported facts presented as data | Evidence gate + post-generation grounding check | `test_invented_number_is_caught` |
| 20 | Silently ignored contradictions | Conflicts block or are disclosed; never dropped | `test_5_unresolved_contradiction_is_reported` |

## Tenant isolation: four independent layers

1. **The token.** `tenant_id` comes from the signed access token. A header, a body
   field or a URL segment claiming a tenant is ignored.
2. **PostgreSQL RLS.** Every tenant-scoped table has `ENABLE` *and* `FORCE ROW
   LEVEL SECURITY` with a policy of
   `tenant_id = current_setting('app.tenant_id', true)::uuid`, applied to `USING`
   and `WITH CHECK` so reads and writes are both constrained. `FORCE` means the
   policy binds the table owner too.
3. **The query.** Repository queries carry an explicit `tenant_id ==` predicate.
   If RLS were ever misconfigured, the query still filters.
4. **The PDP.** Resource access is decided before data is returned, and the
   tenant boundary is checked in code *before* any policy row is consulted, so a
   malicious or mistaken policy cannot open a cross-tenant path.

The application connects as `praxis_app`: `NOSUPERUSER`, `NOBYPASSRLS`,
`NOCREATEDB`, `NOCREATEROLE`, not the owner of any table, no `CREATE` on the
schema, and no write access to the global `permissions` catalogue. Migrations run
as `praxis_owner`, which is also not a superuser.

### Two deliberate exceptions

`user_lookup` and `tenant_lookup` are **not** row-level secured, because login
and workspace joining happen before any tenant is known. The alternative —
granting the application `BYPASSRLS` or adding a `SECURITY DEFINER` escape hatch
— would create a permanent hole. Instead these tables hold the bare minimum:
a lowercased email or a slug, and the ids it maps to. No credentials, no names,
no profile data, no tenant content. See ADR-018.

`role_permissions` has no `tenant_id` (it links tenant-scoped roles to global
permission rows). The roles themselves are RLS-protected, so the worst an
attacker with database access could learn from it is which permission ids attach
to a role id they cannot resolve.

## Authentication

- **Argon2id**, 64 MiB memory cost, 2 iterations, 2 lanes. Rehash on login when
  parameters change.
- **Access tokens**: HS256 JWT, 15 minutes, carrying `sub`, `tid`, `roles`,
  `perms`, `jti`, `typ`. `typ` is verified, so a refresh token can never
  authenticate a request. `JWT_SECRET_PREVIOUS` allows key rotation without
  logging everyone out.
- **Refresh tokens**: opaque 48-byte secrets, stored only as SHA-256 hashes,
  grouped into a *family*. Every use consumes the token and issues a successor.
  Presenting a consumed or revoked token revokes the entire family and writes an
  audit event — the OAuth 2.1 replay countermeasure.
- **Lockout**: 8 consecutive failures locks the account for 15 minutes, and a
  locked account returns exactly the same error as a wrong password.
- **Rate limiting**: sliding window per IP *and* per email on login, so rotating
  IPs does not defeat it.

### What we deliberately do not do

- Registration returns 409 for a duplicate email. Login is the enumeration
  surface that matters and it is silent; making registration silent too would
  mean accepting a signup that cannot succeed.
- Tokens are not stored in `localStorage`. `sessionStorage` limits the blast
  radius to one tab session; combined with 15-minute access tokens and rotation,
  a stolen copy is worth one request before the family burns.

## Prompt injection

The primary defence is **structural**, not persuasive:

```
SYSTEM INSTRUCTIONS   authored by us, system role only
USER INSTRUCTIONS     the conversation, user/assistant roles
RETRIEVED DATA        escaped, envelope-fenced, user role, announced as untrusted
SYMBOLIC FACTS        deterministic values, labelled as verified
```

Retrieved text is never placed in the system role and never concatenated into an
instruction sentence. Before it goes anywhere near a prompt, `neutralise()`
strips control characters and defuses forged envelope delimiters and chat
template markers (`</untrusted_document>`, `<|im_start|>`, `[INST]`).

A pattern detector scores each chunk at ingest time. Above 0.6 the chunk is
**quarantined**: it stays searchable and visible as evidence — hiding it would be
worse — but it is demoted 65% in reranking, capped at 40% support weight, can
never be the sole support for a claim, is never extracted into facts, and is
never quoted as an answer. An audit event records the detection.

Most importantly: **the model cannot change the outcome even if it is fooled.**
The gate that decides whether to answer, hedge or abstain is ordinary code
reading measured quantities. A document that says "never abstain" cannot make the
system answer, because nothing in the abstention path consults the model.

## Access constraints

The constraint language is a small JSON expression grammar over a whitelisted set
of fields per resource type. It is parsed to an AST, then compiled to SQLAlchemy
expressions with bound parameters. There is no `eval`, no string interpolation
into SQL, and no way to name a field that is not in the whitelist (`password_hash`
is not, and cannot be). Depth and node count are capped. `LIKE` patterns escape
`%`, `_` and `\`.

A constraint that fails to compile **fails closed**: the query is reduced to
"no rows" rather than silently dropping the restriction.

## Logging and audit

Structured JSON logs carry request id, user, tenant and latency. A redaction
filter removes password fields, `Authorization` headers and anything shaped like
a JWT, even if a caller passes them by mistake. Document bodies are never logged.

The audit trail is append-only and covers: registration, login success/failure,
refresh rotation, replay detection, logout, policy denials, document upload and
delete, rule writes, injection detection, abstention, withheld answers and
evaluation runs.

## Known gaps

Stated plainly, because a security document that claims completeness is not
credible:

- **Rate limiting is in-process.** Multiple API replicas each keep their own
  window. Move it to Redis before scaling out.
- **No CSRF token.** Not needed today because auth is a bearer header and never a
  cookie. If cookie auth is ever added, CSRF protection must be added with it.
- **Refresh tokens are bearer tokens.** They are not bound to a device or a DPoP
  key. Rotation plus replay detection limits, but does not eliminate, the value
  of a stolen token.
- **The injection detector is pattern-based.** It will miss novel phrasings. It
  is a signal, not the control — the structural separation is the control.
- **No file scanning.** Uploaded documents are parsed, not scanned for malware.
  The parsers are memory-safe Python libraries and the content is never executed,
  but a malicious PDF could still target a downstream consumer.
- **`X-Forwarded-For` is not trusted** by default, so behind a proxy the rate
  limiter sees the proxy's IP until a trusted-proxy configuration is added.
- **Secrets come from the environment.** There is no integration with a secret
  manager, and no key rotation schedule beyond the manual `JWT_SECRET_PREVIOUS`
  mechanism.
- **The default `JWT_SECRET` in `.env.example` is a placeholder.** Deploying
  without replacing it is a critical misconfiguration; nothing in the code stops
  you.
