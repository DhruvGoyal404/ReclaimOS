"""SQLite storage, with append-only enforced by the database itself.

Two decisions worth stating.

**stdlib ``sqlite3``, no ORM.** The schema is three tables. An ORM would add a
dependency, a migration story and an abstraction, and buy nothing. SQLite is the
default so a judge can clone and run the whole system with no services at all
(ADR-0004); the Postgres adapter is deliberately not written yet, and
``from_url`` says so out loud rather than failing obscurely.

**Append-only is enforced by triggers, not by the absence of a method.** Omitting
``update()`` from a Python class is a convention; ``RAISE(ABORT)`` is a rule. If
someone opens the database file in a SQLite browser and edits a ledger row, the
write is refused. That is the difference between an audit trail and a log
(ADR-0005).

Connections are per-thread. The idempotency store has to survive genuine
concurrency -- that is its entire job -- so a single shared connection behind a
lock would serialise away the very race the design exists to win.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Final

SCHEMA: Final[str] = """
PRAGMA journal_mode = WAL;

-- Every inbound fact, in arrival order. Includes rejected webhooks: a payload
-- that failed signature verification is a security-relevant event and is
-- recorded as one.
CREATE TABLE IF NOT EXISTS events (
    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id           TEXT    NOT NULL UNIQUE,
    event_type         TEXT    NOT NULL,
    subscription_id    TEXT,
    payment_id         TEXT,
    occurred_at        TEXT    NOT NULL,
    received_at        TEXT    NOT NULL,
    signature_verified INTEGER NOT NULL,
    payload            TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS events_subscription ON events (subscription_id);
CREATE INDEX IF NOT EXISTS events_type ON events (event_type);

-- The decision ledger. prev_hash/entry_hash form the chain.
CREATE TABLE IF NOT EXISTS ledger (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    entry_hash  TEXT NOT NULL UNIQUE,
    payload     TEXT NOT NULL
);

-- Idempotency claims. The PRIMARY KEY is the atomic claim: two racing writers
-- cannot both insert the same key, so exactly one wins and the loser learns the
-- action already happened.
CREATE TABLE IF NOT EXISTS idempotency (
    key        TEXT PRIMARY KEY,
    claimed_at TEXT NOT NULL,
    result     TEXT
);

-- --- append-only enforcement -------------------------------------------------
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;

CREATE TRIGGER IF NOT EXISTS ledger_no_update
BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;

CREATE TRIGGER IF NOT EXISTS ledger_no_delete
BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;

-- A claim's result may be written once and never revised. Overwriting it would
-- let a replay rewrite what the first execution actually did.
CREATE TRIGGER IF NOT EXISTS idempotency_result_write_once
BEFORE UPDATE ON idempotency
WHEN OLD.result IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'idempotency result is write-once'); END;

CREATE TRIGGER IF NOT EXISTS idempotency_no_delete
BEFORE DELETE ON idempotency
BEGIN SELECT RAISE(ABORT, 'idempotency claims are permanent'); END;
"""


class Database:
    """A SQLite database with one connection per thread."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        with self._init_lock:
            self.connection.executescript(SCHEMA)

    @classmethod
    def from_url(cls, url: str) -> Database:
        """Build from a ``RECLAIMOS_DATABASE_URL``.

        Only ``sqlite:///`` is supported. Postgres is in ``docker-compose.yml``
        behind the ``full`` profile but has no adapter yet -- saying so plainly
        beats a confusing driver error three layers down.
        """
        if url.startswith("sqlite:///"):
            return cls(url.removeprefix("sqlite:///"))
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            raise NotImplementedError(
                "The Postgres adapter is not written yet (Phase 5). SQLite is the "
                "supported backend: set RECLAIMOS_DATABASE_URL=sqlite:///./data/reclaimos.db"
            )
        raise ValueError(f"unsupported database URL: {url!r}")

    @property
    def connection(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close this thread's connection, if it has one."""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
