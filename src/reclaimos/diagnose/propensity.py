"""The recovery-propensity rule table — transparent, and deliberately not a model.

ADR-0002 rules out training: our labels come from a generator we wrote, so a
model trained on them would measure whether it can learn our generator. So
propensity is a rule table, and every score decomposes into named factors a human
can read and disagree with.

Two things to be clear about.

**These numbers were authored without consulting the world model.** They are our
hypothesis about recovery, anchored on published dunning literature, and they are
expected to be wrong in places. That gap is the headroom the oracle ceiling
measures; if this table matched the simulator's constants the whole evaluation
would be circular (ADR-0006). ``tests/test_diagnose.py`` asserts the two tables
differ, so a future "improvement" that copies them across fails the build.

**Propensity here means "recoverable by *some* permitted action", not "this retry
will succeed".** An expired card scores medium, because an instrument-update flow
recovers it -- even though retrying the dead card is near-hopeless. Conflating the
two is how dunning systems end up hammering cards that can never authorise.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from reclaimos.diagnose.classifier import Classification
from reclaimos.domain import DeclineClass, Method, SubscriptionRecord

#: Base probability that a subscription is recoverable by some permitted action.
BASE_PROPENSITY: Final[dict[DeclineClass, float]] = {
    # Soft declines are the recoverable population: the instrument and the
    # consent are both fine, only the moment was wrong.
    DeclineClass.SOFT_INSUFFICIENT_FUNDS: 0.60,
    DeclineClass.SOFT_ISSUER_TECHNICAL: 0.70,
    DeclineClass.SOFT_LIMIT_EXCEEDED: 0.50,
    # Hard declines are refusals, not accidents. Near zero, and the stopping
    # rule -- not this score -- is what actually protects the customer.
    DeclineClass.HARD_DO_NOT_HONOR: 0.05,
    DeclineClass.HARD_RISK_FLAGGED: 0.02,
    DeclineClass.HARD_MANDATE_REVOKED: 0.01,
    # Expiry is recoverable, but only through an instrument-update or
    # re-authorisation path. Medium, and never by retrying the dead instrument.
    DeclineClass.EXPIRY_CARD_EXPIRED: 0.35,
    DeclineClass.EXPIRY_MANDATE_EXPIRED: 0.25,
    # A grab-bag skewed toward transient faults, scored cautiously.
    DeclineClass.UNKNOWN: 0.30,
}

#: Below this, a subscription is predicted unrecoverable and the policy should
#: stop rather than spend attempts on it.
RECOVERABLE_THRESHOLD: Final[float] = 0.15

#: Each attempt already spent makes the next one worth less.
#:
#: Named SPENT_ATTEMPT_ rather than ATTEMPT_ because the world model has its own
#: ATTEMPT_DECAY and the import-boundary guard flagged the collision. Two
#: identically-named constants either side of that boundary are exactly how a
#: copy sneaks in unnoticed, and they make a grep ambiguous besides.
SPENT_ATTEMPT_DECAY: Final[float] = 0.65

#: Applied when a soft-looking classification has a hard candidate behind it.
#: This is the whole point of tracking ambiguity: uncertainty must make us more
#: cautious, never more aggressive.
AMBIGUITY_PENALTY: Final[float] = 0.70

#: Applied when the classifier could not place the tuple confidently at all.
LOW_CONFIDENCE_PENALTY: Final[float] = 0.85
LOW_CONFIDENCE_BELOW: Final[float] = 0.60

#: An expired mandate is directly observable on the record, and no retry is
#: permitted against one. Only an outreach path can recover it.
EXPIRED_MANDATE_PENALTY: Final[float] = 0.15

METHOD_FACTOR: Final[dict[Method, float]] = {
    Method.UPI_AUTOPAY: 0.95,
    Method.CARD: 1.00,
    Method.EMANDATE: 0.92,
}


class Factor(BaseModel):
    """One named multiplier, with the reason it applied."""

    model_config = ConfigDict(frozen=True)

    name: str
    multiplier: float
    why: str


class Propensity(BaseModel):
    """A score, and the complete arithmetic that produced it."""

    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0.0, le=1.0)
    base: float
    rule_id: str
    factors: tuple[Factor, ...]

    @property
    def recoverable(self) -> bool:
        return self.score >= RECOVERABLE_THRESHOLD

    def explain(self) -> str:
        """Human-readable arithmetic, for the ledger and for the CLI."""
        steps = " × ".join(f"{f.name} {f.multiplier:.2f}" for f in self.factors)
        return f"{self.base:.2f} × {steps} = {self.score:.3f}" if steps else f"{self.base:.2f}"


def score(
    record: SubscriptionRecord,
    classification: Classification,
    attempts_made: int = 0,
) -> Propensity:
    """Score one subscription. Pure, deterministic, and free of any model."""
    base = BASE_PROPENSITY[classification.decline_class]
    factors: list[Factor] = []

    if attempts_made:
        factors.append(
            Factor(
                name="attempt_decay",
                multiplier=round(SPENT_ATTEMPT_DECAY**attempts_made, 4),
                why=f"{attempts_made} attempt(s) already spent on this failure",
            )
        )

    if classification.hard_possible and not classification.decline_class.is_hard:
        factors.append(
            Factor(
                name="ambiguity",
                multiplier=AMBIGUITY_PENALTY,
                why=(
                    "the gateway tuple is also emitted by a hard decline; treating "
                    "it cautiously because we cannot rule that out"
                ),
            )
        )

    if classification.confidence < LOW_CONFIDENCE_BELOW:
        factors.append(
            Factor(
                name="low_confidence",
                multiplier=LOW_CONFIDENCE_PENALTY,
                why=f"classifier confidence {classification.confidence:.2f} is low",
            )
        )

    if record.mandate.expiry < record.charge_at:
        factors.append(
            Factor(
                name="expired_mandate",
                multiplier=EXPIRED_MANDATE_PENALTY,
                why="consent lapsed before the charge; no retry is permitted",
            )
        )

    factors.append(
        Factor(
            name="method",
            multiplier=METHOD_FACTOR[record.method],
            why=f"{record.method.value} recovers slightly differently to other rails",
        )
    )

    tenure_factor = round(0.90 + 0.20 * (min(record.customer_tenure_months, 24) / 24), 4)
    factors.append(
        Factor(
            name="tenure",
            multiplier=tenure_factor,
            why=f"{record.customer_tenure_months} month(s) of tenure",
        )
    )

    if record.prior_failure_count:
        factors.append(
            Factor(
                name="prior_failures",
                multiplier=round(max(0.75, 1.0 - 0.05 * record.prior_failure_count), 4),
                why=f"{record.prior_failure_count} prior failed charge(s)",
            )
        )

    value = base
    for factor in factors:
        value *= factor.multiplier

    return Propensity(
        score=round(min(1.0, max(0.0, value)), 4),
        base=base,
        rule_id=f"propensity.{classification.decline_class.value.lower()}",
        factors=tuple(factors),
    )
