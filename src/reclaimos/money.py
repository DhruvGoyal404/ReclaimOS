"""Money handling for ReclaimOS.

Rule, enforced by ``tests/test_money.py``: money is **always** an integer number
of paise. Floats are never used to represent, store, sum, or transport an amount
anywhere in this codebase. Razorpay's own API speaks in the smallest currency
unit for exactly this reason; we match it end to end so no rounding can ever be
introduced between our ledger and the gateway.

100 paise = 1 rupee (INR).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import NewType

Paise = NewType("Paise", int)

PAISE_PER_RUPEE = 100
ZERO = Paise(0)


def rupees(amount: str | int) -> Paise:
    """Convert a rupee amount to paise.

    Accepts ``str`` (e.g. ``"499.00"``) or ``int``. Deliberately rejects ``float``:
    ``0.1 + 0.2`` problems have no place near money.
    """
    if isinstance(amount, float):  # pragma: no cover - guarded by type checker too
        raise TypeError("float amounts are forbidden; pass a str like '499.00' or an int")
    quantised = (Decimal(amount) * PAISE_PER_RUPEE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return Paise(int(quantised))


def to_rupee_str(amount: Paise) -> str:
    """Render paise as a plain rupee string, e.g. ``49900`` -> ``'499.00'``."""
    sign = "-" if amount < 0 else ""
    whole, frac = divmod(abs(int(amount)), PAISE_PER_RUPEE)
    return f"{sign}{whole}.{frac:02d}"


def format_inr(amount: Paise) -> str:
    """Render paise for human display with the Indian digit grouping, e.g. ``'₹1,23,456.78'``."""
    sign = "-" if amount < 0 else ""
    whole, frac = divmod(abs(int(amount)), PAISE_PER_RUPEE)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return f"{sign}\u20b9{digits}.{frac:02d}"


def pct(numerator: float, denominator: float) -> float:
    """Percentage helper that returns 0.0 rather than raising on an empty denominator."""
    return 0.0 if denominator == 0 else 100.0 * numerator / denominator
