PORT ?= 8080
URL ?= http://127.0.0.1:$(PORT)

.PHONY: install test lint format run loadgen demo-reset docker-build compose-up clean

install:
	uv sync --python 3.12

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check . --fix

run:
	PORT=$(PORT) uv run aigateway

loadgen:
	uv run python scripts/loadgen.py --url $(URL)

demo-reset:  ## wipe the audit log so budgets start fresh (stop the server first)
	rm -f data/audit.db data/audit.db-wal data/audit.db-shm

docker-build:
	docker build -t ai-gateway .

compose-up:
	docker compose --profile metrics up --build

clean:
	rm -rf .pytest_cache .ruff_cache data
