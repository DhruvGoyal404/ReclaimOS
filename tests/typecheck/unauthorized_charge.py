"""Deliberately-wrong code. Never imported; only ever fed to mypy.

ADR-0003 criterion 3 asks for proof that an unauthorised charge fails to
*typecheck*, not merely that some function raises at runtime. This file is that
proof's input: ``tests/test_mandate.py`` runs mypy over it and asserts the errors
are still reported.

If this file ever starts typechecking cleanly, the teeth have fallen out.
"""

from datetime import datetime

from reclaimos.domain import IST, Method
from reclaimos.policy.gateway import SimulatedGateway
from reclaimos.policy.mandate import ChargeRequest


def charge_without_authorisation() -> ChargeRequest:
    # No MandateToken. Must be a type error, not a runtime surprise.
    return ChargeRequest(
        subscription_id="sub_X",
        amount_paise=9_999_900,
        method=Method.CARD,
        idempotency_key="sub_X:1:retry_charge",
    )


def gateway_accepts_loose_figures() -> None:
    gateway = SimulatedGateway()
    # The gateway takes an authorised request and nothing else.
    gateway.charge(
        subscription_id="sub_X",
        amount_paise=9_999_900,
        method=Method.CARD,
    )


def token_forged_by_hand() -> None:
    from reclaimos.policy.mandate import MandateToken

    # `issuer` is required and private; no caller outside authorize() has it.
    MandateToken(
        fingerprint="deadbeef",
        amount_paise=9_999_900,
        method=Method.CARD,
        at=datetime(2026, 6, 1, tzinfo=IST),
        reason_code="subscription_recovery",
        signature="forged",
    )
