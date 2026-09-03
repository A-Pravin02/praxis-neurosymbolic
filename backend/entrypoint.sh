#!/bin/sh
# Wait for the database, apply migrations, optionally seed, then run the command.
set -e

echo "praxis: waiting for the database..."
python - <<'PY'
import os, sys, time
import psycopg

url = os.environ.get("ADMIN_DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://")
deadline = time.time() + 120
last = None
while time.time() < deadline:
    try:
        with psycopg.connect(url, connect_timeout=3):
            print("praxis: database is up")
            sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        last = exc
        time.sleep(2)
print(f"praxis: database unreachable: {last}", file=sys.stderr)
sys.exit(1)
PY

echo "praxis: applying migrations"
alembic upgrade head

if [ "${SEED_ON_START:-false}" = "true" ]; then
  echo "praxis: seeding demonstration data (idempotent)"
  python -c "from app.core.logging import configure_logging; configure_logging(); from app.seed.demo import seed_all; seed_all()"
else
  python -c "from app.db.provision import ensure_permission_catalogue; ensure_permission_catalogue()"
fi

echo "praxis: starting $*"
exec "$@"
