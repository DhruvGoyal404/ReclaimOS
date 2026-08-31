"""Calendar arithmetic the agent is allowed to do.

The salary-cycle effect is real and well documented, and the *calendar* part of it
is fully observable: the record carries ``charge_at``, so anyone can work out how
long until the month turns.

What is not observable is when the money actually arrives -- which for any given
customer might be a salary credit, might be an ordinary transfer, and is not on
the record at all. The agent gets the calendar and not the answer, which is
exactly the headroom the oracle ceiling measures (ADR-0006).

Written here rather than imported: the world model has its own version of this
calculation, and ``tests/test_import_boundary.py`` forbids policy code from
reaching into the generator. Two implementations of an observable calendar fact
is the correct amount.
"""

from __future__ import annotations

from datetime import datetime

from reclaimos.domain import IST


def hours_until_month_turn(at: datetime) -> float:
    """Hours from ``at`` until 00:00 IST on the first of the next month."""
    local = at.astimezone(IST)
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    boundary = datetime(year, month, 1, 0, 0, tzinfo=IST)
    return (boundary - local).total_seconds() / 3600.0


def day_of_month(at: datetime) -> int:
    """Day of month in IST, for rules that care where in the cycle we are."""
    return at.astimezone(IST).day
