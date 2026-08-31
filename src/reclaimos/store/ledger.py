"""The append-only, hash-chained decision ledger — the audit-trail deliverable.

Each row carries ``prev_hash`` and ``entry_hash`` where
``entry_hash = sha256(prev_hash || canonical_json(payload))``. Editing any row
changes its hash, which breaks the link every later row depends on, so tampering
with one decision requires rewriting the entire tail and is visible in one
command: ``reclaimos ledger verify``.

Appends take ``BEGIN IMMEDIATE``. Without it two concurrent writers could both
read the same head and fork the chain into two branches that each verify locally
and disagree globally -- the one failure mode that would make the whole structure
worthless.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from reclaimos.domain import IST
from reclaimos.store.canonical import GENESIS_HASH, canonical_json, chain_hash
from reclaimos.store.db import Database


class LedgerEntry(BaseModel):
    """One immutable decision record."""

    model_config = ConfigDict(frozen=True)

    seq: int
    recorded_at: datetime
    prev_hash: str
    entry_hash: str
    payload: dict[str, Any]


class ChainBreak(BaseModel):
    """Where and how the chain stopped verifying."""

    model_config = ConfigDict(frozen=True)

    seq: int
    reason: str
    expected: str
    found: str


class ChainVerification(BaseModel):
    """The result of walking the whole chain."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    entries: int
    head: str
    breaks: list[ChainBreak]

    def summary(self) -> str:
        if self.ok:
            return f"chain intact — {self.entries} entries, head {self.head[:16]}…"
        first = self.breaks[0]
        return f"CHAIN BROKEN at entry {first.seq}: {first.reason} ({len(self.breaks)} break(s))"


class DecisionLedger:
    """Append-only and hash-chained. No update or delete path exists."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def head(self) -> str:
        """Hash of the most recent entry, or the genesis constant if empty."""
        row = self._db.connection.execute(
            "SELECT entry_hash FROM ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return GENESIS_HASH if row is None else str(row[0])

    def append(self, payload: Mapping[str, Any]) -> LedgerEntry:
        """Append one entry, linked to the current head."""
        body = canonical_json(payload)
        recorded_at = datetime.now(tz=IST)
        conn = self._db.connection
        # IMMEDIATE takes the write lock up front, so head-read and insert cannot
        # interleave with another writer and fork the chain.
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT entry_hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = GENESIS_HASH if row is None else str(row[0])
            entry_hash = chain_hash(prev_hash, body)
            cursor = conn.execute(
                "INSERT INTO ledger (recorded_at, prev_hash, entry_hash, payload)"
                " VALUES (?, ?, ?, ?)",
                (recorded_at.isoformat(), prev_hash, entry_hash, body),
            )
            seq = int(cursor.lastrowid or 0)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        return LedgerEntry(
            seq=seq,
            recorded_at=recorded_at,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            payload=dict(payload),
        )

    def entries(self) -> list[LedgerEntry]:
        rows = self._db.connection.execute("SELECT * FROM ledger ORDER BY seq").fetchall()
        return [_to_entry(row) for row in rows]

    def count(self) -> int:
        row = self._db.connection.execute("SELECT COUNT(*) FROM ledger").fetchone()
        return int(row[0])

    def verify(self) -> ChainVerification:
        """Recompute every hash and report the first break, and any others.

        Reports *all* breaks rather than stopping at the first: a single edited
        row breaks its own hash and its successor's link, and seeing both makes
        the difference between "one row was altered" and "the tail was rewritten"
        obvious at a glance.
        """
        rows = self._db.connection.execute(
            "SELECT seq, prev_hash, entry_hash, payload FROM ledger ORDER BY seq"
        ).fetchall()

        breaks: list[ChainBreak] = []
        expected_prev = GENESIS_HASH
        head = GENESIS_HASH

        for row in rows:
            seq = int(row["seq"])
            prev_hash = str(row["prev_hash"])
            entry_hash = str(row["entry_hash"])
            body = str(row["payload"])

            if prev_hash != expected_prev:
                breaks.append(
                    ChainBreak(
                        seq=seq,
                        reason="prev_hash does not match the previous entry",
                        expected=expected_prev,
                        found=prev_hash,
                    )
                )
            recomputed = chain_hash(prev_hash, body)
            if recomputed != entry_hash:
                breaks.append(
                    ChainBreak(
                        seq=seq,
                        reason="entry_hash does not match the payload",
                        expected=recomputed,
                        found=entry_hash,
                    )
                )
            expected_prev = entry_hash
            head = entry_hash

        return ChainVerification(ok=not breaks, entries=len(rows), head=head, breaks=breaks)

    def export_jsonl(self, path: Path) -> Path:
        """Write the whole chain out for inspection outside this codebase."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for entry in self.entries():
                fh.write(entry.model_dump_json() + "\n")
        return path


def _to_entry(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        seq=int(row["seq"]),
        recorded_at=datetime.fromisoformat(str(row["recorded_at"])),
        prev_hash=str(row["prev_hash"]),
        entry_hash=str(row["entry_hash"]),
        payload=json.loads(str(row["payload"])),
    )
