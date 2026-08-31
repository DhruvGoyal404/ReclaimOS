# ADR-0004 — Deterministic idempotency keys on every money action

**Status:** Accepted · **Date:** 2026-08-31

## Context

Payment webhooks are delivered at-least-once. Razorpay retries deliveries, networks
duplicate, and any retry loop with a crash in the middle can replay. In a system
whose whole job is to re-attempt charges, a replayed webhook that produces a second
debit is the worst possible bug: it is invisible to the customer until it is not,
and it destroys the trust the audit trail is meant to create.

## Decision

Every money action is guarded by a **deterministic** idempotency key:

```
{subscription_id}:{attempt_no}:{action_type}
```

Deterministic, not random — a replay of the same logical action reconstructs the
same key without needing to remember anything. The key is claimed **before** the
gateway call, in a store where the claim is atomic:

- SQLite (default): a `UNIQUE` index, so the insert is atomic insert-or-fail.
- Redis (optional): `SET NX`.

A claim that fails means the action already happened; the executor returns the
recorded prior result instead of calling the gateway.

## Consequences

- Duplicate charges become structurally impossible rather than unlikely.
- The regression test fires **150 duplicate webhooks** and asserts **0** double
  charges, on both storage backends.
- The idempotency store is an interface with two implementations, so the default
  clean-clone path needs no Redis.
- Note for honesty: this control is being designed in from the start *because* we
  expect to reproduce the race deliberately in Phase 5 and want the fix, the test,
  and the failure-log entry to be real rather than reconstructed.
