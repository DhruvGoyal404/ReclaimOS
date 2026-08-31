"""Metric definitions.

Every headline figure is reported with a bootstrap 95% confidence interval over
the record population, never as a bare point estimate. "One cherry-picked match
proves nothing" cuts against single-run numbers too.

What the intervals do and do not cover, stated once so it is not overclaimed
later: they quantify **sampling variation across subscriptions**. They say nothing
about whether the world model's assumptions are right. Nothing in this repository
can measure that, and SIMULATION.md says so.
"""

from __future__ import annotations

import random
import statistics
from typing import Final

from pydantic import BaseModel, ConfigDict

from reclaimos.domain import RecordOutcome, TerminalReason
from reclaimos.eval.harness import SELF_HALTED
from reclaimos.money import pct

#: Resamples per bootstrap. 2,000 is ample for a two-decimal interval and keeps
#: the whole evaluation under a second on a laptop.
BOOTSTRAP_RESAMPLES: Final[int] = 2_000

#: Fixed so a reported interval is reproducible byte for byte.
BOOTSTRAP_SEED: Final[int] = 20260905


class Interval(BaseModel):
    """A percentile confidence interval."""

    model_config = ConfigDict(frozen=True)

    low: float
    high: float

    def __str__(self) -> str:
        return f"[{self.low:.1f}, {self.high:.1f}]"


class Confusion(BaseModel):
    """Confusion matrix for the ``recoverable`` prediction."""

    model_config = ConfigDict(frozen=True)

    tp: int
    fp: int
    tn: int
    fn: int

    @property
    def precision(self) -> float:
        return 0.0 if self.tp + self.fp == 0 else self.tp / (self.tp + self.fp)

    @property
    def recall(self) -> float:
        return 0.0 if self.tp + self.fn == 0 else self.tp / (self.tp + self.fn)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)


