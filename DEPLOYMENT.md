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

## Step 1 — the database and API

1. Render dashboard → **New → Blueprint** → point it at this repository. It creates the
   `praxis-db` PostgreSQL instance and the `praxis-api` service, wires the connection
   string, and generates `JWT_SECRET` itself — that value is never written down here.
2. **Enable pgvector once**, from the Render shell against the new database:

   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

   Render ships the extension but does not enable it, and the migration that creates the
   vector column fails without it.
3. Seed and ingest, from the Render shell:

   ```bash
   python -c "from app.seed.demo import seed_all; seed_all()"
   python scripts/ingest_demo_policies.py --tenant meridian
   ```

   Without the second command the workspace has users and rules but no documents, and
   every retrieval question abstains.
4. Set `CORS_ORIGINS` to the Vercel URL from step 2.

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
