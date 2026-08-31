"""Webhook ingestion: verify, then normalise, then append. In that order.

The ordering is the security property, so most of these tests are about what
happens *before* anything is parsed.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from reclaimos.ingest import (
    REJECTED_EVENT_TYPE,
    IngestResult,
    compute_signature,
    derive_event_id,
    ingest,
    verify_signature,
)
from reclaimos.store import Database, EventStore

SECRET = "whsec_test_reclaimos"


def _body(
    event: str = "payment.failed",
    payment_id: str = "pay_TEST01",
    subscription_id: str | None = "sub_TEST01",
    created_at: int = 1_780_000_000,
) -> bytes:
    payload: dict[str, Any] = {
        "payment": {
            "entity": {
                "id": payment_id,
                "amount": 49900,
                "currency": "INR",
                "error_code": "BAD_REQUEST_ERROR",
                "error_reason": "insufficient_funds",
            }
        }
    }
    if subscription_id:
        payload["subscription"] = {"entity": {"id": subscription_id}}
    raw = {
        "entity": "event",
        "account_id": "acc_TEST",
        "event": event,
        "payload": payload,
        "created_at": created_at,
    }
    return json.dumps(raw).encode("utf-8")


def _signed(body: bytes) -> str:
    return compute_signature(body, SECRET)


# --- signature verification ---------------------------------------------------


def test_a_correctly_signed_body_verifies() -> None:
    body = _body()
    assert verify_signature(body, _signed(body), SECRET)


def test_a_tampered_body_does_not_verify() -> None:
    body = _body()
    signature = _signed(body)
    assert not verify_signature(body.replace(b"49900", b"99900"), signature, SECRET)


def test_the_wrong_secret_does_not_verify() -> None:
    body = _body()
    assert not verify_signature(body, compute_signature(body, "whsec_wrong"), SECRET)


def test_a_missing_signature_is_a_rejection_not_an_exception() -> None:
    assert verify_signature(_body(), None, SECRET) is False
    assert verify_signature(_body(), "", SECRET) is False


def test_an_empty_secret_fails_closed() -> None:
    """A misconfigured deployment must refuse everything, never accept everything."""
    body = _body()
    assert verify_signature(body, compute_signature(body, ""), "") is False


def test_signature_survives_surrounding_whitespace() -> None:
    body = _body()
    assert verify_signature(body, f"  {_signed(body)}\n", SECRET)


def test_signatures_are_body_specific() -> None:
    assert _signed(_body(payment_id="pay_A")) != _signed(_body(payment_id="pay_B"))


# --- ingestion ----------------------------------------------------------------


def test_a_valid_delivery_is_accepted_and_normalised(db: Database) -> None:
    store = EventStore(db)
    body = _body()
    result = ingest(body, _signed(body), SECRET, store)

    assert result.accepted and not result.duplicate
    assert result.event is not None
    assert result.event.event_type == "payment.failed"
    assert result.event.subscription_id == "sub_TEST01"
    assert result.event.payment_id == "pay_TEST01"
    assert result.event.signature_verified
    assert result.event.occurred_at.tzinfo is not None
    assert store.count(verified_only=True) == 1


def test_the_subscription_id_is_found_on_the_payment_when_absent_elsewhere(
    db: Database,
) -> None:
    raw = json.loads(_body(subscription_id=None))
    raw["payload"]["payment"]["entity"]["subscription_id"] = "sub_FROM_PAYMENT"
    body = json.dumps(raw).encode("utf-8")

    result = ingest(body, _signed(body), SECRET, EventStore(db))
    assert result.event is not None
    assert result.event.subscription_id == "sub_FROM_PAYMENT"


def test_an_unsigned_delivery_is_rejected_and_recorded(db: Database) -> None:
    """A probe against a public endpoint is something the audit trail should show."""
    store = EventStore(db)
    result = ingest(_body(), None, SECRET, store)

    assert not result.accepted
    assert result.event is not None
    assert result.event.event_type == REJECTED_EVENT_TYPE
    assert not result.event.signature_verified
    assert store.count() == 1
    assert store.count(verified_only=True) == 0


def test_a_rejected_body_is_never_stored(db: Database) -> None:
    """Storing unverified content would let anyone who can reach the endpoint
    write arbitrary bytes into an append-only record they cannot be removed from."""
    store = EventStore(db)
    body = _body(payment_id="pay_ATTACKER_CONTROLLED")
    ingest(body, "deadbeef", SECRET, store)

    stored = store.all()[0]
    assert "pay_ATTACKER_CONTROLLED" not in json.dumps(stored.payload)
    assert set(stored.payload) == {"body_sha256", "body_bytes", "reason"}
    assert stored.payload["body_bytes"] == len(body)


def test_a_tampered_delivery_is_rejected(db: Database) -> None:
    body = _body()
    signature = _signed(body)
    tampered = body.replace(b"49900", b"99900")
    result = ingest(tampered, signature, SECRET, EventStore(db))
    assert not result.accepted
    assert "signature verification failed" in result.reason


def test_malformed_json_is_rejected_after_verification(db: Database) -> None:
    body = b"{not json at all"
    result = ingest(body, _signed(body), SECRET, EventStore(db))
    assert not result.accepted
    assert "not valid JSON" in result.reason


def test_a_json_array_is_rejected(db: Database) -> None:
    body = b"[1, 2, 3]"
    result = ingest(body, _signed(body), SECRET, EventStore(db))
    assert not result.accepted
    assert "not a JSON object" in result.reason


def test_a_non_event_envelope_is_rejected(db: Database) -> None:
    body = json.dumps({"entity": "payment", "id": "pay_1"}).encode("utf-8")
    result = ingest(body, _signed(body), SECRET, EventStore(db))
    assert not result.accepted
    assert "not a Razorpay event envelope" in result.reason


# --- at-least-once delivery ---------------------------------------------------


def test_a_redelivery_is_recognised_rather_than_duplicated(db: Database) -> None:
    store = EventStore(db)
    body = _body()
    signature = _signed(body)

    first = ingest(body, signature, SECRET, store)
    second = ingest(body, signature, SECRET, store)

    assert first.accepted and second.accepted
    assert not first.duplicate and second.duplicate
    assert store.count() == 1
    assert second.event is not None and first.event is not None
    assert second.event.seq == first.event.seq


def test_one_hundred_and_fifty_redeliveries_produce_one_event(db: Database) -> None:
    """The event-level half of the replay story. The money-path half is the
    idempotency store's job, and the two are deliberately separate mechanisms."""
    store = EventStore(db)
    body = _body()
    signature = _signed(body)

    results = [ingest(body, signature, SECRET, store) for _ in range(150)]

    assert all(r.accepted for r in results)
    assert sum(1 for r in results if not r.duplicate) == 1
    assert store.count() == 1


