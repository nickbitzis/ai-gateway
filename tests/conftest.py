from __future__ import annotations

import random

import pytest
from fastapi.testclient import TestClient

from aigateway.app import create_app
from aigateway.audit import AuditLog
from aigateway.config import parse_config
from aigateway.providers import MockProvider, UpstreamError

RAW = {
    "upstream": {"provider": "mock", "max_retries": 2, "backoff_base_s": 0.1, "fallback_model": "claude-haiku-5-5"},
    "cache": {"ttl_s": 60, "max_entries": 100},
    "teams": [
        {
            "name": "alpha",
            "key": "k-alpha",
            "monthly_budget_usd": 10.0,
            "requests_per_minute": 5,
            "allowed_models": ["claude-sonnet-5-5", "claude-haiku-5-5"],
        },
        {
            "name": "beta",
            "key": "k-beta",
            "monthly_budget_usd": 0.02,
            "requests_per_minute": 100,
            "allowed_models": ["claude-opus-5-5"],
        },
    ],
}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FlakyProvider:
    """Fails with the given statuses in order, then succeeds via the mock. Records calls."""

    name = "flaky"

    def __init__(self, failures=(), fail_models=()):
        self.failures = list(failures)
        self.fail_models = set(fail_models)
        self.calls: list[tuple[str, list]] = []
        self.inner = MockProvider()

    def complete(self, model, system, messages, max_tokens, **params):
        self.calls.append((model, messages))
        if model in self.fail_models:
            raise UpstreamError(503, "overloaded")
        if self.failures:
            raise UpstreamError(self.failures.pop(0), "fail")
        return self.inner.complete(model, system, messages, max_tokens, **params)


@pytest.fixture
def config():
    return parse_config(RAW)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def provider():
    return FlakyProvider()


@pytest.fixture
def audit():
    return AuditLog(":memory:")


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(config, provider, audit, clock, sleeps):
    app = create_app(config, provider, audit, clock=clock, sleep=sleeps.append, rng=random.Random(7))
    return TestClient(app)


def body(text="Summarize our Q3 churn drivers.", model="claude-sonnet-5-5", max_tokens=256, **extra):
    return {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": text}], **extra}
