"""Baseline recovery policies.

These are written and measured **before** the agent exists (ADR-0006). If the
baselines were built afterwards, we could shape them -- consciously or not -- into
the shape our agent happens to beat. Fixing them first removes that option.

None of them is a straw man. "Retry everything once" is what a large share of
subscription businesses actually do, and "retry three times on a fixed schedule"
is the default in most billing platforms. The interesting question is not whether
a smarter policy beats doing nothing; it is by how much it beats the thing people
really ship, and at what cost in wasted attempts.
"""

from __future__ import annotations

from dataclasses import dataclass

from reclaimos.domain import ActionType, Decision, TerminalReason
from reclaimos.eval.policy import LoopState


@dataclass
class DoNothingPolicy:
    """Write off every failed charge immediately.

    The floor. Its recovery rate is zero by construction, which makes it the
    honest denominator for "money recovered that would otherwise have been lost".
    """

    name: str = "do_nothing"
    description: str = "Write off every failed charge immediately"

    def decide(self, state: LoopState) -> Decision:
        return Decision(
            subscription_id=state.record.subscription_id,
            attempt_no=state.slot + 1,
            action=ActionType.STOP,
            delay_hours=0.0,
            rule_id="baseline.do_nothing",
            propensity=0.0,
            predicted_recoverable=False,
            stop_reason=TerminalReason.NO_ACTION_TAKEN,
            rationale="Baseline: no recovery attempted.",
        )


@dataclass
class RetryOncePolicy:
    """One blind retry 24 hours later, for every record, whatever the decline.

    Claims every failed charge is recoverable, so its recall is 1.0 and its
    precision is just the base rate -- a useful reminder that recall alone is not
    a result.
    """

    delay_hours: float = 24.0
    name: str = "retry_once"
    description: str = "One blind retry at +24h for every record"

    def decide(self, state: LoopState) -> Decision:
        if state.charge_attempts >= 1:
            return _stop(state, "baseline.retry_once.exhausted", TerminalReason.POLICY_STOPPED)
        return Decision(
            subscription_id=state.record.subscription_id,
            attempt_no=state.slot + 1,
            action=ActionType.RETRY_CHARGE,
            delay_hours=self.delay_hours,
            rule_id="baseline.retry_once",
            propensity=0.5,
            predicted_recoverable=True,
            rationale="Baseline: retry once, no diagnosis.",
        )


@dataclass
class RetryThriceFixedPolicy:
    """Three retries on a fixed +24h / +24h / +24h ladder, no diagnosis.

    The billing-platform default. It burns three charge attempts on hard declines
    that were never going to authorise, which is exactly the cost the
    ``false_action_cost`` metric exists to price.
    """

    gaps_hours: tuple[float, ...] = (24.0, 24.0, 24.0)
    name: str = "retry_3x_fixed"
    description: str = "Three fixed retries at +24h intervals, no diagnosis"

    def decide(self, state: LoopState) -> Decision:
        if state.charge_attempts >= len(self.gaps_hours):
            return _stop(state, "baseline.retry_3x.exhausted", TerminalReason.POLICY_STOPPED)
        return Decision(
            subscription_id=state.record.subscription_id,
            attempt_no=state.slot + 1,
            action=ActionType.RETRY_CHARGE,
            delay_hours=self.gaps_hours[state.charge_attempts],
            rule_id=f"baseline.retry_3x.attempt_{state.charge_attempts + 1}",
            propensity=0.5,
            predicted_recoverable=True,
            rationale="Baseline: fixed retry ladder, no diagnosis.",
        )


@dataclass
class ContactOncePolicy:
    """Send one payment link and never retry the instrument.

    Included because it is the opposite failure mode to the retry ladders: zero
    wasted charge attempts and zero mandate risk, but it cannot recover the large
    soft-decline population where the customer simply had no money that morning
    and would have paid on the next attempt without being asked.
    """

    delay_hours: float = 24.0
    name: str = "contact_once"
    description: str = "One payment link at +24h, never retry the instrument"

    def decide(self, state: LoopState) -> Decision:
        if state.contact_actions >= 1:
            return _stop(state, "baseline.contact_once.exhausted", TerminalReason.POLICY_STOPPED)
        return Decision(
            subscription_id=state.record.subscription_id,
            attempt_no=state.slot + 1,
            action=ActionType.SEND_PAYMENT_LINK,
            delay_hours=self.delay_hours,
            rule_id="baseline.contact_once",
            propensity=0.5,
            predicted_recoverable=True,
            rationale="Baseline: outreach only, no instrument retry.",
        )


def _stop(state: LoopState, rule_id: str, reason: TerminalReason) -> Decision:
    return Decision(
        subscription_id=state.record.subscription_id,
        attempt_no=state.slot + 1,
        action=ActionType.STOP,
        delay_hours=0.0,
        rule_id=rule_id,
        propensity=0.0,
        predicted_recoverable=True,
        stop_reason=reason,
        rationale="Baseline: attempt budget exhausted.",
    )


def all_baselines() -> list[
    DoNothingPolicy | RetryOncePolicy | RetryThriceFixedPolicy | ContactOncePolicy
]:
    """Every baseline, in the order they are reported."""
    return [
        DoNothingPolicy(),
        RetryOncePolicy(),
        RetryThriceFixedPolicy(),
        ContactOncePolicy(),
    ]