def test_repeated_rejections_collapse_to_one_row(db: Database) -> None:
    """An endpoint being flooded with the same bad payload must not be able to
    grow our permanent record without bound."""
    store = EventStore(db)
    for _ in range(50):
        ingest(_body(), "bad-signature", SECRET, store)
    assert store.count() == 1


def test_an_explicit_event_id_header_wins_over_the_derived_one(db: Database) -> None:
    store = EventStore(db)
    body = _body()
    result = ingest(body, _signed(body), SECRET, store, event_id="evt_FROM_HEADER")
    assert result.event is not None
    assert result.event.event_id == "evt_FROM_HEADER"


def test_the_derived_event_id_is_stable_and_body_specific() -> None:
    a, b = _body(), _body()
    assert derive_event_id(a, json.loads(a)).event_id == derive_event_id(b, json.loads(b)).event_id

    other = _body(payment_id="pay_B")
    assert (
        derive_event_id(a, json.loads(a)).event_id
        != derive_event_id(other, json.loads(other)).event_id
    )


def test_a_body_with_a_timestamp_and_an_entity_id_is_safe_to_dedupe() -> None:
    body = _body()
    derived = derive_event_id(body, json.loads(body))
    assert derived.dedupable
    assert derived.event_id.startswith("evt_body_")


