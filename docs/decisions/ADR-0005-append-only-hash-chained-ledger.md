# ADR-0005 — Append-only, hash-chained decision ledger and event store

**Status:** Accepted · **Date:** 2026-08-31

## Context

Track 03 asks for an audit trail. A mutable table of decisions is not an audit
trail — it is a cache of the current opinion. If a row can be edited after the
fact, nothing it says about a past money movement can be relied on.

The ledger is also our debugging instrument. The duplicate-charge race we expect to
hit in Phase 5 is only *findable* if the record of what happened cannot be quietly
overwritten by what happened next.

## Decision

Two append-only stores.

**Event store** — every inbound fact (webhook received, signature verified, charge
attempted, outcome observed) appended in order, never updated.

**Decision ledger** — one row per decision, each row carrying
`prev_hash` and `entry_hash`, where
`entry_hash = sha256(prev_hash || canonical_json(payload))`.
Canonical JSON means sorted keys, no whitespace variance, integer paise only, so
the hash is reproducible across machines. Row zero chains from a fixed genesis
constant.

Neither store has an `UPDATE` or `DELETE` path in the data-access layer. A
correction is a new compensating entry, never an edit.

Both are exportable — `reclaimos ledger export` produces the chain plus a
verification command that recomputes every hash and reports the first break.

## Consequences

- Tamper-evidence is cheap and verifiable by a judge in one command.
- Storage grows monotonically. At our scale (hundreds of subscriptions) this is
  irrelevant, and we are not going to pretend to solve retention.
- Every eval run can be replayed from the event store alone.
