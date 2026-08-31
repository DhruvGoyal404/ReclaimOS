"""Mandate teeth, executor ordering, gateway boundary — ADR-0003 criteria 2 and 3.

Criterion 3 was flagged as the one most likely to be quietly downgraded to "there
is a runtime check". These tests are the thing that would notice.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import make_record
from reclaimos.domain import IST, ActionType, DeclineClass, Mandate, Method
from reclaimos.policy.executor import build_charge, execute_charge, execute_contact
from reclaimos.policy.gateway import GatewayResult, PaymentGateway, SimulatedGateway
from reclaimos.policy.mandate import (
    ChargeRequest,
    MandateToken,
    MandateViolation,
    authorize,
    permits,
)
from reclaimos.store import InMemoryIdempotencyStore

REPO = Path(__file__).resolve().parents[1]
AT = datetime(2026, 6, 5, 3, 0, tzinfo=IST)


def _mandate(**over: object) -> Mandate:
    base: dict[str, object] = {
        "max_amount_paise": 100_000,
        "expiry": datetime(2026, 12, 31, tzinfo=IST),
        "allowed_method": Method.CARD,
    }
    return Mandate(**{**base, **over})  # type: ignore[arg-type]


# --- criterion 3: structural, not runtime ------------------------------------


def test_an_unauthorised_charge_fails_to_typecheck() -> None:
    """ADR-0003 criterion 3, proven by running the type checker.

    ``tests/typecheck/unauthorized_charge.py`` builds a ChargeRequest with no
    token, calls the gateway with loose figures, and hand-rolls a MandateToken.
    All three must be rejected by mypy. If this test ever passes trivially because
    the snippet typechecks clean, the teeth have fallen out.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-error-summary",
            "tests/typecheck/unauthorized_charge.py",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"the wrong snippet typechecked clean:\n{output}"
    assert 'Missing positional argument "token" in call to "ChargeRequest"' in output
    assert 'Missing named argument "issuer" for "MandateToken"' in output
    assert 'Unexpected keyword argument "subscription_id" for "charge"' in output


def test_a_mandate_token_cannot_be_constructed_directly() -> None:
    """The private-constructor stand-in Python does not otherwise give us."""
    with pytest.raises(MandateViolation, match="cannot be constructed directly"):
        MandateToken(
            issuer=object(),
            fingerprint="deadbeef",
            amount_paise=49_900,
            method=Method.CARD,
            at=AT,
            reason_code="subscription_recovery",
            signature="forged",
        )


def test_a_token_smuggled_past_the_constructor_still_fails() -> None:
    """Third mechanism: even ``object.__new__`` produces a token that does not
    verify, because the signature is over the envelope it was issued for."""
    forged = object.__new__(MandateToken)
    forged.fingerprint = "deadbeef"  # type: ignore[misc]
    forged.amount_paise = 9_999_900  # type: ignore[misc]
    forged.method = Method.CARD  # type: ignore[misc]
    forged.at = AT  # type: ignore[misc]
    forged.reason_code = "subscription_recovery"  # type: ignore[misc]
    forged._signature = "forged"  # type: ignore[misc]

    assert not forged.verify()
    with pytest.raises(MandateViolation, match="signature is invalid"):
        ChargeRequest(
            forged,
            subscription_id="sub_X",
            amount_paise=9_999_900,
            method=Method.CARD,
            idempotency_key="k",
        )


def test_a_token_cannot_be_repointed_at_a_different_charge() -> None:
    """A token for INR 499 on a card must not authorise INR 99,999 on UPI."""
    token = authorize(_mandate(), amount_paise=49_900, method=Method.CARD, at=AT)

    with pytest.raises(MandateViolation, match="but this request is"):
        ChargeRequest(
            token,
            subscription_id="sub_X",
            amount_paise=99_900,
            method=Method.CARD,
            idempotency_key="k",
        )
    with pytest.raises(MandateViolation, match="but this request is"):
        ChargeRequest(
            token,
            subscription_id="sub_X",
            amount_paise=49_900,
            method=Method.UPI_AUTOPAY,
            idempotency_key="k",
        )


# --- the envelope itself -------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"amount_paise": 100_001}, "exceeds mandate cap"),
        ({"amount_paise": 0}, "must be positive"),
        ({"amount_paise": -1}, "must be positive"),
        ({"method": Method.UPI_AUTOPAY}, "is not the mandated"),
    ],
)
def test_authorize_refuses_out_of_envelope_charges(kwargs: dict[str, object], message: str) -> None:
    args: dict[str, object] = {"amount_paise": 49_900, "method": Method.CARD, "at": AT, **kwargs}
    with pytest.raises(MandateViolation, match=message):
        authorize(_mandate(), **args)  # type: ignore[arg-type]


def test_authorize_refuses_after_expiry() -> None:
    mandate = _mandate()
    with pytest.raises(MandateViolation, match="mandate expired"):
        authorize(
            mandate,
            amount_paise=49_900,
            method=Method.CARD,
            at=mandate.expiry + timedelta(seconds=1),
        )


def test_authorize_issues_a_verifying_token_inside_the_envelope() -> None:
    token = authorize(_mandate(), amount_paise=100_000, method=Method.CARD, at=AT)
    assert token.verify()
    assert token.amount_paise == 100_000


def test_permits_is_a_question_not_a_permission() -> None:
    mandate = _mandate()
    assert permits(mandate, 100_000, Method.CARD, AT)
    assert not permits(mandate, 100_001, Method.CARD, AT)
    assert not permits(mandate, 100_000, Method.UPI_AUTOPAY, AT)


# --- criterion 2: mandate before idempotency ----------------------------------


