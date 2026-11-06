"""FastAPI app: the /v1/messages endpoint runs every request through the policy pipeline."""

from __future__ import annotations

import os
import random
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Body, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from .audit import AuditLog, AuditRecord
from .config import GatewayConfig, load_config
from .logs import get_logger, log_event
from .metrics import Metrics
from .policies import (
    PolicyError,
    RateLimiter,
    ResponseCache,
    authenticate,
    check_budget,
    check_model,
    prompt_text,
    redact_request,
    request_hash,
)
from .pricing import cost_usd, estimate_cost_usd
from .providers import Provider, UpstreamError, call_with_retry, make_provider

STATIC = Path(__file__).parent / "static"
PASSTHROUGH = ("temperature", "top_p", "top_k", "stop_sequences")
MAX_TOKENS_LIMIT = 8192


def error_body(error_type: str, message: str) -> dict:
    return {"type": "error", "error": {"type": error_type, "message": message}}


def validate_body(body: dict) -> None:
    if not isinstance(body.get("model"), str):
        raise PolicyError(400, "invalid_request_error", "model is required")
    mt = body.get("max_tokens")
    if not isinstance(mt, int) or isinstance(mt, bool) or not 1 <= mt <= MAX_TOKENS_LIMIT:
        raise PolicyError(400, "invalid_request_error", f"max_tokens must be an integer from 1 to {MAX_TOKENS_LIMIT}")
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        raise PolicyError(400, "invalid_request_error", "messages must be a non-empty list")
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            raise PolicyError(400, "invalid_request_error", "each message needs role 'user' or 'assistant'")
        if not isinstance(m.get("content"), (str, list)):
            raise PolicyError(400, "invalid_request_error", "message content must be a string or list of blocks")


