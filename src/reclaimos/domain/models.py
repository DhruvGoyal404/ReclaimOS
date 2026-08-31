"""Core domain models.

Everything crossing a boundary in ReclaimOS is a typed Pydantic model. This is
the cheapest high-value guardrail we have: an LLM node that hallucinates a field,
a gateway that changes a payload shape, or a policy that returns a malformed
action all fail loudly at the edge instead of silently reaching the money path.

All money is ``int`` paise (see ``reclaimos.money``). All timestamps are
timezone-aware and carried in IST, because billing-cycle and payday effects are
local-calendar phenomena and a naive UTC datetime would quietly corrupt them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from reclaimos.domain.decline_codes import DeclineClass

IST = ZoneInfo("Asia/Kolkata")


class Method(StrEnum):
    """The recurring-payment rail behind a subscription."""

    UPI_AUTOPAY = "upi_autopay"
    CARD = "card"
    EMANDATE = "emandate"


class ActionType(StrEnum):
    """The complete catalogue of things ReclaimOS is allowed to do.

    Deliberately tiny and closed. An action outside this enum cannot be
    represented, let alone executed.
    """

    RETRY_CHARGE = "retry_charge"
    SEND_PAYMENT_LINK = "send_payment_link"
    REQUEST_CARD_UPDATE = "request_card_update"
    ESCALATE_HUMAN = "escalate_human"
    STOP = "stop"

    @property
    def moves_money(self) -> bool:
        """True for actions that can debit a customer without further consent."""
        return self is ActionType.RETRY_CHARGE


class TerminalReason(StrEnum):
    """Why a subscription's recovery loop ended. Every record gets exactly one."""

    RECOVERED = "recovered"
    POLICY_STOPPED = "policy_stopped"
    HARD_DECLINE_STOP = "hard_decline_stop"
    ATTEMPT_CAP_REACHED = "attempt_cap_reached"
    MANDATE_EXPIRED = "mandate_expired"
    MANDATE_VIOLATION_BLOCKED = "mandate_violation_blocked"
    ESCALATED_TO_HUMAN = "escalated_to_human"
    NO_ACTION_TAKEN = "no_action_taken"
    RECOVERY_WINDOW_CLOSED = "recovery_window_closed"


class Mandate(BaseModel):
    """The bound on what may be done to one customer, modelled on Google AP2.

    The executor refuses anything outside this envelope. Signing and verification
    arrive in Phase 5; the shape is fixed now so the eval harness measures
    mandate violations from day one (the target is, and must stay, zero).
    """

    model_config = ConfigDict(frozen=True)

    max_amount_paise: int = Field(gt=0)
    expiry: datetime
    allowed_method: Method
    reason_code: str = "subscription_recovery"

    def permits(self, amount_paise: int, method: Method, at: datetime) -> bool:
        return (
            amount_paise <= self.max_amount_paise
            and method is self.allowed_method
            and at <= self.expiry
        )


class PaymentAttempt(BaseModel):
    """One observed attempt on a subscription, successful or not.

    The ``error_*`` fields mirror Razorpay's error envelope. The agent sees these;
    it never sees a ``DeclineClass`` -- inferring that is the classifier's job.
    """

    model_config = ConfigDict(frozen=True)

    attempt_no: int = Field(ge=1)
    occurred_at: datetime
    amount_paise: int = Field(ge=0)
    succeeded: bool = False
    action: ActionType = ActionType.RETRY_CHARGE

    error_code: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    error_reason: str | None = None
    error_description: str | None = None


class SubscriptionRecord(BaseModel):
    """A failed recurring charge awaiting recovery. This is the agent's input.

    Contains no ground truth. The true ``DeclineClass``, the latent recovery
    probability, and the outcome draws all live in the sealed world file
    (``reclaimos.generator.outcome_model``) which no policy may read.
    """

    model_config = ConfigDict(frozen=True)

    subscription_id: str
    customer_id: str
    plan_id: str
    method: Method
    plan_amount_paise: int = Field(gt=0)
    currency: str = "INR"

    billing_cycle_day: int = Field(ge=1, le=28)
    charge_at: datetime

    customer_tenure_months: int = Field(ge=0)
    prior_success_count: int = Field(ge=0)
    prior_failure_count: int = Field(ge=0)

    mandate: Mandate
    failed_attempt: PaymentAttempt

    @field_validator("charge_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("charge_at must be timezone-aware (IST)")
        return v


class Decision(BaseModel):
    """One policy decision, fully attributable.

    ``rule_id`` names the exact deterministic rule that fired; ``rationale`` is
    free text that an LLM may later write. The rationale is never an input to
    anything -- it is a human-readable annotation on a decision the rule already
    made. That ordering is the whole of ADR-0001.
    """

    model_config = ConfigDict(frozen=True)

    subscription_id: str
    attempt_no: int = Field(ge=1)
    action: ActionType
    delay_hours: float = Field(ge=0)
    rule_id: str
    propensity: float = Field(ge=0.0, le=1.0)
    predicted_recoverable: bool
    rationale: str = ""

    #: Set when ``action`` is ``STOP``. "Why did we stop" is part of the audit
    #: trail, not something a reader should have to infer from the absence of a
    #: further row.
    stop_reason: TerminalReason | None = None


class AttemptResult(BaseModel):
    """What the world did in response to one action."""

    model_config = ConfigDict(frozen=True)

    succeeded: bool
    amount_paise: int = 0
    occurred_at: datetime
    probability: float = Field(ge=0.0, le=1.0)
    draw: float = Field(ge=0.0, le=1.0)


class RecordOutcome(BaseModel):
    """The end state of one subscription's recovery loop. One row per record."""

    model_config = ConfigDict(frozen=True)

    subscription_id: str
    recovered: bool
    amount_recovered_paise: int = 0
    charge_attempts: int = 0
    contact_actions: int = 0
    cost_paise: int = 0
    hours_to_resolution: float | None = None
    terminal_reason: TerminalReason
    predicted_recoverable: bool = False
    true_recoverable: bool = False
    true_class: DeclineClass = DeclineClass.UNKNOWN
    hard_decline_retries: int = 0
    mandate_violations: int = 0
    decisions: tuple[Decision, ...] = ()

    @property
    def net_recovered_paise(self) -> int:
        return self.amount_recovered_paise - self.cost_paise
