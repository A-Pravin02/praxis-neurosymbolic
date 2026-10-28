# Running PRAXIS without Docker

Every command here was executed on the machine of record. Nothing is written from
memory of how it ought to work.

**Machine of record:** Windows 11, Python 3.11.9, 12 CPU cores, NVIDIA RTX 4050 Laptop
(6.44 GB VRAM, driver 592.82), 108 GB free disk.

The repository ships a `docker-compose.yml` and `Dockerfile`. They are left in place for
anyone who wants them, and are **not used** by anything below.

---

## 1. What actually needs to exist

| Requirement | Why | Can it be skipped? |
|---|---|---|
| Python 3.11 + venv | the backend | no |
| PostgreSQL 17 with `vector` and `pg_trgm` | 376 of 468 tests assert row-level-security and pgvector behaviour | only for the 92 pure-logic tests |
| `torch` + CUDA | the LLM baselines and the from-scratch model | yes, if you only run the symbolic system |
| An open-weights model (~3 GB) | the LLM baselines | yes, baselines then report "not run" rather than being simulated |
| Node + npm | the frontend | yes, for backend-only work |

A GPU is not required to run the system. It is required to reproduce the LLM baselines
and to train the from-scratch model in reasonable time.

## 2. Backend environment

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

On Linux or macOS use `.venv/bin/python` throughout instead of `.venv/Scripts/python.exe`.

Research extras, not needed to run the product:

```bash
# CUDA 12.1 build; use the index-url matching your driver
.venv/Scripts/python.exe -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu121
.venv/Scripts/python.exe -m pip install transformers==4.46.3 accelerate==1.1.1 datasets==3.1.0 sentencepiece
```

Verify CUDA before trusting any throughput number:

```bash
.venv/Scripts/python.exe -c "import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# 2.5.1+cu121 True NVIDIA GeForce RTX 4050 Laptop GPU
```

## 3. The database, without a container

The suite deliberately runs against real PostgreSQL: the isolation guarantees under
test *are* database behaviour, so a SQLite substitute would verify nothing that matters.
Two roles are required, and the application role must **not** be able to bypass row
level security — otherwise the RLS tests pass for the wrong reason.

### Option A — managed PostgreSQL (used for the results in this repository)

Create an instance with a `praxis` database owned by a non-superuser role
`praxis_owner`, then:

```bash
python scripts/setup_cloud_db.py \
  --owner-url "postgresql://praxis_owner:PASSWORD@HOST/praxis?sslmode=require"
```

That script is idempotent and reproduces `docker/postgres/init.sql` exactly: it creates
`praxis_app` with `NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT`, creates
the `praxis_test` database, installs `vector` and `pg_trgm` in both, grants the app role
only what it needs, and then **verifies** that `praxis_app` cannot bypass RLS, failing
loudly if it can. It prints the generated app-role password once.

**Choose a region close to you.** This is not a detail. Measured from the machine of
record:

| Region | Round-trip | Full suite |
|---|---|---|
| `us-east-1` | 270 ms | unusable |
| `ap-southeast-1` | 51 ms | 9 m 32 s |

### Option B — native PostgreSQL on the host

Install PostgreSQL 17 and the pgvector extension, then run `scripts/setup_local_db.py`,
which performs the same role setup against a local superuser connection. On Windows,
pgvector has to be built or obtained as a prebuilt binary, which is the most fragile
part of this route; on Linux `apt install postgresql-17-pgvector` is enough.

### Configuration

Put the connection strings in `.env` at the repository root (it is gitignored):

```
DATABASE_URL=postgresql+psycopg://praxis_app:APP_PASSWORD@HOST/praxis?sslmode=require
ADMIN_DATABASE_URL=postgresql+psycopg://praxis_owner:OWNER_PASSWORD@HOST/praxis?sslmode=require
TEST_DATABASE_URL=postgresql+psycopg://praxis_app:APP_PASSWORD@HOST/praxis_test?sslmode=require
TEST_ADMIN_DATABASE_URL=postgresql+psycopg://praxis_owner:OWNER_PASSWORD@HOST/praxis_test?sslmode=require
```

Use the **direct** endpoint rather than a connection pooler for the admin URL: migrations
take advisory locks that a transaction pooler does not carry across statements.

## 4. Tests

```bash
cd backend

# everything (needs the database). Expected: 468 passed
DATABASE_URL="$TEST_DATABASE_URL" ADMIN_DATABASE_URL="$TEST_ADMIN_DATABASE_URL" \
  .venv/Scripts/python.exe -m pytest -q

# pure logic only, no database at all. Expected: 92 passed
.venv/Scripts/python.exe -m pytest -q -m no_db
```

`conftest.py` reads `DATABASE_URL` and `ADMIN_DATABASE_URL` via `setdefault`, so the
environment wins over the defaults — which is why the test URLs are exported into those
names rather than used directly.

### If you get 440 errors that all say `DeadlockDetected`

The suite truncates shared tables between tests. A `pytest` process from an earlier
cancelled run, still holding locks on the same database, deadlocks every subsequent
test. This is not a defect in the suite. Kill the strays and re-run:

```bash
# Windows
powershell "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*pytest*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }"
```

Then, if sessions remain server-side:

```sql
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE datname = 'praxis_test' AND pid <> pg_backend_pid();
```

## 5. Running the application

```bash
# backend
cd backend && .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000

# frontend, in a second terminal
cd frontend && npm install && npm run dev
```

