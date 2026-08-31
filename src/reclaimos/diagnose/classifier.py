"""Deterministic decline classification.

Given the gateway's error tuple, decide which ``DeclineClass`` it represents.
Lookup, not inference: the same tuple must produce the same class on every
machine and every replay, because a retry decision hangs off it (ADR-0007).

**No model is consulted here, and none can be.** This module has no LLM client,
no network call and no configuration that could introduce one. That is checkable
by reading its imports.

The interesting part is ambiguity. Some ``(code, reason)`` tuples are emitted by
more than one true class -- a generic "the bank declined it" is the obvious case --
and no classifier reading only the gateway payload can resolve them. Rather than
hide that, the classifier:

* picks the most likely candidate on a stated, externally-justified prior,
* reports ``confidence`` below 1.0, and
* sets ``hard_possible`` when any candidate is a hard decline.

That last flag is what makes ambiguity *conservative* downstream. A bucket that
might be a stolen card should not be retried as aggressively as one that
certainly is not. Ambiguity makes the policy more cautious, never more aggressive.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from reclaimos.domain import DECLINE_CODES, DeclineClass, PaymentAttempt

#: Confidence for a tuple owned by exactly one class. Not 1.0: the taxonomy is
#: modelled from Razorpay's documented error shape rather than reconciled against
#: captured traffic, so a tuple we think is unique may not be
#: (docs/failure-log.md). Reserving headroom keeps that honest.
UNAMBIGUOUS_CONFIDENCE: Final[float] = 0.95

#: Confidence for a tuple we cannot place at all.
UNKNOWN_CONFIDENCE: Final[float] = 0.20

#: Which candidate to prefer when a tuple has several owners.
#:
#: Justification, stated so it can be argued with: across published card-decline
#: breakdowns, insufficient funds is consistently the single largest reason
#: behind a generic issuer decline, ahead of do-not-honor, with transient
#: technical faults smaller than both. This ordering is *not* derived from our own
#: generator -- doing that would make the benchmark circular (ADR-0006) -- and it
#: is deliberately allowed to be wrong.
AMBIGUITY_PREFERENCE: Final[tuple[DeclineClass, ...]] = (
    DeclineClass.SOFT_INSUFFICIENT_FUNDS,
    DeclineClass.HARD_DO_NOT_HONOR,
    DeclineClass.SOFT_ISSUER_TECHNICAL,
    DeclineClass.SOFT_LIMIT_EXCEEDED,
    DeclineClass.EXPIRY_CARD_EXPIRED,
    DeclineClass.EXPIRY_MANDATE_EXPIRED,
    DeclineClass.HARD_RISK_FLAGGED,
    DeclineClass.HARD_MANDATE_REVOKED,
    DeclineClass.HARD_NOT_PERMITTED,
    DeclineClass.UNKNOWN,
)


class Classification(BaseModel):
    """What the gateway error tuple tells us, and how sure we are."""

    model_config = ConfigDict(frozen=True)

    decline_class: DeclineClass
    confidence: float = Field(ge=0.0, le=1.0)
    rule_id: str
    ambiguous: bool
    candidates: tuple[DeclineClass, ...]

    #: True when any candidate for this tuple is a hard decline. Drives the
    #: conservatism penalty in the propensity table: a bucket that *might* be a
    #: stolen card is not retried like one that certainly is not.
    hard_possible: bool

    #: The exact gateway fields the decision was made from, so a ledger row can
    #: be re-derived without going back to the raw webhook.
    evidence: dict[str, str | None]


def _index() -> dict[tuple[str, str, str, str], tuple[DeclineClass, ...]]:
    """Map every gateway tuple to the classes that can emit it."""
    table: dict[tuple[str, str, str, str], list[DeclineClass]] = {}
    for code in DECLINE_CODES:
        key = (code.code, code.source, code.step, code.reason)
        owners = table.setdefault(key, [])
        if code.true_class not in owners:
            owners.append(code.true_class)
    return {key: tuple(owners) for key, owners in table.items()}


TUPLE_INDEX: Final[dict[tuple[str, str, str, str], tuple[DeclineClass, ...]]] = _index()


def _prefer(candidates: tuple[DeclineClass, ...]) -> DeclineClass:
    for preferred in AMBIGUITY_PREFERENCE:
        if preferred in candidates:
            return preferred
    return candidates[0]  # pragma: no cover - AMBIGUITY_PREFERENCE covers the enum


def classify(attempt: PaymentAttempt) -> Classification:
    """Classify one failed attempt. Pure, total, and free of any model."""
    evidence: dict[str, str | None] = {
        "error_code": attempt.error_code,
        "error_source": attempt.error_source,
        "error_step": attempt.error_step,
        "error_reason": attempt.error_reason,
    }

    key = (
        attempt.error_code or "",
        attempt.error_source or "",
        attempt.error_step or "",
        attempt.error_reason or "",
    )
    candidates = TUPLE_INDEX.get(key)

    if candidates is None:
        # An unrecognised tuple is not a licence to guess. It is UNKNOWN, at low
        # confidence, and the propensity table treats it cautiously.
        return Classification(
            decline_class=DeclineClass.UNKNOWN,
            confidence=UNKNOWN_CONFIDENCE,
            rule_id="classify.unrecognised_tuple",
            ambiguous=True,
            candidates=(DeclineClass.UNKNOWN,),
            hard_possible=True,
            evidence=evidence,
        )

    if len(candidates) == 1:
        return Classification(
            decline_class=candidates[0],
            confidence=UNAMBIGUOUS_CONFIDENCE,
            rule_id=f"classify.exact.{candidates[0].value.lower()}",
            ambiguous=False,
            candidates=candidates,
            hard_possible=candidates[0].is_hard,
            evidence=evidence,
        )

    chosen = _prefer(candidates)
    return Classification(
        decline_class=chosen,
        confidence=round(1.0 / len(candidates), 4),
        rule_id=f"classify.ambiguous.{len(candidates)}_candidates",
        ambiguous=True,
        candidates=candidates,
        hard_possible=any(c.is_hard for c in candidates),
        evidence=evidence,
    )
