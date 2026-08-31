# ADR-0003 — Every money action is explainable, bounded and gated

**Status:** Accepted · **Date:** 2026-08-31

## Context

"Autonomous agent moves money" is only acceptable if three separate questions have
crisp answers: why did it do that, what stopped it going further, and who approved
the risky ones.

## Decision

Three independent controls, each enforced in code rather than in prompt text.

### Explainable

Every ledger row links, in one chain:
inputs -> propensity score -> the exact policy rule id that fired -> LLM rationale
-> action -> outcome. `Decision.rule_id` is mandatory and is a real identifier, not
a description.

### Bounded — the recovery mandate

Every action carries a signed **recovery mandate** modelled on Google AP2:
`max_amount_paise`, currency, `expiry`, `allowed_method`, `reason_code`. The
executor calls `Mandate.permits(...)` before any gateway call and refuses anything
outside the envelope. Mandate violations are a reported eval metric whose target is
and must remain **zero**.

### Gated — tiered autonomy

| Tier | Actions | Gate |
| --- | --- | --- |
| Low risk | schedule a soft-decline retry, send a payment link, request card update | auto-execute |
| High risk | refund, payout, amount above `RECLAIMOS_HITL_THRESHOLD_PAISE` (default 5,000 INR), more than `RECLAIMOS_MAX_ATTEMPTS` attempts | human review queue |

### Stopping rules

Hard declines stop immediately — zero retries, always. The attempt cap defaults to
**4**. Razorpay's own documentation disagrees with itself here (3 in one place, 4 in
another); rather than guess silently we state the assumption in `config.py`, make it
an environment variable, and will reconcile it against live behaviour when the
test-mode slice lands.

## Consequences

- An out-of-envelope action is impossible to execute, not merely discouraged.
- The HITL queue means the system can be conservative without being useless.
- Three eval metrics exist purely to prove these controls hold: mandate violations,
  hard-decline retries, and stop-rule adherence.

## Phase 4 acceptance criteria — binding

Added 2026-08-31 after review. Today the mandate check lives in the eval harness
(`eval/harness.py`), which is the *scorekeeper*. It genuinely blocks — the action
never reaches the world — but the harness is not an executor, and there is no
production path yet for it to guard. Writing these criteria down now is the point:
under time pressure the runtime check alone will look sufficient, and it is not.

**Phase 4 is not complete until all four hold.** No partial credit.

1. **`src/reclaimos/policy/executor.py` exists and owns enforcement.**
   `execute()` raises `MandateViolation` for any action outside the envelope.

2. **Ordering is mandate → idempotency → gateway.** The mandate check happens
   *before* the idempotency key is claimed, so a refused action never burns a key
   and can never be replayed as "already done". A test asserts the key store is
   untouched after a refusal.

3. **Type-level teeth, not only a runtime check.** `ChargeRequest` cannot be
   constructed directly: it is obtainable only from `Mandate.authorize(...)`,
   which returns a `MandateToken`, and the Razorpay SDK wrapper accepts a
   `ChargeRequest` and nothing else — no loose amount/method arguments. An
   unauthorised charge must fail to *typecheck*, not merely fail at runtime. A
   `mypy` check on a deliberately-wrong snippet proves it.

4. **Defence in depth is kept and distinguishable.** The harness check stays, and
   a test asserts the *executor* rejects first — so when a violation is caught we
   can say which gate caught it. A violation reaching the harness gate in
   production code would mean the executor was bypassed, and that must be a
   visible test failure rather than a silent success.

The baselines will continue to record mandate violations. That is correct: they
never consult the mandate, which is exactly the dumb behaviour they exist to
model. The agent must be structurally incapable of the same thing.
