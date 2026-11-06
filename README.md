# AI Gateway

A FastAPI service that sits between a company's internal apps and the Claude API. Apps send a normal Claude Messages request to `POST /v1/messages` with a per-team gateway key. The gateway checks the key, the model allowlist, the team's rate limit, and its monthly budget, strips PII from the prompt, serves repeats from a cache, calls Claude with retries and a fallback model, and writes every request to an append-only audit log. An admin dashboard shows spend by team against budget, blocked requests, cache hit rate, and redactions. It runs offline with a deterministic mock upstream by default.

![Dashboard after one loadgen run](docs/screenshot.png)

## Why

Once a dozen teams call an LLM API directly, a platform team loses track of who spends what, which prompts carry customer data out of the network, and why the bill jumped. The usual fix is a gateway: one place that owns the API key, enforces per-team quotas and budgets, redacts PII, caches, retries, and logs. This repo is a small working version of that pattern, with each policy as a small function that is tested on its own.

## Architecture

```
 internal app ──POST /v1/messages──▶ ┌──────────────────────── AI Gateway ────────────────────────┐
   x-gateway-key: gw-...             │ 1 auth (key → team)                         401             │
                                     │ 2 model allowlist                           403             │
                                     │ 3 token-bucket rate limit (per team)        429 Retry-After │
                                     │ 4 PII redaction (email, phone, SSN, card)                   │
                                     │ 5 exact-match cache (sha256 of request) ──▶ hit: cost $0    │
                                     │ 6 budget check (month spend + estimate)     402             │
                                     │ 7 upstream call, retry 429/5xx with         502 if all fail │
                                     │   backoff + jitter, then fallback model                     │
                                     │ 8 audit log (SQLite, append-only) + metrics                 │
                                     └───────────┬──────────────────────────────┬─────────────────┘
                                                 │                              │
                                     Provider interface               /metrics  /admin/usage  /
                                     mock (default) | anthropic       Prometheus  JSON   dashboard
```

| Piece | File |
|---|---|
| Teams, keys, budgets, limits, allowed models | `config/gateway.yaml` (synthetic keys) |
| Policy steps: auth, allowlist, token bucket, budget, redaction wrapper, cache | `src/aigateway/policies.py` |
| PII patterns and Luhn check | `src/aigateway/pii.py` |
| Price table: Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50 per million tokens | `src/aigateway/pricing.py` |
| Mock and Anthropic providers, retry with full jitter, fallback | `src/aigateway/providers.py` |
| Audit log with triggers that block UPDATE and DELETE | `src/aigateway/audit.py` |
| Prometheus text output, no client library | `src/aigateway/metrics.py` |
| Endpoints and pipeline wiring | `src/aigateway/app.py` |
| Dashboard (one HTML file, plain JS) | `src/aigateway/static/index.html` |

Details worth knowing:

- **Budget** is checked before the call with a worst-case estimate (input chars / 4 plus the full `max_tokens` of output at the model's price), and the actual cost from the upstream `usage` is recorded after. Month spend is summed from the audit log, so it survives restarts.
- **Rate limit** is a token bucket per team: capacity equals the requests-per-minute limit, refilled continuously. A 429 carries `Retry-After` in whole seconds.
- **Redaction** runs before the cache key is computed and before anything leaves. Card numbers are only redacted if they pass a Luhn check, so order numbers are left alone.
- **Cache** keys are `team + sha256(normalized request)`: key order and surrounding whitespace do not matter, and teams never share entries. Cache hits cost $0 and skip the budget check.
- **Audit log** stores team, model, tokens, cost, latency, cache hit, redaction counts by type, status, and a sha256 of the request. It never stores prompt text.
- **Errors** use the Anthropic error shape: `{"type": "error", "error": {"type": "rate_limit_error", "message": "..."}}`.
- **Response headers**: `x-request-id`, `x-gateway-cache`, `x-gateway-cost-usd`, `x-gateway-redactions`, `x-gateway-attempts`, and `x-gateway-fallback` when the fallback model answered.

## How to run

Requires [uv](https://docs.astral.sh/uv/). Docker is optional.

```bash
make install            # uv sync --python 3.12
make run                # gateway on http://127.0.0.1:8080, mock upstream
```

In a second terminal:

```bash
make loadgen            # synthetic traffic from three teams
open http://127.0.0.1:8080/        # dashboard
curl -s http://127.0.0.1:8080/metrics
curl -s http://127.0.0.1:8080/admin/usage
```

One request by hand:

```bash
curl -s http://127.0.0.1:8080/v1/messages \
  -H 'x-gateway-key: gw-support-91be55' -H 'content-type: application/json' \
  -d '{"model":"claude-haiku-5-5","max_tokens":200,
       "messages":[{"role":"user","content":"Reply to dana@fabrikam-mail.example about her refund"}]}'
```

The loadgen run tells three stories. On a fresh audit log it produces:

| Team | Setup | 200 | 402 | 403 | 429 |
|---|---|---|---|---|---|
| checkout | 40 burst requests at 20 per minute | 20 (16 from cache) | 0 | 0 | 20 |
| growth | Opus, `max_tokens` 1024, $0.15 budget | 21 | 9 | 0 | 0 |
| support | 30 prompts with emails, phones, SSNs, cards; one Opus call | 30 (8 from cache) | 0 | 1 | 0 |

Plus 2 requests with a bad or missing key (401), 30 PII items redacted, and a 33.8% cache hit rate. Budgets are monthly and read from the audit log, so run `make demo-reset` (with the server stopped) to start the story over.

To call the real Claude API, install the extra and set the key in your shell:

```bash
uv sync --extra claude
export ANTHROPIC_API_KEY=...       # see .env.example
GATEWAY_PROVIDER=anthropic make run
```

Docker (not run locally on this machine; the image is built and smoke tested in CI):

```bash
docker compose up --build                      # gateway on :8080
docker compose --profile metrics up --build    # plus Prometheus on :9090 scraping /metrics
```

## Tests

45 tests, all offline, using FastAPI's TestClient with a fake upstream, a fake clock, and a recorded sleep function.

```bash
make test     # uv run pytest -q
make lint     # ruff check and ruff format --check
```

- `test_pii.py` (7): emails, SSNs (invalid area numbers skipped), cards only when Luhn passes, three phone formats, clean text untouched, content blocks redacted without mutating input.
- `test_policies.py` (11): auth, allowlist, token bucket burst and refill, `Retry-After` value, per-team buckets, price table, budget estimate and hard stop, request hash normalization, cache TTL and LRU eviction, config validation.
- `test_providers.py` (8): mock determinism and simulated latency, backoff bounds, retry then success, no retry on 400, fallback after retries run out, Anthropic SDK response and error mapping with a fake client.
- `test_audit.py` (2): UPDATE and DELETE are rejected, month spend ignores last month.
- `test_gateway.py` (17): end to end through HTTP: response shape, request id propagation, 400, 401, 403, 429 then recovery, 402 once spend runs out, PII never reaches the upstream, raw prompts never reach the database, cache hits skip the upstream and cost $0, per-team cache, retry, fallback, 502, `/metrics` output, `/admin/usage` and the dashboard, `/healthz`.

CI (`.github/workflows/ci.yml`) runs ruff and pytest, then builds the Docker image, starts it, and sends one request through it.

## Deploy

Infrastructure as code lives in `deploy/`: Terraform for AWS (ECS Fargate), and a Helm chart for teams that run Kubernetes. Nothing here has been applied to a real AWS account or cluster. See "What was validated" below.

### AWS architecture

```
 internal apps                         VPC (2 AZs)
 (office/VPN CIDRs) ──443──▶ ┌──────────────────────────────────────────────────────────┐
                             │ public subnets    ALB (HTTPS listener if certificate_arn │
                             │                   is set, port 80 redirects)             │
                             │                   target group health check: /healthz    │
                             │                        │ :8080, from the ALB SG only     │
                             │ private subnets   ECS Fargate service, 1 to N tasks      │
                             │                   read-only root FS, user 10001          │
                             │                   autoscaling on CPU and requests/target │
                             │                        │ 443 out only                    │
                             │ public subnets    NAT gateway (optional)                 │
                             └────────────────────────┼─────────────────────────────────┘
                                                      ▼
                                              Anthropic API

 Secrets Manager ──(execution role, at task start)──▶ ANTHROPIC_API_KEY, GATEWAY_CONFIG_YAML
 ECR (scan on push, immutable tags, lifecycle) ──▶ image pull
 tasks ──awslogs──▶ CloudWatch Logs (/ecs/<name>, retention set per environment)
```

| Module | What it creates |
|---|---|
| `deploy/terraform/modules/network` | VPC, two public and two private subnets across two AZs, internet gateway, one NAT gateway behind `enable_nat_gateway`, a locked down default security group |
| `deploy/terraform/modules/secrets` | Two Secrets Manager secrets: the Anthropic API key and the gateway config (team keys, budgets, limits). Terraform creates the containers only, so values never enter state |
| `deploy/terraform/modules/service` | ECR repository, ECS cluster, task definition with the container health check, CloudWatch log group, ALB and target group, security groups, IAM roles, autoscaling |

Design choices worth knowing:

- **Secrets** reach the container through the task definition's `secrets` block, never `environment`. The gateway reads its team config from a file, so the container start command writes `GATEWAY_CONFIG_YAML` to `/tmp/gateway.yaml`, unsets the variable, then starts the app. No Python code changed for this.
- **IAM** is split in two. The execution role (used by the ECS agent) can pull from this one ECR repository, write to this one log group, and read these two secrets. The task role (what the app runs as) has no permissions, because the gateway makes no AWS calls. Both trust policies are limited to ECS tasks in the same account.
- **Without NAT** (`enable_nat_gateway = false`, staging default), tasks run in the public subnets with a public IP so they can still reach ECR and the Anthropic API. Their security group accepts traffic only from the ALB, so they are not reachable from the internet. Prod uses private subnets and the NAT gateway.
- **Deploys** use the ECS circuit breaker with automatic rollback, 100% minimum healthy, and image tags that cannot be overwritten (`latest` is rejected by a validation rule).
- **Tags**: every resource gets `Project`, `Environment`, `ManagedBy` and `Repository` through the provider's `default_tags`.

### The audit log on Fargate

The SQLite audit log does **not** survive a task restart on Fargate. `/app/data` is task-local scratch storage, so a new task starts with an empty log, and that also resets each team's month spend, which the budget check reads from that log. With more than one task, each task also has its own log and its own view of spend.

I chose not to add an EFS volume. The gateway opens SQLite in WAL mode, and WAL relies on shared memory that does not work across network file systems, so EFS would trade a visible problem for silent corruption risk. The production path is **RDS for PostgreSQL** (Multi-AZ in prod) as the audit store, which also gives every task one shared view of spend. That is a code change in `audit.py` (a Postgres implementation of the same insert and month-spend queries) plus a `database` module here; neither is in this repo yet. Until then, treat the AWS deployment as stateless and the budgets as per-task and best effort.

### Commands

Requires Terraform 1.6 or newer (the tests need 1.7 or newer), AWS credentials, and an existing S3 bucket and DynamoDB table for state.

```bash
cd deploy/terraform
cp backend.hcl.example backend.hcl          # fill in bucket, key, region, lock table
terraform init -backend-config=backend.hcl

# 1. Create the ECR repository and secrets first, so there is somewhere to push.
terraform apply -var-file=environments/staging.tfvars \
  -target=module.service.aws_ecr_repository.this -target=module.secrets

# 2. Fill the secrets (values go straight to AWS, never into Terraform state).
aws secretsmanager put-secret-value --secret-id ai-gateway-staging/anthropic-api-key \
  --secret-string "$ANTHROPIC_API_KEY"
aws secretsmanager put-secret-value --secret-id ai-gateway-staging/gateway-config \
  --secret-string file://../../config/gateway.yaml   # use real team keys, not the demo ones

# 3. Build and push an image tagged with the git SHA (CI would normally do this).
REPO=$(terraform output -raw ecr_repository_url)
TAG=$(git rev-parse --short HEAD)
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build -t "$REPO:$TAG" ../.. && docker push "$REPO:$TAG"

# 4. Everything else.
terraform apply -var-file=environments/staging.tfvars -var image_tag="$TAG"
curl -s "$(terraform output -raw gateway_url)/healthz"
```

Prod is the same with `environments/prod.tfvars`, a separate state key in `backend.hcl`, and `-var certificate_arn=...` (a check block warns if prod is planned without one).

Kubernetes instead of ECS:

```bash
kubectl create secret generic ai-gateway \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --from-file=gateway.yaml=config/gateway.yaml
helm install gw deploy/helm/ai-gateway \
  --set image.repository=<registry>/ai-gateway --set image.tag="$TAG" \
  --set metrics.serviceMonitor.enabled=true    # if the Prometheus Operator is installed
```

The chart runs pods as user 10001 with a read-only root filesystem, all capabilities dropped, and no service account token. It adds startup, readiness and liveness probes on `/healthz`, an HPA (2 to 6 pods on CPU), a PodDisruptionBudget, zone spreading, and an optional ServiceMonitor. Secrets are referenced by name (`existingSecret`); the chart never creates them. Every value is documented in `deploy/helm/ai-gateway/values.yaml`.

### Rough monthly cost, staging

About **$40 a month**, or about **$74 with the NAT gateway on**. Assumptions: us-east-1 on-demand list prices, the `staging.tfvars` shape (one task at 0.25 vCPU and 0.5 GB running all month, no NAT, internet-facing ALB), light traffic, and no Claude API usage (that is billed separately and is the real cost driver).

| Item | Basis | Per month |
|---|---|---|
| ALB | $0.0225 per hour plus under 1 LCU | about $18 |
| Fargate | 0.25 vCPU at $0.04048 and 0.5 GB at $0.004445 per hour | about $9 |
| Public IPv4 | 2 for the ALB, 1 for the task, $0.005 per hour each | about $11 |
| Secrets Manager | 2 secrets at $0.40 | under $1 |
| CloudWatch Logs, ECR, data transfer | about 1 GB of logs, a few image versions | about $2 |
| NAT gateway (if on) | $0.045 per hour plus $0.045 per GB processed | adds about $33 plus data |

Check current prices in the AWS Pricing Calculator before relying on these.

### What was validated, and what was not

Run locally on macOS with official binaries (SHA256 checked against the published sums): Terraform 1.17.1 and Helm 4.3.1.

- `terraform fmt -check -recursive`: clean.
- `terraform init -backend=false` and `terraform validate`: valid, with AWS provider 5.100.0 (lock file committed with hashes for linux_amd64, darwin_amd64 and darwin_arm64).
- `terraform test` (`deploy/terraform/tests/wiring.tftest.hcl`): 4 runs pass with each of `staging.tfvars` and `prod.tfvars`, against a **mocked** AWS provider. They check two public and two private subnets, no NAT gateway in staging and one in prod, an HTTP URL without a certificate and HTTPS with one, and that `image_tag = "latest"` and an unknown environment are rejected.
- `helm lint --strict` (with the ServiceMonitor on and off) and `helm template`: pass, and the rendered manifests parse as YAML.

Not validated: no `terraform plan` or `apply` against a real AWS account (so AWS has not confirmed the resources, quotas, or IAM policies), no install on a real Kubernetes cluster, and no Docker build on this machine. tflint and checkov run only in CI (`.github/workflows/infra.yml`), with checkov set to report without failing.

```bash
make infra-check    # terraform fmt, validate, test, and helm lint (needs terraform and helm on PATH)
```

## Production notes

- Rate limit buckets and the cache live in process memory. With more than one replica, move both to Redis (a Lua script for the token bucket) so limits hold across instances.
- Gateway keys sit in plain text in the config for the demo. In production, store only key hashes, issue keys through a secret manager, and add rotation and expiry. Put `/admin` behind SSO.
- SQLite with a trigger is enough to show append-only intent. At scale, ship audit events to a queue and into a warehouse or WORM object storage, and reconcile spend daily against the provider's invoice.
- Regex redaction catches structured PII only. Names, addresses, and free-text health data need an NER model or a DLP service, plus a per-team policy for block versus redact.
- Budget checks race when one team sends many requests at once. A real gateway reserves the estimate atomically, then settles the actual cost.
- Add streaming (server-sent events) passthrough, prompt caching headers, and per-team model routing rules before teams would adopt it.

## License

MIT. See [LICENSE](LICENSE).