def test_a_refused_charge_never_burns_an_idempotency_key() -> None:
    """ADR-0003 criterion 2, and the reason the ordering is not a preference.

    A burnt key would be worse than the refusal: the next legitimate attempt at
    that logical action would find the key claimed and skip itself, silently and
    permanently.
    """
    record = make_record(plan_amount_paise=49_900, mandate_multiple=1)
    store = InMemoryIdempotencyStore()

    with pytest.raises(MandateViolation):
        build_charge(record, attempt_no=1, at=AT, amount_paise=99_999_900)

    assert store.count() == 0, "a refused charge claimed a key"


def test_an_expired_mandate_refuses_before_any_key_is_claimed() -> None:
    record = make_record(mandate_expiry_offset_days=-1)
    store = InMemoryIdempotencyStore()

    with pytest.raises(MandateViolation, match="mandate expired"):
        build_charge(record, attempt_no=1, at=record.charge_at)

    assert store.count() == 0


def test_the_gateway_is_never_reached_by_a_refused_charge() -> None:
    record = make_record(mandate_multiple=1)
    gateway = SimulatedGateway(charge_succeeds=True)

    with pytest.raises(MandateViolation):
        request = build_charge(record, attempt_no=1, at=AT, amount_paise=99_999_900)
        execute_charge(request, gateway, InMemoryIdempotencyStore())

    assert gateway.charges == []


# --- the executor ---------------------------------------------------------------


def test_a_permitted_charge_claims_a_key_then_calls_the_gateway() -> None:
    record = make_record()
    store = InMemoryIdempotencyStore()
    gateway = SimulatedGateway(charge_succeeds=True)

    request = build_charge(record, attempt_no=1, at=record.charge_at)
    receipt = execute_charge(request, gateway, store)

    assert receipt.executed and receipt.succeeded and not receipt.replayed
    assert receipt.amount_paise == record.plan_amount_paise
    assert len(gateway.charges) == 1
    claim = store.get(request.idempotency_key)
    assert claim is not None and claim.completed


def test_a_replayed_charge_returns_the_recorded_result_without_charging_again() -> None:
    """The whole of ADR-0004: a webhook replay cannot produce a second debit."""
    record = make_record()
    store = InMemoryIdempotencyStore()
    gateway = SimulatedGateway(charge_succeeds=True)

    first = execute_charge(build_charge(record, 1, record.charge_at), gateway, store)
    second = execute_charge(build_charge(record, 1, record.charge_at), gateway, store)

    assert first.executed and not first.replayed
    assert second.replayed and not second.executed
    assert second.succeeded == first.succeeded
    assert len(gateway.charges) == 1, "the gateway was called twice for one logical action"


def test_one_hundred_and_fifty_replays_produce_exactly_one_charge() -> None:
    record = make_record()
    store = InMemoryIdempotencyStore()
    gateway = SimulatedGateway(charge_succeeds=True)

    receipts = [
        execute_charge(build_charge(record, 1, record.charge_at), gateway, store)
        for _ in range(150)
    ]

    assert sum(1 for r in receipts if r.executed) == 1
    assert len(gateway.charges) == 1


def test_distinct_attempts_are_distinct_actions() -> None:
    record = make_record()
    store = InMemoryIdempotencyStore()
    gateway = SimulatedGateway(charge_succeeds=False)

    execute_charge(build_charge(record, 1, record.charge_at), gateway, store)
    execute_charge(build_charge(record, 2, record.charge_at), gateway, store)

    assert len(gateway.charges) == 2
    assert store.count() == 2


# --- outreach --------------------------------------------------------------------


def test_outreach_needs_no_mandate_but_still_claims_a_key() -> None:
    """A link does not debit anyone. Messaging a customer four times because a
    webhook was redelivered four times is still its own kind of harm."""
    record = make_record(mandate_expiry_offset_days=-5)
    store = InMemoryIdempotencyStore()
    gateway = SimulatedGateway()

    first = execute_contact(record, ActionType.SEND_PAYMENT_LINK, 1, gateway, store)
    second = execute_contact(record, ActionType.SEND_PAYMENT_LINK, 1, gateway, store)

    assert first.executed and second.replayed
    assert len(gateway.contacts) == 1


def test_a_money_moving_action_cannot_sneak_through_the_contact_path() -> None:
    record = make_record()
    with pytest.raises(MandateViolation, match="must go through execute_charge"):
        execute_contact(
            record, ActionType.RETRY_CHARGE, 1, SimulatedGateway(), InMemoryIdempotencyStore()
        )


def test_the_simulated_gateway_satisfies_the_protocol() -> None:
    assert isinstance(SimulatedGateway(), PaymentGateway)


def test_a_failed_charge_records_zero_recovered() -> None:
    record = make_record()
    store = InMemoryIdempotencyStore()
    receipt = execute_charge(
        build_charge(record, 1, record.charge_at), SimulatedGateway(charge_succeeds=False), store
    )
    assert receipt.executed and not receipt.succeeded
    assert receipt.amount_paise == 0


def test_the_ledger_payload_carries_the_authorisation_trail() -> None:
    record = make_record(true_class=DeclineClass.SOFT_INSUFFICIENT_FUNDS)
    payload = build_charge(record, 1, record.charge_at).as_ledger_payload()
    assert set(payload) >= {
        "subscription_id",
        "amount_paise",
        "idempotency_key",
        "mandate_fingerprint",
        "authorised_at",
    }
    assert isinstance(payload["amount_paise"], int)


def test_gateway_results_are_typed() -> None:
    assert GatewayResult(succeeded=True).succeeded is True
