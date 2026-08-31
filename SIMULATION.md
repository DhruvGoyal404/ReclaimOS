# SIMULATION.md — what is simulated and what is live

This file exists so nobody has to infer it. Every number ReclaimOS reports is
produced by one of two paths, and this page says which.

## The short version

| | Simulated batch | Live test-mode slice |
| --- | --- | --- |
| Gateway | `SimulatedGateway` | `LiveTestModeGateway` (real Razorpay test mode) |
| Scale | 200+ subscriptions | 10–20 subscriptions, recorded |
| Purpose | measure policy quality | prove the integration is real |
| Money | **simulated INR**, never real, never test-mode-real | Razorpay test mode, no real funds |
| Outcomes | drawn from the world model in `reclaimos.generator.outcome_model` | whatever Razorpay actually returns |
| Reported in | EVAL.md metrics tables | EVAL.md appendix + demo recording |

## Why the batch is simulated

Razorpay test mode can create a failed charge and fire the webhook chain. It cannot
model whether a *retry three days later* would have succeeded, because test-mode
outcomes are chosen by the caller, not by an issuer. There is no realistic
recovery signal to measure there.

So running 200 records against test mode would produce a number that looks live and
means nothing — the worst of both worlds. Instead we split the claim:

- **Does the policy make good decisions?** Measured at scale against a world model
  whose assumptions are written down and criticisable (see below).
- **Does the integration actually work end to end?** Demonstrated on a small live
  test-mode run with real signature-verified webhooks, real API calls, and a real
  ledger, captured on video.

Additional practical reasons: test-mode card tokens expire in roughly 3 days, and a
200-call live run on stage is a coin flip we have no reason to take.

## What the world model assumes

Full detail lives in `src/reclaimos/generator/outcome_model.py`, which cites each
number at its use site. In summary, the latent recovery probability depends on:

- **Decline class** — soft declines are recoverable, hard declines essentially are
  not, expiry classes are recoverable only through an instrument-update path.
- **Attempt number** — each successive attempt on the same failure decays.
- **Retry timing** — retries near salary-credit days recover materially better;
  retries within a few hours of the original failure recover materially worse.
- **Issuer downtime windows** — a retry inside an ongoing outage mostly fails.
- **Method** — UPI AutoPay fails more often than cards (~8–15% vs ~2–3%) and
  recovers somewhat differently.
- **Tenure and payment history** — long-tenured customers with clean histories both
  recover and respond to outreach better.

Base rates are anchored on published vendor ranges for involuntary churn and
dunning recovery. They are **ranges, not measurements**, and they are used as
ranges. Nothing here is calibrated against proprietary Razorpay data, because we do
not have any.

## What this means for the headline numbers

- Every rupee figure from the batch is **simulated INR under a declared recovery
  model**. We never write "we recovered X" without that qualifier.
- The comparison that carries the weight is **relative**: agent versus do-nothing,
  versus retry-once, versus retry-3x, versus an oracle ceiling — all facing
  identical random draws (common random numbers). A relative improvement under a
  stated model is a defensible claim; an absolute recovery figure would not be.
- Confidence intervals are bootstrap intervals over the record population. They
  quantify sampling variation. They do **not** quantify model misspecification, and
  nothing in this repo can.

## Known limits we are not hiding

- The world model was written by the same person who wrote the policy engine. The
  mitigations — latent factors the rules do not encode, ambiguous decline tuples, a
  sealed held-out split, an oracle ceiling — reduce this problem but do not remove
  it.
- Real issuer behaviour varies by bank, network, city and hour in ways no
  hand-built model captures.
- The live slice proves plumbing, not economics. It is not evidence that the
  simulated recovery rates are right.

## What the test account allowed vs what remains simulation-validated

The Razorpay Subscriptions API is gated behind full account (KYC) activation.
This test account deliberately has not done KYC, so `/plans` and `/subscriptions`
return 401. Confirmed from three dashboard tabs — not a fixable configuration
issue.

**What the live slice covers**, on this account:

- Real authentication (basic auth, test key accepted)
- Real customer, order and payment-link creation via the API
- Real error envelopes from failed payments (`BAD_REQUEST_ERROR` / `business` /
  `payment_initiation` / `international_transaction_not_allowed` — a class our
  taxonomy did not contain, now added as `HARD_NOT_PERMITTED`)
- Real signature-verified webhook delivery through the same `ingest()` pipeline
- Taxonomy reconciliation against live data (surfaced failure-log entry #5)

**What remains simulation-validated only:**

- Recurring charge → fail → retry/contact → recover: the full subscription
  recovery loop. This is the 68.0% [56.0, 78.7] headline result, validated by a
  sealed simulated batch of 250 records. It never depended on live subscriptions.

The boundary is drawn here and in [`docs/live-slice.md`](docs/live-slice.md).
Every rupee figure in EVAL.md is labelled simulated INR; the live slice is
labelled proof-of-integration. Neither pretends to be the other.
