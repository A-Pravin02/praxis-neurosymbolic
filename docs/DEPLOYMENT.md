# Deployment

## What `docker compose up` gives you

| Service | Image | Port | Role |
|---|---|---|---|
| `db` | `pgvector/pgvector:pg16` | 5432 | PostgreSQL 16 with pgvector and pg_trgm |
| `backend` | built from `backend/` | 8000 | FastAPI, migrations, seeding |
| `frontend` | built from `frontend/` | 8080 | nginx serving the SPA and proxying `/api` |

On first boot `docker/postgres/init.sql` runs as the superuser and creates the
`praxis_owner` and `praxis_app` roles, the `praxis` and `praxis_test` databases,
and the extensions. Both roles are `NOSUPERUSER` and `NOBYPASSRLS`; the
application role owns nothing.

The backend entrypoint waits for the database, applies migrations as
`praxis_owner`, seeds the demo workspaces when `SEED_ON_START=true`, provisions
the permission catalogue, and then execs uvicorn as an unprivileged user.

## Before deploying anywhere real

This is an MVP. The checklist below is the minimum, not a production readiness
certificate.

### Must do

1. **Generate a real `JWT_SECRET`.**
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
   The value in `.env.example` is a placeholder and nothing in the code stops you
   deploying with it.
2. **Change every database password.** `praxis_app_pw` and `praxis_owner_pw` are
   in a file in this repository.
3. **Set `ENVIRONMENT=production`.** This disables `/api/docs` and
   `/api/openapi.json`.
4. **Set `SEED_ON_START=false`.** Demo accounts with a published password must
   not exist in a real deployment.
5. **Terminate TLS in front of the frontend** and add HSTS. Nothing here serves
   HTTPS.
6. **Set `CORS_ORIGINS`** to your actual origin. The default includes localhost.

### Should do

7. **Move rate limiting to Redis** if you run more than one API replica; the
   in-process limiter gives each replica its own window.
8. **Configure a trusted proxy** so `X-Forwarded-For` can be honoured, otherwise
   the rate limiter and the audit trail see the proxy's IP.
9. **Ship logs somewhere.** They are structured JSON on stdout with request id,
   user, tenant and latency, ready for aggregation.
10. **Back up the database.** It holds conversations, documents, embeddings,
    facts, rules and the audit trail. `pg_dump` is enough to start.
11. **Pin image digests** rather than tags if reproducibility matters.
12. **Set resource limits** on the containers; embedding and inference are CPU
    bound.

## Environment

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` hides the API docs |
| `DATABASE_URL` | app role | Least privilege, used by the application |
| `ADMIN_DATABASE_URL` | owner role | Migrations and provisioning only |
| `JWT_SECRET` | placeholder | **Must be replaced** |
| `JWT_SECRET_PREVIOUS` | empty | Set during key rotation to avoid mass logout |
| `ACCESS_TOKEN_TTL_SECONDS` | 900 | 15 minutes |
| `REFRESH_TOKEN_TTL_SECONDS` | 1209600 | 14 days |
| `LLM_PROVIDER` | `mock` | `mock`, `openai`, `anthropic`, `ollama` |
| `LLM_API_KEY` | empty | Required for cloud providers |
| `EMBEDDING_PROVIDER` | `hashing` | Changing this invalidates stored vectors |
| `EMBEDDING_DIMENSION` | 384 | Part of the column type; changing needs a migration |
| `ANSWER_CONFIDENCE_THRESHOLD` | 0.55 | Raise to make the system more cautious |
| `CAVEAT_CONFIDENCE_THRESHOLD` | 0.40 | Below this it abstains |
| `EVIDENCE_RELEVANCE_THRESHOLD` | 0.18 | Below this it abstains |
| `RETRIEVAL_TOP_K` / `RERANK_TOP_K` | 24 / 8 | Candidate pool and final evidence set |
| `MAX_UPLOAD_BYTES` | 15 MB | Also enforced by nginx `client_max_body_size` |
| `RATE_LIMIT_ENABLED` | true | Disabled in the test suite |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | `text` for readable local logs |
| `SEED_ON_START` | `true` in compose | **Set to `false` in production** |

## Operations

```bash
docker compose ps
docker compose logs -f backend
curl localhost:8000/api/health

# Migrations (already run by the entrypoint)
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade -1

# Seed or re-seed the demo workspaces (idempotent)
docker compose exec backend python -c \
  "from app.seed.demo import seed_all; seed_all()"

# Ingest a dataset from the host
python scripts/ingest_dataset.py ./data --tenant acme --as admin@acme.test

# Benchmark
docker compose exec backend python -m app.evaluation.harness  # or scripts/run_eval.py

# Full reset, destroying all data
docker compose down -v && docker compose up --build
```

`/api/health` reports `status`, `database`, `llm_provider` and
`embedding_provider`, and returns `degraded` when the database is unreachable.
Both containers have Docker healthchecks.

## Scaling notes

- **The API is stateless** apart from the in-process rate limiter, so it scales
  horizontally once that moves to Redis.
- **The database is the bottleneck.** Vector search, lexical search and RLS all
  run there. Add read replicas for retrieval before sharding anything.
- **ivfflat with `lists=100`** suits the demo corpus. Tune `lists` and `probes`,
  or move to HNSW, as the corpus grows; rebuild the index after bulk ingestion.
- **Ingestion is synchronous.** A large corpus wants a background worker; the
  ingestion service is already a plain function, so moving it behind a queue does
  not require redesign.

## Not included

No Kubernetes manifests, no Terraform, no CI pipeline, no secret manager
integration, no blue/green story, no multi-region anything. Those are deliberate
omissions for an MVP, not oversights — but they are omissions, and a real
deployment needs them.
