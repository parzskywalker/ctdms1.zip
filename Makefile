# Common tasks.  `make help`
PY ?= python3

help:
	@grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*##/ -/'

install: ## install Python dependencies
	$(PY) -m pip install -r requirements.txt

demo: ## (re)create the SQLite demo database with realistic data
	$(PY) -m app.cli seed-demo --reset

run: ## start the dev server on http://127.0.0.1:8000
	$(PY) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

migrate: ## apply database migrations
	$(PY) -m app.cli init-db

test: ## run the test suite (SQLite)
	$(PY) -m pytest -q

test-pg: ## run the test suite on PostgreSQL (CTDMS_TEST_DATABASE_URL must point to a disposable DB)
	$(PY) -m pytest -q

backup: ## write a database backup to data/backups
	$(PY) -m app.cli backup

up: ## docker compose up (PostgreSQL + app + backups)
	docker compose up -d --build
