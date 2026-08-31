# EVAL.md — measured results

> **This file is generated.** Do not edit by hand — run
> `uv run reclaimos report` and commit the result.

_Generated 2026-08-31 12:11 +05:30 · split `test` · generator 1.0.0 · seed 42 · 250 records (175 train / 75 test)_

## What these numbers are

**Every rupee figure below is simulated INR under a declared recovery model, not money moved through Razorpay.** The batch runs against a stochastic world simulator because test mode cannot model whether a retry three days later would have succeeded — measuring policy quality there would produce a number that looks live and means nothing. A separate small live test-mode slice proves the integration is real. [SIMULATION.md](SIMULATION.md) draws the line precisely.

The comparison that carries the weight is **relative**: each policy against the others and against a truth-reading ceiling, all facing **identical random draws**. Confidence intervals are percentile bootstrap intervals over the record population; they quantify sampling variation across subscriptions and say nothing about whether the world model's assumptions are right.

## Which split this is, and how often we have looked

These numbers come from the **held-out test split**. This is the measurement that counts.

Held-out reads to date, per `data/runs/held-out-reads.log`: **2**. `reclaimos eval` defaults to the development split; scoring the held-out split requires an explicit `--split test` and appends a timestamped, checksummed line to that log. A checksum proves *which* data was scored — it says nothing about how many times we looked, which is what actually corrupts a held-out set.

**Disclosure.** During Phase 1 this discipline was not yet enforced, and the held-out split was read repeatedly while the harness was being built — the eval command defaulted to it. No parameter was fitted to a held-out result (the baselines have no free parameters, and the world constants were authored before the first run and never revised), but one test did encode a threshold chosen after seeing the held-out gap. That assertion has been moved to the development split and the defaults inverted. The lapse is recorded in [docs/failure-log.md](docs/failure-log.md) rather than quietly corrected.

## Dataset

- Generator `1.0.0`, seed `42`
- 250 records — 175 train / 75 test (held out, never seen during tuning)
- Test split SHA-256: `001d7c1c3d85286203fbce32ac9103059cc514a8b41a9ca102b5be60b6360cb9`
- Attempt cap: 4
- Agent config: `4ed35761c28b921f…` (frozen)
- Declared costs: ₹2.00 per charge attempt, ₹0.50 per customer contact

Realised failure mix:

| family | share | class | share |
| --- | ---: | --- | ---: |
| soft | 46.0% | `EXPIRY_CARD_EXPIRED` | 11.6% |
| hard | 30.0% | `EXPIRY_MANDATE_EXPIRED` | 3.2% |
| expiry | 14.8% | `HARD_DO_NOT_HONOR` | 18.8% |
| unknown | 9.2% | `HARD_MANDATE_REVOKED` | 6.4% |
|  |  | `HARD_RISK_FLAGGED` | 4.8% |
|  |  | `SOFT_INSUFFICIENT_FUNDS` | 31.2% |
|  |  | `SOFT_ISSUER_TECHNICAL` | 10.0% |
|  |  | `SOFT_LIMIT_EXCEEDED` | 4.8% |
|  |  | `UNKNOWN` | 9.2% |

## Money recovered

| policy | recovery rate | 95% CI | gross | cost | net | 95% CI (net) | % of achievable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| do_nothing | 0.0% | [0.0, 0.0] | ₹0.00 | ₹0.00 | ₹0.00 | ₹0.00–₹0.00 | 0.0% |
| retry_once | 22.7% | [13.3, 32.0] | ₹11,683.00 | ₹144.00 | ₹11,539.00 | ₹5,248.00–₹19,429.00 | 20.4% |
| retry_3x_fixed | 37.3% | [25.3, 48.0] | ₹20,972.00 | ₹354.00 | ₹20,618.00 | ₹11,896.00–₹30,445.00 | 36.6% |
| contact_once | 26.7% | [17.3, 37.3] | ₹23,480.00 | ₹37.50 | ₹23,442.50 | ₹10,147.50–₹42,536.50 | 41.0% |
| reclaimos_agent | 68.0% | [56.0, 78.7] | ₹47,349.00 | ₹179.00 | ₹47,170.00 | ₹30,486.00–₹66,953.00 | 82.7% |
| **oracle_ceiling** | 73.3% | [62.7, 82.7] | ₹57,245.00 | ₹164.00 | ₹57,081.00 | ₹37,111.50–₹81,998.50 | 100.0% |

