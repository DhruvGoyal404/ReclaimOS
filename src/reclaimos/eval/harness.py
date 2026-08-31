"""Runs a policy against the sealed world, one record at a time.

The harness is the only component that touches both sides. It hands the policy a
``LoopState`` containing nothing but observable facts, takes back a ``Decision``,
enforces the safety envelope itself, and only then asks the world what happened.

Enforcement lives here rather than inside the policies on purpose: a baseline that
ignores mandates must still be *stopped*, so that the violation is recorded as a
measured safety failure instead of an unmeasured one. The same will be true of our
own agent, which is the point.
"""

from __future__ import annotations

from datetime import timedelta

from reclaimos.domain import (
    ActionType,
    AttemptResult,
    Decision,
    RecordOutcome,
    SubscriptionRecord,
    TerminalReason,
)
from reclaimos.eval.costs import cost_of
from reclaimos.eval.policy import LoopState, Policy
from reclaimos.generator.outcome_model import (
    MAX_SLOTS,
    RECOVERY_WINDOW_HOURS,
    OracleResult,
    WorldRecord,
    oracle_recovers,
    resolve,
)

#: Terminal states in which the policy halted itself. The complement -- attempt
#: cap, closed window, blocked mandate -- means the harness had to stop it.
SELF_HALTED: frozenset[TerminalReason] = frozenset(
    {
        TerminalReason.RECOVERED,
        TerminalReason.POLICY_STOPPED,
        TerminalReason.HARD_DECLINE_STOP,
        TerminalReason.ESCALATED_TO_HUMAN,
        TerminalReason.NO_ACTION_TAKEN,
        TerminalReason.MANDATE_EXPIRED,
    }
)


def compute_oracles(
    records: list[SubscriptionRecord],
    world: dict[str, WorldRecord],
    max_attempts: int,
) -> dict[str, OracleResult]:
    """Ceiling results for a whole split, computed once.

    The ceiling depends only on the record and the sealed truth, never on the
    policy under test, so recomputing it per policy was pure waste -- and it was
    the dominant cost of a full evaluation.
    """
    return {
        r.subscription_id: oracle_recovers(r, world[r.subscription_id], max_attempts)
        for r in records
    }


def run_record(
    policy: Policy,
    record: SubscriptionRecord,
    truth: WorldRecord,
    oracle_max_attempts: int,
    oracle: OracleResult | None = None,
) -> RecordOutcome:
    """Run one subscription through one policy to a terminal state."""
    elapsed = 0.0
    charge_attempts = 0
    contact_actions = 0
    cost = 0
    hard_decline_retries = 0
    mandate_violations = 0
    decisions: list[Decision] = []
    history: list[tuple[Decision, AttemptResult]] = []

    predicted_recoverable = False
    recovered = False
    amount_recovered = 0
    hours_to_resolution: float | None = None
    terminal: TerminalReason

    while True:
        state = LoopState(
            record=record,
            elapsed_hours=elapsed,
            charge_attempts=charge_attempts,
            contact_actions=contact_actions,
            slot=len(history),
            history=tuple(history),
        )
        decision = policy.decide(state)
        if not decisions:
            predicted_recoverable = decision.predicted_recoverable
        decisions.append(decision)

        if decision.action is ActionType.STOP:
            if decision.stop_reason is not None:
                terminal = decision.stop_reason
            elif not history:
                terminal = TerminalReason.NO_ACTION_TAKEN
            else:
                terminal = TerminalReason.POLICY_STOPPED
            break

        if decision.action is ActionType.ESCALATE_HUMAN:
            terminal = TerminalReason.ESCALATED_TO_HUMAN
            break

        # --- safety envelope, enforced by the harness ----------------------
        if len(history) >= MAX_SLOTS:
            terminal = TerminalReason.ATTEMPT_CAP_REACHED
            break

        t_hours = elapsed + decision.delay_hours
        if t_hours > RECOVERY_WINDOW_HOURS:
            terminal = TerminalReason.RECOVERY_WINDOW_CLOSED
            break

        if decision.action.moves_money:
            at = record.charge_at + timedelta(hours=t_hours)
            if not record.mandate.permits(record.plan_amount_paise, record.method, at):
                # The action never reaches the gateway. It is still counted: an
                # attempted out-of-envelope debit is a safety failure whether or
                # not the guard caught it.
                mandate_violations += 1
                terminal = TerminalReason.MANDATE_VIOLATION_BLOCKED
                break

        # --- ask the world -------------------------------------------------
        result = resolve(
            record=record,
            truth=truth,
            action=decision.action,
            t_hours=t_hours,
            slot=len(history),
            charge_attempts=charge_attempts,
            contact_actions=contact_actions,
        )
        history.append((decision, result))
        cost += int(cost_of(decision.action))
        elapsed = t_hours

        if decision.action.moves_money:
            charge_attempts += 1
            if truth.true_class.is_hard:
                # Money spent on a decline that was never going to authorise.
                hard_decline_retries += 1
        else:
            contact_actions += 1

        if result.succeeded:
            recovered = True
            amount_recovered = record.plan_amount_paise
            hours_to_resolution = t_hours
            terminal = TerminalReason.RECOVERED
            break

    if oracle is None:
        oracle = oracle_recovers(record, truth, oracle_max_attempts)

    return RecordOutcome(
        subscription_id=record.subscription_id,
        recovered=recovered,
        amount_recovered_paise=amount_recovered,
        charge_attempts=charge_attempts,
        contact_actions=contact_actions,
        cost_paise=cost,
        hours_to_resolution=hours_to_resolution,
        terminal_reason=terminal,
        predicted_recoverable=predicted_recoverable,
        true_recoverable=oracle.recovered,
        true_class=truth.true_class,
        hard_decline_retries=hard_decline_retries,
        mandate_violations=mandate_violations,
        decisions=tuple(decisions),
    )


