# Convenience targets. Everything here is a thin wrapper around the commands in
# docs/DEVELOPMENT.md; nothing is hidden behind make.

.PHONY: help up down logs rebuild reset test test-security eval seed ingest \
        migrate shell fmt frontend-dev frontend-build

help:
	@echo "up             start the full stack (build if needed)"
	@echo "down           stop the stack, keeping data"
	@echo "reset          destroy all data and start clean"
	@echo "logs           follow the backend logs"
	@echo "test           run the backend test suite"
	@echo "test-security  run the security regression suite only"
	@echo "eval           run the four-way evaluation benchmark"
	@echo "seed           re-seed the demo workspaces (idempotent)"
	@echo "migrate        apply database migrations"
	@echo "ingest P=path  ingest a dataset"

up:
	docker compose up --build -d
	@echo "app  -> http://localhost:8080"
	@echo "api  -> http://localhost:8000/api/docs"

down:
	docker compose down

reset:
	docker compose down -v
	docker compose up --build -d

logs:
	docker compose logs -f backend

test:
	cd backend && python -m pytest -q

test-security:
	cd backend && python -m pytest tests/test_security.py tests/test_tenancy.py -v

eval:
	python scripts/run_eval.py

seed:
	python scripts/seed_demo.py

migrate:
	cd backend && python -m alembic upgrade head

ingest:
	python scripts/ingest_dataset.py $(P) --tenant $(or $(TENANT),acme) --as $(or $(USER_EMAIL),admin@acme.test)

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build
