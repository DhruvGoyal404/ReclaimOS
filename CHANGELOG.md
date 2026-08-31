# Changelog

All notable changes to ReclaimOS are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] — 2026-09-01

First public release, built for the Razorpay AI Buildathon 2026 (Track 03,
AI Revenue Recovery).

### Added
- Deterministic recovery agent: detect -> diagnose -> decide -> execute -> stop
  -> record, with the LLM kept off the money path (narration and message
  drafting only).
- Stochastic world simulator and eval harness with a sealed held-out split,
  bootstrap 95% confidence intervals, and honest baselines.
- Signed recovery mandates (max amount / method / expiry), a tiered
  human-in-the-loop gate, and structural mandate enforcement: a `ChargeRequest`
  is constructible only from a `MandateToken`.
- Append-only, hash-chained decision ledger; deterministic idempotency keys on
  every money action.
- HMAC-verified webhook ingestion and a live Razorpay test-mode slice
  (auth, payment link, error-envelope reconciliation, signature-verified webhook).
- Seven architecture decision records and a running failure log.

### Results
- Held-out recovery 68.0% (95% CI [56.0, 78.7]) vs best baseline 37.3%
  ([25.3, 48.0]); +30.7 points, zero mandate violations, 85% fewer hard-decline
  retries. Configuration frozen and the split read once. See `EVAL.md`.
