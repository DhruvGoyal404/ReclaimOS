"""World-model behaviour.

These tests pin the qualitative shape of the simulator -- the asymmetries a
recovery policy is supposed to discover. If one of them ever fails, either the
world changed or a "fix" quietly removed the thing that made the problem hard.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from conftest import make_record, make_truth
from reclaimos.domain import IST, ActionType, DeclineClass, Method
from reclaimos.generator.outcome_model import (
    MAX_PROBABILITY,
    RETRY_BASE,
    hours_to_next_salary_credit,
    oracle_recovers,
    resolve,
    success_probability,
)

RETRY = ActionType.RETRY_CHARGE
LINK = ActionType.SEND_PAYMENT_LINK
CARD_UPDATE = ActionType.REQUEST_CARD_UPDATE


def _p(record: object, truth: object, action: ActionType, t: float = 48.0, **kw: int) -> float:
    return success_probability(
        record,  # type: ignore[arg-type]
        truth,  # type: ignore[arg-type]
        action,
        t,
        kw.get("charge_attempts", 0),
        kw.get("contact_actions", 0),
    )


# --- the core asymmetry: soft recovers, hard does not ----------------------


@pytest.mark.parametrize("cls", [c for c in DeclineClass if c.is_hard])
def test_retrying_a_hard_decline_is_near_hopeless(cls: DeclineClass) -> None:
    record = make_record(true_class=cls)
    truth = make_truth(record, cls)
    assert _p(record, truth, RETRY) < 0.05


@pytest.mark.parametrize("cls", [c for c in DeclineClass if c.is_soft])
def test_retrying_a_soft_decline_is_worth_doing(cls: DeclineClass) -> None:
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, funds_return_hours=1.0)
    assert _p(record, truth, RETRY) > 0.25


def test_probabilities_stay_inside_zero_and_the_cap() -> None:
    for cls in DeclineClass:
        record = make_record(true_class=cls, tenure_months=48)
        truth = make_truth(record, cls, base_intent=1.0)
        for action in ActionType:
            for t in (0.0, 1.0, 24.0, 200.0, 700.0):
                p = _p(record, truth, action, t)
                assert 0.0 <= p <= MAX_PROBABILITY


def test_stop_and_escalate_never_touch_the_world() -> None:
    record = make_record()
    truth = make_truth(record, DeclineClass.SOFT_INSUFFICIENT_FUNDS)
    assert _p(record, truth, ActionType.STOP) == 0.0
    assert _p(record, truth, ActionType.ESCALATE_HUMAN) == 0.0


# --- latent factors a policy cannot read -----------------------------------


def test_retrying_before_the_money_returns_is_much_worse() -> None:
    """The single most valuable thing a policy can get right about timing."""
    cls = DeclineClass.SOFT_INSUFFICIENT_FUNDS
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, funds_return_hours=60.0)
    too_early = _p(record, truth, RETRY, t=48.0)
    after = _p(record, truth, RETRY, t=72.0)
    assert after > 2 * too_early


def test_retrying_into_an_ongoing_outage_is_wasted() -> None:
    cls = DeclineClass.SOFT_ISSUER_TECHNICAL
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, outage_end_hours=36.0)
    during = _p(record, truth, RETRY, t=24.0)
    after = _p(record, truth, RETRY, t=48.0)
    assert after > 5 * during


def test_each_further_attempt_is_worth_less() -> None:
    cls = DeclineClass.SOFT_INSUFFICIENT_FUNDS
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, funds_return_hours=1.0)
    first = _p(record, truth, RETRY, charge_attempts=0)
    third = _p(record, truth, RETRY, charge_attempts=2)
    assert third < first / 2


# --- action fit: the interesting, non-obvious part -------------------------


def test_a_payment_link_is_weakest_for_insufficient_funds() -> None:
    """A link does not create money. A policy that treats outreach as a universal
    fallback should measurably lose here."""
    record = make_record()
    short = _p(record, make_truth(record, DeclineClass.SOFT_INSUFFICIENT_FUNDS), LINK)
    expired = _p(record, make_truth(record, DeclineClass.EXPIRY_CARD_EXPIRED), LINK)
    assert expired > short


def test_a_payment_link_beats_a_retry_on_a_hard_decline() -> None:
    cls = DeclineClass.HARD_DO_NOT_HONOR
    record = make_record(true_class=cls)
    truth = make_truth(record, cls)
    assert _p(record, truth, LINK) > _p(record, truth, RETRY)


def test_card_update_is_the_right_move_only_for_an_expired_card() -> None:
    cls = DeclineClass.EXPIRY_CARD_EXPIRED
    record = make_record(true_class=cls)
    truth = make_truth(record, cls)
    assert _p(record, truth, CARD_UPDATE) > _p(record, truth, RETRY)

    other = DeclineClass.SOFT_INSUFFICIENT_FUNDS
    record2 = make_record(true_class=other)
    truth2 = make_truth(record2, other, funds_return_hours=1.0)
    assert _p(record2, truth2, CARD_UPDATE) < _p(record2, truth2, RETRY)


@pytest.mark.parametrize("method", [Method.UPI_AUTOPAY, Method.EMANDATE])
def test_asking_for_a_card_update_off_the_card_rail_pays_nothing(method: Method) -> None:
    cls = DeclineClass.EXPIRY_CARD_EXPIRED
    record = make_record(true_class=cls, method=method)
    assert _p(record, make_truth(record, cls), CARD_UPDATE) == 0.0


def test_retry_base_rates_are_defined_for_every_class() -> None:
    assert set(RETRY_BASE) == set(DeclineClass)


# --- common random numbers --------------------------------------------------


def test_the_same_slot_always_faces_the_same_draw() -> None:
    """Two policies reaching slot 2 must face identical luck -- that is what makes
    a difference between them a difference in judgement."""
    cls = DeclineClass.SOFT_INSUFFICIENT_FUNDS
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, draw=0.31)
    a = resolve(record, truth, RETRY, 48.0, 2, 0, 0)
    b = resolve(record, truth, LINK, 96.0, 2, 1, 1)
    assert a.draw == b.draw == 0.31


def test_resolve_is_deterministic() -> None:
    cls = DeclineClass.SOFT_INSUFFICIENT_FUNDS
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, draw=0.2, funds_return_hours=1.0)
    first = resolve(record, truth, RETRY, 48.0, 0, 0, 0)
    second = resolve(record, truth, RETRY, 48.0, 0, 0, 0)
    assert first == second
    assert first.succeeded is (first.draw < first.probability)


def test_a_lucky_draw_succeeds_and_an_unlucky_one_does_not() -> None:
    cls = DeclineClass.SOFT_ISSUER_TECHNICAL
    record = make_record(true_class=cls)
    lucky = resolve(record, make_truth(record, cls, draw=0.01), RETRY, 48.0, 0, 0, 0)
    unlucky = resolve(record, make_truth(record, cls, draw=0.99), RETRY, 48.0, 0, 0, 0)
    assert lucky.succeeded and not unlucky.succeeded
    assert lucky.amount_paise == record.plan_amount_paise
    assert unlucky.amount_paise == 0


# --- salary-credit helper ---------------------------------------------------


def test_salary_credit_is_always_in_the_future_and_within_a_month() -> None:
    for day in (1, 15, 28):
        at = datetime(2026, 6, day, 3, 0, tzinfo=IST)
        hours = hours_to_next_salary_credit(at)
        assert 0 < hours <= 31 * 24


def test_salary_credit_rolls_over_the_year_boundary() -> None:
    assert 0 < hours_to_next_salary_credit(datetime(2026, 12, 20, 3, 0, tzinfo=IST)) <= 31 * 24


# --- the ceiling -------------------------------------------------------------


def test_the_ceiling_never_retries_against_an_expired_mandate() -> None:
    """A ceiling that ignored the consent envelope would not be a ceiling any real
    policy could reach."""
    cls = DeclineClass.EXPIRY_MANDATE_EXPIRED
    record = make_record(true_class=cls, mandate_expiry_offset_days=-5)
    truth = make_truth(record, cls, draw=0.05, base_intent=0.9)
    assert oracle_recovers(record, truth, 4).charge_attempts == 0


def test_the_ceiling_recovers_an_easy_record_and_not_an_impossible_one() -> None:
    easy_cls = DeclineClass.SOFT_ISSUER_TECHNICAL
    easy = make_record(true_class=easy_cls)
    assert oracle_recovers(easy, make_truth(easy, easy_cls, draw=0.05), 4).recovered

    hard_cls = DeclineClass.HARD_MANDATE_REVOKED
    hopeless = make_record(true_class=hard_cls)
    assert not oracle_recovers(
        hopeless, make_truth(hopeless, hard_cls, draw=0.99, base_intent=0.0), 4
    ).recovered


def test_the_ceiling_spends_actions_it_can_be_charged_for() -> None:
    cls = DeclineClass.HARD_DO_NOT_HONOR
    record = make_record(true_class=cls)
    result = oracle_recovers(record, make_truth(record, cls, draw=0.99), 4)
    assert not result.recovered
    assert result.charge_attempts + result.contact_actions == 4
