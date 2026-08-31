"""Idempotency claims — the control that makes a duplicate charge impossible.

The key is **deterministic**: ``{subscription_id}:{attempt_no}:{action_type}``.
Deterministic rather than random, because a replay has to reconstruct the same
key without remembering anything. A crash between claiming and executing must
still produce the same key on the next attempt, or the guarantee evaporates
exactly when it is needed (ADR-0004).

Claiming is atomic in the storage layer, not in Python. SQLite's ``PRIMARY KEY``
gives insert-or-fail; Redis would give ``SET NX``. A read-then-write in
application code would be a textbook time-of-check/time-of-use race, which is
precisely the bug this component exists to prevent.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from reclaimos.domain import IST, ActionType
from reclaimos.store.canonical import canonical_json
from reclaimos.store.db import Database


class ResultAlreadyRecorded(RuntimeError):
    """Raised on an attempt to revise what a completed action did."""


def idempotency_key(subscription_id: str, attempt_no: int, action: ActionType) -> str:
    """The deterministic key for one logical money action."""
    return f"{subscription_id}:{attempt_no}:{action.value}"


class Claim(BaseModel):
    """A claimed key, and the result of the action if it has completed."""

    model_config = ConfigDict(frozen=True)

    key: str
    claimed_at: datetime
    result: dict[str, Any] | None = None

    @property
    def completed(self) -> bool:
        return self.result is not None


@runtime_checkable
class IdempotencyStore(Protocol):
    """Two backends satisfy this: SQLite by default, in-memory for unit tests.

    Redis is the third, and is why this is a Protocol rather than a class: the
    interface is the part the executor depends on.
    """

    def claim(self, key: str) -> bool:
        """Atomically claim ``key``. ``True`` means this caller won and may act."""
        ...

    def record_result(self, key: str, result: Mapping[str, Any]) -> None:
        """Record what the action did. Write-once."""
        ...

    def get(self, key: str) -> Claim | None: ...

    def count(self) -> int: ...


class SqliteIdempotencyStore:
    """Default backend. The claim is a single ``INSERT`` against a PRIMARY KEY."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def claim(self, key: str) -> bool:
        try:
            self._db.connection.execute(
                "INSERT INTO idempotency (key, claimed_at) VALUES (?, ?)",
                (key, datetime.now(tz=IST).isoformat()),
            )
        except sqlite3.IntegrityError:
            # Someone already claimed it. Not an error -- it is the mechanism.
            return False
        return True

    def record_result(self, key: str, result: Mapping[str, Any]) -> None:
        cursor = self._db.connection.execute(
            "UPDATE idempotency SET result = ? WHERE key = ? AND result IS NULL",
            (canonical_json(result), key),
        )
        if cursor.rowcount == 0:
            existing = self.get(key)
            if existing is None:
                raise ResultAlreadyRecorded(f"no claim exists for {key!r}")
            raise ResultAlreadyRecorded(f"result for {key!r} was already recorded")

    def get(self, key: str) -> Claim | None:
        row = self._db.connection.execute(
            "SELECT key, claimed_at, result FROM idempotency WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        raw = row["result"]
        return Claim(
            key=str(row["key"]),
            claimed_at=datetime.fromisoformat(str(row["claimed_at"])),
            result=None if raw is None else json.loads(str(raw)),
        )

    def count(self) -> int:
        row = self._db.connection.execute("SELECT COUNT(*) FROM idempotency").fetchone()
        return int(row[0])


class InMemoryIdempotencyStore:
    """Process-local backend for unit tests and single-process runs.

    The lock is not decoration: without it, ``key in self._claims`` followed by an
    assignment is the same check-then-act race the SQLite backend avoids by
    pushing the decision into the storage engine.
    """

    def __init__(self) -> None:
        self._claims: dict[str, Claim] = {}
        self._lock = threading.Lock()

    def claim(self, key: str) -> bool:
        with self._lock:
            if key in self._claims:
                return False
            self._claims[key] = Claim(key=key, claimed_at=datetime.now(tz=IST))
            return True

    def record_result(self, key: str, result: Mapping[str, Any]) -> None:
        with self._lock:
            existing = self._claims.get(key)
            if existing is None:
                raise ResultAlreadyRecorded(f"no claim exists for {key!r}")
            if existing.result is not None:
                raise ResultAlreadyRecorded(f"result for {key!r} was already recorded")
            self._claims[key] = existing.model_copy(update={"result": dict(result)})

    def get(self, key: str) -> Claim | None:
        with self._lock:
            return self._claims.get(key)

    def count(self) -> int:
        with self._lock:
            return len(self._claims)
