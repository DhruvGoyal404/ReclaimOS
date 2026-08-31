"""Decline-code taxonomy shared by the generator and (later) the classifier.

The nine ``DeclineClass`` values are the vocabulary the whole system reasons in.
The ``DECLINE_CODES`` table maps gateway-shaped error tuples onto those classes.

Two deliberate design notes, both load-bearing for honest metrics:

1. **Codes are modelled on Razorpay's error taxonomy** (``code`` / ``source`` /
   ``step`` / ``reason``), not copied verbatim from a doc page. When the live
   test-mode slice lands we reconcile these against real webhook payloads; until
   then treat the strings as representative, not authoritative. Tracked in
   ``docs/failure-log.md``.

2. **Some tuples are genuinely ambiguous.** ``AMBIGUOUS_TUPLES`` lists
   ``(code, reason)`` pairs emitted by more than one true class -- the real world
   does this constantly, and it means a perfect classifier is impossible. Without
   it, precision/recall on "recoverable" would be free marks. See ADR-0006.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple


class DeclineClass(StrEnum):
    """What actually went wrong, in the vocabulary the policy engine reasons in."""

    SOFT_INSUFFICIENT_FUNDS = "SOFT_INSUFFICIENT_FUNDS"
    SOFT_ISSUER_TECHNICAL = "SOFT_ISSUER_TECHNICAL"
    SOFT_LIMIT_EXCEEDED = "SOFT_LIMIT_EXCEEDED"
    HARD_RISK_FLAGGED = "HARD_RISK_FLAGGED"
    HARD_DO_NOT_HONOR = "HARD_DO_NOT_HONOR"
    HARD_MANDATE_REVOKED = "HARD_MANDATE_REVOKED"
    EXPIRY_CARD_EXPIRED = "EXPIRY_CARD_EXPIRED"
    EXPIRY_MANDATE_EXPIRED = "EXPIRY_MANDATE_EXPIRED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_soft(self) -> bool:
        return self.name.startswith("SOFT_")

    @property
    def is_hard(self) -> bool:
        return self.name.startswith("HARD_")

    @property
    def is_expiry(self) -> bool:
        return self.name.startswith("EXPIRY_")


class DeclineCode(NamedTuple):
    """A gateway-shaped error tuple plus the true class that produced it."""

    code: str
    source: str
    step: str
    reason: str
    description: str
    true_class: DeclineClass


DECLINE_CODES: tuple[DeclineCode, ...] = (
    # --- soft: money wasn't there ------------------------------------------
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "customer",
        "payment_authorization",
        "insufficient_funds",
        "Your account has insufficient balance.",
        DeclineClass.SOFT_INSUFFICIENT_FUNDS,
    ),
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "bank",
        "payment_authorization",
        "payment_failed",
        "The payment could not be completed by the bank.",
        DeclineClass.SOFT_INSUFFICIENT_FUNDS,  # ambiguous with DO_NOT_HONOR below
    ),
    # --- soft: the rails were down -----------------------------------------
    DeclineCode(
        "GATEWAY_ERROR",
        "gateway",
        "payment_authorization",
        "gateway_technical_error",
        "The gateway did not respond in time.",
        DeclineClass.SOFT_ISSUER_TECHNICAL,
    ),
    DeclineCode(
        "GATEWAY_ERROR",
        "issuer",
        "payment_authorization",
        "issuer_down",
        "The issuing bank is temporarily unavailable.",
        DeclineClass.SOFT_ISSUER_TECHNICAL,
    ),
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "bank",
        "payment_authorization",
        "payment_failed",
        "The payment could not be completed by the bank.",
        DeclineClass.SOFT_ISSUER_TECHNICAL,  # same tuple as the two above/below
    ),
    # --- soft: over a per-txn or velocity limit ----------------------------
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "issuer",
        "payment_authorization",
        "payment_limit_exceeded",
        "The transaction exceeds the permitted limit.",
        DeclineClass.SOFT_LIMIT_EXCEEDED,
    ),
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "issuer",
        "payment_authorization",
        "debit_limit_exceeded",
        "The per-transaction debit limit was exceeded.",
        DeclineClass.SOFT_LIMIT_EXCEEDED,
    ),
    # --- hard: risk / fraud -------------------------------------------------
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "issuer",
        "payment_authorization",
        "card_reported_lost_or_stolen",
        "The card has been reported lost or stolen.",
        DeclineClass.HARD_RISK_FLAGGED,
    ),
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "business",
        "payment_authentication",
        "payment_risk_check_failed",
        "The payment failed a risk check.",
        DeclineClass.HARD_RISK_FLAGGED,
    ),
    # --- hard: issuer refuses ----------------------------------------------
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "issuer",
        "payment_authorization",
        "do_not_honor",
        "The issuing bank declined the transaction.",
        DeclineClass.HARD_DO_NOT_HONOR,
    ),
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "bank",
        "payment_authorization",
        "payment_failed",
        "The payment could not be completed by the bank.",
        DeclineClass.HARD_DO_NOT_HONOR,  # the ambiguous tuple, third owner
    ),
    # --- hard: the customer withdrew consent -------------------------------
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "customer",
        "payment_initiation",
        "mandate_revoked",
        "The customer has cancelled this mandate.",
        DeclineClass.HARD_MANDATE_REVOKED,
    ),
    # --- expiry: the instrument aged out ------------------------------------
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "customer",
        "payment_initiation",
        "card_expired",
        "The card has expired.",
        DeclineClass.EXPIRY_CARD_EXPIRED,
    ),
    DeclineCode(
        "BAD_REQUEST_ERROR",
        "customer",
        "payment_initiation",
        "mandate_expired",
        "The mandate validity period has ended.",
        DeclineClass.EXPIRY_MANDATE_EXPIRED,
    ),
    # --- genuinely uninformative --------------------------------------------
    DeclineCode(
        "GATEWAY_ERROR",
        "gateway",
        "payment_response",
        "server_error",
        "An unspecified error occurred.",
        DeclineClass.UNKNOWN,
    ),
)

#: ``(code, reason)`` tuples emitted by more than one true class. A classifier
#: seeing only the gateway payload cannot resolve these -- this is the error
#: floor that keeps our precision/recall numbers honest.
AMBIGUOUS_TUPLES: frozenset[tuple[str, str]] = frozenset(
    {
        (c.code, c.reason)
        for c in DECLINE_CODES
        if sum(1 for o in DECLINE_CODES if (o.code, o.reason) == (c.code, c.reason)) > 1
    }
)


def codes_for(decline_class: DeclineClass) -> tuple[DeclineCode, ...]:
    """Every gateway tuple that the given true class can produce."""
    return tuple(c for c in DECLINE_CODES if c.true_class is decline_class)
