"""Domain-model invariants."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from reclaimos.domain import (
    AMBIGUOUS_TUPLES,
    DECLINE_CODES,
    IST,
    ActionType,
    DeclineClass,
    Mandate,
    Method,
    PaymentAttempt,
    SubscriptionRecord,
    codes_for,
)


def _mandate(**over: object) -> Mandate:
    base: dict[str, object] = {
        "max_amount_paise": 100_000,
        "expiry": datetime(2026, 12, 31, tzinfo=IST),
        "allowed_method": Method.CARD,
    }
    return Mandate(**{**base, **over})  # type: ignore[arg-type]


def test_every_decline_class_has_at_least_one_code() -> None:
    for cls in DeclineClass:
        assert codes_for(cls), f"{cls} has no gateway tuple"


def test_true_class_partitions_the_code_table() -> None:
    assert sum(len(codes_for(c)) for c in DeclineClass) == len(DECLINE_CODES)


def test_ambiguous_tuples_are_genuinely_shared() -> None:
    """The error floor must exist, or precision/recall would be free marks."""
    assert AMBIGUOUS_TUPLES, "no ambiguous tuples: the classifier task is too easy"
    for code, reason in AMBIGUOUS_TUPLES:
        owners = {c.true_class for c in DECLINE_CODES if (c.code, c.reason) == (code, reason)}
        assert len(owners) > 1


def test_decline_class_predicates_are_exhaustive_and_exclusive() -> None:
    for cls in DeclineClass:
        flags = [cls.is_soft, cls.is_hard, cls.is_expiry]
        assert sum(flags) <= 1
        if cls is not DeclineClass.UNKNOWN:
            assert any(flags), f"{cls} belongs to no family"


def test_only_retry_charge_moves_money() -> None:
    movers = [a for a in ActionType if a.moves_money]
    assert movers == [ActionType.RETRY_CHARGE]


def test_mandate_refuses_amount_method_and_expiry_breaches() -> None:
    m = _mandate()
    now = datetime(2026, 6, 1, tzinfo=IST)
    assert m.permits(100_000, Method.CARD, now)
    assert not m.permits(100_001, Method.CARD, now)
    assert not m.permits(100_000, Method.UPI_AUTOPAY, now)
    assert not m.permits(100_000, Method.CARD, m.expiry + timedelta(seconds=1))


def test_mandate_rejects_non_positive_cap() -> None:
    with pytest.raises(ValidationError):
        _mandate(max_amount_paise=0)


def test_subscription_rejects_naive_datetime() -> None:
    attempt = PaymentAttempt(
        attempt_no=1, occurred_at=datetime(2026, 6, 1, tzinfo=IST), amount_paise=49900
    )
    with pytest.raises(ValidationError):
        SubscriptionRecord(
            subscription_id="sub_x",
            customer_id="cust_x",
            plan_id="plan_x",
            method=Method.CARD,
            plan_amount_paise=49900,
            billing_cycle_day=5,
            charge_at=datetime(2026, 6, 1),  # naive -- must be rejected
            customer_tenure_months=3,
            prior_success_count=2,
            prior_failure_count=1,
            mandate=_mandate(),
            failed_attempt=attempt,
        )
