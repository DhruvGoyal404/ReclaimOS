"""Population and failure-mix profiles for the synthetic generator.

Every number in this module is an assumption. Each is stated at its use site with
the reasoning behind it, so a reader can disagree with a specific figure rather
than with the whole dataset. See SIMULATION.md for what these assumptions permit
us to claim, and ADR-0006 for why the generator must not encode our policy rules.

Sources are published vendor ranges for involuntary churn and dunning recovery.
They are ranges, and they are used as ranges. Nothing here is calibrated against
proprietary Razorpay data, because we do not have any.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Final

from reclaimos.domain import DeclineClass, Method

# ---------------------------------------------------------------------------
# Population mix
# ---------------------------------------------------------------------------

#: Rail mix for an India-first subscription business. UPI AutoPay leads on volume,
#: which matters because it also fails several times more often than cards.
METHOD_WEIGHTS: Final[dict[Method, float]] = {
    Method.UPI_AUTOPAY: 0.45,
    Method.CARD: 0.40,
    Method.EMANDATE: 0.15,
}

#: Plan tiers in rupees, with weights skewed to the low end as real subscription
#: books are. The two largest tiers exist so the human-in-the-loop amount
#: threshold (default INR 5,000) is actually exercised by the dataset.
PLAN_TIERS_RUPEES: Final[tuple[tuple[int, float], ...]] = (
    (199, 0.14),
    (299, 0.18),
    (499, 0.22),
    (799, 0.13),
    (999, 0.13),
    (1_499, 0.08),
    (2_999, 0.05),
    (4_999, 0.03),
    (7_999, 0.03),
    (12_999, 0.01),
)

# ---------------------------------------------------------------------------
# Failure mix, conditioned on rail
# ---------------------------------------------------------------------------

#: P(decline class | method). Conditioning on the rail is not decoration: a card
#: cannot expire on a UPI mandate, and mandate revocation is far more common on
#: UPI AutoPay where cancelling is two taps inside the payer's own app. A flat
#: unconditional mix would let a policy "learn" impossible combinations.
CLASS_WEIGHTS_BY_METHOD: Final[dict[Method, dict[DeclineClass, float]]] = {
    Method.CARD: {
        DeclineClass.SOFT_INSUFFICIENT_FUNDS: 0.26,
        DeclineClass.SOFT_ISSUER_TECHNICAL: 0.08,
        DeclineClass.SOFT_LIMIT_EXCEEDED: 0.04,
        DeclineClass.HARD_DO_NOT_HONOR: 0.20,
        DeclineClass.HARD_RISK_FLAGGED: 0.09,
        DeclineClass.HARD_MANDATE_REVOKED: 0.02,
        DeclineClass.EXPIRY_CARD_EXPIRED: 0.22,
        DeclineClass.EXPIRY_MANDATE_EXPIRED: 0.02,
        DeclineClass.UNKNOWN: 0.07,
    },
    Method.UPI_AUTOPAY: {
        DeclineClass.SOFT_INSUFFICIENT_FUNDS: 0.38,
        DeclineClass.SOFT_ISSUER_TECHNICAL: 0.12,
        DeclineClass.SOFT_LIMIT_EXCEEDED: 0.06,
        DeclineClass.HARD_DO_NOT_HONOR: 0.14,
        DeclineClass.HARD_RISK_FLAGGED: 0.04,
        DeclineClass.HARD_MANDATE_REVOKED: 0.12,
        DeclineClass.EXPIRY_CARD_EXPIRED: 0.00,  # structurally impossible
        DeclineClass.EXPIRY_MANDATE_EXPIRED: 0.06,
        DeclineClass.UNKNOWN: 0.08,
    },
    Method.EMANDATE: {
        DeclineClass.SOFT_INSUFFICIENT_FUNDS: 0.36,
        DeclineClass.SOFT_ISSUER_TECHNICAL: 0.08,
        DeclineClass.SOFT_LIMIT_EXCEEDED: 0.03,
        DeclineClass.HARD_DO_NOT_HONOR: 0.16,
        DeclineClass.HARD_RISK_FLAGGED: 0.03,
        DeclineClass.HARD_MANDATE_REVOKED: 0.13,
        DeclineClass.EXPIRY_CARD_EXPIRED: 0.00,  # structurally impossible
        DeclineClass.EXPIRY_MANDATE_EXPIRED: 0.12,
        DeclineClass.UNKNOWN: 0.09,
    },
}

#: Target aggregate bands, asserted by ``tests/test_generator.py``. These are the
#: bands the brief calls for; the per-method tables above must roll up into them.
AGGREGATE_BANDS: Final[dict[str, tuple[float, float]]] = {
    "soft": (0.40, 0.50),
    "hard": (0.25, 0.33),
    "expiry": (0.10, 0.15),
    "unknown": (0.04, 0.10),
}

# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------


def _weighted_choice[T](rng: random.Random, weights: Mapping[T, float]) -> T:
    population = list(weights)
    return rng.choices(population, weights=[weights[k] for k in population], k=1)[0]


def sample_method(rng: random.Random) -> Method:
    """Draw a payment rail."""
    return _weighted_choice(rng, METHOD_WEIGHTS)


def sample_decline_class(rng: random.Random, method: Method) -> DeclineClass:
    """Draw a true decline class conditioned on the rail."""
    weights = {k: v for k, v in CLASS_WEIGHTS_BY_METHOD[method].items() if v > 0}
    return _weighted_choice(rng, weights)


def sample_plan_amount_paise(rng: random.Random) -> int:
    """Draw a plan price, returned in integer paise."""
    tiers = dict(PLAN_TIERS_RUPEES)
    return _weighted_choice(rng, tiers) * 100


def sample_tenure_months(rng: random.Random) -> int:
    """Draw customer tenure.

    Skewed toward newer customers: subscription books are dominated by recent
    cohorts, and tenure matters because long-tenured customers both recover and
    respond to outreach materially better.
    """
    return min(48, int(rng.expovariate(1 / 9.0)))


def sample_billing_cycle_day(rng: random.Random) -> int:
    """Draw a billing day of month, clustered at the start of the month.

    Capped at 28 so every cycle day exists in February. Real merchants do this
    for the same reason.
    """
    if rng.random() < 0.45:
        return rng.randint(1, 5)
    return rng.randint(6, 28)


def marginal_class_weights() -> dict[DeclineClass, float]:
    """Roll the per-method tables up into the unconditional failure mix."""
    marginal: dict[DeclineClass, float] = dict.fromkeys(DeclineClass, 0.0)
    for method, p_method in METHOD_WEIGHTS.items():
        for cls, p_cls in CLASS_WEIGHTS_BY_METHOD[method].items():
            marginal[cls] += p_method * p_cls
    return marginal


def marginal_family_weights() -> dict[str, float]:
    """Roll the marginal class mix up into soft / hard / expiry / unknown."""
    marginal = marginal_class_weights()
    families = {"soft": 0.0, "hard": 0.0, "expiry": 0.0, "unknown": 0.0}
    for cls, weight in marginal.items():
        if cls.is_soft:
            families["soft"] += weight
        elif cls.is_hard:
            families["hard"] += weight
        elif cls.is_expiry:
            families["expiry"] += weight
        else:
            families["unknown"] += weight
    return families
