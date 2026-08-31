"""The append-only event store.

Every inbound fact lands here in arrival order and is never revised. Rejected
webhooks are recorded too: a payload that failed signature verification is a
security-relevant event, and a store that silently drops them cannot answer
"was anyone probing this endpoint".

There is no ``update`` and no ``delete`` on this class, and the database refuses
both anyway (``store.db.SCHEMA``). A correction is a new event, never an edit.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from reclaimos.domain import IST
from reclaimos.store.canonical import canonical_json
from reclaimos.store.db import Database


class InboundEvent(BaseModel):
    """A normalised fact, before it has a place in the sequence."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    event_type: str
    occurred_at: datetime
    signature_verified: bool
    payload: dict[str, Any]
    subscription_id: str | None = None
    payment_id: str | None = None


class StoredEvent(InboundEvent):
    """An event with its position in the sequence."""

    seq: int
    received_at: datetime


class EventStore:
    """Append-only, deduplicating on ``event_id``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, event: InboundEvent) -> tuple[StoredEvent, bool]:
        """Append an event. Returns ``(stored, is_new)``.

        Webhook delivery is at-least-once, so a redelivery is expected traffic
        rather than an error. The ``UNIQUE`` constraint on ``event_id`` makes
        deduplication atomic: a duplicate loses the insert and reads back the
        original instead of creating a second row.

        Note the scope. This is *event-level* dedup, which stops a redelivered
        webhook becoming two facts. It is not the money-path guarantee -- that is
        the idempotency store's job (ADR-0004), and the two are deliberately
        separate mechanisms.
        """
        received_at = datetime.now(tz=IST)
        try:
            self._db.connection.execute(
                "INSERT INTO events (event_id, event_type, subscription_id, payment_id,"
                " occurred_at, received_at, signature_verified, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.event_type,
                    event.subscription_id,
                    event.payment_id,
                    event.occurred_at.isoformat(),
                    received_at.isoformat(),
                    int(event.signature_verified),
                    canonical_json(event.payload),
                ),
            )
        except sqlite3.IntegrityError:
            existing = self.get(event.event_id)
            if existing is None:  # pragma: no cover - only on a concurrent delete
                raise
            return existing, False

        stored = self.get(event.event_id)
        assert stored is not None
        return stored, True

    def get(self, event_id: str) -> StoredEvent | None:
        row = self._db.connection.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return None if row is None else _to_event(row)

    def all(self) -> list[StoredEvent]:
        rows = self._db.connection.execute("SELECT * FROM events ORDER BY seq").fetchall()
        return [_to_event(row) for row in rows]

    def for_subscription(self, subscription_id: str) -> list[StoredEvent]:
        rows = self._db.connection.execute(
            "SELECT * FROM events WHERE subscription_id = ? ORDER BY seq",
            (subscription_id,),
        ).fetchall()
        return [_to_event(row) for row in rows]

    def count(self, *, verified_only: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM events"
        if verified_only:
            sql += " WHERE signature_verified = 1"
        row = self._db.connection.execute(sql).fetchone()
        return int(row[0])


def _to_event(row: sqlite3.Row) -> StoredEvent:
    import json

    return StoredEvent(
        seq=int(row["seq"]),
        event_id=str(row["event_id"]),
        event_type=str(row["event_type"]),
        subscription_id=row["subscription_id"],
        payment_id=row["payment_id"],
        occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
        received_at=datetime.fromisoformat(str(row["received_at"])),
        signature_verified=bool(row["signature_verified"]),
        payload=json.loads(str(row["payload"])),
    )
