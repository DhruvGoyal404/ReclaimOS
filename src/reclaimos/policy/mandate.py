"""Mandate authorisation — the type-level teeth from ADR-0003 criterion 3.

ADR-0003 flagged this as the criterion most likely to be quietly downgraded to
"there is a runtime check". So the design is arranged so that an unauthorised
charge is not merely refused at runtime but **cannot be constructed**:

* ``ChargeRequest`` requires a ``MandateToken``. Building one without a token is
  a type error, proven by running mypy on a deliberately-wrong snippet
  (``tests/typecheck/unauthorized_charge.py``).
* ``MandateToken`` cannot be constructed directly. Its ``__init__`` demands a
  private sentinel that only :func:`authorize` holds, so ``MandateToken(...)``
  raises rather than producing a usable token.
* Even a token smuggled past both (via ``object.__new__``, say) fails: it carries
  an HMAC over the exact envelope it was issued for, and ``ChargeRequest``
  verifies that signature against its own fields. A token issued for INR 499 on a
  card cannot be re-pointed at INR 9,999 on a UPI mandate.

Three independent mechanisms, because this is the control that stands between an
autonomous agent and somebody's bank account.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import datetime
from hashlib import sha256
from typing import Any, Final

from reclaimos.domain import Mandate, Method

#: Signing key for authorisation tokens, fresh per process.
#:
#: Deliberately NOT persisted. A token is only meaningful inside the process that
#: issued it, for the request it was issued for; there is no scenario where we
#: want one to survive a restart and be replayed. Idempotency keys, which *are*
#: deterministic and durable, are the mechanism for surviving a crash (ADR-0004).
_TOKEN_KEY: Final[bytes] = secrets.token_bytes(32)

#: Only :func:`authorize` holds this. It is the private-constructor stand-in that
#: Python does not otherwise give us.
_ISSUER: Final[object] = object()


class MandateViolation(RuntimeError):
    """An action outside the consent envelope. Never caught to continue anyway."""

    def __init__(self, reason: str, *, subscription_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.subscription_id = subscription_id


def _fingerprint(mandate: Mandate) -> str:
    """Identify the exact envelope a token was issued against."""
    material = (
        f"{mandate.max_amount_paise}:{mandate.allowed_method.value}:"
        f"{mandate.expiry.isoformat()}:{mandate.reason_code}"
    )
    return sha256(material.encode("utf-8")).hexdigest()


def _sign(fingerprint: str, amount_paise: int, method: Method, at: datetime) -> str:
    material = f"{fingerprint}|{amount_paise}|{method.value}|{at.isoformat()}"
    return hmac.new(_TOKEN_KEY, material.encode("utf-8"), sha256).hexdigest()


class MandateToken:
    """Proof that one specific charge was authorised against one specific mandate.

    Construct via :func:`authorize`. Calling ``MandateToken(...)`` directly raises
    ``MandateViolation`` — the sentinel is what makes the constructor private in
    the absence of language support.
    """

    __slots__ = ("_signature", "amount_paise", "at", "fingerprint", "method", "reason_code")

    def __init__(
        self,
        *,
        issuer: object,
        fingerprint: str,
        amount_paise: int,
        method: Method,
        at: datetime,
        reason_code: str,
        signature: str,
    ) -> None:
        if issuer is not _ISSUER:
            raise MandateViolation(
                "MandateToken cannot be constructed directly; use Mandate.authorize()"
            )
        self.fingerprint = fingerprint
        self.amount_paise = amount_paise
        self.method = method
        self.at = at
        self.reason_code = reason_code
        self._signature = signature

    def verify(self) -> bool:
        """True if this token's signature matches the envelope it claims."""
        expected = _sign(self.fingerprint, self.amount_paise, self.method, self.at)
        return hmac.compare_digest(expected, self._signature)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"MandateToken(amount_paise={self.amount_paise}, "
            f"method={self.method.value}, at={self.at.isoformat()})"
        )


def authorize(
    mandate: Mandate,
    *,
    amount_paise: int,
    method: Method,
    at: datetime,
    subscription_id: str | None = None,
) -> MandateToken:
    """Check the envelope and issue a token, or raise ``MandateViolation``.

    This is the only way to obtain a token, and therefore the only way to build a
    ``ChargeRequest``. It runs **before** any idempotency key is claimed, so a
    refused action never burns a key and can never be replayed as "already done"
    (ADR-0003 criterion 2).
    """
    if amount_paise <= 0:
        raise MandateViolation(
            f"charge amount must be positive, got {amount_paise}", subscription_id=subscription_id
        )
    if amount_paise > mandate.max_amount_paise:
        raise MandateViolation(
            f"amount {amount_paise} exceeds mandate cap {mandate.max_amount_paise}",
            subscription_id=subscription_id,
        )
    if method is not mandate.allowed_method:
        raise MandateViolation(
            f"method {method.value} is not the mandated {mandate.allowed_method.value}",
            subscription_id=subscription_id,
        )
    if at > mandate.expiry:
        raise MandateViolation(
            f"mandate expired at {mandate.expiry.isoformat()}, charge at {at.isoformat()}",
            subscription_id=subscription_id,
        )

    fingerprint = _fingerprint(mandate)
    return MandateToken(
        issuer=_ISSUER,
        fingerprint=fingerprint,
        amount_paise=amount_paise,
        method=method,
        at=at,
        reason_code=mandate.reason_code,
        signature=_sign(fingerprint, amount_paise, method, at),
    )


def permits(mandate: Mandate, amount_paise: int, method: Method, at: datetime) -> bool:
    """Non-raising probe, for a policy deciding whether an action is even possible.

    A policy uses this to choose a different action rather than to justify calling
    the executor anyway. It is a question, not a permission.
    """
    try:
        authorize(mandate, amount_paise=amount_paise, method=method, at=at)
    except MandateViolation:
        return False
    return True


class ChargeRequest:
    """An authorised charge. The only thing the gateway wrapper accepts.

    Requires a ``MandateToken``, so ``ChargeRequest(amount_paise=..., method=...)``
    is a *type* error, not a runtime one. The token's signature is verified against
    these fields on construction, so a forged or re-pointed token fails here too.
    """

    __slots__ = ("amount_paise", "idempotency_key", "method", "subscription_id", "token")

    def __init__(
        self,
        token: MandateToken,
        *,
        subscription_id: str,
        amount_paise: int,
        method: Method,
        idempotency_key: str,
    ) -> None:
        if not token.verify():
            raise MandateViolation(
                "mandate token signature is invalid", subscription_id=subscription_id
            )
        if token.amount_paise != amount_paise or token.method is not method:
            raise MandateViolation(
                f"token authorises {token.amount_paise} on {token.method.value}, "
                f"but this request is {amount_paise} on {method.value}",
                subscription_id=subscription_id,
            )
        self.token = token
        self.subscription_id = subscription_id
        self.amount_paise = amount_paise
        self.method = method
        self.idempotency_key = idempotency_key

    def as_ledger_payload(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "amount_paise": self.amount_paise,
            "method": self.method.value,
            "idempotency_key": self.idempotency_key,
            "mandate_fingerprint": self.token.fingerprint,
            "authorised_at": self.token.at,
            "reason_code": self.token.reason_code,
        }
