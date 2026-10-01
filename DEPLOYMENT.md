# Deploying Praxis

Three pieces, and only one of them belongs on Vercel.

| Piece | Where | Why |
|---|---|---|
| `frontend/` — the React app | **Vercel** | static files; a CDN is the right host |
| `backend/` — the FastAPI service | **not Vercel** | loads a 130 MB ONNX encoder and holds a database pool |
| PostgreSQL + pgvector | **managed Postgres** | vector search happens in the database |

## Why the backend cannot go on Vercel

Retrieval runs `bge-small-en-v1.5` through ONNX Runtime — about 130 MB of model loaded at
startup and kept in memory. Vector search runs in PostgreSQL through the pgvector
extension. Neither fits a serverless function: the model would reload on every cold start,
and there is nowhere for the database to live. Forcing it there would mean dropping
semantic retrieval, which is the part that makes the paraphrase demo work at all.

`render.yaml` declares the API and its database together.

## The paid plan is not optional

`render.yaml` asks for a `standard` web service and a `basic-256mb` database. Both cost
money, and dropping either to a free plan does not produce a cheaper Praxis — it produces
one that answers nothing.

The encoder is the reason. On a plan too small to hold it, `get_encoder`
(`backend/app/neural/encoder.py`) catches the load failure, logs a warning and falls back
to lexical hashing. The service starts, the health check passes, and every page looks
right. Measured on the 255-case test split
(`eval_reports/research/deploy-encoder/`, reproduce with
`python scripts/run_research.py --split test --systems neurosymbolic neurosymbolic_lexical`):

| | semantic encoder | lexical fallback |
|---|---|---|
| Accuracy | **0.796** [0.75, 0.84] | **0.059** [0.03, 0.09] |
| Coverage | **0.943** | **0.000** |
| Over-abstention | 0.057 | **1.000** |
| Hallucination rate | 0.149 | 0.000 |

The fallback's hallucination rate of zero is not a result, it is the shape of the failure:
it abstains on every answerable question, so it never says anything false because it never
says anything. A visitor sees a system that refuses its own demo.

So the deployment choice is binary. Pay for the plans in `render.yaml`, or run the demo
locally with `docker compose up` — which costs nothing and is the configuration every
benchmark in this repository was measured on. A free-tier Render deployment is worse than
no deployment, because it looks live.

## Step 1 — the database and API

1. Render dashboard → **New → Blueprint** → point it at this repository. It creates the
   `praxis-db` PostgreSQL instance and the `praxis-api` service, wires the connection
   string, and generates `JWT_SECRET` itself — that value is never written down here.
2. **Enable two extensions once**, from the Render shell against the new database:

   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE EXTENSION IF NOT EXISTS pg_trgm;
   ```

   Render ships both and enables neither, and the first migration refuses to run without
   them. It names the one it is missing, so enabling only `vector` gets you a second
   failure about `pg_trgm` on the next deploy rather than a working service; enable both
   now. Verified on 30 September 2026 by deploying the built image against a database
   created exactly this way.
3. Seed and ingest, from the Render shell:

   ```bash
   python -c "from app.seed.demo import seed_all; seed_all()"
   python scripts/ingest_demo_policies.py --tenant meridian
   ```

   Without the second command the workspace has users and rules but no documents, and
   every retrieval question abstains.
4. Set `CORS_ORIGINS` to the Vercel URL from step 2.

Render issues a single database role, and the schema's privilege separation expects two:
an owner that migrates and provisions the permission catalogue, and an application role
that may not write it. The migration detects that it is running as the application role
and skips the revokes that would otherwise take away the rights the service needs at
startup; with two distinct roles configured, the separation applies as before. Without
that check the first deploy failed at `permission denied for table permissions`, which
says nothing about the cause.

`LLM_PROVIDER` defaults to the offline provider, so a fresh deployment answers with no
credential at all. For a hosted model set `LLM_PROVIDER=openai`, `LLM_BASE_URL` and
`LLM_API_KEY` as secret environment variables in the dashboard — never in `.env`, which is
git-ignored precisely so a key cannot be committed by accident.

## Step 2 — the frontend

```bash
cd frontend
npx vercel --prod
```

Then set, in the Vercel dashboard:

```
VITE_API_BASE_URL = https://praxis-api.onrender.com/api
```

and redeploy. `VITE_` variables are compiled into the browser bundle, so **never put a
secret in one**.

## Deploying the frontend alone

It builds and loads, and the sign-in page appears. Signing in will fail, because there is
no API behind it. Deploy the backend first, or the link demonstrates nothing.

## What is deliberately not automated

Creating the Vercel and Render projects requires signing in to your accounts. Nothing here
does that, and no credential is stored in this repository. Check before any deploy:

```bash
git ls-files | xargs grep -lniE "sk-[a-z0-9]{20}|api[_-]?key *= *['\"][^'\"]{16}"
```

which should print nothing.
