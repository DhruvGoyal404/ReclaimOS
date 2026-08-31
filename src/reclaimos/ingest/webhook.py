"""Webhook ingestion: verify, normalise, append. In that order, always.

The order is the security property. Nothing is parsed as meaningful, and nothing
reaches a decision, until the signature verifies. A rejected payload is still
*recorded* -- an endpoint being probed is something the audit trail should be able
to show -- but only its digest and a reason, never its content.

Storing the body of an unverified request would let anyone who can reach the
endpoint write arbitrary bytes into our permanent, append-only record. Since it
cannot be deleted afterwards, the safe choice is not to accept it at all.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import uuid4

from reclaimos.domain import IST
from reclaimos.ingest.signature import verify_signature
from reclaimos.store.canonical import sha256_hex
from reclaimos.store.events import EventStore, InboundEvent, StoredEvent

#: Recorded when a payload fails verification or cannot be parsed.
REJECTED_EVENT_TYPE: Final[str] = "webhook.rejected"


@dataclass(frozen=True)
class IngestResult:
    """What happened to one delivery."""

    accepted: bool
    duplicate: bool
    reason: str
    event: StoredEvent | None = None

    #: False when the delivery could not be safely deduplicated. See
    #: ``derive_event_id``: we fail toward a duplicate row rather than a collapse.
    dedupable: bool = True

    @property
    def stored(self) -> bool:
        return self.event is not None


@dataclass(frozen=True)
class DerivedEventId:
    """A fallback id, and whether it is safe to deduplicate on."""

    event_id: str
    dedupable: bool
    reason: str


def _distinguishing(raw: Mapping[str, Any]) -> bool:
    """Does this body carry enough to tell two distinct events apart?

    A timestamp *and* at least one entity id. Both are inside the digest, so when
    both are present a digest collision requires two events of the same type, for
    the same entity, in the same second -- which is a genuine redelivery, not two
    distinct facts.
    """
    has_time = isinstance(raw.get("created_at"), int | float)
    has_entity = any(
        _extract(dict(raw), entity, field) is not None
        for entity, field in (
            ("payment", "id"),
            ("subscription", "id"),
            ("invoice", "id"),
            ("order", "id"),
        )
    )
    return has_time and has_entity


def derive_event_id(body: bytes, raw: Mapping[str, Any] | None = None) -> DerivedEventId:
    """A fallback id for a delivery that arrived without an event-id header.

    Razorpay sends an event id in ``X-Razorpay-Event-Id``; when it is present it
    always wins. This is what happens when it is not.

    **The failure direction is chosen deliberately: fail toward a duplicate row,
    never toward a collapse.** A duplicate event is visible in the store and can
    be reconciled later. Two distinct failed charges silently merged into one row
    is invisible, permanent (the store is append-only), and costs a customer their
    recovery. The asymmetry is not close.

    So the digest is used as the id only when the body carries a timestamp *and*
    an entity id, which makes a collision a genuine redelivery. Otherwise the id
    is made unique per delivery, deduplication is switched off for it, and the id
    itself is prefixed ``evt_nodedupe_`` so the condition is visible in the store
    rather than inferred.
    """
    digest = sha256_hex(body)[:32]
    if raw is not None and _distinguishing(raw):
        return DerivedEventId(
            event_id=f"evt_body_{digest}",
            dedupable=True,
            reason="body carries a timestamp and an entity id",
        )
    return DerivedEventId(
        event_id=f"evt_nodedupe_{digest}_{uuid4().hex}",
        dedupable=False,
        reason=(
            "body lacks a timestamp or an entity id, so a digest match cannot be "
            "distinguished from two separate events; not deduplicating"
        ),
    )


def _extract(payload: dict[str, Any], entity: str, field: str) -> str | None:
    node = payload.get("payload", {})
    if not isinstance(node, dict):
        return None
    holder = node.get(entity)
    if not isinstance(holder, dict):
        return None
    inner = holder.get("entity")
    if not isinstance(inner, dict):
        return None
    value = inner.get(field)
    return str(value) if isinstance(value, str) else None


def parse(raw: dict[str, Any], event_id: str) -> InboundEvent:
    """Normalise a verified Razorpay webhook body into an ``InboundEvent``.

    The subscription id is looked for in both the subscription entity and on the
    payment entity, because which one carries it depends on the event.
    """
    event_type = raw.get("event")
    if raw.get("entity") != "event" or not isinstance(event_type, str):
        raise ValueError("not a Razorpay event envelope")

    created_at = raw.get("created_at")
    occurred_at = (
        datetime.fromtimestamp(created_at, tz=IST)
        if isinstance(created_at, int | float)
        else datetime.now(tz=IST)
    )

    subscription_id = _extract(raw, "subscription", "id") or _extract(
        raw, "payment", "subscription_id"
    )

    return InboundEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        signature_verified=True,
        payload=raw,
        subscription_id=subscription_id,
        payment_id=_extract(raw, "payment", "id"),
    )


def _reject(store: EventStore, body: bytes, reason: str) -> IngestResult:
    """Record a rejection by digest and reason only -- never the body."""
    digest = sha256_hex(body)
    event = InboundEvent(
        event_id=f"rej_{digest[:32]}",
        event_type=REJECTED_EVENT_TYPE,
        occurred_at=datetime.now(tz=IST),
        signature_verified=False,
        payload={"body_sha256": digest, "body_bytes": len(body), "reason": reason},
    )
    stored, is_new = store.append(event)
    return IngestResult(accepted=False, duplicate=not is_new, reason=reason, event=stored)


def ingest(
    body: bytes,
    signature: str | None,
    secret: str,
    store: EventStore,
    event_id: str | None = None,
) -> IngestResult:
    """Verify, normalise and append one webhook delivery.

    Never raises for bad input: a malformed or unsigned delivery is an expected
    condition on a public endpoint, and the caller wants a result to return, not
    an exception to translate.
    """
    if not verify_signature(body, signature, secret):
        return _reject(store, body, "signature verification failed")

    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _reject(store, body, f"body is not valid JSON: {exc}")

    if not isinstance(raw, dict):
        return _reject(store, body, "body is not a JSON object")

    derived: DerivedEventId | None = None
    if event_id is None:
        derived = derive_event_id(body, raw)

    try:
        event = parse(raw, event_id or (derived.event_id if derived else ""))
    except ValueError as exc:
        return _reject(store, body, str(exc))

    stored, is_new = store.append(event)
    if not is_new:
        reason = "redelivery of an already-recorded event"
    elif derived is not None and not derived.dedupable:
        reason = f"accepted without deduplication: {derived.reason}"
    else:
        reason = "accepted"
    return IngestResult(
        accepted=True,
        duplicate=not is_new,
        reason=reason,
        event=stored,
        dedupable=derived.dedupable if derived is not None else True,
    )