def create_app(
    config: GatewayConfig | None = None,
    provider: Provider | None = None,
    audit: AuditLog | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> FastAPI:
    config = config or load_config(os.environ.get("GATEWAY_CONFIG", "config/gateway.yaml"))
    provider = provider or make_provider(
        os.environ.get("GATEWAY_PROVIDER", config.upstream.provider), config.upstream.mock_latency_ms
    )
    if audit is None:
        db_path = os.environ.get("GATEWAY_DB", "data/audit.db")
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        audit = AuditLog(db_path)
    limiter = RateLimiter(clock)
    cache = ResponseCache(config.cache.ttl_s, config.cache.max_entries, clock)
    metrics = Metrics()
    logger = get_logger()
    started = datetime.now(UTC)

    app = FastAPI(title="AI Gateway", version="1.0.0")
    app.state.config, app.state.audit, app.state.metrics = config, audit, metrics
    app.state.cache, app.state.limiter, app.state.provider = cache, limiter, provider

    @app.middleware("http")
    async def request_id_mw(request: Request, call_next):
        rid = request.headers.get("x-request-id") or "req_" + uuid.uuid4().hex[:16]
        request.state.request_id = rid
        request.state.team = None
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        if request.url.path not in ("/healthz", "/metrics"):
            log_event(
                logger,
                "request",
                request_id=rid,
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                team=request.state.team,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError):
        return JSONResponse(error_body("invalid_request_error", "request body must be a JSON object"), 400)

    @app.post("/v1/messages")
    def messages(
        request: Request,
        body: dict = Body(...),  # noqa: B008
        x_gateway_key: str | None = Header(default=None),
    ):
        t0 = time.perf_counter()
        rid = request.state.request_id
        team_name, model = "unknown", body.get("model") if isinstance(body.get("model"), str) else None
        reds: Counter = Counter()
        phash = None

        def finish(status, outcome, *, result=None, cost=0.0, cache_hit=False, attempts=0, fallback=False):
            latency = time.perf_counter() - t0
            in_t = result["usage"]["input_tokens"] if result and not cache_hit else 0
            out_t = result["usage"]["output_tokens"] if result and not cache_hit else 0
            audit.append(
                AuditRecord(
                    request_id=rid,
                    team=team_name,
                    model=(result or {}).get("model", model),
                    status=status,
                    outcome=outcome,
                    input_tokens=in_t,
                    output_tokens=out_t,
                    cost_usd=cost,
                    latency_ms=round(latency * 1000, 2),
                    cache_hit=cache_hit,
                    redactions=dict(reds),
                    prompt_sha256=phash,
                    upstream_attempts=attempts,
                )
            )
            metrics.observe(
                team_name,
                status,
                latency,
                in_t,
                out_t,
                cost,
                cache_hit,
                dict(reds),
                retries=max(0, attempts - 1),
                fallback=fallback,
            )

        try:
            team = authenticate(config, x_gateway_key)
            team_name = request.state.team = team.name
            validate_body(body)
            check_model(team, model)
            limiter.check(team)

            red = redact_request(body.get("system"), body["messages"])
            reds = red.counts
            params = {k: body[k] for k in PASSTHROUGH if k in body}
            upstream_body = {
                "model": model,
                "max_tokens": body["max_tokens"],
                "system": red.system,
                "messages": red.messages,
                **params,
            }
            phash = request_hash(upstream_body)
            cache_key = f"{team.name}:{phash}"

            headers = {"x-gateway-redactions": str(sum(reds.values()))}
            cached = cache.get(cache_key)
            if cached is not None:
                finish(200, "cache_hit", result=cached, cache_hit=True)
                return JSONResponse(cached, headers={**headers, "x-gateway-cache": "hit", "x-gateway-cost-usd": "0"})

            spent = audit.month_spend(team.name)
            estimate = estimate_cost_usd(model, prompt_text(red.system, red.messages), body["max_tokens"])
            check_budget(team, spent, estimate)

            fb = config.upstream.fallback_model
            fb = fb if fb in team.allowed_models else None
            outcome = call_with_retry(
                provider,
                model,
                red.system,
                red.messages,
                body["max_tokens"],
                params,
                max_retries=config.upstream.max_retries,
                base=config.upstream.backoff_base_s,
                cap=config.upstream.backoff_max_s,
                fallback_model=fb,
                sleep=sleep,
                rng=rng,
            )
            r = outcome.result
            cost = cost_usd(r.model, r.input_tokens, r.output_tokens)
            result = {
                "id": r.upstream_id or "msg_" + uuid.uuid4().hex[:16],
                "type": "message",
                "role": "assistant",
                "model": r.model,
                "content": [{"type": "text", "text": r.text}],
                "stop_reason": r.stop_reason,
                "usage": {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens},
            }
            cache.put(cache_key, result)
            finish(
                200,
                "fallback" if outcome.used_fallback else "ok",
                result=result,
                cost=cost,
                attempts=outcome.attempts,
                fallback=outcome.used_fallback,
            )
            headers.update(
                {
                    "x-gateway-cache": "miss",
                    "x-gateway-cost-usd": f"{cost:.6f}",
                    "x-gateway-attempts": str(outcome.attempts),
                }
            )
            if outcome.used_fallback:
                headers["x-gateway-fallback"] = r.model
            return JSONResponse(result, headers=headers)

        except PolicyError as e:
            finish(e.status, e.error_type)
            return JSONResponse(error_body(e.error_type, e.message), e.status, headers=e.headers)
        except UpstreamError as e:
            finish(502, "upstream_error", attempts=config.upstream.max_retries + 1)
            log_event(logger, "upstream_failed", request_id=rid, team=team_name, upstream_status=e.status)
            return JSONResponse(error_body("api_error", f"upstream failed after retries (last status {e.status})"), 502)

    @app.get("/healthz")
    def healthz():
        return {
            "status": "ok",
            "provider": provider.name,
            "teams": len(config.teams),
            "audit_rows": audit.count(),
            "started_at": started.isoformat(timespec="seconds"),
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics_endpoint():
        return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")

    @app.get("/admin/usage")
    def usage():
        by_team = audit.usage_by_team()
        teams, total_ok, total_hits = [], 0, 0
        red_total: Counter = Counter()
        for t in config.teams:
            u = by_team.get(t.name, {})
            spend = float(u.get("spend", 0) or 0)
            total_ok += u.get("ok", 0)
            total_hits += u.get("cache_hits", 0)
            red_total.update(u.get("redactions", {}))
            teams.append(
                {
                    "team": t.name,
                    "budget_usd": t.monthly_budget_usd,
                    "spend_usd": round(spend, 6),
                    "budget_used_pct": round(100 * spend / t.monthly_budget_usd, 1) if t.monthly_budget_usd else 0,
                    "requests_per_minute": t.requests_per_minute,
                    "requests": u.get("requests", 0),
                    "ok": u.get("ok", 0),
                    "rate_limited": u.get("rate_limited", 0),
                    "budget_blocked": u.get("budget_blocked", 0),
                    "denied": u.get("denied", 0),
                    "upstream_errors": u.get("upstream_errors", 0),
                    "cache_hits": u.get("cache_hits", 0),
                    "input_tokens": u.get("input_tokens", 0),
                    "output_tokens": u.get("output_tokens", 0),
                    "redactions": u.get("redactions", {}),
                }
            )
        return {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "provider": provider.name,
            "teams": teams,
            "unauthenticated": by_team.get("unknown", {}).get("requests", 0),
            "cache_hit_rate": round(total_hits / total_ok, 3) if total_ok else 0.0,
            "redactions": dict(red_total),
            "recent": audit.recent(25),
        }

    @app.get("/")
    def dashboard():
        return FileResponse(STATIC / "index.html")

    return app
