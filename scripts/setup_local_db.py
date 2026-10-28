"""Bootstrap the local Postgres DB for Praxis.

Run once after installing PostgreSQL:
    python scripts/setup_local_db.py

What this does:
  1. Connects as the postgres superuser
  2. Creates the two application roles (praxis_owner, praxis_app) if absent
  3. Creates the praxis database owned by praxis_owner if absent
  4. Installs pgvector and pg_trgm extensions
  5. Grants schema-level permissions
  6. Runs Alembic migrations (head)
  7. Provisions the permission catalogue (requires admin role)
  8. Seeds the demo workspace (optional, --seed flag)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
PG_BIN = _find_pg_bin()


def _find_pg_bin() -> str:
    """Find the PostgreSQL bin directory on common Windows install paths."""
    import glob
    candidates = [
        r"C:\Program Files\PostgreSQL\*\bin",
        r"C:\Program Files (x86)\PostgreSQL\*\bin",
    ]
    for pattern in candidates:
        matches = sorted(glob.glob(pattern), reverse=True)
        if matches:
            return matches[0]
    return ""  # hope it's on PATH


def psql(sql: str, *, user: str = "postgres", dbname: str = "postgres",
         pg_password: str = "") -> None:
    env = os.environ.copy()
    if pg_password:
        env["PGPASSWORD"] = pg_password
    bin_psql = str(Path(PG_BIN) / "psql") if PG_BIN else "psql"
    result = subprocess.run(
        [bin_psql, "-U", user, "-d", dbname, "-c", sql],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        print(f"  [warn] {result.stderr.strip()[:200]}")
    else:
        print(f"  ok: {sql[:80]}")


def run_alembic(env_extra: dict) -> None:
    env = os.environ.copy()
    env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("ALEMBIC FAILED:")
        print(result.stdout[-2000:])
        print(result.stderr[-2000:])
        sys.exit(1)
    print("  Migrations applied.")


def run_python(module: str, env_extra: dict) -> None:
    env = os.environ.copy()
    env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}; {module}.main()"],
        cwd=BACKEND, env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  [warn] {module} failed: {result.stderr[-500:]}")
    else:
        print(f"  {module} ok.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap local Praxis DB")
    parser.add_argument("--pg-password", default="", help="postgres superuser password")
    parser.add_argument("--seed", action="store_true", help="Seed the demo workspace")
    args = parser.parse_args()

    pw = args.pg_password or os.environ.get("PGPASSWORD", "")

    print("=== 1. Creating roles ===")
    psql("CREATE ROLE praxis_owner LOGIN PASSWORD 'praxis_owner_pw';", pg_password=pw)
    psql("CREATE ROLE praxis_app   LOGIN PASSWORD 'praxis_app_pw';",   pg_password=pw)

    print("\n=== 2. Creating database ===")
    psql("CREATE DATABASE praxis OWNER praxis_owner;", pg_password=pw)

    print("\n=== 3. Installing extensions ===")
    psql("CREATE EXTENSION IF NOT EXISTS vector;",   user="postgres", dbname="praxis", pg_password=pw)
    psql("CREATE EXTENSION IF NOT EXISTS pg_trgm;",  user="postgres", dbname="praxis", pg_password=pw)

    print("\n=== 4. Granting schema permissions ===")
    psql("GRANT USAGE ON SCHEMA public TO praxis_app;", user="postgres", dbname="praxis", pg_password=pw)
    psql("GRANT CREATE ON SCHEMA public TO praxis_owner;", user="postgres", dbname="praxis", pg_password=pw)
    psql("ALTER DEFAULT PRIVILEGES FOR ROLE praxis_owner IN SCHEMA public "
         "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO praxis_app;",
         user="postgres", dbname="praxis", pg_password=pw)
    psql("ALTER DEFAULT PRIVILEGES FOR ROLE praxis_owner IN SCHEMA public "
         "GRANT USAGE, SELECT ON SEQUENCES TO praxis_app;",
         user="postgres", dbname="praxis", pg_password=pw)

    env = {
        "DATABASE_URL": "postgresql+psycopg://praxis_app:praxis_app_pw@localhost:5432/praxis",
        "ADMIN_DATABASE_URL": "postgresql+psycopg://praxis_owner:praxis_owner_pw@localhost:5432/praxis",
        "LLM_PROVIDER": "mock",
        "EMBEDDING_PROVIDER": "hashing",
        "JWT_SECRET": "local-dev-secret",
        "ENVIRONMENT": "development",
    }

    print("\n=== 5. Running Alembic migrations ===")
    run_alembic(env)

    print("\n=== 6. Provisioning permission catalogue ===")
    prov_code = (
        "import sys; sys.path.insert(0, '.'); "
        "from app.db.provision import ensure_permission_catalogue; "
        "n = ensure_permission_catalogue(); print(f'  Added {n} permissions.')"
    )
    e2 = os.environ.copy()
    e2.update(env)
    r = subprocess.run([sys.executable, "-c", prov_code], cwd=BACKEND, env=e2,
                       capture_output=True, text=True)
    print(r.stdout.strip() or "  ok.")
    if r.returncode != 0:
        print(f"  [warn] {r.stderr[-500:]}")

    if args.seed:
        print("\n=== 7. Seeding demo workspace ===")
        seed_code = (
            "import sys; sys.path.insert(0, '.'); "
            "from app.seed import demo; demo.seed_all(); print('  Seeded.')"
        )
        r2 = subprocess.run([sys.executable, "-c", seed_code], cwd=BACKEND, env=e2,
                            capture_output=True, text=True)
        print(r2.stdout.strip() or "  ok.")
        if r2.returncode != 0:
            print(f"  [warn] {r2.stderr[-500:]}")

    print("\n✅ DB ready. Start the server with:")
    print("   cd backend && .venv\\Scripts\\uvicorn app.main:app --reload --port 8000")


if __name__ == "__main__":
    main()
