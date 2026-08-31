"""Shared fixtures. Everything here is seeded; no test may depend on wall time."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from reclaimos.domain import (
    IST,
    DeclineClass,
    Mandate,
    Method,
    PaymentAttempt,
    SubscriptionRecord,
    codes_for,
)
from reclaimos.generator import WorldRecord, generate
from reclaimos.generator.outcome_model import MAX_SLOTS
from reclaimos.store import Database

SEED = 7
N = 120


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """A throwaway SQLite database, schema applied, one per test."""
    return Database(tmp_path / "reclaimos.db")


@pytest.fixture(scope="session")
def dataset() -> tuple[list[SubscriptionRecord], dict[str, WorldRecord]]:
    """A small seeded dataset, generated once for the whole session."""
    return generate(N, SEED)


def make_record(
    *,
    true_class: DeclineClass = DeclineClass.SOFT_INSUFFICIENT_FUNDS,
    method: Method = Method.CARD,
    plan_amount_paise: int = 49_900,
    tenure_months: int = 12,
    mandate_expiry_offset_days: int = 365,
    mandate_multiple: int = 2,
    charge_at: datetime | None = None,
) -> SubscriptionRecord:
    """Build one fully-specified record for a targeted test."""
    at = charge_at or datetime(2026, 6, 5, 3, 0, tzinfo=IST)
    code = codes_for(true_class)[0]
    return SubscriptionRecord(
        subscription_id=f"sub_TEST{true_class.name[:8]}",
        customer_id="cust_TEST0000000",
        plan_id="plan_TEST0000000",
        method=method,
        plan_amount_paise=plan_amount_paise,
        billing_cycle_day=at.day,
        charge_at=at,
        customer_tenure_months=tenure_months,
        prior_success_count=tenure_months,
        prior_failure_count=0,
        mandate=Mandate(
            max_amount_paise=plan_amount_paise * mandate_multiple,
            expiry=at + timedelta(days=mandate_expiry_offset_days),
            allowed_method=method,
        ),
        failed_attempt=PaymentAttempt(
            attempt_no=1,
            occurred_at=at,
            amount_paise=plan_amount_paise,
            succeeded=False,
            error_code=code.code,
            error_source=code.source,
            error_step=code.step,
            error_reason=code.reason,
            error_description=code.description,
        ),
    )


def make_truth(
    record: SubscriptionRecord,
    true_class: DeclineClass,
    *,
    draw: float = 0.5,
    funds_return_hours: float = 0.0,
    outage_end_hours: float = 0.0,
    base_intent: float = 0.5,
) -> WorldRecord:
    """Sealed truth with every latent pinned, so a test asserts one thing."""
    return WorldRecord(
        subscription_id=record.subscription_id,
        true_class=true_class,
        funds_return_hours=funds_return_hours,
        outage_end_hours=outage_end_hours,
        base_intent=base_intent,
        draws=tuple([draw] * MAX_SLOTS),
    )
