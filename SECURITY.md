# SECURITY.md

ReclaimOS moves money. This page states what protects that, and what does not.

## Test mode only

ReclaimOS is built against **Razorpay test mode**. `RAZORPAY_KEY_ID` must begin with
`rzp_test_`; a live key is rejected at startup rather than trusted. No code path in
this repository is intended for, or safe for, live keys.

## Secrets

- All credentials live in `.env`, which is git-ignored. `.env.example` documents
  every variable and contains no real values.
- Phases 0 and 1 (generator and eval harness) read **no** credentials at all. A
  judge can clone, run the full evaluation, and never supply a key.
- Nothing prints a secret. `reclaimos config` renders settings with credential
  fields excluded by construction — they are not fields on the settings object.
- If a key is ever committed, treat it as burned: rotate it in the Razorpay
  dashboard first, rewrite history second.

## Webhook handling

- Every inbound webhook is **HMAC-SHA256 signature verified** against
  `RAZORPAY_WEBHOOK_SECRET` before it is parsed as anything meaningful, using a
  constant-time comparison.
- Verification happens before persistence and before any decision. An unverified
  payload is recorded as a rejected event and goes no further.
- Webhook delivery is at-least-once. Replay safety comes from the idempotency layer
  below, not from hoping deliveries are unique.

## Money-path controls

These are the controls that matter, each with an ADR:

| Control | Mechanism | ADR |
| --- | --- | --- |
| LLM cannot move money | closed action enum, rules select actions, LLM writes prose only | ADR-0001 |
| Bounded actions | signed recovery mandate; executor refuses out-of-envelope | ADR-0003 |
| Human gate | tiered autonomy, review queue above threshold | ADR-0003 |
| No duplicate charges | deterministic idempotency key claimed atomically before the gateway call | ADR-0004 |
| Tamper-evident record | append-only hash-chained ledger, no UPDATE or DELETE path | ADR-0005 |

## Prompt injection

Customer-supplied text (names, notes, payment descriptions) reaches the LLM nodes.
Because those nodes cannot select actions, an injection can at worst corrupt an
explanation string or a draft message. It cannot cause a charge. Beyond that
structural containment:

- LLM output is parsed into strict Pydantic models and **fails closed** to a
  deterministic template on any validation error.
- Generated customer-facing text passes a PII redaction pass before it is stored or
  sent.

## PII

Synthetic data contains no real personal data. In the live slice, customer
identifiers are stored as Razorpay ids; free-text fields are redacted before
storage. We do not store card numbers, tokens, UPI handles or bank identifiers —
the gateway holds those.

## Dependencies

Locked via `uv.lock`. CI runs on the lockfile. Direct dependencies are deliberately
few; Guardrails AI was cut partly to keep the transitive tree small (see CLAUDE.md).

## Reporting a problem

Open a GitHub issue for anything non-sensitive. For something you would rather not
post publicly, contact the maintainer through the GitHub profile linked on this
repository.

## What this is not

This is a hackathon project built in days by one person. It has not had a security
audit, a threat model review, or a penetration test. The controls above are real and
tested, but the claim is "designed carefully", not "certified".
