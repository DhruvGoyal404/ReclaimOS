"""Persistence: append-only event store, hash-chained ledger, idempotency claims.

Append-only is enforced by SQLite triggers, not by the absence of a method. See
``db.SCHEMA`` and ADR-0005.
"""

from reclaimos.store.canonical import (
    GENESIS_HASH,
    NonCanonicalPayload,
    canonical_json,
    chain_hash,
    sha256_hex,
)
from reclaimos.store.db import Database
from reclaimos.store.events import EventStore, InboundEvent, StoredEvent
from reclaimos.store.idempotency import (
    Claim,
    IdempotencyStore,
    InMemoryIdempotencyStore,
    ResultAlreadyRecorded,
    SqliteIdempotencyStore,
    idempotency_key,
)
from reclaimos.store.ledger import ChainBreak, ChainVerification, DecisionLedger, LedgerEntry

__all__ = [
    "GENESIS_HASH",
    "ChainBreak",
    "ChainVerification",
    "Claim",
    "Database",
    "DecisionLedger",
    "EventStore",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "InboundEvent",
    "LedgerEntry",
    "NonCanonicalPayload",
    "ResultAlreadyRecorded",
    "SqliteIdempotencyStore",
    "StoredEvent",
    "canonical_json",
    "chain_hash",
    "idempotency_key",
    "sha256_hex",
]