Demo sign-ins are documented in the project README (`support@meridian.test`,
`admin@meridian.test`).

## 6. Reproducing the benchmarks

The language-model baselines need a real model. With none configured
(`LLM_PROVIDER=mock`) they are reported as **not run**, never simulated.

### Local open-weights (no API key)

```bash
cd backend
export HF_HOME="$PWD/.model_cache"
.venv/Scripts/python.exe -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen2.5-1.5B-Instruct', allow_patterns=['*.json','*.safetensors','tokenizer*','*.txt'])"
```

Qwen2.5-1.5B-Instruct is 3.10 GB; SmolLM2-1.7B-Instruct is 3.43 GB. Measured download
throughput on the machine of record was 2.15 MB/s, so budget ~25 minutes each.

```bash
cd ..
export HF_HOME="$PWD/backend/.model_cache"
export LLM_PROVIDER=local LOCAL_LLM_MODEL=Qwen/Qwen2.5-1.5B-Instruct

python scripts/run_ns_benchmark.py --split copilot \
  --systems neurosymbolic,neurosymbolic_lexical,rag_extractive,llm_only,llm_cot,llm_rag,llm_rag_rules,llm_rag_selfcheck
```

Reports land in `eval_reports/` as JSON and Markdown. Then:

```bash
python scripts/analyze_benchmark.py eval_reports/ns-benchmark-meridian-copilot-<stamp>.json
```

which writes `-stats.json` with McNemar exact tests and paired bootstrap confidence
intervals.

Notes that will save you time:
- **Greedy decoding** is used throughout, so a run repeats exactly on the same hardware.
- **The first generation call is not representative.** Kernel autotuning made the first
  call measure 2.7 tokens/s against ~26 tokens/s steady-state. Warm up before timing.
- **Do not run CPU-heavy work alongside a benchmark.** Latency is a reported metric.
- **The `dev` split cannot run the LLM systems.** Its compact `{q:, goal:, act:}` schema
  carries no `user`, and the LLM systems need the asker's identity. `dev` exists to
  calibrate similarity thresholds for the symbolic path. Use `test` for smoke runs.
- **`retrieval_accuracy` is `n/a` on `copilot`** because that split declares no expected
  `clauses`. Measure retrieval on `test` (8 cases) or `holdout` (3).

### Hosted provider instead

Any OpenAI-compatible endpoint works; only the URL, key and model change.

```bash
LLM_PROVIDER=openai LLM_BASE_URL=https://api.groq.com/openai/v1 \
LLM_API_KEY=... LLM_MODEL=llama-3.3-70b-versatile \
python scripts/run_ns_benchmark.py --split copilot
```

## 7. The from-scratch language model

```bash
# what fits on this GPU, and how fast
python scripts/train_slm.py --list-configs

# corpus, tokenizer and token streams. Aborts if a benchmark question is present.
python scripts/prepare_slm_data.py --corpus wikitext103 --vocab-size 8192 \
  --out artifacts/slm/data

# short pilot first - never commit to a long run unmeasured
python scripts/train_slm.py --preset pilot --max-steps 200 \
  --data artifacts/slm/data --out artifacts/slm/runs/pilot

# the reported run
python scripts/train_slm.py --preset small --max-steps 6000 \
  --data artifacts/slm/data --out artifacts/slm/runs/small

# resume after an interruption
python scripts/train_slm.py --preset small --out artifacts/slm/runs/small --resume
```

Measured on the machine of record (`artifacts/slm/hardware_probe.json`): the `small`
preset is 29.9M parameters, 42,948 tokens/s, 3.17 GB peak at batch 16 / context 512.

**A Windows-specific trap.** Exceeding VRAM does **not** raise `OutOfMemoryError` — the
WDDM driver spills silently to host memory. The `base` preset reported 7.26 GB "working"
on a 6.44 GB card while throughput collapsed from 16,075 to 1,193 tokens/s. Judge
feasibility by tokens/s, not by peak memory alone.

## 8. Expected resource requirements

| Task | Time | Disk | VRAM |
|---|---|---|---|
| Full test suite | ~10 min | — | — |
| Pure-logic tests | ~10 s | — | — |
| One model download | ~25 min at 2.15 MB/s | 3.1-3.5 GB | — |
| Benchmark, 8 systems, 53 cases | ~35 min | — | 3.5 GB |
| Corpus preparation (wikitext-103) | 10-60 min | ~1.5 GB | — |
| Training, `small`, 6000 steps | ~2-3 h | ~1 GB per run | 3.2 GB |

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ValidationError: llm_provider Input should be 'mock', 'openai'...` | A `local` value on an older checkout. `app/config.py` must include `"local"` in the literal |
| 440 errors, `DeadlockDetected` on `TRUNCATE` | Orphaned `pytest` processes hold locks. See §4 |
| `alembic upgrade failed` / connection timeout | Wrong host, or the admin URL points at a transaction pooler. Use the direct endpoint |
| `KeyError: 'user'` from `_who` | The LLM systems were pointed at the `dev` split. Use `test` |
| Generation at 2-3 tokens/s | First-call kernel autotuning. Warm up, then measure |
| `extension "vector" is not available` | Extensions must be installed by a superuser before migrations; the migration role deliberately cannot create them. Re-run `setup_cloud_db.py` |
| Suite takes far longer than 10 minutes | Database round-trip latency. Check the region; 270 ms makes it unusable |
| Symlink warnings from `huggingface_hub` | Harmless on Windows without Developer Mode; downloads just use more disk |