def run_policy(
    policy: Policy,
    records: list[SubscriptionRecord],
    world: dict[str, WorldRecord],
    oracle_max_attempts: int,
    oracles: dict[str, OracleResult] | None = None,
) -> list[RecordOutcome]:
    """Run a policy across a whole split."""
    return [
        run_record(
            policy,
            record,
            world[record.subscription_id],
            oracle_max_attempts,
            None if oracles is None else oracles[record.subscription_id],
        )
        for record in records
    ]


def run_oracle(
    records: list[SubscriptionRecord],
    world: dict[str, WorldRecord],
    oracle_max_attempts: int,
    oracles: dict[str, OracleResult] | None = None,
) -> list[RecordOutcome]:
    """The ceiling, computed by reading the sealed truth.

    **This is not a policy.** It sees ``funds_return_hours``, ``outage_end_hours``
    and ``base_intent``, none of which are observable from a record. It exists to
    answer "how much of the achievable money did we leave on the table", and it is
    labelled as truth-reading everywhere it is reported. Comparing our agent to it
    is honest; presenting it as a result would not be.

    It is charged the same per-action costs as every other policy, so its net
    figure is comparable rather than flattering.
    """
    outcomes: list[RecordOutcome] = []
    for record in records:
        truth = world[record.subscription_id]
        oracle = (
            oracle_recovers(record, truth, oracle_max_attempts)
            if oracles is None
            else oracles[record.subscription_id]
        )
        cost = oracle.charge_attempts * int(cost_of(ActionType.RETRY_CHARGE)) + (
            oracle.contact_actions * int(cost_of(ActionType.SEND_PAYMENT_LINK))
        )
        outcomes.append(
            RecordOutcome(
                subscription_id=record.subscription_id,
                recovered=oracle.recovered,
                amount_recovered_paise=record.plan_amount_paise if oracle.recovered else 0,
                charge_attempts=oracle.charge_attempts,
                contact_actions=oracle.contact_actions,
                cost_paise=cost,
                hours_to_resolution=oracle.hours if oracle.recovered else None,
                terminal_reason=(
                    TerminalReason.RECOVERED if oracle.recovered else TerminalReason.POLICY_STOPPED
                ),
                predicted_recoverable=oracle.recovered,
                true_recoverable=oracle.recovered,
                true_class=truth.true_class,
            )
        )
    return outcomes
