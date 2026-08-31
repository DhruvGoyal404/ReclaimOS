"""Idempotency claims under contention.

The single-threaded tests here are table stakes. The one that matters is
``test_only_one_of_many_racing_claimants_wins``: a claim that is merely *usually*
exclusive is a duplicate charge waiting for a busy afternoon.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from reclaimos.domain import ActionType
from reclaimos.store import (
    Database,
    InMemoryIdempotencyStore,
    ResultAlreadyRecorded,
    SqliteIdempotencyStore,
    idempotency_key,
)
from reclaimos.store.idempotency import IdempotencyStore

RACERS = 64


def _stores(db: Database) -> list[IdempotencyStore]:
    return [SqliteIdempotencyStore(db), InMemoryIdempotencyStore()]


# --- the key itself ----------------------------------------------------------


def test_the_key_is_deterministic_and_reconstructible() -> None:
    """A replay must rebuild the same key without remembering anything, or a
    crash between claiming and executing loses the guarantee (ADR-0004)."""
    a = idempotency_key("sub_ABC", 2, ActionType.RETRY_CHARGE)
    b = idempotency_key("sub_ABC", 2, ActionType.RETRY_CHARGE)
    assert a == b == "sub_ABC:2:retry_charge"


def test_the_key_separates_attempts_actions_and_subscriptions() -> None:
    keys = {
        idempotency_key("sub_A", 1, ActionType.RETRY_CHARGE),
        idempotency_key("sub_A", 2, ActionType.RETRY_CHARGE),
        idempotency_key("sub_A", 1, ActionType.SEND_PAYMENT_LINK),
        idempotency_key("sub_B", 1, ActionType.RETRY_CHARGE),
    }
    assert len(keys) == 4


# --- claiming ----------------------------------------------------------------


def test_a_key_can_be_claimed_exactly_once(db: Database) -> None:
    for store in _stores(db):
        assert store.claim("sub_A:1:retry_charge") is True
        assert store.claim("sub_A:1:retry_charge") is False
        assert store.count() == 1


def test_different_keys_do_not_collide(db: Database) -> None:
    for store in _stores(db):
        assert store.claim("sub_A:1:retry_charge")
        assert store.claim("sub_A:2:retry_charge")
        assert store.count() == 2


def test_an_unclaimed_key_reads_back_as_nothing(db: Database) -> None:
    for store in _stores(db):
        assert store.get("never_claimed") is None


def test_only_one_of_many_racing_claimants_wins(db: Database) -> None:
    """The test the whole component exists for.

    Sixty-four threads start together on a barrier and race for one key. Exactly
    one must win. Anything else is a duplicate charge in production.
    """
    for store in _stores(db):
        barrier = threading.Barrier(RACERS)
        key = "sub_RACE:1:retry_charge"

        def attempt(
            _: int,
            store: IdempotencyStore = store,
            barrier: threading.Barrier = barrier,
            key: str = key,
        ) -> bool:
            barrier.wait()
            return store.claim(key)

        with ThreadPoolExecutor(max_workers=RACERS) as pool:
            results = list(pool.map(attempt, range(RACERS)))

        assert sum(results) == 1, f"{sum(results)} winners in {type(store).__name__}"
        assert store.count() == 1


# --- results are write-once ---------------------------------------------------


def test_a_result_can_be_recorded_and_read_back(db: Database) -> None:
    for store in _stores(db):
        store.claim("k")
        assert store.get("k") is not None
        assert store.get("k").completed is False  # type: ignore[union-attr]

        store.record_result("k", {"payment_id": "pay_1", "amount_paise": 49900})
        claim = store.get("k")
        assert claim is not None and claim.completed
        assert claim.result == {"payment_id": "pay_1", "amount_paise": 49900}


def test_a_result_cannot_be_revised(db: Database) -> None:
    """Overwriting a result would let a replay rewrite what the first execution
    actually did -- which is the specific lie the audit trail must not tell."""
    for store in _stores(db):
        store.claim("k")
        store.record_result("k", {"payment_id": "pay_1"})
        with pytest.raises(ResultAlreadyRecorded, match="already recorded"):
            store.record_result("k", {"payment_id": "pay_IMPOSTOR"})
        claim = store.get("k")
        assert claim is not None and claim.result == {"payment_id": "pay_1"}


def test_recording_a_result_for_an_unclaimed_key_is_refused(db: Database) -> None:
    for store in _stores(db):
        with pytest.raises(ResultAlreadyRecorded, match="no claim exists"):
            store.record_result("never_claimed", {"payment_id": "pay_1"})


def test_the_database_refuses_a_result_overwrite_even_by_raw_sql(db: Database) -> None:
    """Defence in depth: the Python guard and the trigger are independent."""
    import sqlite3

    store = SqliteIdempotencyStore(db)
    store.claim("k")
    store.record_result("k", {"payment_id": "pay_1"})
    with pytest.raises(sqlite3.IntegrityError, match="write-once"):
        db.connection.execute("UPDATE idempotency SET result = '{}' WHERE key = 'k'")


def test_claims_cannot_be_deleted(db: Database) -> None:
    import sqlite3

    SqliteIdempotencyStore(db).claim("k")
    with pytest.raises(sqlite3.IntegrityError, match="permanent"):
        db.connection.execute("DELETE FROM idempotency")


def test_money_floats_cannot_reach_a_recorded_result(db: Database) -> None:
    from reclaimos.store import NonCanonicalPayload

    store = SqliteIdempotencyStore(db)
    store.claim("k")
    with pytest.raises(NonCanonicalPayload, match="integer paise"):
        store.record_result("k", {"amount_paise": 499.0})


def test_both_backends_satisfy_the_protocol(db: Database) -> None:
    for store in _stores(db):
        assert isinstance(store, IdempotencyStore)
