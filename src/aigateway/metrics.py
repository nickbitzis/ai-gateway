"""Prometheus text exposition without extra dependencies."""

from __future__ import annotations

import threading
from collections import defaultdict

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _labels(d: dict) -> str:
    if not d:
        return ""
    inner = ",".join(f'{k}="{str(v)}"' for k, v in sorted(d.items()))
    return "{" + inner + "}"


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = defaultdict(int)  # (team, status)
        self.tokens = defaultdict(int)  # (team, direction)
        self.cost = defaultdict(float)  # team
        self.cache_hits = defaultdict(int)  # team
        self.redactions = defaultdict(int)  # kind
        self.upstream_retries = 0
        self.fallbacks = 0
        self.bucket_counts = [0] * len(LATENCY_BUCKETS)
        self.latency_sum = 0.0
        self.latency_count = 0

    def observe(
        self,
        team,
        status,
        latency_s,
        input_tokens=0,
        output_tokens=0,
        cost=0.0,
        cache_hit=False,
        redactions=None,
        retries=0,
        fallback=False,
    ):
        with self.lock:
            self.requests[(team, str(status))] += 1
            self.tokens[(team, "input")] += input_tokens
            self.tokens[(team, "output")] += output_tokens
            self.cost[team] += cost
            if cache_hit:
                self.cache_hits[team] += 1
            for kind, n in (redactions or {}).items():
                self.redactions[kind] += n
            self.upstream_retries += retries
            self.fallbacks += int(fallback)
            for i, b in enumerate(LATENCY_BUCKETS):
                if latency_s <= b:
                    self.bucket_counts[i] += 1
            self.latency_sum += latency_s
            self.latency_count += 1

    def render(self) -> str:
        lines: list[str] = []
        with self.lock:

            def family(name, kind, help_text, samples):
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} {kind}")
                for labels, value in samples:
                    lines.append(f"{name}{_labels(labels)} {value}")

            family(
                "gateway_requests_total",
                "counter",
                "Requests by team and HTTP status.",
                [({"team": t, "status": s}, v) for (t, s), v in sorted(self.requests.items())],
            )
            family(
                "gateway_tokens_total",
                "counter",
                "Tokens billed by team and direction.",
                [({"team": t, "direction": d}, v) for (t, d), v in sorted(self.tokens.items())],
            )
            family(
                "gateway_cost_usd_total",
                "counter",
                "Spend in USD by team.",
                [({"team": t}, f"{v:.6f}") for t, v in sorted(self.cost.items())],
            )
            family(
                "gateway_cache_hits_total",
                "counter",
                "Responses served from cache by team.",
                [({"team": t}, v) for t, v in sorted(self.cache_hits.items())],
            )
            family(
                "gateway_redactions_total",
                "counter",
                "PII items redacted by type.",
                [({"type": k}, v) for k, v in sorted(self.redactions.items())],
            )
            family("gateway_upstream_retries_total", "counter", "Upstream retries.", [({}, self.upstream_retries)])
            family(
                "gateway_fallbacks_total", "counter", "Requests served by the fallback model.", [({}, self.fallbacks)]
            )
            lines.append("# HELP gateway_request_duration_seconds Request latency through the gateway.")
            lines.append("# TYPE gateway_request_duration_seconds histogram")
            for b, c in zip(LATENCY_BUCKETS, self.bucket_counts, strict=True):
                lines.append(f'gateway_request_duration_seconds_bucket{{le="{b}"}} {c}')
            lines.append(f'gateway_request_duration_seconds_bucket{{le="+Inf"}} {self.latency_count}')
            lines.append(f"gateway_request_duration_seconds_sum {self.latency_sum:.6f}")
            lines.append(f"gateway_request_duration_seconds_count {self.latency_count}")
        return "\n".join(lines) + "\n"
