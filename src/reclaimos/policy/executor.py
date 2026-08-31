"""The executor — the only thing that moves money, and the order it does it in.

**mandate → idempotency → gateway.** The ordering is a requirement, not a
preference (ADR-0003 criterion 2). Authorisation happens before the idempotency
key is claimed, so a refused action never burns a key. Burning one would be worse
than the refusal: the next legitimate attempt at that logical action would find
the key already claimed and skip itself, silently, forever.

The type system carries the first half of that ordering. ``execute`` accepts a
``ChargeRequest``, which cannot exist without a ``MandateToken``, so by the time
this function runs authorisation has provably already happened. It re-verifies
anyway, because time passes between authorising and executing and a mandate can
expire in the gap.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from reclaimos.domain import IST, ActionType, Mandate, Method, SubscriptionRecord
from reclaimos.policy.gateway import GatewayResult, PaymentGateway
from reclaimos.policy.mandate import ChargeRequest, MandateViolation, authorize
from reclaimos.store.idempotency import IdempotencyStore, idempotency_key


class ExecutionReceipt(BaseModel):
    """What happened, including when nothing happened because it already had."""

    model_config = ConfigDict(frozen=True)

    subscription_id: str
    action: ActionType
    idempotency_key: str
    executed: bool
    replayed: bool
    succeeded: bool
    amount_paise: int = 0
    reference: str | None = None
    detail: str = ""

    def as_ledger_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def build_charge(
    record: SubscriptionRecord,
    attempt_no: int,
    at: datetime,
    *,
    amount_paise: int | None = None,
    method: Method | None = None,
    mandate: Mandate | None = None,
) -> ChargeRequest:
    """Authorise a charge and package it. Raises ``MandateViolation`` if refused.

    The only route to a ``ChargeRequest``, and it takes no shortcut: the token
    comes from ``authorize``, which is where the envelope is actually checked.
    """
    envelope = mandate or record.mandate
    amount = record.plan_amount_paise if amount_paise is None else amount_paise
    rail = method or record.method

    token = authorize(
        envelope,
        amount_paise=amount,
        method=rail,
        at=at,
        subscription_id=record.subscription_id,
    )
    return ChargeRequest(
        token,
        subscription_id=record.subscription_id,
        amount_paise=amount,
        method=rail,
        idempotency_key=idempotency_key(
            record.subscription_id, attempt_no, ActionType.RETRY_CHARGE
        ),
    )


def execute_charge(
    request: ChargeRequest,
    gateway: PaymentGateway,
    idempotency: IdempotencyStore,
) -> ExecutionReceipt:
    """Claim the key, then call the gateway. Never the other way round.

    A lost claim means this exact logical action already ran. We return its
    recorded result rather than calling the gateway again — that is the whole of
    ADR-0004, and the reason a webhook replay cannot produce a second debit.
    """
    if not request.token.verify():  # pragma: no cover - construction already verifies
        raise MandateViolation(
            "mandate token failed verification at execution time",
            subscription_id=request.subscription_id,
        )

    if not idempotency.claim(request.idempotency_key):
        prior = idempotency.get(request.idempotency_key)
        recorded = prior.result if prior and prior.result else {}
        return ExecutionReceipt(
            subscription_id=request.subscription_id,
            action=ActionType.RETRY_CHARGE,
            idempotency_key=request.idempotency_key,
            executed=False,
            replayed=True,
            succeeded=bool(recorded.get("succeeded", False)),
            amount_paise=int(recorded.get("amount_paise", 0)),
            reference=recorded.get("reference"),
            detail="already executed; returning the recorded result",
        )

    result: GatewayResult = gateway.charge(request)
    receipt = ExecutionReceipt(
        subscription_id=request.subscription_id,
        action=ActionType.RETRY_CHARGE,
        idempotency_key=request.idempotency_key,
        executed=True,
        replayed=False,
        succeeded=result.succeeded,
        amount_paise=request.amount_paise if result.succeeded else 0,
        reference=result.reference,
        detail=result.detail,
    )
    idempotency.record_result(
        request.idempotency_key,
        {
            "succeeded": result.succeeded,
            "amount_paise": receipt.amount_paise,
            "reference": result.reference,
            "recorded_at": datetime.now(tz=IST).isoformat(),
        },
    )
    return receipt


def execute_contact(
    record: SubscriptionRecord,
    action: ActionType,
    attempt_no: int,
    gateway: PaymentGateway,
    idempotency: IdempotencyStore,
) -> ExecutionReceipt:
    """Outreach: a payment link or an instrument-update request.

    No mandate is required — neither debits anyone, they ask. The idempotency
    claim still applies, because messaging a customer four times because a webhook
    was redelivered four times is its own kind of harm.
    """
    if action.moves_money:  # pragma: no cover - guarded by callers and by type
        raise MandateViolation(
            f"{action.value} moves money and must go through execute_charge",
            subscription_id=record.subscription_id,
        )

    key = idempotency_key(record.subscription_id, attempt_no, action)
    if not idempotency.claim(key):
        return ExecutionReceipt(
            subscription_id=record.subscription_id,
            action=action,
            idempotency_key=key,
            executed=False,
            replayed=True,
            succeeded=False,
            detail="already sent; not contacting the customer again",
        )

    result = gateway.contact(record, action)
    receipt = ExecutionReceipt(
        subscription_id=record.subscription_id,
        action=action,
        idempotency_key=key,
        executed=True,
        replayed=False,
        succeeded=result.succeeded,
        reference=result.reference,
        detail=result.detail,
    )
    idempotency.record_result(
        key,
        {
            "succeeded": result.succeeded,
            "reference": result.reference,
            "recorded_at": datetime.now(tz=IST).isoformat(),
        },
    )
    return receipt
