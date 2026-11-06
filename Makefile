PORT ?= 8080
ENV ?= staging
TF_DIR := deploy/terraform
CHART := deploy/helm/ai-gateway
TERRAFORM ?= terraform
HELM ?= helm
URL ?= http://127.0.0.1:$(PORT)

.PHONY: install test lint format run loadgen demo-reset docker-build compose-up clean \
	tf-fmt tf-validate tf-test tf-plan helm-lint helm-template infra-check

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

# Infrastructure (see the Deploy section of the README)

tf-fmt:
	$(TERRAFORM) -chdir=$(TF_DIR) fmt -check -recursive

tf-validate:
	$(TERRAFORM) -chdir=$(TF_DIR) init -backend=false -input=false
	$(TERRAFORM) -chdir=$(TF_DIR) validate

tf-test: tf-validate  ## plan and apply against a mocked AWS provider, no account needed
	$(TERRAFORM) -chdir=$(TF_DIR) test -var-file=environments/staging.tfvars
	$(TERRAFORM) -chdir=$(TF_DIR) test -var-file=environments/prod.tfvars

tf-plan:  ## real plan, needs AWS credentials and backend.hcl: make tf-plan ENV=staging
	$(TERRAFORM) -chdir=$(TF_DIR) init -backend-config=backend.hcl -reconfigure
	$(TERRAFORM) -chdir=$(TF_DIR) plan -var-file=environments/$(ENV).tfvars -out=$(ENV).tfplan

helm-lint:
	$(HELM) lint $(CHART) --strict

helm-template:
	$(HELM) template gw $(CHART) --set metrics.serviceMonitor.enabled=true

infra-check: tf-fmt tf-test helm-lint
