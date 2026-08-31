# ReclaimOS

**An autonomous, fully-auditable agent that recovers revenue lost to failed
recurring payments — where the LLM is never allowed to move money.**

Razorpay AI Buildathon 2026 · Track 03, AI Revenue Recovery · built on Razorpay
**test mode**.

---

## The problem

Involuntary churn is the quietest large leak in a subscription business. A card
expires, a balance is short, an issuer has a bad afternoon — the customer never
decided to leave, and the business loses them anyway.

Published vendor estimates put this at roughly **9% of MRR**, with **20–40% of
subscription churn being involuntary** rather than chosen. The rails matter: **UPI
AutoPay fails far more often than cards (~8–15% versus ~2–3%)**, which makes this
sharper in India than in the markets most dunning tools were designed for. Optimised
dunning recovers roughly half or more of failed charges.

These are vendor-published ranges, cited as ranges. We do not have proprietary
Razorpay data and do not pretend to.

## What ReclaimOS does

One loop, per failed charge, done rigorously:

```
detect → diagnose → decide → execute (bounded + gated) → know when to stop → record
```

Then it reports money recovered across a 200+ record batch, next to an **honest
exception list** of everything it could not recover and why.

## The one thing to notice

**The LLM is off the money path.** Money movement is a deterministic, idempotent
state machine. The model is used for exactly two things:

1. turning a raw decline code into a human-readable root cause for the ledger, and
2. drafting the customer-facing dunning message (Hinglish supported).

An LLM output can never trigger a money action — only the deterministic policy
engine can. This is enforced structurally, not by prompt. The `Explanation` the
model returns carries two strings and provenance: **no decline class, no score, no
action, no amount.** A model response has nowhere to put a decision even if it
tried, and a test asserts that field set so adding one fails the build. The
classification and the propensity score are computed *before* the model is called
and passed in as inputs. The Razorpay MCP server is mounted **READ_ONLY**, so the
model can read everything and move nothing.

The fallback is not a mode we hope never to hit — it is the path CI and a fresh
clone both take, because the Anthropic SDK is an optional extra and neither has a
key. Ten malformed responses and five client exceptions are each *forced* in tests
and must land on the deterministic template; which path ran is recorded on every
explanation, because an unlabelled fallback is a small lie told at scale.

See [docs/architecture.md](docs/architecture.md) — in the diagram, no dashed line
ever enters a decision node.

### What we deliberately did *not* use AI for

| Decision | Why not AI |
| --- | --- |
| Choosing the recovery action | Must be attributable to a named rule and identical on replay |
| Scoring recovery propensity | A transparent rule table beats a model trained on labels we generated ourselves ([ADR-0002](docs/decisions/ADR-0002-no-model-training.md)) |
| Classifying decline codes | Deterministic lookup; ambiguity is flagged, not guessed at by a model |
| Deciding when to stop | Stopping rules are the safety property; they cannot be probabilistic |
| Executing a charge | Idempotency key, mandate check, gateway call — no model in the path |

## Four controls, each failing independently

| Control | Mechanism | ADR |
| --- | --- | --- |
| LLM cannot act | closed action enum; rules pick actions, model writes prose | [0001](docs/decisions/ADR-0001-llm-off-the-money-path.md) |
| Bounded | signed recovery mandate (max amount, method, expiry) modelled on Google AP2; executor refuses out-of-envelope | [0003](docs/decisions/ADR-0003-explainable-bounded-gated.md) |
| Gated | tiered autonomy — low-risk auto-executes, high-risk goes to a human review queue | [0003](docs/decisions/ADR-0003-explainable-bounded-gated.md) |
| Never double-charges | deterministic idempotency key `sub:attempt:action`, claimed atomically before any gateway call | [0004](docs/decisions/ADR-0004-idempotency.md) |

Every decision lands in an **append-only, hash-chained ledger**
([ADR-0005](docs/decisions/ADR-0005-append-only-hash-chained-ledger.md)) linking
inputs → propensity → the exact rule that fired → LLM rationale → action → outcome.

## Results

See **[EVAL.md](EVAL.md)** for the full metrics table, confidence intervals and the
exception list.

Two things about how those numbers are produced, stated up front rather than buried:

- The 200+ record batch runs against a **stochastic world simulator**, not live
  Razorpay. Test mode cannot model whether a retry three days later would have
  succeeded, so measuring policy quality there would produce a number that looks
  live and means nothing. A small **live test-mode slice** proves the integration is
  real. [SIMULATION.md](SIMULATION.md) draws the line precisely.
- The generator does **not** label recoverability using the same rules the policy
  engine uses — that would make precision and recall 1.0 by construction. Ground
  truth is a sampled outcome from latent factors the rule table does not encode
  ([ADR-0006](docs/decisions/ADR-0006-eval-first-stochastic-world.md)).

Every headline number is a mean with a **bootstrap 95% confidence interval**, and
every policy faces **identical random draws**. One cherry-picked match proves
nothing.

### The headline, on held-out data

