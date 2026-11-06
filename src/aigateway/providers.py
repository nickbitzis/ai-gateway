"""Upstream providers behind one interface, plus retry with backoff and an optional fallback model."""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .policies import prompt_text
from .pricing import estimate_input_tokens

RETRYABLE = {429, 500, 502, 503, 504, 529}


@dataclass
class UpstreamResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str = "end_turn"
    upstream_id: str = ""


class UpstreamError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"upstream {status}: {message}")
        self.status = status


class Provider(Protocol):
    name: str

    def complete(self, model: str, system, messages: list[dict], max_tokens: int, **params) -> UpstreamResult: ...


class MockProvider:
    """Deterministic offline provider. Same request in, same answer and token counts out."""

    name = "mock"

    WORDS = (
        "the team should batch requests, cache stable prompts, and route simple work to a smaller model "
        "while keeping an audit trail for every call and a budget alert at eighty percent of spend"
    ).split()

    def __init__(self, latency_ms: tuple[int, int] | None = None, sleep: Callable[[float], None] = time.sleep):
        self.latency_ms = latency_ms  # simulated upstream latency range, off by default (tests)
        self.sleep = sleep

    def complete(self, model, system, messages, max_tokens, **params) -> UpstreamResult:
        text_in = prompt_text(system, messages)
        digest = hashlib.sha256(f"{model}|{text_in}".encode()).digest()
        if self.latency_ms:
            lo, hi = self.latency_ms
            per_token = 0.3 if "opus" in model else 0.15 if "sonnet" in model else 0.05
            self.sleep((lo + (hi - lo) * digest[1] / 255 + per_token * min(max_tokens, 60 + digest[0] * 2)) / 1000)
        n_out = min(max_tokens, 60 + digest[0] * 2)  # 60 to 570 tokens
        n_words = max(5, min(60, n_out // 6))
        words = [self.WORDS[(digest[i % 32] + i) % len(self.WORDS)] for i in range(n_words)]
        text = "Mock answer: " + " ".join(words) + "."
        return UpstreamResult(
            text=text,
            model=model,
            input_tokens=estimate_input_tokens(text_in),
            output_tokens=n_out,
            upstream_id="msg_mock_" + digest.hex()[:16],
        )


class AnthropicProvider:
    """Real Claude API via the official SDK. Imported lazily so the gateway runs without it."""

    name = "anthropic"

    def __init__(self, client=None):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on install extras
                raise RuntimeError("Install the Claude extra first: uv sync --extra claude") from exc
            client = anthropic.Anthropic(max_retries=0)  # the gateway owns retries
        self.client = client

    def complete(self, model, system, messages, max_tokens, **params) -> UpstreamResult:
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages, **params}
        if system:
            kwargs["system"] = system
        try:
            resp = self.client.messages.create(**kwargs)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status is None:
                status = 503 if "connect" in type(exc).__name__.lower() else 500
            raise UpstreamError(status, str(exc)) from exc
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return UpstreamResult(
            text=text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            stop_reason=resp.stop_reason or "end_turn",
            upstream_id=resp.id,
        )


def make_provider(name: str, mock_latency_ms: tuple[int, int] | None = None) -> Provider:
    if name == "mock":
        return MockProvider(latency_ms=mock_latency_ms)
    if name == "anthropic":
        return AnthropicProvider()
    raise ValueError(f"unknown provider {name!r}")


def backoff_delay(attempt: int, base: float, cap: float, rng: random.Random) -> float:
    """Full jitter: uniform between 0 and min(cap, base * 2**attempt)."""
    return rng.uniform(0, min(cap, base * (2**attempt)))


@dataclass
class CallOutcome:
    result: UpstreamResult
    attempts: int
    used_fallback: bool


def call_with_retry(
    provider: Provider,
    model: str,
    system,
    messages: list[dict],
    max_tokens: int,
    params: dict | None = None,
    *,
    max_retries: int = 3,
    base: float = 0.2,
    cap: float = 4.0,
    fallback_model: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> CallOutcome:
    """Try the model with retries on 429/5xx. If it still fails, try the fallback model once through
    the same retry loop. Non-retryable errors (400, 401, ...) are raised right away."""
    rng = rng or random.Random()
    params = params or {}
    attempts = 0
    models = [model] + ([fallback_model] if fallback_model and fallback_model != model else [])
    last: UpstreamError | None = None
    for i, m in enumerate(models):
        for attempt in range(max_retries + 1):
            attempts += 1
            try:
                res = provider.complete(m, system, messages, max_tokens, **params)
                return CallOutcome(res, attempts, used_fallback=i > 0)
            except UpstreamError as exc:
                last = exc
                if exc.status not in RETRYABLE:
                    raise
                if attempt < max_retries:
                    sleep(backoff_delay(attempt, base, cap, rng))
    assert last is not None
    raise last