class PolicyMetrics(BaseModel):
    """Everything EVAL.md reports about one policy on one split."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    n: int

    # --- money -------------------------------------------------------------
    recovered: int
    recovery_rate: float
    recovery_rate_ci: Interval
    gross_recovered_paise: int
    cost_paise: int
    net_recovered_paise: int
    net_recovered_ci_paise: Interval
    recoverable_money_paise: int
    money_capture_rate: float

    # --- efficiency ---------------------------------------------------------
    charge_attempts: int
    contact_actions: int
    attempts_per_recovery: float
    median_hours_to_recovery: float | None
    p90_hours_to_recovery: float | None

    # --- false action -------------------------------------------------------
    hard_decline_retries: int
    hard_decline_retry_rate: float
    wasted_cost_paise: int

    # --- safety invariants (targets: 0, 0, 100%) ----------------------------
    mandate_violations: int
    escalations: int
    gated_paise: int
    self_halt_rate: float

    # --- classification -----------------------------------------------------
    confusion: Confusion

    # --- breakdowns ---------------------------------------------------------
    by_class: dict[str, tuple[int, int]]
    by_terminal: dict[str, int]


def _bootstrap(
    outcomes: list[RecordOutcome],
    statistic: str,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Interval:
    """Percentile bootstrap over records.

    Resampling *records* (not runs) is the right unit here: the world's random
    draws are fixed by design so that every policy faces identical luck, which
    means run-to-run variation is zero and the only sampling question left is
    which subscriptions happened to be in the book.
    """
    if not outcomes:
        return Interval(low=0.0, high=0.0)

    rng = random.Random(BOOTSTRAP_SEED)
    n = len(outcomes)
    if statistic == "recovery_rate":
        values = [100.0 if o.recovered else 0.0 for o in outcomes]
    elif statistic == "net_recovered":
        values = [float(o.net_recovered_paise) for o in outcomes]
    else:  # pragma: no cover - guarded by callers
        raise ValueError(f"unknown statistic {statistic!r}")

    means: list[float] = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()

    lo = means[int(0.025 * resamples)]
    hi = means[min(resamples - 1, int(0.975 * resamples))]
    if statistic == "net_recovered":
        # Report the interval on the split total, not the per-record mean.
        return Interval(low=lo * n, high=hi * n)
    return Interval(low=lo, high=hi)


def compute(
    name: str,
    description: str,
    outcomes: list[RecordOutcome],
    values: dict[str, int],
) -> PolicyMetrics:
    """Reduce a policy's per-record outcomes to the reported metric set.

    ``values`` maps subscription id to plan amount in paise. It is passed in
    rather than read off the outcomes because an outcome only carries an amount
    when recovery succeeded -- and the achievable-money denominator has to include
    the records we *missed*, which is precisely where a policy looks worse.
    """
    n = len(outcomes)
    recovered = [o for o in outcomes if o.recovered]
    hours = sorted(o.hours_to_resolution for o in recovered if o.hours_to_resolution is not None)

    gross = sum(o.amount_recovered_paise for o in outcomes)
    cost = sum(o.cost_paise for o in outcomes)
    charge_attempts = sum(o.charge_attempts for o in outcomes)
    contact_actions = sum(o.contact_actions for o in outcomes)

    # Money the truth-reading ceiling could have reached. The denominator for
    # "how much of the achievable money did we actually capture".
    recoverable_money = sum(values[o.subscription_id] for o in outcomes if o.true_recoverable)

    # Cost spent on records the ceiling could not have recovered either: money
    # burned with no upside available, under any policy.
    wasted = sum(o.cost_paise for o in outcomes if not o.true_recoverable)

    confusion = Confusion(
        tp=sum(1 for o in outcomes if o.predicted_recoverable and o.true_recoverable),
        fp=sum(1 for o in outcomes if o.predicted_recoverable and not o.true_recoverable),
        tn=sum(1 for o in outcomes if not o.predicted_recoverable and not o.true_recoverable),
        fn=sum(1 for o in outcomes if not o.predicted_recoverable and o.true_recoverable),
    )

    by_class: dict[str, tuple[int, int]] = {}
    for outcome in outcomes:
        key = outcome.true_class.value
        got, total = by_class.get(key, (0, 0))
        by_class[key] = (got + int(outcome.recovered), total + 1)

    by_terminal: dict[str, int] = {}
    for outcome in outcomes:
        key = outcome.terminal_reason.value
        by_terminal[key] = by_terminal.get(key, 0) + 1

    return PolicyMetrics(
        name=name,
        description=description,
        n=n,
        recovered=len(recovered),
        recovery_rate=pct(len(recovered), n),
        recovery_rate_ci=_bootstrap(outcomes, "recovery_rate"),
        gross_recovered_paise=gross,
        cost_paise=cost,
        net_recovered_paise=gross - cost,
        net_recovered_ci_paise=_bootstrap(outcomes, "net_recovered"),
        recoverable_money_paise=recoverable_money,
        money_capture_rate=pct(gross, recoverable_money),
        charge_attempts=charge_attempts,
        contact_actions=contact_actions,
        attempts_per_recovery=(
            (charge_attempts + contact_actions) / len(recovered) if recovered else 0.0
        ),
        median_hours_to_recovery=statistics.median(hours) if hours else None,
        p90_hours_to_recovery=hours[int(0.9 * (len(hours) - 1))] if hours else None,
        hard_decline_retries=sum(o.hard_decline_retries for o in outcomes),
        hard_decline_retry_rate=pct(
            sum(o.hard_decline_retries for o in outcomes), max(1, charge_attempts)
        ),
        wasted_cost_paise=wasted,
        mandate_violations=sum(o.mandate_violations for o in outcomes),
        escalations=sum(
            1 for o in outcomes if o.terminal_reason is TerminalReason.ESCALATED_TO_HUMAN
        ),
        # Money withheld pending review. Counted as NOT recovered: no simulated
        # reviewer approves anything, so escalation costs the headline figure.
        # That is deliberate -- a policy must not be able to improve its safety
        # numbers by escalating everything.
        gated_paise=sum(
            values[o.subscription_id]
            for o in outcomes
            if o.terminal_reason is TerminalReason.ESCALATED_TO_HUMAN
        ),
        self_halt_rate=pct(sum(1 for o in outcomes if o.terminal_reason in SELF_HALTED), n),
        confusion=confusion,
        by_class=dict(sorted(by_class.items())),
        by_terminal=dict(sorted(by_terminal.items())),
    )


class ExceptionRow(BaseModel):
    """One unrecovered record, with the reason it stayed unrecovered."""

    model_config = ConfigDict(frozen=True)

    subscription_id: str
    true_class: str
    terminal_reason: str
    amount_paise: int
    charge_attempts: int
    contact_actions: int
    was_recoverable: bool


def exceptions(outcomes: list[RecordOutcome], values: dict[str, int]) -> list[ExceptionRow]:
    """Build the exception list: everything the policy did not recover.

    Sorted so the expensive mistakes come first -- records that *were* recoverable
    and were missed anyway, largest amount first. That ordering is the point: an
    exception list that buries the misses among the impossible cases is decoration.
    """
    rows = [
        ExceptionRow(
            subscription_id=o.subscription_id,
            true_class=o.true_class.value,
            terminal_reason=o.terminal_reason.value,
            amount_paise=values.get(o.subscription_id, 0),
            charge_attempts=o.charge_attempts,
            contact_actions=o.contact_actions,
            was_recoverable=o.true_recoverable,
        )
        for o in outcomes
        if not o.recovered
    ]
    rows.sort(key=lambda r: (not r.was_recoverable, -r.amount_paise))
    return rows