Scored **once** on the sealed test split (75 records), with the agent configuration
frozen and committed beforehand — `4ed35761c28b921f…`, fifteen parameters chosen
a priori from published dunning practice, **no tuning run at all**:

| policy | recovery | 95% CI | % of achievable | hard-decline retries | mandate violations |
| --- | ---: | ---: | ---: | ---: | ---: |
| retry_3x_fixed | 37.3% | [25.3, 48.0] | 36.6% | 69 | 3 |
| **reclaimos_agent** | **68.0%** | **[56.0, 78.7]** | **82.7%** | 10 | **0** |
| oracle_ceiling *(reads sealed truth)* | 73.3% | [62.7, 82.7] | 100% | 0 | 0 |

Two claims, and only two, because the intervals support exactly these:

- **The separation from the best baseline is real.** [56.0, 78.7] does not overlap
  [25.3, 48.0]. That is a genuine +30.7 point gap, not sampling noise.
- **We cannot distinguish the agent from the ceiling at this sample size, which is
  not the same as matching it.** The intervals overlap, so the remaining 5.3 points
  is inside the noise. The honest claim is that we cannot measure the shortfall from
  here — not that there is none.

The agent recovers **1.8× more** than the retry ladder while committing **zero**
mandate violations and **85% fewer** hard-decline retries. Its remaining 10 hard
retries are all on the ambiguous gateway tuple that soft declines also emit — the
error floor the taxonomy deliberately preserves, not avoidable mistakes.

We deliberately did **not** run a grid search. The untuned configuration already
reaches 88% of the ceiling on the development split, and optimising fifteen knobs
for the last few points would have traded a clean claim — *the decline model is
right by construction* — for a dirtier one, while risking the sealed split for
marginal gain.

### What the baselines already show

A blind retry ladder buys its recovery with actions that could never have worked.
On the development split, `retry_3x_fixed` spends **154 retries against hard
declines** — cards reported stolen, issuers refusing outright, revoked mandates,
none of which authorise on the second attempt or the tenth — and attempts **5
debits against consent that had already expired**. `contact_once` commits **zero
of each**, and recovers less.

**That gap is what ReclaimOS is aimed at: the ladder's recovery rate without the
ladder's waste.** It is stated here because it holds on *both* splits. An earlier,
more quotable claim — that outreach netted more money than the ladder — did not:
it was a 75-record sampling artifact that reversed on the larger split, and the
confidence intervals had said so all along. That correction is written up in
[docs/failure-log.md](docs/failure-log.md), because how we caught it matters more
than that we had it.

## Quickstart

Needs Python 3.12 (or just [`uv`](https://docs.astral.sh/uv/), which fetches it).
**No API keys, no Docker, no `.env`** — the full evaluation runs on a clean clone.

```bash
git clone https://github.com/DhruvGoyal404/reclaimos
cd reclaimos

uv sync --extra dev
uv run pytest                              # green

uv run reclaimos gen --n 250 --seed 42     # synthetic subscriptions + sealed world
uv run reclaimos eval --policy all         # every baseline, identical random draws
uv run reclaimos report                    # regenerates EVAL.md
```

Postgres and Redis are optional, behind a compose profile:

```bash
docker compose --profile full up -d
```

Inspect the audit trail:

```bash
uv run reclaimos ledger stats     # events, ledger entries, idempotency claims
uv run reclaimos ledger verify    # recompute every hash; exits non-zero on a break
uv run reclaimos ledger export    # the whole chain as JSONL
```

Windows: `./tasks.ps1 check` runs format, lint, types and tests. Elsewhere:
`make check`.

## Repository map

```
src/reclaimos/
  domain/       typed vocabulary — actions, decline classes, mandates, outcomes
  generator/    synthetic subscriptions + the stochastic world model
  eval/         metrics, baselines, harness, EVAL.md rendering
  ingest/       HMAC-verified webhook ingestion — verify, normalise, append
  store/        append-only event store, hash-chained ledger, idempotency claims
  diagnose/     deterministic classifier, propensity rules, advisory LLM explainer
  policy/       mandate teeth, executor, HITL queue, the recovery agent
docs/
  architecture.md    the diagram, and why it is shaped that way
  failure-log.md     what broke, how we got out — written as it happened
  decisions/         six ADRs, one per locked decision
SIMULATION.md   what is simulated, what is live, and what that permits us to claim
SECURITY.md     secrets, webhook verification, money-path controls, prompt injection
```

## Status

| Phase | | |
| --- | --- | --- |
| 0 | Scaffold, ADRs, typed domain | done |
| 1 | Generator + eval harness + baselines | done |
| 2 | Webhook ingestion, event store, hash-chained ledger, idempotency | done |
| 3 | Classifier, propensity table, LLM explainer | done |
| 4 | Policy engine, mandates, HITL queue, idempotent executor | done |
| 5 | Live Razorpay test-mode slice, observability, demo capture | next |
| 6 | Full eval run, EVAL.md, live test-mode slice | |
| 7 | Demo capture and pitch | |

## Licence

MIT. See [LICENSE](LICENSE).
