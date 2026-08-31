"""ReclaimOS's own recovery policy.

A pure function of observable state, implementing the same ``Policy`` interface
the baselines do — so it is scored by exactly the code path that produced their
numbers, with no gentler route available to it.

The order below is the safety argument, and it is deliberate:

1. **Stopping rules first.** Hard declines, closed windows, exhausted budgets.
   A stop can never be overridden by a later step wanting to act.
2. **Then the mandate.** A retry is only proposed if ``authorize`` would issue a
   token at the planned moment. This is why the agent's *measured* mandate
   violations are zero while the baselines' are not: the executor's gate fires
   here, before the harness's defence-in-depth gate ever sees the action
   (ADR-0003 criterion 4).
3. **Then the human gate.** Anything above the threshold is escalated instead of
   executed.
4. **Only then, the action.**

LangGraph will wrap this as a checkpointed graph with first-class HITL pauses.
It is deliberately not needed for the logic to run: if LangGraph were removed
tomorrow, this file and its tests would be unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from reclaimos.diagnose import Classification, Propensity, classify, score
from reclaimos.domain import ActionType, Decision, DeclineClass, Method, TerminalReason
from reclaimos.policy.config import AgentConfig
from reclaimos.policy.hitl import ReviewQueue
from reclaimos.policy.mandate import permits
from reclaimos.policy.timing import hours_until_month_turn

if TYPE_CHECKING:  # pragma: no cover
    # Type-only. Importing this at runtime would make the policy layer depend on
    # the eval package, which imports the policy layer back -- a cycle that the
    # test suite hid because pytest happened to import in the working order.
    # `from reclaimos.policy import AgentConfig` failed outright. See
    # docs/failure-log.md.
    from reclaimos.eval.policy import LoopState


@dataclass
class ReclaimAgent:
    """The deterministic recovery policy. No model, no network, no world access."""

    config: AgentConfig = field(default_factory=AgentConfig.frozen_or_default)
    queue: ReviewQueue | None = None
    name: str = "reclaimos_agent"
    description: str = "ReclaimOS — diagnosis-driven recovery with bounded, gated actions"

    # --- helpers ----------------------------------------------------------

    def _charge_budget(self, classification: Classification) -> int:
        budget = self.config.max_charge_attempts
        if self.config.ambiguity_shortens_ladder and classification.hard_possible:
            budget -= 1
        return max(0, budget)

    def _retry_target_hours(self, state: LoopState, classification: Classification) -> float:
        """Absolute hours since the original failure at which to retry."""
        cls = classification.decline_class
        index = min(state.charge_attempts, len(self.config.soft_retry_delays_hours) - 1)
        ladder = self.config.soft_retry_delays_hours[index]

        if cls is DeclineClass.SOFT_ISSUER_TECHNICAL:
            return max(ladder, self.config.technical_retry_delay_hours)
        if cls is DeclineClass.SOFT_LIMIT_EXCEEDED:
            return max(ladder, self.config.limit_exceeded_delay_hours)

        if cls is DeclineClass.SOFT_INSUFFICIENT_FUNDS and self.config.payday_alignment:
            # A short balance is a timing problem. The calendar is on the record,
            # so waiting for the month to turn is available to us; how long the
            # money actually takes to arrive is not.
            payday = (
                hours_until_month_turn(state.record.charge_at) + self.config.payday_buffer_hours
            )
            if payday <= self.config.payday_max_wait_hours:
                return max(ladder, payday)
        return ladder

    def _stop(self, state: LoopState, rule_id: str, reason: TerminalReason, why: str) -> Decision:
        return Decision(
            subscription_id=state.record.subscription_id,
            attempt_no=state.slot + 1,
            action=ActionType.STOP,
            delay_hours=0.0,
            rule_id=rule_id,
            propensity=0.0,
            predicted_recoverable=False,
            stop_reason=reason,
            rationale=why,
        )

    def _act(
        self,
        state: LoopState,
        action: ActionType,
        delay_hours: float,
        rule_id: str,
        propensity: Propensity,
        why: str,
    ) -> Decision:
        return Decision(
            subscription_id=state.record.subscription_id,
            attempt_no=state.slot + 1,
            action=action,
            delay_hours=max(0.0, delay_hours),
            rule_id=rule_id,
            propensity=propensity.score,
            predicted_recoverable=propensity.recoverable,
            rationale=why,
        )

    # --- the decision -----------------------------------------------------

    def decide(self, state: LoopState) -> Decision:
        record = state.record
        classification = classify(record.failed_attempt)
        propensity = score(record, classification, attempts_made=state.charge_attempts)
        cls = classification.decline_class

        # --- 1. stopping rules, before anything can want to act -----------
        if state.elapsed_hours >= self.config.recovery_window_hours:
            return self._stop(
                state,
                "agent.stop.window_closed",
                TerminalReason.RECOVERY_WINDOW_CLOSED,
                "Recovery window closed; further contact is noise.",
            )

        charge_budget = self._charge_budget(classification)
        charges_left = state.charge_attempts < charge_budget
        contacts_left = state.contact_actions < self.config.max_contact_actions

        # --- 2. choose a family of action ---------------------------------
        wants_charge = (
            charges_left
            and not cls.is_hard
            and cls is not DeclineClass.EXPIRY_CARD_EXPIRED
            and propensity.score >= self.config.retry_min_propensity
        )

        if wants_charge:
            target = self._retry_target_hours(state, classification)
            delay = max(1.0, target - state.elapsed_hours)
            at = record.charge_at + timedelta(hours=state.elapsed_hours + delay)

            # --- 3. the mandate, before proposing the action --------------
            if not permits(record.mandate, record.plan_amount_paise, record.method, at):
                # Consent will not cover this charge. Do not propose it; the
                # executor would refuse it and the harness would count a
                # violation. Fall through to outreach instead.
                wants_charge = False
            else:
                # --- 4. the human gate ---------------------------------
                if record.plan_amount_paise > self.config.hitl_amount_threshold_paise:
                    if self.queue is not None:
                        self.queue.submit(
                            subscription_id=record.subscription_id,
                            action=ActionType.RETRY_CHARGE,
                            amount_paise=record.plan_amount_paise,
                            reason=(
                                f"charge of {record.plan_amount_paise} paise exceeds the "
                                f"{self.config.hitl_amount_threshold_paise} paise auto-approve limit"
                            ),
                            rule_id="agent.gate.amount_threshold",
                            at=at,
                        )
                    return Decision(
                        subscription_id=record.subscription_id,
                        attempt_no=state.slot + 1,
                        action=ActionType.ESCALATE_HUMAN,
                        delay_hours=0.0,
                        rule_id="agent.gate.amount_threshold",
                        propensity=propensity.score,
                        predicted_recoverable=propensity.recoverable,
                        rationale=(
                            "Amount is above the auto-approve limit; queued for human "
                            "review rather than executed."
                        ),
                    )

                return self._act(
                    state,
                    ActionType.RETRY_CHARGE,
                    delay,
                    f"agent.retry.{cls.value.lower()}.attempt_{state.charge_attempts + 1}",
                    propensity,
                    f"{cls.value} with propensity {propensity.explain()}; "
                    f"retrying {target:.0f}h after the original failure.",
                )

        # --- outreach -----------------------------------------------------
        if contacts_left:
            if cls is DeclineClass.EXPIRY_CARD_EXPIRED and record.method is Method.CARD:
                if self.config.contact_on_expiry:
                    return self._act(
                        state,
                        ActionType.REQUEST_CARD_UPDATE,
                        max(1.0, self.config.contact_delay_hours - state.elapsed_hours),
                        "agent.contact.card_update",
                        propensity,
                        "The card has expired; retrying it cannot work. Asking the "
                        "customer to update the instrument instead.",
                    )
            elif cls.is_hard and self.config.contact_on_hard_decline:
                return self._act(
                    state,
                    ActionType.SEND_PAYMENT_LINK,
                    max(1.0, self.config.contact_delay_hours - state.elapsed_hours),
                    f"agent.contact.hard_decline.{cls.value.lower()}",
                    propensity,
                    f"{cls.value} will never authorise on a retry. One payment link so "
                    "the customer can choose another instrument; no charge attempts.",
                )
            elif not charges_left or not wants_charge:
                return self._act(
                    state,
                    ActionType.SEND_PAYMENT_LINK,
                    max(1.0, self.config.contact_delay_hours - state.elapsed_hours),
                    "agent.contact.fallback",
                    propensity,
                    "Charge attempts are exhausted or not permitted; asking the customer directly.",
                )

        # --- nothing left worth doing --------------------------------------
        if cls.is_hard:
            return self._stop(
                state,
                f"agent.stop.hard_decline.{cls.value.lower()}",
                TerminalReason.HARD_DECLINE_STOP,
                f"{cls.value} is a refusal, not an accident. Halting.",
            )
        if propensity.score < self.config.retry_min_propensity:
            return self._stop(
                state,
                "agent.stop.below_threshold",
                TerminalReason.POLICY_STOPPED,
                f"Propensity {propensity.score:.3f} is below the "
                f"{self.config.retry_min_propensity:.2f} floor; further attempts spend "
                "money with no expected return.",
            )
        # POLICY_STOPPED, not ATTEMPT_CAP_REACHED: the agent is choosing to stop
        # inside its own budget. ATTEMPT_CAP_REACHED belongs to the harness, for a
        # policy that had to be stopped -- conflating the two would have counted
        # this agent as a runaway and quietly dropped it out of the self-halt
        # metric. Caught by test_the_agent_always_terminates.
        return self._stop(
            state,
            "agent.stop.budget_exhausted",
            TerminalReason.POLICY_STOPPED,
            f"Budget spent: {state.charge_attempts} charge attempt(s), "
            f"{state.contact_actions} contact(s).",
        )