`oracle_ceiling` is **not a policy**. It reads the sealed latent state (when a short balance recovers, how long an issuer outage lasts, how willing a customer is to pay) that no record exposes. It is the ceiling a perfect policy could reach on this data, and it exists so that "% of achievable" has an honest denominator.

### What these intervals separate, and what they do not

**The agent is cleanly ahead of the best baseline.** Its 95% interval [56.0, 78.7] does not overlap `retry_3x_fixed`'s [25.3, 48.0]. On 75 records that separation is real, not a sampling artifact.

**The agent cannot be distinguished from the ceiling here, and that is not the same as matching it.** Its interval [56.0, 78.7] overlaps the ceiling's [62.7, 82.7], so at this sample size the remaining gap (5.3 points) is inside the noise. The honest claim is that we cannot measure the shortfall from here — not that there is none.

### Against doing nothing

| policy | net above floor | per record |
| --- | ---: | ---: |
| retry_once | ₹11,539.00 | ₹153.85 |
| retry_3x_fixed | ₹20,618.00 | ₹274.91 |
| contact_once | ₹23,442.50 | ₹312.57 |
| reclaimos_agent | ₹47,170.00 | ₹628.93 |
| **oracle_ceiling** | ₹57,081.00 | ₹761.08 |

## Efficiency

| policy | charge attempts | contacts | actions per recovery | median time | p90 time |
| --- | ---: | ---: | ---: | ---: | ---: |
| do_nothing | 0 | 0 | 0.00 | — | — |
| retry_once | 72 | 0 | 4.24 | 24h | 24h |
| retry_3x_fixed | 177 | 0 | 6.32 | 24h | 72h |
| contact_once | 0 | 75 | 3.75 | 24h | 24h |
| reclaimos_agent | 72 | 70 | 2.78 | 48h | 168h |
| **oracle_ceiling** | 57 | 100 | 2.85 | 24h | 120h |

## Safety invariants

Retries against hard declines and mandate violations are **false actions**: money and customer patience spent where no policy could have recovered anything. The target for mandate violations is zero, always.

| policy | hard-decline retries | as % of attempts | mandate violations | escalated | gated | self-halted | wasted cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| do_nothing | 0 | 0.0% | 0 | 0 | ₹0.00 | 100.0% | ₹0.00 |
| retry_once | 23 | 31.9% | 3 | 0 | ₹0.00 | 96.0% | ₹36.00 |
| retry_3x_fixed | 69 | 39.0% | 3 | 0 | ₹0.00 | 96.0% | ₹108.00 |
| contact_once | 0 | 0.0% | 0 | 0 | ₹0.00 | 100.0% | ₹10.00 |
| reclaimos_agent | 10 | 13.9% | 0 | 1 | ₹7,999.00 | 100.0% | ₹43.50 |
| **oracle_ceiling** | 0 | 0.0% | 0 | 0 | ₹0.00 | 100.0% | ₹59.50 |

### How escalations are resolved

**They are not.** No simulated reviewer approves anything. An escalated subscription terminates as `escalated_to_human`, executes no action, and counts as **not recovered**; the money involved appears in the *gated* column and nowhere else.

That is the unflattering choice, on purpose. If escalations were auto-approved, a policy could buy a perfect safety record by escalating everything and still collect the recovery. Counting them as losses means over-escalating costs the headline number, so the safety metrics cannot be gamed from that direction. The cost is that our reported recovery rate is a floor: a real deployment with a human reviewer would recover some of the gated amount.

