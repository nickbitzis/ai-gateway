import random

import pytest
from conftest import FlakyProvider

from aigateway.providers import AnthropicProvider, MockProvider, UpstreamError, backoff_delay, call_with_retry

MSGS = [{"role": "user", "content": "hello"}]


def test_mock_is_deterministic():
    a = MockProvider().complete("claude-haiku-5-5", None, MSGS, 300)
    b = MockProvider().complete("claude-haiku-5-5", None, MSGS, 300)
    assert a == b
    assert 0 < a.output_tokens <= 300 and a.input_tokens > 0


def test_backoff_is_capped_full_jitter():
    rng = random.Random(1)
    for attempt in range(10):
        d = backoff_delay(attempt, 0.2, 4.0, rng)
        assert 0 <= d <= min(4.0, 0.2 * 2**attempt)


def test_retry_then_success():
    p = FlakyProvider(failures=[429, 503])
    sleeps = []
    out = call_with_retry(p, "claude-sonnet-5-5", None, MSGS, 100, max_retries=3, sleep=sleeps.append)
    assert out.attempts == 3 and not out.used_fallback
    assert len(sleeps) == 2


def test_non_retryable_raises_immediately():
    p = FlakyProvider(failures=[400])
    with pytest.raises(UpstreamError) as e:
        call_with_retry(p, "claude-sonnet-5-5", None, MSGS, 100, sleep=lambda s: None)
    assert e.value.status == 400 and len(p.calls) == 1


def test_fallback_model_after_retries_exhausted():
    p = FlakyProvider(fail_models={"claude-sonnet-5-5"})
    out = call_with_retry(
        p, "claude-sonnet-5-5", None, MSGS, 100, max_retries=2, fallback_model="claude-haiku-5-5", sleep=lambda s: None
    )
    assert out.used_fallback and out.result.model == "claude-haiku-5-5"
    assert out.attempts == 4  # 3 on primary, 1 on fallback


class _Block:
    type = "text"
    text = "hi there"


class _Usage:
    input_tokens = 12
    output_tokens = 3


class _Resp:
    id = "msg_123"
    model = "claude-opus-5-5"
    content = [_Block()]
    usage = _Usage()
    stop_reason = "end_turn"


class _FakeClient:
    def __init__(self, exc=None):
        self.exc = exc
        self.kwargs = None
        self.messages = self

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return _Resp()


def test_anthropic_provider_maps_sdk_response():
    client = _FakeClient()
    r = AnthropicProvider(client).complete("claude-opus-5-5", "be brief", MSGS, 50, temperature=0)
    assert r.text == "hi there" and r.input_tokens == 12 and r.output_tokens == 3
    assert client.kwargs["system"] == "be brief" and client.kwargs["temperature"] == 0


def test_anthropic_provider_maps_errors_to_status():
    exc = Exception("overloaded")
    exc.status_code = 529
    with pytest.raises(UpstreamError) as e:
        AnthropicProvider(_FakeClient(exc)).complete("claude-opus-5-5", None, MSGS, 50)
    assert e.value.status == 529


def test_mock_latency_is_simulated_and_deterministic():
    slept = []
    p = MockProvider(latency_ms=(40, 160), sleep=slept.append)
    p.complete("claude-opus-5-5", None, MSGS, 300)
    p.complete("claude-opus-5-5", None, MSGS, 300)
    assert len(slept) == 2 and slept[0] == slept[1]
    assert 0.04 <= slept[0] <= 0.16 + 0.3 * 300 / 1000
