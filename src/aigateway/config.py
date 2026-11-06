"""Load and validate the gateway config (teams, budgets, limits, upstream settings)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .pricing import PRICES


@dataclass(frozen=True)
class Team:
    name: str
    key: str
    monthly_budget_usd: float
    requests_per_minute: int
    allowed_models: tuple[str, ...]


@dataclass(frozen=True)
class UpstreamConfig:
    provider: str = "mock"
    max_retries: int = 3
    backoff_base_s: float = 0.2
    backoff_max_s: float = 4.0
    fallback_model: str | None = None
    mock_latency_ms: tuple[int, int] | None = None


@dataclass(frozen=True)
class CacheConfig:
    ttl_s: float = 600
    max_entries: int = 5000


@dataclass(frozen=True)
class GatewayConfig:
    teams: tuple[Team, ...]
    upstream: UpstreamConfig = field(default_factory=UpstreamConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)

    def team_by_key(self, key: str) -> Team | None:
        for team in self.teams:
            if team.key == key:
                return team
        return None


class ConfigError(ValueError):
    pass


def parse_config(raw: dict) -> GatewayConfig:
    teams = []
    seen_keys: set[str] = set()
    for t in raw.get("teams", []):
        models = tuple(t.get("allowed_models", []))
        unknown = [m for m in models if m not in PRICES]
        if unknown:
            raise ConfigError(f"team {t.get('name')}: unknown models {unknown}")
        if t["key"] in seen_keys:
            raise ConfigError(f"duplicate key for team {t['name']}")
        seen_keys.add(t["key"])
        if int(t["requests_per_minute"]) <= 0:
            raise ConfigError(f"team {t['name']}: requests_per_minute must be positive")
        teams.append(
            Team(
                name=t["name"],
                key=t["key"],
                monthly_budget_usd=float(t["monthly_budget_usd"]),
                requests_per_minute=int(t["requests_per_minute"]),
                allowed_models=models,
            )
        )
    if not teams:
        raise ConfigError("config has no teams")
    up_raw = dict(raw.get("upstream", {}))
    if up_raw.get("mock_latency_ms"):
        up_raw["mock_latency_ms"] = tuple(up_raw["mock_latency_ms"])
    up = UpstreamConfig(**up_raw)
    if up.fallback_model and up.fallback_model not in PRICES:
        raise ConfigError(f"unknown fallback model {up.fallback_model}")
    return GatewayConfig(teams=tuple(teams), upstream=up, cache=CacheConfig(**raw.get("cache", {})))


def load_config(path: str | Path) -> GatewayConfig:
    text = Path(path).read_text()
    raw = json.loads(text) if str(path).endswith(".json") else yaml.safe_load(text)
    return parse_config(raw)
