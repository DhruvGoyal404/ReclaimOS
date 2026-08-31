"""The stochastic world model — the sealed ground truth behind every metric.

This is the single most important file for the honesty of our numbers, so it is
worth being precise about what it is.

**It is a simulator, not a labelled dataset.** It does not decide "this record is
recoverable"; it decides how the world *responds* to whatever action a policy
actually took. Any policy can therefore be scored, including ones we have not
written yet, and no policy is compared against a fixed answer key derived from
our own rules. That circularity is what ADR-0006 exists to prevent.

Three properties make the resulting metrics meaningful:

1. **Latent factors the policy rule table does not encode.** ``funds_return_hours``
   (when a short balance actually recovers), ``outage_end_hours`` (how long an
   issuer is down) and ``base_intent`` (how willing this customer is to pay when
   asked) all move the true probability and none of them is observable from the
   record. A policy can approximate them with calendar heuristics; it cannot read
   them. That gap is the headroom the oracle ceiling measures.

2. **Common random numbers.** One uniform draw is fixed per ``(record, slot)`` at
   generation time and shared by every policy. Two policies taking the same action
   at the same point face the *same* luck, so a difference between them is a
   difference in judgement rather than sampling noise. This is standard variance
   reduction, and it is what makes the baseline comparisons fair.

3. **Everything below is an assumption, stated at its use site.** A reader can
   disagree with one number instead of with the whole dataset. See SIMULATION.md.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Final, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from reclaimos.domain import (
    IST,
    ActionType,
    AttemptResult,
    DeclineClass,
    Method,
    SubscriptionRecord,
)

#: Hard ceiling on world-resolved actions per record. A safety net for the
#: harness, deliberately larger than any policy's own attempt cap so that a
#: runaway policy is *observed* hitting the wall rather than silently truncated.
MAX_SLOTS: Final[int] = 8

#: After 30 days an unrecovered subscription is written off. Beyond this the
#: customer has effectively churned and further contact is noise.
RECOVERY_WINDOW_HOURS: Final[float] = 720.0

#: Nothing is ever certain. Caps every probability below 1.0.
MAX_PROBABILITY: Final[float] = 0.97


# ---------------------------------------------------------------------------
# Base rates
# ---------------------------------------------------------------------------

#: P(a retry succeeds | class), before timing, decay and customer effects.
#:
#: Soft declines are the recoverable population and hard declines essentially are
#: not -- that asymmetry is the entire reason a stopping rule earns its keep.
#: Retrying an expired card is near-hopeless by construction: the instrument is
#: wrong, so only an instrument-update path can help. UNKNOWN sits in the middle
#: because it is a grab-bag skewed toward transient faults.
RETRY_BASE: Final[dict[DeclineClass, float]] = {
    DeclineClass.SOFT_INSUFFICIENT_FUNDS: 0.55,
    DeclineClass.SOFT_ISSUER_TECHNICAL: 0.72,
    DeclineClass.SOFT_LIMIT_EXCEEDED: 0.45,
    DeclineClass.HARD_DO_NOT_HONOR: 0.04,
    DeclineClass.HARD_RISK_FLAGGED: 0.01,
    DeclineClass.HARD_MANDATE_REVOKED: 0.00,
    DeclineClass.EXPIRY_CARD_EXPIRED: 0.02,
    DeclineClass.EXPIRY_MANDATE_EXPIRED: 0.00,
    DeclineClass.UNKNOWN: 0.35,
}

#: How receptive a customer is to a payment link, given why the charge failed.
#:
#: The counter-intuitive entry is the important one: a payment link is *weakest*
#: for insufficient funds, because a link does not create money. It is strongest
#: where the instrument failed but the intent survives. A policy that treats
#: "send a link" as a universal fallback is measurably wrong here.
LINK_RECEPTIVITY: Final[dict[DeclineClass, float]] = {
    DeclineClass.SOFT_INSUFFICIENT_FUNDS: 0.50,
    DeclineClass.SOFT_ISSUER_TECHNICAL: 0.60,
    DeclineClass.SOFT_LIMIT_EXCEEDED: 0.70,
    DeclineClass.HARD_DO_NOT_HONOR: 0.85,
    DeclineClass.HARD_RISK_FLAGGED: 0.75,
    DeclineClass.HARD_MANDATE_REVOKED: 0.25,
    DeclineClass.EXPIRY_CARD_EXPIRED: 0.90,
    DeclineClass.EXPIRY_MANDATE_EXPIRED: 0.85,
    DeclineClass.UNKNOWN: 0.70,
}

#: How well an instrument-update request fits the failure. Only meaningful on the
#: card rail -- asking a UPI AutoPay customer to update a card is nonsense, and a
#: policy that misroutes there should be punished for it, so the payoff is zero.
CARD_UPDATE_FIT: Final[dict[DeclineClass, float]] = {
    DeclineClass.EXPIRY_CARD_EXPIRED: 0.95,
    DeclineClass.HARD_RISK_FLAGGED: 0.60,
    DeclineClass.HARD_DO_NOT_HONOR: 0.35,
}
CARD_UPDATE_FIT_DEFAULT: Final[float] = 0.08

#: Each successive charge attempt on the same failure is worth less than the last.
ATTEMPT_DECAY: Final[float] = 0.62

#: Repeated outreach fatigues faster than repeated retries: the customer sees it.
CONTACT_DECAY: Final[float] = 0.70

#: UPI AutoPay recovers slightly worse than cards; e-mandate batches are slowest.
METHOD_MULTIPLIER: Final[dict[Method, float]] = {
    Method.UPI_AUTOPAY: 0.95,
    Method.CARD: 1.00,
    Method.EMANDATE: 0.90,
}


# ---------------------------------------------------------------------------
# Sealed per-record truth
# ---------------------------------------------------------------------------


class WorldRecord(BaseModel):
    """Everything about a record that a policy is not allowed to see.

    Persisted to ``<split>.world.json``, which the harness loads and no policy
    ever receives.
    """

    model_config = ConfigDict(frozen=True)

    subscription_id: str
    true_class: DeclineClass

    #: Hours after the original failure at which the customer's balance recovers.
    #: Only meaningful for insufficient funds. Drawn as the earlier of an ordinary
    #: income event and the next salary credit, so it is usually days rather than
    #: weeks -- retrying too early is the mistake this variable punishes.
    funds_return_hours: float = 0.0

    #: Hours after the original failure at which an issuer outage ends. Only
    #: meaningful for technical declines. Unobservable from the record, which is
    #: the point: no calendar heuristic recovers it.
    outage_end_hours: float = 0.0

    #: This customer's willingness to pay when asked directly, in [0, 1].
    base_intent: float = Field(ge=0.0, le=1.0, default=0.0)

    #: Common random numbers: one uniform draw per action slot, fixed at
    #: generation time and shared by every policy.
    draws: tuple[float, ...] = ()


def hours_to_next_salary_credit(at: datetime) -> float:
    """Hours from ``at`` until the next salary-credit window.

    Modelled as 11:00 IST on the 1st of the following month. Indian payroll
    convention is the last working day or the first few days of the month; the
    noise applied by the caller absorbs the difference. This is an approximation
    a policy could also make -- which is deliberate, because the *unobservable*
    part of the timing is the ordinary-income draw it is combined with.
    """
    at_ist = at.astimezone(IST)
    year, month = (at_ist.year + 1, 1) if at_ist.month == 12 else (at_ist.year, at_ist.month + 1)
    payday = datetime(year, month, 1, 11, 0, tzinfo=IST)
    return (payday - at_ist).total_seconds() / 3600.0


def draw_world_record(
    rng: random.Random,
    subscription_id: str,
    true_class: DeclineClass,
    charge_at: datetime,
    tenure_months: int,
) -> WorldRecord:
    """Sample the sealed latent state for one subscription."""
    funds_return = 0.0
    if true_class is DeclineClass.SOFT_INSUFFICIENT_FUNDS:
        # Money comes back either from ordinary income (mean ~72h) or from the
        # next salary credit, whichever lands first.
        ordinary = 6.0 + rng.expovariate(1 / 66.0)
        payday = hours_to_next_salary_credit(charge_at) + rng.uniform(-6.0, 18.0)
        funds_return = max(2.0, min(ordinary, payday))

    outage_end = 0.0
    if true_class is DeclineClass.SOFT_ISSUER_TECHNICAL:
        outage_end = rng.uniform(1.5, 30.0)

    # Willingness to pay when asked, nudged up by tenure: long-tenured customers
    # both value the service more and answer their messages more.
    tenure_frac = min(tenure_months, 24) / 24.0
    intent = rng.betavariate(2.2, 3.0) * (0.80 + 0.40 * tenure_frac)

    return WorldRecord(
        subscription_id=subscription_id,
        true_class=true_class,
        funds_return_hours=funds_return,
        outage_end_hours=outage_end,
        base_intent=min(1.0, intent),
        draws=tuple(rng.random() for _ in range(MAX_SLOTS)),
    )


# ---------------------------------------------------------------------------
# The probability surface
# ---------------------------------------------------------------------------


def _timing_multiplier(t_hours: float) -> float:
    """Generic shape of recovery against time since the original failure.

    Too soon and nothing has changed; too late and the customer has moved on.
    """
    if t_hours < 6.0:
        return 0.45
    if t_hours < 24.0:
        return 0.85
    if t_hours <= 120.0:
        return 1.00
    if t_hours <= 336.0:
        return 0.88
    return 0.72


def _customer_multiplier(record: SubscriptionRecord) -> float:
    """Tenure and payment history, combined."""
    tenure_frac = min(record.customer_tenure_months, 24) / 24.0
    tenure = 0.88 + 0.24 * tenure_frac
    history = max(0.70, 1.0 - 0.06 * record.prior_failure_count)
    return tenure * history


def success_probability(
    record: SubscriptionRecord,
    truth: WorldRecord,
    action: ActionType,
    t_hours: float,
    charge_attempts: int,
    contact_actions: int,
) -> float:
    """True probability that ``action`` succeeds, ``t_hours`` after the failure.

    ``t_hours`` is measured from the original failed charge, not from the previous
    action, so a policy that dawdles pays for the elapsed time rather than only
    for the last gap.
    """
    cls = truth.true_class
    customer = _customer_multiplier(record)

    if action is ActionType.RETRY_CHARGE:
        p = RETRY_BASE[cls]
        p *= _timing_multiplier(t_hours)
        p *= ATTEMPT_DECAY**charge_attempts
        p *= METHOD_MULTIPLIER[record.method]
        p *= customer
        if cls is DeclineClass.SOFT_INSUFFICIENT_FUNDS:
            # The single most valuable thing a policy can get right: wait until
            # the money is actually there.
            p *= 1.50 if t_hours >= truth.funds_return_hours else 0.55
        elif cls is DeclineClass.SOFT_ISSUER_TECHNICAL:
            # Retrying into an ongoing outage is close to free money burned.
            p *= 0.15 if t_hours < truth.outage_end_hours else 1.20
        return min(MAX_PROBABILITY, max(0.0, p))

    if action is ActionType.SEND_PAYMENT_LINK:
        p = truth.base_intent * LINK_RECEPTIVITY[cls]
        p *= CONTACT_DECAY**contact_actions
        p *= customer
        if t_hours > 336.0:
            p *= 0.80
        return min(MAX_PROBABILITY, max(0.0, p))

    if action is ActionType.REQUEST_CARD_UPDATE:
        if record.method is not Method.CARD:
            return 0.0  # there is no card to update
        p = truth.base_intent * CARD_UPDATE_FIT.get(cls, CARD_UPDATE_FIT_DEFAULT)
        p *= CONTACT_DECAY**contact_actions
        p *= customer
        return min(MAX_PROBABILITY, max(0.0, p))

    # STOP and ESCALATE_HUMAN never touch the world.
    return 0.0


def resolve(
    record: SubscriptionRecord,
    truth: WorldRecord,
    action: ActionType,
    t_hours: float,
    slot: int,
    charge_attempts: int,
    contact_actions: int,
) -> AttemptResult:
    """Ask the world what happened.

    Uses the common random number fixed for ``(record, slot)``, so every policy
    that reaches this slot faces the same draw.
    """
    p = success_probability(record, truth, action, t_hours, charge_attempts, contact_actions)
    draw = truth.draws[min(slot, len(truth.draws) - 1)]
    occurred_at = record.charge_at + timedelta(hours=t_hours)
    return AttemptResult(
        succeeded=draw < p,
        amount_paise=record.plan_amount_paise if draw < p else 0,
        occurred_at=occurred_at,
        probability=p,
        draw=draw,
    )


# ---------------------------------------------------------------------------
# The oracle ceiling
# ---------------------------------------------------------------------------

#: Delays the oracle is allowed to consider, in hours since the original failure.
#: A coarse grid on purpose -- a finer one would let the oracle exploit the
#: model's exact shape and overstate the ceiling.
ORACLE_DELAY_GRID: Final[tuple[float, ...]] = (0.5, 6.0, 24.0, 48.0, 72.0, 120.0, 168.0)

ORACLE_ACTIONS: Final[tuple[ActionType, ...]] = (
    ActionType.RETRY_CHARGE,
    ActionType.SEND_PAYMENT_LINK,
    ActionType.REQUEST_CARD_UPDATE,
)


class OracleResult(NamedTuple):
    """What the truth-reading ceiling achieved on one record."""

    recovered: bool
    hours: float
    charge_attempts: int
    contact_actions: int


def oracle_recovers(
    record: SubscriptionRecord,
    truth: WorldRecord,
    max_attempts: int,
) -> OracleResult:
    """Would a policy that could read the sealed truth recover this record?

    Greedy: at each slot pick the highest-probability (action, delay) pair the
    mandate permits, then consult the same common random number any real policy
    would face. Greedy is optimal *within* a slot -- success is ``draw < p``, so
    maximising ``p`` maximises the chance -- and only mildly suboptimal across
    slots because of attempt decay. We therefore call this a **ceiling estimate**,
    not the optimum, and label it that way wherever it is reported.

    Action counts are returned so the ceiling can be charged the same costs as
    every other policy. A ceiling that spends nothing would flatter itself.
    """
    elapsed = 0.0
    charge_attempts = 0
    contact_actions = 0

    for slot in range(min(max_attempts, MAX_SLOTS)):
        best_p = -1.0
        best_action: ActionType | None = None
        best_t = 0.0

        for action in ORACLE_ACTIONS:
            for delay in ORACLE_DELAY_GRID:
                t = elapsed + delay
                if t > RECOVERY_WINDOW_HOURS:
                    continue
                at = record.charge_at + timedelta(hours=t)
                if action.moves_money and not record.mandate.permits(
                    record.plan_amount_paise, record.method, at
                ):
                    continue  # the ceiling must respect the same envelope
                p = success_probability(record, truth, action, t, charge_attempts, contact_actions)
                if p > best_p:
                    best_p, best_action, best_t = p, action, t

        if best_action is None:
            break

        draw = truth.draws[min(slot, len(truth.draws) - 1)]
        elapsed = best_t
        if best_action.moves_money:
            charge_attempts += 1
        else:
            contact_actions += 1

        if draw < best_p:
            return OracleResult(True, best_t, charge_attempts, contact_actions)

    return OracleResult(False, elapsed, charge_attempts, contact_actions)
