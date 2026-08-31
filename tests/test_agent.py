"""The agent's safety properties and the HITL contract.

The ordering claim from ADR-0003 criterion 4 is measured here rather than
asserted: the agent's mandate violations are **zero** because it refuses the
action itself, so the harness's defence-in-depth gate never sees one. The
baselines' are not, because they never ask.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from conftest import make_record, make_truth
from reclaimos.domain import IST, ActionType, DeclineClass, Method, TerminalReason
from reclaimos.eval.harness import run_record
from reclaimos.eval.policy import LoopState
from reclaimos.policy import AgentConfig, ReclaimAgent, ReviewQueue, ReviewStatus
from reclaimos.policy.timing import hours_until_month_turn

CONFIG = AgentConfig()


def _state(record: object, **over: object) -> LoopState:
    return LoopState(record=record, **over)  # type: ignore[arg-type]


def _agent(**over: object) -> ReclaimAgent:
    return ReclaimAgent(config=CONFIG.model_copy(update=over))


# --- stopping rules come first ------------------------------------------------


@pytest.mark.parametrize("cls", [c for c in DeclineClass if c.is_hard])
def test_a_hard_decline_is_never_retried(cls: DeclineClass) -> None:
    """The single most important behaviour in the system."""
    record = make_record(true_class=cls)
    agent = _agent()

    for charge_attempts in range(4):
        for contacts in range(3):
            decision = agent.decide(
                _state(record, charge_attempts=charge_attempts, contact_actions=contacts)
            )
            assert decision.action is not ActionType.RETRY_CHARGE, (cls, charge_attempts)


@pytest.mark.parametrize("cls", [c for c in DeclineClass if c.is_hard])
def test_a_hard_decline_gets_one_ask_then_stops(cls: DeclineClass) -> None:
    """Never a retry ladder, but the customer can still choose another instrument."""
    record = make_record(true_class=cls)
    agent = _agent()

    first = agent.decide(_state(record))
    assert first.action is ActionType.SEND_PAYMENT_LINK

    exhausted = agent.decide(
        _state(record, contact_actions=CONFIG.max_contact_actions, elapsed_hours=48.0)
    )
    assert exhausted.action is ActionType.STOP
    assert exhausted.stop_reason is TerminalReason.HARD_DECLINE_STOP


def test_an_expired_card_is_routed_to_an_update_not_a_retry() -> None:
    record = make_record(true_class=DeclineClass.EXPIRY_CARD_EXPIRED, method=Method.CARD)
    decision = _agent().decide(_state(record))
    assert decision.action is ActionType.REQUEST_CARD_UPDATE


def test_the_recovery_window_closes_the_loop() -> None:
    record = make_record()
    decision = _agent().decide(_state(record, elapsed_hours=CONFIG.recovery_window_hours + 1))
    assert decision.action is ActionType.STOP
    assert decision.stop_reason is TerminalReason.RECOVERY_WINDOW_CLOSED


def test_the_agent_always_terminates() -> None:
    """No configuration may produce a policy that never stops."""
    for cls in DeclineClass:
        record = make_record(true_class=cls)
        truth = make_truth(record, cls, draw=0.999)
        outcome = run_record(_agent(), record, truth, 4)
        assert outcome.terminal_reason is not TerminalReason.ATTEMPT_CAP_REACHED, cls


# --- the mandate gate fires in the agent, not in the harness -------------------


def test_the_agent_never_proposes_a_charge_the_mandate_forbids() -> None:
    """ADR-0003 criterion 4, measured.

    The agent asks ``authorize`` before proposing, so the harness's
    defence-in-depth gate never has to fire. A violation counted here would mean
    the executor's gate had been bypassed.
    """
    record = make_record(
        true_class=DeclineClass.EXPIRY_MANDATE_EXPIRED, mandate_expiry_offset_days=-5
    )
    truth = make_truth(record, DeclineClass.EXPIRY_MANDATE_EXPIRED, draw=0.99)
    outcome = run_record(_agent(), record, truth, 4)

    assert outcome.mandate_violations == 0
    assert outcome.terminal_reason is not TerminalReason.MANDATE_VIOLATION_BLOCKED
    assert all(d.action is not ActionType.RETRY_CHARGE for d in outcome.decisions)


def test_a_lapsed_mandate_still_allows_outreach() -> None:
    """Consent limits debits. They do not stop us asking."""
    record = make_record(
        true_class=DeclineClass.EXPIRY_MANDATE_EXPIRED, mandate_expiry_offset_days=-5
    )
    decision = _agent().decide(_state(record))
    assert decision.action is ActionType.SEND_PAYMENT_LINK


def test_the_agent_beats_the_baselines_on_mandate_safety(dataset: object) -> None:
    records, world = dataset  # type: ignore[misc]
    from reclaimos.eval.baselines import RetryThriceFixedPolicy
    from reclaimos.eval.harness import compute_oracles, run_policy

    oracles = compute_oracles(records, world, 4)
    agent = run_policy(_agent(), records, world, 4, oracles)
    ladder = run_policy(RetryThriceFixedPolicy(), records, world, 4, oracles)

    assert sum(o.mandate_violations for o in agent) == 0
    assert sum(o.mandate_violations for o in ladder) > 0

    agent_hard = sum(o.hard_decline_retries for o in agent)
    ladder_hard = sum(o.hard_decline_retries for o in ladder)
    assert agent_hard < ladder_hard / 3, f"{agent_hard} vs {ladder_hard}"


def test_the_agents_remaining_hard_retries_are_exactly_the_ambiguity_floor(
    dataset: object,
) -> None:
    """Not zero, and it should not be.

    A handful of hard declines arrive on a gateway tuple that soft declines also
    emit. No classifier reading only the payload can separate them, so a retry
    there is not a mistake the agent could have avoided -- it is the error floor
    the taxonomy deliberately preserves (ADR-0006).

    The property worth asserting is therefore not "zero hard retries" but "every
    hard retry is on an unresolvable tuple". If the agent ever retries a hard
    decline whose tuple was unambiguous, that IS a bug and this fails.
    """
    records, world = dataset  # type: ignore[misc]
    from reclaimos.domain import AMBIGUOUS_TUPLES
    from reclaimos.eval.harness import run_policy

    by_id = {r.subscription_id: r for r in records}
    outcomes = run_policy(_agent(), records, world, 4)

    offenders = [o for o in outcomes if o.hard_decline_retries > 0]
    assert offenders, "no hard retries at all; the floor is untested"

    for outcome in offenders:
        attempt = by_id[outcome.subscription_id].failed_attempt
        assert (attempt.error_code, attempt.error_reason) in AMBIGUOUS_TUPLES, (
            f"{outcome.subscription_id} was retried on an unambiguous hard decline "
            f"({attempt.error_reason}) -- that is a real mistake, not the floor"
        )


# --- the human gate --------------------------------------------------------------


def test_a_large_charge_is_escalated_rather_than_executed() -> None:
    record = make_record(plan_amount_paise=1_299_900, mandate_multiple=2)
    queue = ReviewQueue()
    agent = ReclaimAgent(config=CONFIG, queue=queue)

    decision = agent.decide(_state(record))

    assert decision.action is ActionType.ESCALATE_HUMAN
    assert decision.rule_id == "agent.gate.amount_threshold"
    assert len(queue.pending()) == 1
    assert queue.gated_paise() == 1_299_900


def test_an_escalated_action_does_not_execute() -> None:
    """The contract: a gate that lets the action through while someone thinks
    about it is not a gate."""
    record = make_record(plan_amount_paise=1_299_900, mandate_multiple=2)
    truth = make_truth(record, DeclineClass.SOFT_INSUFFICIENT_FUNDS, draw=0.001)
    queue = ReviewQueue()

    outcome = run_record(ReclaimAgent(config=CONFIG, queue=queue), record, truth, 4)

    assert outcome.terminal_reason is TerminalReason.ESCALATED_TO_HUMAN
    assert outcome.charge_attempts == 0
    assert not outcome.recovered, "an escalated charge was executed anyway"
    assert outcome.cost_paise == 0


def test_escalating_one_record_does_not_block_the_others(dataset: object) -> None:
    records, world = dataset  # type: ignore[misc]
    from reclaimos.eval.harness import run_policy

    queue = ReviewQueue()
    outcomes = run_policy(ReclaimAgent(config=CONFIG, queue=queue), records, world, 4)

    escalated = [o for o in outcomes if o.terminal_reason is TerminalReason.ESCALATED_TO_HUMAN]
    assert escalated, "the threshold never fired; the test proves nothing"
    assert len(outcomes) == len(records)
    assert any(o.recovered for o in outcomes), "the batch stopped making progress"


def test_a_small_charge_is_not_escalated() -> None:
    record = make_record(plan_amount_paise=49_900)
    decision = _agent().decide(_state(record))
    assert decision.action is not ActionType.ESCALATE_HUMAN


# --- the review queue itself ------------------------------------------------------


def test_review_items_start_pending_and_resolve_once() -> None:
    queue = ReviewQueue()
    item = queue.submit("sub_X", ActionType.RETRY_CHARGE, 700_000, "over limit", "rule.x")

    assert item.pending and queue.gated_paise() == 700_000
    approved = queue.approve(item.review_id)
    assert approved.status is ReviewStatus.APPROVED
    assert approved.resolved_at is not None
    assert queue.gated_paise() == 0

    with pytest.raises(ValueError, match="already approved"):
        queue.approve(item.review_id)


def test_a_rejected_item_is_also_terminal() -> None:
    queue = ReviewQueue()
    item = queue.submit("sub_X", ActionType.RETRY_CHARGE, 700_000, "over limit", "rule.x")
    assert queue.reject(item.review_id).status is ReviewStatus.REJECTED
    with pytest.raises(ValueError, match="already rejected"):
        queue.reject(item.review_id)


# --- timing ----------------------------------------------------------------------


def test_month_turn_arithmetic_is_sane() -> None:
    assert 0 < hours_until_month_turn(datetime(2026, 6, 15, 3, 0, tzinfo=IST)) <= 31 * 24
    assert 0 < hours_until_month_turn(datetime(2026, 12, 20, 3, 0, tzinfo=IST)) <= 31 * 24
    early = hours_until_month_turn(datetime(2026, 6, 1, 0, 0, tzinfo=IST))
    late = hours_until_month_turn(datetime(2026, 6, 28, 0, 0, tzinfo=IST))
    assert early > late


def test_insufficient_funds_waits_for_the_month_to_turn_when_it_is_close() -> None:
    """A short balance is a timing problem; the calendar is on the record."""
    late = make_record(
        true_class=DeclineClass.SOFT_INSUFFICIENT_FUNDS,
        charge_at=datetime(2026, 6, 26, 3, 0, tzinfo=IST),
    )
    decision = _agent().decide(_state(late))
    assert decision.action is ActionType.RETRY_CHARGE
    # ~5 days to the month turn, so the agent should wait past the default 48h.
    assert decision.delay_hours > 48.0


def test_a_technical_decline_is_retried_after_the_outage_window() -> None:
    record = make_record(true_class=DeclineClass.SOFT_ISSUER_TECHNICAL)
    decision = _agent().decide(_state(record))
    assert decision.action is ActionType.RETRY_CHARGE
    assert decision.delay_hours >= CONFIG.technical_retry_delay_hours


# --- determinism and explainability -------------------------------------------------


def test_the_agent_is_deterministic() -> None:
    record = make_record()
    a, b = _agent(), _agent()
    assert a.decide(_state(record)) == b.decide(_state(record))


def test_every_decision_names_a_rule_and_shows_its_reasoning() -> None:
    for cls in DeclineClass:
        record = make_record(true_class=cls)
        decision = _agent().decide(_state(record))
        assert decision.rule_id.startswith("agent.")
        assert decision.rationale
        if decision.action is ActionType.STOP:
            assert decision.stop_reason is not None


def test_ambiguity_costs_a_charge_attempt() -> None:
    """Uncertainty should cost attempts, not customers."""
    cautious = _agent(ambiguity_shortens_ladder=True)
    blunt = _agent(ambiguity_shortens_ladder=False)
    record = make_record(true_class=DeclineClass.SOFT_INSUFFICIENT_FUNDS)

    # index 1 is the tuple shared with a hard decline
    from reclaimos.diagnose import classify

    ambiguous = record.failed_attempt.model_copy(
        update={"error_source": "bank", "error_reason": "payment_failed"}
    )
    poisoned = record.model_copy(update={"failed_attempt": ambiguous})
    assert classify(ambiguous).hard_possible

    budget_cautious = cautious._charge_budget(classify(ambiguous))
    budget_blunt = blunt._charge_budget(classify(ambiguous))
    assert budget_cautious == budget_blunt - 1
    assert cautious.decide(_state(poisoned)).rule_id


# --- the freeze ---------------------------------------------------------------


def test_the_frozen_config_is_committed_and_matches_its_fingerprint() -> None:
    """The config that produced the headline number must be in the repository,
    and must not have been edited in place afterwards."""
    from reclaimos.policy.config import FROZEN_CONFIG_PATH

    assert FROZEN_CONFIG_PATH.exists(), "no frozen config; the headline number is unattributable"
    frozen = AgentConfig.load()
    assert frozen.fingerprint().startswith("4ed35761c28b921f")


def test_a_hand_edited_config_is_refused(tmp_path: object) -> None:
    """Editing the JSON by hand must not silently change the agent while EVAL.md
    still quotes the old hash."""
    import json
    from pathlib import Path

    path = Path(str(tmp_path)) / "agent-frozen.json"
    AgentConfig().save(path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["config"]["max_charge_attempts"] = 8  # a quiet buff
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="edited by hand"):
        AgentConfig.load(path)


def test_the_defaults_are_the_frozen_values() -> None:
    """No tuning happened. If a default ever diverges from the frozen file, the
    a-priori claim in EVAL.md stops being true."""
    assert AgentConfig() == AgentConfig.load()
