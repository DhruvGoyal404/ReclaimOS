"""The payment gateway boundary.

``charge`` accepts a ``ChargeRequest`` **and nothing else** — no loose amount, no
loose method, no subscription id it could pair with the wrong figure. That is the
second half of ADR-0003's type-level teeth: even if someone reached past
``execute_charge``, the only value the gateway will take is one that provably came
through ``Mandate.authorize``.

``SimulatedGateway`` is the deterministic implementation used by tests and the
demo. ``LiveTestModeGateway`` (Phase 6) will wrap the Razorpay SDK behind exactly
this interface, so the executor and every test above it stay unchanged.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from reclaimos.domain import ActionType, SubscriptionRecord
from reclaimos.policy.mandate import ChargeRequest


class GatewayResult(BaseModel):
    """What the rail said."""

    model_config = ConfigDict(frozen=True)

    succeeded: bool
    reference: str | None = None
    detail: str = ""


@runtime_checkable
class PaymentGateway(Protocol):
    """The narrow surface the executor depends on."""

    def charge(self, request: ChargeRequest) -> GatewayResult:
        """Present a charge. Takes an authorised request, never loose figures."""
        ...

    def contact(self, record: SubscriptionRecord, action: ActionType) -> GatewayResult:
        """Send a payment link or an instrument-update request."""
        ...


class SimulatedGateway:
    """Deterministic gateway for tests and the demo.

    Deliberately dumb: it records what it was asked to do and returns a scripted
    answer. Whether a recovery *would* have worked is the world model's question,
    and the world model is sealed away from anything a policy can reach.
    """

    def __init__(self, *, charge_succeeds: bool = False, contact_succeeds: bool = True) -> None:
        self.charge_succeeds = charge_succeeds
        self.contact_succeeds = contact_succeeds
        self.charges: list[ChargeRequest] = []
        self.contacts: list[tuple[str, ActionType]] = []

    def charge(self, request: ChargeRequest) -> GatewayResult:
        self.charges.append(request)
        return GatewayResult(
            succeeded=self.charge_succeeds,
            reference=f"pay_SIM{len(self.charges):06d}",
            detail="simulated charge",
        )

    def contact(self, record: SubscriptionRecord, action: ActionType) -> GatewayResult:
        self.contacts.append((record.subscription_id, action))
        return GatewayResult(
            succeeded=self.contact_succeeds,
            reference=f"msg_SIM{len(self.contacts):06d}",
            detail="simulated outreach",
        )
