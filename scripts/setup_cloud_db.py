"""Provision a managed PostgreSQL instance the way `docker/postgres/init.sql` does.

Why this exists
---------------
The test suite deliberately runs against a real PostgreSQL with pgvector and row level
security, because the isolation guarantees under test *are* database behaviour. The
repository normally gets that from Docker. This programme may not use Docker, and no
native PostgreSQL is installed, so the same ownership model is reproduced on a managed
instance instead.

The model being reproduced (see `docker/postgres/init.sql`):

  * `praxis_owner` owns the schema and runs migrations.
  * `praxis_app` is the application role. It is NOT the owner and it may NOT bypass
    row level security, which is the whole point of the RLS tests.
  * `vector` and `pg_trgm` are installed ahead of the migrations, because the migration
    role is not permitted to create extensions.
  * A second database, `praxis_test`, carries the same ownership model for the suite.

Usage
-----
    python scripts/setup_cloud_db.py --owner-url <postgres://praxis_owner:...@host/praxis>

The app role's password is generated and printed once so it can be put in `.env`;
pass --app-password to supply your own instead.

This script is idempotent: running it twice is harmless.
"""
from __future__ import annotations

import argparse
import secrets
import sys

try:
    import psycopg
    from psycopg import sql
except ModuleNotFoundError:  # pragma: no cover
    sys.exit("psycopg is required: pip install 'psycopg[binary]'")

APP_ROLE = "praxis_app"
OWNER_ROLE = "praxis_owner"
EXTENSIONS = ("vector", "pg_trgm")


def _connect(url: str) -> psycopg.Connection:
    # autocommit: CREATE DATABASE cannot run inside a transaction block.
    return psycopg.connect(url, autocommit=True)


def _exists(conn: psycopg.Connection, sql: str, param: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(sql, (param,))
        return cur.fetchone() is not None


def ensure_app_role(conn: psycopg.Connection, password: str) -> bool:
    """Create the least-privilege application role. Returns True if newly created."""
    # Role DDL does not accept bind parameters, so the password has to be a quoted
    # literal. psycopg.sql.Literal does the escaping; never interpolate it by hand.
    pw = sql.Literal(password)
    if _exists(conn, "SELECT 1 FROM pg_roles WHERE rolname = %s", APP_ROLE):
        with conn.cursor() as cur:
            # Keep the stored password in step with the one we are about to hand out.
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
                    sql.Identifier(APP_ROLE), pw)
            )
        return False
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} "
                "NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(APP_ROLE), pw)
        )
    return True


def ensure_database(conn: psycopg.Connection, name: str) -> bool:
    """Create `name` owned by the owner role. Returns True if newly created."""
    if _exists(conn, "SELECT 1 FROM pg_database WHERE datname = %s", name):
        return False
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}" OWNER {OWNER_ROLE}')
    return True


def grant_in_database(url: str, dbname: str) -> None:
    """Install the extensions and grant the app role exactly what it needs."""
    target = _swap_database(url, dbname)
    with _connect(target) as conn, conn.cursor() as cur:
        for ext in EXTENSIONS:
            cur.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
        cur.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        # The app role must reach tables the OWNER creates during migration.
        cur.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
        )
        cur.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
        )
        # Anything already migrated needs the grant applied retrospectively.
        cur.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
        )
        cur.execute(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"
        )


def _swap_database(url: str, dbname: str) -> str:
    """Replace the database component of a libpq URL, preserving the query string."""
    head, _, tail = url.rpartition("/")
    query = ""
    if "?" in tail:
        _, _, query = tail.partition("?")
        query = "?" + query
    return f"{head}/{dbname}{query}"


def verify(url: str, dbname: str) -> None:
    with _connect(_swap_database(url, dbname)) as conn, conn.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension ORDER BY 1")
        exts = [r[0] for r in cur.fetchall()]
        missing = [e for e in EXTENSIONS if e not in exts]
        cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = %s", (APP_ROLE,))
        row = cur.fetchone()
    print(f"  {dbname}: extensions={exts}")
    if missing:
        sys.exit(f"  FAIL {dbname}: missing extensions {missing}")
    if row is None:
        sys.exit(f"  FAIL {dbname}: role {APP_ROLE} not visible")
    if row[0]:
        sys.exit(f"  FAIL {dbname}: {APP_ROLE} can bypass RLS - the RLS tests would be meaningless")
    print(f"  {dbname}: {APP_ROLE} present, NOBYPASSRLS confirmed")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--owner-url", required=True,
                    help="libpq URL for praxis_owner on the primary database")
    ap.add_argument("--app-password", default=None,
                    help="password for praxis_app (generated if omitted)")
    ap.add_argument("--test-database", default="praxis_test",
                    help="name of the suite's database (default: praxis_test)")
    args = ap.parse_args()

    password = args.app_password or secrets.token_urlsafe(24)

    with _connect(args.owner_url) as conn:
        created_role = ensure_app_role(conn, password)
        created_db = ensure_database(conn, args.test_database)
    print(f"role {APP_ROLE}: {'created' if created_role else 'already present (password reset)'}")
    print(f"database {args.test_database}: {'created' if created_db else 'already present'}")

    primary = args.owner_url.rpartition("/")[2].partition("?")[0]
    for dbname in (primary, args.test_database):
        grant_in_database(args.owner_url, dbname)

    print("verifying:")
    for dbname in (primary, args.test_database):
        verify(args.owner_url, dbname)

    if args.app_password is None:
        print("\napp role password (store in .env, shown once):")
        print(password)


if __name__ == "__main__":
    main()
