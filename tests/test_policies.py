import pytest
from conftest import FakeClock

from aigateway.config import ConfigError, load_config, parse_config
from aigateway.policies import (
    PolicyError,
    RateLimiter,
    ResponseCache,
    TokenBucket,
    authenticate,
    check_budget,
    check_model,
    request_hash,
)
from aigateway.pricing import cost_usd, estimate_cost_usd


def test_authenticate(config):
    assert authenticate(config, "k-alpha").name == "alpha"
    for key in (None, "", "nope"):
        with pytest.raises(PolicyError) as e:
            authenticate(config, key)
        assert e.value.status == 401


def test_model_allowlist(config):
    alpha = config.team_by_key("k-alpha")
    check_model(alpha, "claude-haiku-5-5")
    with pytest.raises(PolicyError) as e:
        check_model(alpha, "claude-opus-5-5")
    assert e.value.status == 403


def test_token_bucket_burst_then_refill():
    clock = FakeClock()
    b = TokenBucket(60, clock)  # 1 token per second
    assert all(b.take() == 0 for _ in range(60))
    wait = b.take()
    assert wait == pytest.approx(1.0)
    clock.t += 1.0
    assert b.take() == 0


def test_rate_limiter_sets_retry_after(config):
    clock = FakeClock()
    rl = RateLimiter(clock)
    alpha = config.team_by_key("k-alpha")  # 5 rpm, so one token per 12 s
    for _ in range(5):
        rl.check(alpha)
    with pytest.raises(PolicyError) as e:
        rl.check(alpha)
    assert e.value.status == 429
    assert e.value.headers["Retry-After"] == "12"


def test_rate_limits_are_per_team(config):
    rl = RateLimiter(FakeClock())
    alpha, beta = config.team_by_key("k-alpha"), config.team_by_key("k-beta")
    for _ in range(5):
        rl.check(alpha)
    rl.check(beta)  # beta has its own bucket


def test_pricing_table():
    assert cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(2.0)
    assert cost_usd("claude-haiku-5-5", 0, 1_000_000) == pytest.approx(0.5)
    assert estimate_cost_usd("claude-opus-5-5", "x" * 400, 1000) == pytest.approx((108 * 4 + 1000 * 20) / 1e6)


def test_budget_check(config):
    beta = config.team_by_key("k-beta")  # $0.02 budget
    check_budget(beta, 0.0, 0.01)
    with pytest.raises(PolicyError) as e:
        check_budget(beta, 0.015, 0.01)  # estimate would overshoot
    assert e.value.status == 402
    with pytest.raises(PolicyError):
        check_budget(beta, 0.02, 0.0)  # already spent


def test_request_hash_normalizes():
    a = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "hi  "}]}
    b = {"max_tokens": 5, "model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
    c = {"model": "m", "max_tokens": 6, "messages": [{"role": "user", "content": "hi"}]}
    assert request_hash(a) == request_hash(b)
    assert request_hash(a) != request_hash(c)


def test_cache_ttl_and_lru():
    clock = FakeClock()
    cache = ResponseCache(ttl_s=10, max_entries=2, clock=clock)
    cache.put("a", {"v": 1})
    cache.put("b", {"v": 2})
    cache.get("a")
    cache.put("c", {"v": 3})  # evicts b, the least recently used
    assert cache.get("b") is None and cache.get("a") == {"v": 1}
    clock.t += 11
    assert cache.get("a") is None


def test_config_rejects_unknown_model():
    with pytest.raises(ConfigError):
        parse_config(
            {
                "teams": [
                    {
                        "name": "x",
                        "key": "k",
                        "monthly_budget_usd": 1,
                        "requests_per_minute": 1,
                        "allowed_models": ["gpt-9"],
                    }
                ]
            }
        )


def test_shipped_config_loads():
    cfg = load_config("config/gateway.yaml")
    assert {t.name for t in cfg.teams} == {"checkout", "growth", "support"}
