# ADR-0001 — The LLM stays off the money path

**Status:** Accepted · **Date:** 2026-08-31

## Context

The obvious way to build a recovery agent is to hand an LLM a set of payment tools
and let it decide what to do. That design is easy to demo and impossible to trust:
a prompt injection in a customer note, a hallucinated amount, or an ordinary
sampling accident becomes a real debit against a real customer. Nothing in the
loop bounds it, and nothing after the fact explains it.

## Decision

Money movement is a **deterministic, idempotent state machine**. The LLM is used
for exactly two things:

1. Turning a raw decline code plus context into a human-readable root-cause
   explanation attached to the ledger row.
2. Drafting the customer-facing dunning message (Hinglish supported).

An LLM output can **never** trigger a money action. Only the deterministic policy
engine selects actions, and it reads structured fields — never model prose.

Enforcement, not just intent:

- The action catalogue is a closed `ActionType` enum. An action outside it cannot
  be represented, so it cannot be executed.
- LLM outputs are parsed into strict Pydantic models and **fail closed** to a
  deterministic template on any validation error.
- `Decision.rationale` is written *after* `Decision.action` is chosen and is never
  read back as an input.

## Consequences

- The demo is less flashy than a tool-calling agent, and we accept that.
- Every money action is attributable to a named rule, which is what the audit trail
  needs to be worth anything.
- A model outage degrades explanations to templates; recovery keeps running.
- The LLM can read everything (via the READ_ONLY MCP surface) and move nothing.
