# Development

## Fastest path

```bash
cp .env.example .env
docker compose up --build
```

Frontend on http://localhost:8080, API on http://localhost:8000, docs at
http://localhost:8000/api/docs. Migrations run and the demo workspaces are seeded
automatically (`SEED_ON_START=true`).

## Working on the backend directly

The database still comes from Docker; everything else runs on the host.

```bash
docker compose up -d db

cd backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # Windows
# .venv/bin/pip install -r requirements-dev.txt                     # macOS / Linux

# point .env at localhost rather than the compose network
sed -i 's|@db:5432|@localhost:5432|g' ../.env

python -m alembic upgrade head
python ../scripts/seed_demo.py
python -m uvicorn app.main:app --reload --port 8000
```

## Working on the frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to :8000
npm run typecheck
npm run build
```

## Tests

The suite runs against a real PostgreSQL instance with pgvector and row level
security, because the isolation guarantees under test are database behaviour. A
SQLite substitute would test nothing that matters.

```bash
cd backend
python -m pytest                                  # everything, ~20s
python -m pytest tests/test_security.py -v        # the security regressions
python -m pytest -k "contradiction or abstain"
python -m pytest --cov=app --cov-report=term-missing
```

`conftest.py` points at the `praxis_test` database (created by the container's
init script), migrates it, truncates it and seeds the demo workspaces once per
session. It does **not** drop the schema: pgvector and pg_trgm are installed into
`public` by the superuser at container init, and the migration role deliberately
cannot create extensions.

If the test database ever loses its extensions:

```bash
docker exec praxis-db psql -U postgres -d praxis_test \
  -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

## Migrations

```bash
cd backend
python -m alembic revision --autogenerate -m "what changed"
python -m alembic upgrade head
python -m alembic downgrade -1
```

Two rules learned the hard way:

1. **Never build a migration from `Base.metadata`.** Revision 0001 originally
   called `create_all`, which meant every later model change was silently
   absorbed into it — and the *second* migration then collided with the first on
   a fresh database. Migrations must be explicit DDL, fixed at the moment they
   are written.
2. **New tenant-scoped tables need RLS.** Add the table to `TENANT_SCOPED_TABLES`
   in the migration and enable `ROW LEVEL SECURITY` + `FORCE` with the isolation
   policy. `test_every_tenant_scoped_table_has_forced_rls` will fail if you
   forget, and `test_migrations_match_the_models` will fail if the models and the
   migrations drift apart.

Migrations run as `praxis_owner`; the application runs as `praxis_app`, which
cannot create or alter anything.

## Provider configuration

The default is fully offline. To use a real model:

```bash
LLM_PROVIDER=openai      LLM_MODEL=gpt-4o-mini  LLM_API_KEY=sk-...
LLM_PROVIDER=anthropic   LLM_MODEL=claude-sonnet-4-5  LLM_API_KEY=sk-ant-...
LLM_PROVIDER=ollama      LLM_MODEL=llama3.1  LLM_BASE_URL=http://localhost:11434

EMBEDDING_PROVIDER=openai  EMBEDDING_MODEL=text-embedding-3-small
```

Changing the embedding provider or dimension invalidates stored vectors; re-ingest
or re-embed. Then re-run `python scripts/run_eval.py` — the benchmark numbers in
`docs/EVALUATION.md` are for the offline provider and will differ.

## Project layout

```
backend/app/
  api/          routers and Pydantic schemas — HTTP shape only
  auth/         passwords, tokens, registration, login, rotation
  tenancy/      Principal, permission catalogue
  policy/       constraint language, PDP, query filters
  rag/          parsers, chunker, embeddings, injection screening, retriever
  knowledge/    fact schema, extraction, persistence
  symbolic/     rule grammar, inference engine, contradiction detection
  neuro/        LLM providers, prompts, intent parsing, heuristics
  verification/ claim verification, the evidence gate
  orchestrator/ router, pipeline, conversation manager
  evaluation/   cases and harness
  seed/         demo corpus and workspaces
```

Dependency direction: `api -> orchestrator -> {neuro, rag, knowledge, symbolic,
policy, verification} -> db`. `symbolic` and `policy` are pure — no LLM, no
network, no database — which is what makes their output trustworthy enough to
override generated text.

## Conventions

- Type hints everywhere; `from __future__ import annotations` at the top.
- Comments explain *why*, not *what*. If a line needs a comment to say what it
  does, rename something instead.
- Errors are typed (`app/core/errors.py`) and never leak internals.
- Anything security-relevant gets a test in `tests/test_security.py` that fails
  if the control is removed.
