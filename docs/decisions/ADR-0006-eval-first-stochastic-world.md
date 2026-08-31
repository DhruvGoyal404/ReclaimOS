# ADR-0006 — Eval harness first, and the generator is a world simulator

**Status:** Accepted · **Date:** 2026-08-31

## Context

Our entire competitive claim is *honest, measured money recovered*. Two ways to
lose that claim:

1. Build the agent first, then build the scoreboard around whatever it happens to
   do well.
2. Build a generator that labels `recoverable?` using the same decline-code rules
   the policy engine uses. Then precision and recall are 1.0 by construction, and
   the number means nothing at all. This is the same circularity that rules out
   training a model (ADR-0002), one layer up.

## Decision

**The eval harness and the data generator are built before the agent.** Baselines
are implemented and measured while the agent column is still empty, so the agent
can never be tuned against a baseline we later reshaped.

**The generator is a stochastic world simulator, not a labelled dataset.** It
defines a latent recovery probability

```
P(recover | decline_class, attempt_no, retry_delay_hours, day_of_month,
            method, tenure, issuer_downtime)
```

drawn from published vendor ranges, and it exposes
`world.resolve(record, action, timing) -> AttemptResult`. Any policy can be scored,
because the world responds to whatever the policy actually did rather than being
compared against a fixed label.

Three properties make this defensible:

- **Latent factors the rule table does not encode.** Payday and salary-cycle
  timing, issuer downtime windows, and customer tenure all move the true
  probability. Our rule table knows about some of these and not others, on purpose,
  so there is real headroom and real error.
- **Ambiguous decline tuples.** Some `(code, reason)` pairs are emitted by more
  than one true class, so a perfect classifier is impossible and the measured error
  floor is genuine.
- **Common random numbers.** One uniform draw is fixed per `(record, attempt_no)`
  at generation time and shared by every policy. All policies face identical luck,
  so differences between them are differences in judgement, not in sampling noise.
  This is standard variance reduction, and it makes baseline comparisons fair.

Ground-truth labels still exist for the precision and recall table, but they are
the world's *sampled outcome under the oracle action* — not a rule.

The held-out test split is never seen during tuning, and its checksum is recorded
in EVAL.md.

## Consequences

- Headline numbers are reported as mean with a bootstrap 95% confidence interval
  over records, never a single point estimate from a single run. "One cherry-picked
  match proves nothing."
- We can measure an oracle ceiling and report how far short the agent falls, which
  is a far more useful number than a raw recovery rate.
- The world model is a set of assumptions, and it is wrong in ways we cannot
  quantify. `SIMULATION.md` states exactly what is simulated and what is live, and
  every recovered-rupee figure is labelled simulated.
