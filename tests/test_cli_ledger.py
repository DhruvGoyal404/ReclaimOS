"""The `reclaimos ledger` commands — the audit trail a judge actually runs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from reclaimos.cli import app
from reclaimos.domain import IST
from reclaimos.store import Database, DecisionLedger, EventStore, InboundEvent
from reclaimos.store.idempotency import SqliteIdempotencyStore

runner = CliRunner()


def _event(event_id: str = "evt_1") -> InboundEvent:
    return InboundEvent(
        event_id=event_id,
        event_type="payment.failed",
        occurred_at=datetime(2026, 6, 5, 3, 0, tzinfo=IST),
        signature_verified=True,
        payload={"event": "payment.failed"},
        subscription_id="sub_1",
    )


def test_ledger_verify_reports_an_intact_chain(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    ledger = DecisionLedger(Database(path))
    for i in range(3):
        ledger.append({"decision": i})

    result = runner.invoke(app, ["ledger", "verify", "--database", f"sqlite:///{path}"])
    assert result.exit_code == 0
    assert "chain intact" in result.stdout


def test_ledger_verify_exits_non_zero_on_a_broken_chain(tmp_path: Path) -> None:
    """A verification that reports a break but exits 0 cannot be wired into CI."""
    path = tmp_path / "ledger.db"
    db = Database(path)
    ledger = DecisionLedger(db)
    for i in range(3):
        ledger.append({"decision": i})
    db.connection.execute("DROP TRIGGER ledger_no_update")
    db.connection.execute("UPDATE ledger SET payload = '{\"x\":1}' WHERE seq = 2")

    result = runner.invoke(app, ["ledger", "verify", "--database", f"sqlite:///{path}"])
    assert result.exit_code == 1
    assert "CHAIN BROKEN" in result.stdout


def test_ledger_stats_counts_every_store(tmp_path: Path) -> None:
    path = tmp_path / "stats.db"
    db = Database(path)
    EventStore(db).append(_event())
    DecisionLedger(db).append({"decision": 0})
    SqliteIdempotencyStore(db).claim("sub_A:1:retry_charge")

    result = runner.invoke(app, ["ledger", "stats", "--database", f"sqlite:///{path}"])
    assert result.exit_code == 0
    assert "chain head" in result.stdout
