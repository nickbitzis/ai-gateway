"""Policy steps. Each is a small function or class that raises PolicyError to stop a request."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from .config import GatewayConfig, Team
from .pii import redact_content


class PolicyError(Exception):
    """A request rejected by policy. Maps to an HTTP status and an Anthropic-style error type."""

    def __init__(self, status: int, error_type: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.message = message
        self.headers = headers or {}


# 1. Auth ---------------------------------------------------------------------------------------


def authenticate(config: GatewayConfig, key: str | None) -> Team:
    if not key:
        raise PolicyError(401, "authentication_error", "missing x-gateway-key header")
    team = config.team_by_key(key)
    if team is None:
        raise PolicyError(401, "authentication_error", "invalid gateway key")
    return team


# 2. Model allowlist ----------------------------------------------------------------------------


def check_model(team: Team, model: str) -> None:
    if model not in team.allowed_models:
        raise PolicyError(
            403,
            "permission_error",
            f"team '{team.name}' may not use model '{model}'. Allowed: {', '.join(team.allowed_models)}",
        )


# 3. Rate limit (token bucket per team) ----------------------------------------------------------


class TokenBucket:
    """Capacity = requests per minute. Refills continuously at rpm/60 tokens per second."""

    def __init__(self, rpm: int, clock: Callable[[], float] = time.monotonic):
        self.capacity = float(rpm)
        self.rate = rpm / 60.0
        self.tokens = float(rpm)
        self.clock = clock
        self.updated = clock()

    def take(self) -> float:
        """Take one token. Return 0 on success, or seconds to wait until one is available."""
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        return (1 - self.tokens) / self.rate


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.buckets: dict[str, TokenBucket] = {}
        self.lock = threading.Lock()

    def check(self, team: Team) -> None:
        with self.lock:
            bucket = self.buckets.get(team.name)
            if bucket is None:
                bucket = self.buckets[team.name] = TokenBucket(team.requests_per_minute, self.clock)
            wait = bucket.take()
        if wait > 0:
            retry_after = max(1, math.ceil(wait))
            raise PolicyError(
                429,
                "rate_limit_error",
                f"team '{team.name}' is over {team.requests_per_minute} requests per minute",
                headers={"Retry-After": str(retry_after)},
            )


# 4. Budget -------------------------------------------------------------------------------------


def check_budget(team: Team, spent_usd: float, estimate_usd: float) -> None:
    if spent_usd >= team.monthly_budget_usd:
        raise PolicyError(
            402,
            "budget_exhausted",
            f"team '{team.name}' has spent ${spent_usd:.4f} of its ${team.monthly_budget_usd:.2f} monthly budget",
        )
    if spent_usd + estimate_usd > team.monthly_budget_usd:
        raise PolicyError(
            402,
            "budget_exhausted",
            f"request could cost up to ${estimate_usd:.4f}; team '{team.name}' has "
            f"${team.monthly_budget_usd - spent_usd:.4f} left this month. Lower max_tokens or use a cheaper model.",
        )


# 5. PII redaction ------------------------------------------------------------------------------


@dataclass
class Redacted:
    system: object
    messages: list[dict]
    counts: Counter


def redact_request(system, messages: list[dict]) -> Redacted:
    counts: Counter = Counter()
    new_system = system
    if system:
        new_system, c = redact_content(system)
        counts += c
    new_messages = []
    for m in messages:
        content, c = redact_content(m.get("content", ""))
        counts += c
        new_messages.append({**m, "content": content})
    return Redacted(new_system, new_messages, counts)


def prompt_text(system, messages: list[dict]) -> str:
    """Flatten system and messages into plain text, for token estimates."""
    parts: list[str] = []

    def add(content):
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b.get("text", ""))

    if system:
        add(system)
    for m in messages:
        add(m.get("content", ""))
    return "\n".join(parts)


# 6. Response cache -----------------------------------------------------------------------------


def _normalize_content(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content.strip()}]
    return [{**b, "text": b["text"].strip()} if isinstance(b, dict) and b.get("type") == "text" else b for b in content]


def request_hash(body: dict) -> str:
    """Stable sha256 over the fields that change the answer. Whitespace and key order do not matter."""
    norm = {
        "model": body.get("model"),
        "max_tokens": body.get("max_tokens"),
        "temperature": body.get("temperature"),
        "system": _normalize_content(body["system"]) if body.get("system") else None,
        "messages": [
            {"role": m.get("role"), "content": _normalize_content(m.get("content", ""))}
            for m in body.get("messages", [])
        ],
    }
    blob = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


class ResponseCache:
    """Exact-match LRU cache with a TTL. Keyed per team so teams never see each other's answers."""

    def __init__(self, ttl_s: float = 600, max_entries: int = 5000, clock: Callable[[], float] = time.monotonic):
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self.clock = clock
        self.data: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self.lock:
            item = self.data.get(key)
            if item is None:
                return None
            stored_at, value = item
            if self.clock() - stored_at > self.ttl_s:
                del self.data[key]
                return None
            self.data.move_to_end(key)
            return value

    def put(self, key: str, value: dict) -> None:
        with self.lock:
            self.data[key] = (self.clock(), value)
            self.data.move_to_end(key)
            while len(self.data) > self.max_entries:
                self.data.popitem(last=False)
