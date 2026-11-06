import sqlite3
from datetime import UTC, datetime

import pytest

from aigateway.audit import AuditLog, AuditRecord


def test_append_only():
    log = AuditLog(":memory:")
    log.append(AuditRecord(request_id="r1", team="alpha", model="m", status=200, outcome="ok", cost_usd=0.5))
    with pytest.raises(sqlite3.DatabaseError):
        log.conn.execute("UPDATE audit_log SET cost_usd = 0")
    with pytest.raises(sqlite3.DatabaseError):
        log.conn.execute("DELETE FROM audit_log")
    assert log.count() == 1


def test_month_spend_ignores_last_month():
    log = AuditLog(":memory:")
    log.append(
        AuditRecord(
            request_id="r1",
            team="a",
            model="m",
            status=200,
            outcome="ok",
            cost_usd=1.0,
            ts="2026-10-31T23:59:59.000+00:00",
        )
    )
    log.append(
        AuditRecord(
            request_id="r2",
            team="a",
            model="m",
            status=200,
            outcome="ok",
            cost_usd=2.0,
            ts="2026-11-01T00:00:01.000+00:00",
        )
    )
    assert log.month_spend("a", now=datetime(2026, 11, 6, tzinfo=UTC)) == pytest.approx(2.0)
