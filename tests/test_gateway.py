"""End-to-end tests through the HTTP API with a fake upstream."""

from conftest import body

H_ALPHA = {"x-gateway-key": "k-alpha"}
H_BETA = {"x-gateway-key": "k-beta"}


def test_happy_path_returns_claude_shape(client):
    r = client.post("/v1/messages", json=body(), headers=H_ALPHA)
    assert r.status_code == 200
    j = r.json()
    assert j["type"] == "message" and j["content"][0]["type"] == "text"
    assert j["usage"]["input_tokens"] > 0
    assert r.headers["x-gateway-cache"] == "miss"
    assert r.headers["x-request-id"].startswith("req_")


def test_request_id_is_propagated(client):
    r = client.post("/v1/messages", json=body(), headers={**H_ALPHA, "x-request-id": "trace-42"})
    assert r.headers["x-request-id"] == "trace-42"
    assert client.app.state.audit.recent(1)[0]["request_id"] == "trace-42"


def test_401_without_key(client):
    r = client.post("/v1/messages", json=body())
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"


def test_403_model_not_allowed(client):
    r = client.post("/v1/messages", json=body(model="claude-opus-5-5"), headers=H_ALPHA)
    assert r.status_code == 403


def test_400_on_bad_body(client):
    r = client.post("/v1/messages", json={"model": "claude-sonnet-5-5", "messages": []}, headers=H_ALPHA)
    assert r.status_code == 400
    r = client.post("/v1/messages", content=b"not json", headers={**H_ALPHA, "content-type": "application/json"})
    assert r.status_code == 400


def test_429_with_retry_after_then_recovers(client, clock):
    codes = [client.post("/v1/messages", json=body(f"q{i}"), headers=H_ALPHA).status_code for i in range(6)]
    assert codes == [200] * 5 + [429]
    r = client.post("/v1/messages", json=body("again"), headers=H_ALPHA)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    clock.t += 12
    assert client.post("/v1/messages", json=body("later"), headers=H_ALPHA).status_code == 200


def test_402_when_budget_runs_out(client):
    statuses = []
    for i in range(10):
        r = client.post("/v1/messages", json=body(f"plan {i}", model="claude-opus-5-5", max_tokens=400), headers=H_BETA)
        statuses.append(r.status_code)
    assert 200 in statuses and 402 in statuses
    assert statuses.index(402) > 0 and all(s == 402 for s in statuses[statuses.index(402) :])
    spent = client.app.state.audit.month_spend("beta")
    assert spent <= 0.02


def test_pii_is_redacted_before_upstream(client, provider):
    text = "Customer jo@acme-widgets.example, SSN 123-45-6789, card 4111-1111-1111-1111, phone 415-555-0134."
    r = client.post("/v1/messages", json=body(text, system="Reply to bob@x.io"), headers=H_ALPHA)
    assert r.status_code == 200 and r.headers["x-gateway-redactions"] == "5"
    sent = provider.calls[-1][1][0]["content"]
    for secret in ("jo@acme", "123-45-6789", "4111-1111", "415-555"):
        assert secret not in sent
    row = client.app.state.audit.recent(1)[0]
    assert row["redactions"] == {"card": 1, "email": 2, "phone": 1, "ssn": 1}


def test_audit_never_stores_raw_prompt(client):
    client.post("/v1/messages", json=body("secret plan for Project Lantern"), headers=H_ALPHA)
    dump = "\n".join(client.app.state.audit.conn.iterdump())
    assert "Project Lantern" not in dump
    assert len(client.app.state.audit.recent(1)[0]["prompt_sha256"]) == 64


def test_cache_hit_costs_nothing_and_skips_upstream(client, provider):
    r1 = client.post("/v1/messages", json=body("Same question"), headers=H_ALPHA)
    r2 = client.post("/v1/messages", json=body("  Same question  "), headers=H_ALPHA)
    assert r1.json() == r2.json()
    assert r2.headers["x-gateway-cache"] == "hit" and r2.headers["x-gateway-cost-usd"] == "0"
    assert len(provider.calls) == 1
    assert client.app.state.audit.recent(1)[0]["cost_usd"] == 0


def test_cache_is_per_team(client):
    # Same prompt from two teams must not share a cache entry.
    client.post("/v1/messages", json=body("shared", model="claude-opus-5-5", max_tokens=50), headers=H_BETA)
    r = client.post("/v1/messages", json=body("shared", model="claude-opus-5-5", max_tokens=50), headers=H_BETA)
    assert r.headers["x-gateway-cache"] == "hit"
    r = client.post("/v1/messages", json=body("shared", max_tokens=50), headers=H_ALPHA)
    assert r.headers["x-gateway-cache"] == "miss"


def test_upstream_retry_then_success(client, provider, sleeps):
    provider.failures = [503, 429]
    r = client.post("/v1/messages", json=body("flaky"), headers=H_ALPHA)
    assert r.status_code == 200 and r.headers["x-gateway-attempts"] == "3"
    assert len(sleeps) == 2


def test_fallback_model_used_when_primary_down(client, provider):
    provider.fail_models = {"claude-sonnet-5-5"}
    r = client.post("/v1/messages", json=body("fallback please"), headers=H_ALPHA)
    assert r.status_code == 200
    assert r.json()["model"] == "claude-haiku-5-5"
    assert r.headers["x-gateway-fallback"] == "claude-haiku-5-5"


def test_502_when_upstream_and_fallback_fail(client, provider):
    provider.fail_models = {"claude-sonnet-5-5", "claude-haiku-5-5"}
    r = client.post("/v1/messages", json=body("down"), headers=H_ALPHA)
    assert r.status_code == 502 and r.json()["error"]["type"] == "api_error"


def test_metrics_exposition(client):
    client.post("/v1/messages", json=body("m1"), headers=H_ALPHA)
    client.post("/v1/messages", json=body("m1"), headers=H_ALPHA)
    client.post("/v1/messages", json=body())
    text = client.get("/metrics").text
    assert 'gateway_requests_total{status="200",team="alpha"} 2' in text
    assert 'gateway_requests_total{status="401",team="unknown"} 1' in text
    assert 'gateway_cache_hits_total{team="alpha"} 1' in text
    assert 'gateway_request_duration_seconds_bucket{le="+Inf"} 3' in text
    assert "# TYPE gateway_request_duration_seconds histogram" in text


def test_admin_usage_and_dashboard(client):
    client.post("/v1/messages", json=body("call me at 415-555-0134"), headers=H_ALPHA)
    client.post("/v1/messages", json=body("call me at 415-555-0134"), headers=H_ALPHA)
    client.post("/v1/messages", json=body(), headers={"x-gateway-key": "stolen"})
    u = client.get("/admin/usage").json()
    assert u["unauthenticated"] == 1
    alpha = next(t for t in u["teams"] if t["team"] == "alpha")
    assert alpha["requests"] == 2 and alpha["cache_hits"] == 1 and alpha["spend_usd"] > 0
    assert u["cache_hit_rate"] == 0.5
    assert u["redactions"] == {"phone": 2}
    assert len(u["recent"]) == 3
    page = client.get("/")
    assert page.status_code == 200 and "/admin/usage" in page.text


def test_healthz(client):
    j = client.get("/healthz").json()
    assert j["status"] == "ok" and j["teams"] == 2
