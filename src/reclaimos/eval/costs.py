"""Declared cost assumptions.

Recovery is not free, and a policy that ignores cost looks better than it is.
These two constants are assumptions, not measurements. They are stated here, in
one place, and quoted in EVAL.md so that any number derived from them can be
recomputed under different assumptions by changing exactly one file.
"""

from __future__ import annotations

from typing import Final

from reclaimos.domain import ActionType
from reclaimos.money import Paise

#: Cost of presenting one charge attempt. Covers gateway and network fees on a
#: declined transaction. INR 2.00 is a conservative round figure for the Indian
#: rails; the real number varies by acquirer and is not public.
CHARGE_ATTEMPT_COST: Final[Paise] = Paise(200)

#: Cost of one customer contact (payment link, instrument-update request). INR
#: 0.50 covers messaging. Deliberately much cheaper than a charge attempt, which
#: is why a policy is not penalised much for reaching out and is penalised for
#: hammering a card that will never authorise.
CONTACT_COST: Final[Paise] = Paise(50)


def cost_of(action: ActionType) -> Paise:
    """What one action costs us, whether or not it succeeds."""
    if action is ActionType.RETRY_CHARGE:
        return CHARGE_ATTEMPT_COST
    if action in (ActionType.SEND_PAYMENT_LINK, ActionType.REQUEST_CARD_UPDATE):
        return CONTACT_COST
    return Paise(0)
