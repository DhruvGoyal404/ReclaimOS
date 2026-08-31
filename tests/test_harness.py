"""Harness behaviour: the safety envelope, and the wall between policy and truth."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from conftest import make_record, make_truth
from reclaimos.domain import ActionType, Decision, DeclineClass, TerminalReason
from reclaimos.eval.baselines import (
    ContactOncePolicy,
    DoNothingPolicy,
    RetryOncePolicy,
    RetryThriceFixedPolicy,
)
from reclaimos.eval.costs import CHARGE_ATTEMPT_COST, CONTACT_COST
from reclaimos.eval.harness import run_record
from reclaimos.eval.policy import LoopState
from reclaimos.generator.outcome_model import MAX_SLOTS, RECOVERY_WINDOW_HOURS


@dataclass
class RunawayPolicy:
    """Retries forever with no delay. Exists to prove the harness stops it."""

    name: str = "runaway"
    description: str = "Never stops. The harness must."

    def decide(self, state: LoopState) -> Decision:
        return Decision(
            subscription_id=state.record.subscription_id,
            attempt_no=state.slot + 1,
            action=ActionType.RETRY_CHARGE,
            delay_hours=1.0,
            rule_id="test.runaway",
            propensity=1.0,
            predicted_recoverable=True,
        )


@dataclass
class DawdlingPolicy:
    """Waits far past the write-off window before acting."""

    name: str = "dawdling"
    description: str = "Acts after the recovery window has closed"

    def decide(self, state: LoopState) -> Decision:
        return Decision(
            subscription_id=state.record.subscription_id,
            attempt_no=state.slot + 1,
            action=ActionType.RETRY_CHARGE,
            delay_hours=RECOVERY_WINDOW_HOURS + 1.0,
            rule_id="test.dawdling",
            propensity=1.0,
            predicted_recoverable=True,
        )


# --- the wall between a policy and the truth --------------------------------


def test_loop_state_exposes_no_ground_truth() -> None:
    """Structural, not aspirational: a policy cannot read the world because the
    world is not reachable from anything it is handed."""
    names = {f.name for f in dataclasses.fields(LoopState)}
    assert names == {
        "record",
        "elapsed_hours",
        "charge_attempts",
        "contact_actions",
        "slot",
        "history",
    }
    annotations = " ".join(str(f.type) for f in dataclasses.fields(LoopState))
    for leaked in ("WorldRecord", "true_class", "base_intent", "draws"):
        assert leaked not in annotations


# --- terminal states ---------------------------------------------------------


def test_do_nothing_spends_nothing_and_says_why() -> None:
    record = make_record()
    truth = make_truth(record, DeclineClass.SOFT_INSUFFICIENT_FUNDS, draw=0.01)
    outcome = run_record(DoNothingPolicy(), record, truth, 4)

    assert outcome.terminal_reason is TerminalReason.NO_ACTION_TAKEN
    assert not outcome.recovered
    assert outcome.charge_attempts == outcome.contact_actions == 0
    assert outcome.cost_paise == 0
    assert outcome.predicted_recoverable is False


def test_a_successful_retry_records_the_money_and_the_time() -> None:
    cls = DeclineClass.SOFT_ISSUER_TECHNICAL
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, draw=0.01)
    outcome = run_record(RetryOncePolicy(), record, truth, 4)

    assert outcome.recovered
    assert outcome.terminal_reason is TerminalReason.RECOVERED
    assert outcome.amount_recovered_paise == record.plan_amount_paise
    assert outcome.hours_to_resolution == 24.0
    assert outcome.cost_paise == int(CHARGE_ATTEMPT_COST)
    assert outcome.net_recovered_paise == record.plan_amount_paise - int(CHARGE_ATTEMPT_COST)


def test_an_exhausted_policy_stops_itself() -> None:
    cls = DeclineClass.HARD_DO_NOT_HONOR
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, draw=0.99)
    outcome = run_record(RetryThriceFixedPolicy(), record, truth, 4)

    assert outcome.terminal_reason is TerminalReason.POLICY_STOPPED
    assert outcome.charge_attempts == 3


# --- the safety envelope -----------------------------------------------------


def test_an_expired_mandate_blocks_the_charge_and_is_counted() -> None:
    """The guard catching it does not make it a non-event: an attempted
    out-of-envelope debit is a safety failure and is reported as one."""
    cls = DeclineClass.EXPIRY_MANDATE_EXPIRED
    record = make_record(true_class=cls, mandate_expiry_offset_days=-5)
    truth = make_truth(record, cls, draw=0.01)
    outcome = run_record(RetryOncePolicy(), record, truth, 4)

    assert outcome.terminal_reason is TerminalReason.MANDATE_VIOLATION_BLOCKED
    assert outcome.mandate_violations == 1
    assert outcome.charge_attempts == 0
    assert outcome.cost_paise == 0


def test_outreach_is_never_blocked_by_a_mandate() -> None:
    """A payment link does not debit anyone, so consent limits do not apply."""
    cls = DeclineClass.EXPIRY_MANDATE_EXPIRED
    record = make_record(true_class=cls, mandate_expiry_offset_days=-5)
    truth = make_truth(record, cls, draw=0.99)
    outcome = run_record(ContactOncePolicy(), record, truth, 4)

    assert outcome.mandate_violations == 0
    assert outcome.contact_actions == 1
    assert outcome.cost_paise == int(CONTACT_COST)


def test_the_harness_stops_a_policy_that_will_not_stop_itself() -> None:
    cls = DeclineClass.HARD_RISK_FLAGGED
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, draw=0.999)
    outcome = run_record(RunawayPolicy(), record, truth, 4)

    assert outcome.terminal_reason is TerminalReason.ATTEMPT_CAP_REACHED
    assert outcome.charge_attempts == MAX_SLOTS


def test_acting_past_the_write_off_window_is_refused() -> None:
    cls = DeclineClass.SOFT_INSUFFICIENT_FUNDS
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, draw=0.01, funds_return_hours=1.0)
    outcome = run_record(DawdlingPolicy(), record, truth, 4)

    assert outcome.terminal_reason is TerminalReason.RECOVERY_WINDOW_CLOSED
    assert outcome.charge_attempts == 0


# --- false-action accounting -------------------------------------------------


def test_retries_against_hard_declines_are_counted_as_false_actions() -> None:
    cls = DeclineClass.HARD_RISK_FLAGGED
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, draw=0.99)
    outcome = run_record(RetryThriceFixedPolicy(), record, truth, 4)
    assert outcome.hard_decline_retries == 3


def test_retries_against_soft_declines_are_not_false_actions() -> None:
    cls = DeclineClass.SOFT_INSUFFICIENT_FUNDS
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, draw=0.99, funds_return_hours=500.0)
    outcome = run_record(RetryThriceFixedPolicy(), record, truth, 4)
    assert outcome.charge_attempts == 3
    assert outcome.hard_decline_retries == 0


def test_outreach_is_never_a_hard_decline_retry() -> None:
    cls = DeclineClass.HARD_MANDATE_REVOKED
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, draw=0.99)
    outcome = run_record(ContactOncePolicy(), record, truth, 4)
    assert outcome.hard_decline_retries == 0


# --- the audit trail ---------------------------------------------------------


def test_every_decision_names_the_rule_that_fired() -> None:
    cls = DeclineClass.SOFT_INSUFFICIENT_FUNDS
    record = make_record(true_class=cls)
    truth = make_truth(record, cls, draw=0.99, funds_return_hours=500.0)
    outcome = run_record(RetryThriceFixedPolicy(), record, truth, 4)

    assert outcome.decisions
    for decision in outcome.decisions:
        assert decision.rule_id
        assert decision.subscription_id == record.subscription_id
        if decision.action is ActionType.STOP:
            assert decision.stop_reason is not None
