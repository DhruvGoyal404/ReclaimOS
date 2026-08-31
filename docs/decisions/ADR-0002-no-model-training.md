# ADR-0002 — No model training; a transparent rule table instead

**Status:** Accepted · **Date:** 2026-08-31

## Context

Recovery propensity — "how likely is this failed charge to succeed if we retry?" —
looks like a supervised learning problem. It is, when you have years of real
outcomes. We have a synthetic generator we wrote ourselves.

Training a model on labels our own generator produced and then evaluating it on
data from that same generator measures one thing: whether the model can learn our
generator. It is a circular benchmark. Reporting an accuracy figure from it would
be the single fastest way to lose the honesty argument this project is built on.

## Decision

Recovery propensity is a **transparent, literature-grounded rule table** keyed on
decline class, attempt number, retry timing, method and tenure. Every score is
traceable to a named rule with a stated source.

Shape of the logic:

| Decline class | Propensity | Action |
| --- | --- | --- |
| Insufficient funds (soft) | high | retry inside an optimal window |
| Issuer or gateway technical (soft) | high | short-delay retry |
| Limit exceeded (soft) | medium | delayed retry |
| Card expired | medium | route to card-update flow, do not retry blind |
| Mandate expired | medium | re-authorisation flow |
| Risk flagged / do-not-honor / mandate revoked (hard) | ~0 | **stop immediately** |

A calibrated gradient-boosted scorer stays on the table as an **optional bonus
module**, to be added only after the core loop is complete, and only if EVAL.md
labels it explicitly as *demonstrating calibration methodology, not real-world
accuracy*. It is not being built now.

## Consequences

- Nothing in the recovery decision is opaque; a judge can read the rule that fired.
- We cannot claim state-of-the-art prediction, and we do not want to.
- The rule table is a hypothesis the world simulator is free to contradict — which
  is exactly what makes the measured precision and recall meaningful (see ADR-0006).