## Predicting `recoverable`

Ground truth is the world's sampled outcome under the ceiling policy — not a rule, and not anything the classifier can read. Some gateway `(code, reason)` tuples are emitted by more than one true class, so a perfect score is impossible by construction; that error floor is deliberate ([ADR-0006](docs/decisions/ADR-0006-eval-first-stochastic-world.md)).

| policy | precision | recall | F1 | TP | FP | TN | FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| do_nothing | 0.000 | 0.000 | 0.000 | 0 | 0 | 20 | 55 |
| retry_once | 0.733 | 1.000 | 0.846 | 55 | 20 | 0 | 0 |
| retry_3x_fixed | 0.733 | 1.000 | 0.846 | 55 | 20 | 0 | 0 |
| contact_once | 0.733 | 1.000 | 0.846 | 55 | 20 | 0 | 0 |
| reclaimos_agent | 0.836 | 0.836 | 0.836 | 46 | 9 | 11 | 9 |
| **oracle_ceiling** | 1.000 | 1.000 | 1.000 | 55 | 0 | 20 | 0 |

## Recovery rate by true decline class

| decline class | n | do_nothing | retry_once | retry_3x_fixed | contact_once | reclaimos_agent | **oracle_ceiling** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `EXPIRY_CARD_EXPIRED` | 9 | 0% | 0% | 0% | 33% | 67% | 67% |
| `EXPIRY_MANDATE_EXPIRED` | 3 | 0% | 0% | 0% | 33% | 33% | 33% |
| `HARD_DO_NOT_HONOR` | 14 | 0% | 0% | 0% | 36% | 43% | 57% |
| `HARD_MANDATE_REVOKED` | 3 | 0% | 0% | 0% | 33% | 33% | 33% |
| `HARD_RISK_FLAGGED` | 6 | 0% | 0% | 0% | 17% | 33% | 50% |
| `SOFT_INSUFFICIENT_FUNDS` | 24 | 0% | 29% | 58% | 8% | 83% | 88% |
| `SOFT_ISSUER_TECHNICAL` | 7 | 0% | 71% | 100% | 43% | 100% | 100% |
| `SOFT_LIMIT_EXCEEDED` | 4 | 0% | 75% | 100% | 50% | 100% | 100% |
| `UNKNOWN` | 5 | 0% | 40% | 60% | 40% | 80% | 80% |

## Exception list

Everything `reclaimos_agent` (the baseline with the highest recovery rate) left unrecovered: **24 records**, of which **5 were reachable by the ceiling** and are therefore genuine misses rather than impossible cases. Misses are listed first, largest amount first.

