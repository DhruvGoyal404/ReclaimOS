"""Canonical form, append-only enforcement, and the hash chain.

The claims this file has to actually prove, because the README makes them:
the ledger is tamper-evident, the stores refuse edits at the database level, and
concurrent writers cannot fork the chain.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from reclaimos.domain import IST, ActionType, DeclineClass
from reclaimos.store import (
    GENESIS_HASH,
    Database,
    DecisionLedger,
    EventStore,
    InboundEvent,
    NonCanonicalPayload,
    canonical_json,
    chain_hash,
)

# --- canonical form ---------------------------------------------------------


def test_key_order_does_not_change_the_canonical_form() -> None:
    """Two machines rendering the same decision must produce the same bytes."""
    a = canonical_json({"b": 1, "a": 2, "c": {"z": 1, "y": 2}})
    b = canonical_json({"c": {"y": 2, "z": 1}, "a": 2, "b": 1})
    assert a == b == '{"a":2,"b":1,"c":{"y":2,"z":1}}'


def test_datetimes_and_enums_encode_deterministically() -> None:
    out = canonical_json(
        {"at": datetime(2026, 6, 5, 3, 0, tzinfo=IST), "action": ActionType.RETRY_CHARGE}
    )
    assert '"action":"retry_charge"' in out
    assert "2026-06-05T03:00:00+05:30" in out


def test_non_ascii_survives_unescaped() -> None:
    """Hinglish dunning copy lands in these payloads; escaping it would make the
    ledger unreadable to the humans it exists for."""
    out = canonical_json({"message": "Aapka payment fail ho gaya — ₹499 due"})
    assert "₹499" in out and "\\u" not in out


def test_a_float_in_a_paise_field_is_refused() -> None:
    """The static test bans float money annotations; this catches the runtime case,
    where a dict built on the fly carries a rounding error into a permanent record."""
    with pytest.raises(NonCanonicalPayload, match="integer paise"):
        canonical_json({"amount_paise": 499.0})
    with pytest.raises(NonCanonicalPayload, match="integer paise"):
        canonical_json({"outer": [{"plan_amount_paise": 1.5}]})


def test_floats_are_fine_where_they_are_not_money() -> None:
    assert '"propensity":0.62' in canonical_json({"propensity": 0.62})


def test_unserialisable_values_fail_loudly() -> None:
    with pytest.raises(NonCanonicalPayload):
        canonical_json({"thing": object()})


def test_chain_hash_depends_on_both_inputs() -> None:
    base = chain_hash(GENESIS_HASH, '{"a":1}')
    assert base != chain_hash(GENESIS_HASH, '{"a":2}')
    assert base != chain_hash("f" * 64, '{"a":1}')
    assert base == chain_hash(GENESIS_HASH, '{"a":1}')
    assert len(base) == 64


# --- append-only, enforced by the database ----------------------------------


def _event(event_id: str = "evt_1") -> InboundEvent:
    return InboundEvent(
        event_id=event_id,
        event_type="payment.failed",
        occurred_at=datetime(2026, 6, 5, 3, 0, tzinfo=IST),
        signature_verified=True,
        payload={"event": "payment.failed"},
        subscription_id="sub_1",
    )


def test_events_cannot_be_updated_or_deleted(db: Database) -> None:
    """Omitting an update() method is a convention. RAISE(ABORT) is a rule."""
    EventStore(db).append(_event())
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.connection.execute("UPDATE events SET event_type = 'tampered'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.connection.execute("DELETE FROM events")


def test_ledger_rows_cannot_be_updated_or_deleted(db: Database) -> None:
    DecisionLedger(db).append({"decision": "stop"})
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.connection.execute("UPDATE ledger SET payload = '{}'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.connection.execute("DELETE FROM ledger")


# --- the event store ---------------------------------------------------------


def test_a_redelivered_event_does_not_become_a_second_fact(db: Database) -> None:
    store = EventStore(db)
    first, is_new = store.append(_event())
    again, is_new_again = store.append(_event())

    assert is_new and not is_new_again
    assert store.count() == 1
    assert again.seq == first.seq


def test_one_hundred_and_fifty_redeliveries_produce_one_row(db: Database) -> None:
    """Webhook delivery is at-least-once. Redelivery is traffic, not an error."""
    store = EventStore(db)
    for _ in range(150):
        store.append(_event())
    assert store.count() == 1


def test_distinct_events_are_all_kept_in_arrival_order(db: Database) -> None:
    store = EventStore(db)
    for i in range(5):
        store.append(_event(f"evt_{i}"))
    assert [e.event_id for e in store.all()] == [f"evt_{i}" for i in range(5)]
    assert [e.seq for e in store.all()] == sorted(e.seq for e in store.all())


def test_events_are_queryable_by_subscription(db: Database) -> None:
    store = EventStore(db)
    store.append(_event("evt_a"))
    other = _event("evt_b").model_copy(update={"subscription_id": "sub_2"})
    store.append(other)
    assert [e.event_id for e in store.for_subscription("sub_1")] == ["evt_a"]


def test_payload_round_trips_through_storage(db: Database) -> None:
    store = EventStore(db)
    payload = {"event": "payment.failed", "nested": {"amount_paise": 49900, "ok": True}}
    stored, _ = store.append(_event().model_copy(update={"payload": payload}))
    assert store.get(stored.event_id) is not None
    assert stored.payload == payload


# --- the hash chain ----------------------------------------------------------


def test_an_empty_ledger_heads_at_genesis(db: Database) -> None:
    ledger = DecisionLedger(db)
    assert ledger.head() == GENESIS_HASH
    assert ledger.verify().ok
    assert ledger.verify().entries == 0


def test_entries_link_head_to_tail(db: Database) -> None:
    ledger = DecisionLedger(db)
    first = ledger.append({"n": 1})
    second = ledger.append({"n": 2})

    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.entry_hash
    assert ledger.head() == second.entry_hash
    assert ledger.verify().ok


def test_verification_catches_an_edited_payload(db: Database) -> None:
    """Simulates an attacker with direct file access: drop the guard trigger and
    rewrite a row. The chain must still give them away."""
    ledger = DecisionLedger(db)
    for i in range(5):
        ledger.append({"decision": i, "class": DeclineClass.SOFT_INSUFFICIENT_FUNDS})
    assert ledger.verify().ok

    db.connection.execute("DROP TRIGGER ledger_no_update")
    db.connection.execute("UPDATE ledger SET payload = '{\"decision\":999}' WHERE seq = 3")

    report = ledger.verify()
    assert not report.ok
    assert report.breaks[0].seq == 3
    assert "entry_hash does not match" in report.breaks[0].reason
    assert "CHAIN BROKEN at entry 3" in report.summary()


def test_verification_catches_a_rewritten_link(db: Database) -> None:
    ledger = DecisionLedger(db)
    for i in range(4):
        ledger.append({"decision": i})

    db.connection.execute("DROP TRIGGER ledger_no_update")
    db.connection.execute("UPDATE ledger SET prev_hash = ? WHERE seq = 3", ("a" * 64,))

    report = ledger.verify()
    assert not report.ok
    assert any(b.seq == 3 and "prev_hash" in b.reason for b in report.breaks)


def test_deleting_a_row_breaks_the_chain(db: Database) -> None:
    ledger = DecisionLedger(db)
    for i in range(4):
        ledger.append({"decision": i})

    db.connection.execute("DROP TRIGGER ledger_no_delete")
    db.connection.execute("DELETE FROM ledger WHERE seq = 2")

    assert not ledger.verify().ok


def test_concurrent_appends_do_not_fork_the_chain(db: Database) -> None:
    """Two writers reading the same head would produce two branches that each
    verify locally and disagree globally. BEGIN IMMEDIATE is what prevents it."""
    ledger = DecisionLedger(db)

    def write(n: int) -> None:
        for i in range(5):
            ledger.append({"writer": n, "i": i})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))

    report = ledger.verify()
    assert report.ok, report.summary()
    assert report.entries == 40
    assert len({e.entry_hash for e in ledger.entries()}) == 40


def test_the_chain_exports_for_outside_inspection(db: Database, tmp_path: Path) -> None:
    ledger = DecisionLedger(db)
    for i in range(3):
        ledger.append({"decision": i})
    out = ledger.export_jsonl(tmp_path / "chain.jsonl")
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert '"prev_hash"' in lines[0] and '"entry_hash"' in lines[0]


# --- database plumbing --------------------------------------------------------


def test_from_url_accepts_sqlite(tmp_path: Path) -> None:
    db = Database.from_url(f"sqlite:///{tmp_path / 'x.db'}")
    assert db.path.exists()


def test_from_url_says_plainly_that_postgres_is_not_written_yet() -> None:
    with pytest.raises(NotImplementedError, match="not written yet"):
        Database.from_url("postgresql://reclaimos:reclaimos@localhost:5432/reclaimos")


def test_from_url_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="unsupported database URL"):
        Database.from_url("mysql://nope")