def test_deriving_without_the_parsed_body_never_dedupes() -> None:
    """No parsed body means no way to check the id is distinguishing, so the
    conservative branch is the only one available."""
    body = _body()
    assert not derive_event_id(body).dedupable


def test_a_body_without_a_timestamp_is_never_deduplicated() -> None:
    """Fail toward a duplicate row, never toward a collapse.

    A duplicate event is visible and reconcilable. Two distinct failed charges
    merged into one row is invisible, permanent (the store is append-only), and
    costs a customer their recovery. The asymmetry is not close.
    """
    raw = json.loads(_body())
    del raw["created_at"]
    body = json.dumps(raw).encode("utf-8")

    first = derive_event_id(body, raw)
    second = derive_event_id(body, raw)
    assert not first.dedupable and not second.dedupable
    assert first.event_id != second.event_id
    assert first.event_id.startswith("evt_nodedupe_")


def test_a_body_without_any_entity_id_is_never_deduplicated() -> None:
    raw = {"entity": "event", "event": "subscription.pending", "created_at": 1, "payload": {}}
    body = json.dumps(raw).encode("utf-8")
    assert not derive_event_id(body, raw).dedupable


def test_two_undedupable_deliveries_produce_two_rows(db: Database) -> None:
    """The end-to-end consequence: both are kept, and the store shows why."""
    raw = json.loads(_body())
    del raw["created_at"]
    body = json.dumps(raw).encode("utf-8")
    signature = _signed(body)
    store = EventStore(db)

    first = ingest(body, signature, SECRET, store)
    second = ingest(body, signature, SECRET, store)

    assert first.accepted and second.accepted
    assert not first.dedupable and not second.dedupable
    assert not second.duplicate
    assert store.count() == 2, "an un-dedupable delivery must not collapse"
    assert all(e.event_id.startswith("evt_nodedupe_") for e in store.all())
    assert "not deduplicating" in second.reason


def test_an_event_id_header_restores_deduplication(db: Database) -> None:
    """Once Razorpay's header is confirmed present, the fallback stops mattering."""
    raw = json.loads(_body())
    del raw["created_at"]
    body = json.dumps(raw).encode("utf-8")
    signature = _signed(body)
    store = EventStore(db)

    for _ in range(5):
        result = ingest(body, signature, SECRET, store, event_id="evt_HEADER")
    assert result.dedupable
    assert store.count() == 1


def test_distinct_deliveries_are_kept_separately(db: Database) -> None:
    store = EventStore(db)
    for i in range(4):
        body = _body(payment_id=f"pay_{i}")
        ingest(body, _signed(body), SECRET, store)
    assert store.count() == 4


def test_concurrent_redeliveries_still_produce_exactly_one_event(db: Database) -> None:
    """The sequential redelivery test proves dedup; it does not prove dedup under
    a burst. Razorpay retries on a schedule, and a slow consumer can have several
    deliveries of the same event in flight at once, which is exactly when a
    check-then-insert would let two rows through.
    """
    store = EventStore(db)
    body = _body()
    signature = _signed(body)
    barrier = threading.Barrier(32)

    def deliver(_: int) -> IngestResult:
        barrier.wait()
        return ingest(body, signature, SECRET, store)

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(deliver, range(32)))

    assert store.count() == 1
    assert sum(1 for r in results if not r.duplicate) == 1, "more than one delivery won"
    assert all(r.accepted for r in results)
    seqs = {r.event.seq for r in results if r.event is not None}
    assert len(seqs) == 1, f"deliveries disagreed about the event's position: {seqs}"