| subscription | true class | ended as | amount | charges | contacts | was reachable |
| --- | --- | --- | ---: | ---: | ---: | :---: |
| `sub_1ENNFAZ37VG688` | `HARD_DO_NOT_HONOR` | `escalated_to_human` | ₹7,999.00 | 0 | 0 | yes |
| `sub_HJ74NL04LLV1A7` | `SOFT_INSUFFICIENT_FUNDS` | `policy_stopped` | ₹799.00 | 3 | 2 | yes |
| `sub_R46GA2TDEC8JP4` | `HARD_DO_NOT_HONOR` | `hard_decline_stop` | ₹799.00 | 0 | 2 | yes |
| `sub_KRXK2C9PFRT013` | `HARD_DO_NOT_HONOR` | `policy_stopped` | ₹499.00 | 2 | 2 | yes |
| `sub_S09TKDGHGAUK9U` | `HARD_RISK_FLAGGED` | `hard_decline_stop` | ₹299.00 | 0 | 2 | yes |
| `sub_B1V2CCQFG9294B` | `EXPIRY_CARD_EXPIRED` | `policy_stopped` | ₹4,999.00 | 0 | 2 | no |
| `sub_YJYW6CLMAMR5V6` | `SOFT_INSUFFICIENT_FUNDS` | `policy_stopped` | ₹2,999.00 | 2 | 2 | no |
| `sub_GNNHHD9EDE1ZUB` | `EXPIRY_MANDATE_EXPIRED` | `policy_stopped` | ₹2,999.00 | 0 | 2 | no |
| `sub_TPV6PJ3N3QN7HU` | `UNKNOWN` | `policy_stopped` | ₹1,499.00 | 2 | 2 | no |
| `sub_2UZAXZ2NZ6Y51P` | `EXPIRY_CARD_EXPIRED` | `policy_stopped` | ₹1,499.00 | 0 | 2 | no |
| `sub_BJLZZ2VV9YMTZD` | `HARD_DO_NOT_HONOR` | `hard_decline_stop` | ₹1,499.00 | 0 | 2 | no |
| `sub_DWWCGM41PEZUL8` | `EXPIRY_CARD_EXPIRED` | `policy_stopped` | ₹1,499.00 | 0 | 2 | no |
| `sub_V32EHXVCY67XKW` | `HARD_RISK_FLAGGED` | `hard_decline_stop` | ₹999.00 | 0 | 2 | no |
| `sub_EG6TX3392NUS7T` | `HARD_RISK_FLAGGED` | `hard_decline_stop` | ₹799.00 | 0 | 2 | no |
| `sub_BWQX4NU07QKTFR` | `HARD_DO_NOT_HONOR` | `hard_decline_stop` | ₹499.00 | 0 | 2 | no |

_9 further rows omitted. The complete list for every policy is written to `data/runs/exceptions-test-<policy>.csv`._

## What a blind retry ladder actually buys

On this split `retry_3x_fixed` recovers 37.3% [25.3, 48.0] against `contact_once` at 26.7% [17.3, 37.3]. But it pays for that recovery with actions that could never have worked:

| | retry_3x_fixed | contact_once |
| --- | ---: | ---: |
| Retries against hard declines | 69 | 0 |
| Debits attempted against expired consent | 3 | 0 |
| Cost burned on unreachable records | ₹108.00 | ₹10.00 |

A hard decline — a card reported stolen, an issuer refusing outright, a revoked mandate -- will not authorise on the second attempt or the tenth. A mandate violation is worse than wasted: it is a debit attempted without live consent, which our own executor is built to refuse ([ADR-0003](docs/decisions/ADR-0003-explainable-bounded-gated.md)).

**This is the target ReclaimOS is aimed at: the ladder's recovery rate without the ladder's waste.** Unlike a money-ranking between these two baselines, it holds on both splits — which is the only reason it is stated here at all (see [docs/failure-log.md](docs/failure-log.md)).

## The agent column is deliberately empty

ReclaimOS's own policy does not exist yet. The harness, the baselines and every metric above were built first, on purpose ([ADR-0006](docs/decisions/ADR-0006-eval-first-stochastic-world.md)): a scoreboard built after the player is a scoreboard shaped around the player. When the agent lands in Phase 4 it implements the same `Policy` interface and is scored by this same code path — there is no gentler route available to it.

## Assumptions a reader may want to disagree with

- Base recovery probabilities per decline class, and the timing, decay, method and tenure multipliers — all in `src/reclaimos/generator/outcome_model.py`, each cited at its use site.
- Cost per charge attempt (₹2.00) and per contact (₹0.50) — `src/reclaimos/eval/costs.py`. Real acquirer economics are not public.
- The failure mix, conditioned on payment rail — `src/reclaimos/generator/profiles.py`, anchored on published vendor ranges for involuntary churn, used as ranges.
- The ceiling is greedy, not optimal: it maximises probability within each slot, which is exact for a single slot and mildly suboptimal across slots. It is therefore a ceiling *estimate*, and a very slightly conservative one.

