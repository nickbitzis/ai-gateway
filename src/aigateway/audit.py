"""Append-only audit log in SQLite. Stores a hash of the prompt, never the prompt itself."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    request_id TEXT NOT NULL,
    team TEXT NOT NULL,
    model TEXT,
    status INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    redactions TEXT NOT NULL DEFAULT '{}',
    prompt_sha256 TEXT,
    upstream_attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_audit_team_ts ON audit_log(team, ts);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


@dataclass
class AuditRecord:
    request_id: str
    team: str
    model: str | None
    status: int
    outcome: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cache_hit: bool = False
    redactions: dict = field(default_factory=dict)
    prompt_sha256: str | None = None
    upstream_attempts: int = 0
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds"))


def month_start(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="milliseconds")


class AuditLog:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None

    def append(self, r: AuditRecord) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO audit_log (ts, request_id, team, model, status, outcome, input_tokens,
                   output_tokens, cost_usd, latency_ms, cache_hit, redactions, prompt_sha256, upstream_attempts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.ts,
                    r.request_id,
                    r.team,
                    r.model,
                    r.status,
                    r.outcome,
                    r.input_tokens,
                    r.output_tokens,
                    r.cost_usd,
                    r.latency_ms,
                    int(r.cache_hit),
                    json.dumps(dict(r.redactions), sort_keys=True),
                    r.prompt_sha256,
                    r.upstream_attempts,
                ),
            )

    def month_spend(self, team: str, now: datetime | None = None) -> float:
        with self.lock:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM audit_log WHERE team = ? AND ts >= ?",
                (team, month_start(now)),
            ).fetchone()
        return float(row[0])

    def recent(self, limit: int = 25) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["redactions"] = json.loads(d["redactions"])
            d["cache_hit"] = bool(d["cache_hit"])
            out.append(d)
        return out

    def usage_by_team(self, now: datetime | None = None) -> dict[str, dict]:
        with self.lock:
            rows = self.conn.execute(
                """SELECT team, COUNT(*) AS requests, SUM(cost_usd) AS spend,
                   SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
                   SUM(cache_hit) AS cache_hits, SUM(status = 200) AS ok,
                   SUM(status = 429) AS rate_limited, SUM(status = 402) AS budget_blocked,
                   SUM(status IN (401, 403)) AS denied, SUM(status >= 500) AS upstream_errors
                   FROM audit_log WHERE ts >= ? GROUP BY team""",
                (month_start(now),),
            ).fetchall()
            red_rows = self.conn.execute(
                "SELECT team, redactions FROM audit_log WHERE ts >= ? AND redactions != '{}'",
                (month_start(now),),
            ).fetchall()
        out = {row["team"]: {k: (row[k] or 0) for k in row.keys() if k != "team"} for row in rows}
        for row in red_rows:
            reds = out[row["team"]].setdefault("redactions", {})
            for kind, n in json.loads(row["redactions"]).items():
                reds[kind] = reds.get(kind, 0) + n
        return out

    def count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
