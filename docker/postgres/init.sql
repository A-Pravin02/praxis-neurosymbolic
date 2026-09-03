-- Runs once, as the superuser, on first container start.
-- Creates the two roles the platform uses and hands schema ownership to the
-- non-superuser owner role. The application role is deliberately NOT the owner
-- and is NOT allowed to bypass row level security.

CREATE ROLE praxis_owner LOGIN PASSWORD 'praxis_owner_pw' NOBYPASSRLS NOSUPERUSER NOCREATEDB;
CREATE ROLE praxis_app   LOGIN PASSWORD 'praxis_app_pw'   NOBYPASSRLS NOSUPERUSER NOCREATEDB
                                                          NOCREATEROLE NOINHERIT;

ALTER DATABASE praxis OWNER TO praxis_owner;

\connect praxis

-- The public schema must belong to the owner role, not to the app role.
ALTER SCHEMA public OWNER TO praxis_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO praxis_app;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- A separate database for the test suite, same ownership model.
CREATE DATABASE praxis_test OWNER praxis_owner;
\connect praxis_test
ALTER SCHEMA public OWNER TO praxis_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO praxis_app;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
