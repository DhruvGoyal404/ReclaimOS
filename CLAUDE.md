# CLAUDE.md — ReclaimOS project context

Read this first in every session. It carries the full brief so no context is lost
between sessions.

## What this is

**ReclaimOS** — an autonomous, fully-auditable agent that recovers revenue lost to
failed recurring payments (involuntary churn) on Razorpay **test-mode** APIs.

Submission for the **Razorpay AI Buildathon 2026**, **Track 03 — AI Revenue
Recovery**. Applications close **5 September 2026**. Deliverables: this public repo
plus a 5-minute pitch video. Solo builder: Dhruv Goyal.

The loop, per failed charge:
`detect -> diagnose -> decide -> execute (bounded + gated) -> know when to stop -> record`,
then report money recovered across a 200+ record batch with an honest exception
list of what could not be recovered and why.

## Standing rules — read before doing anything

### Git is the user's, exclusively

**Never run a git or `gh` command that writes.** No `init`, `add`, `commit`,
`push`, `tag`, `rebase`, `merge`, `restore`, `checkout`, no `gh repo create`, no
`gh pr`. Dhruv owns the entire history and runs every write himself; a commit
authored by an assistant would force him to rebase, which he does not want.

At every logical checkpoint, **print the exact commands for him to run** — a
`git add` line and a `git commit -m "..."` with a properly written message — and
stop there. Do not offer to run them.

Read-only git (`status`, `log`, `diff`, `check-ignore`, `ls-files`) is permitted
for verification, and should be announced as read-only when used.

The repository is **private and local**. It goes public after a full audit, once
coding and demo capture are finished.

### Every number in the pitch is copy-pasted, never paraphrased

The video script and the application answers quote figures **verbatim from the
generated EVAL.md** — `68.0%`, `[56.0, 78.7]`, `+30.7pp`, `82.7% of achievable`,
zero mandate violations, 85% fewer hard-decline retries. No rounding, no
restating from memory, no "roughly".

This is the same rule that governs the repo, extended to the pitch, and it exists
because of a specific failure: the one headline this project got wrong survived
because it lived in prose nobody recomputed (`docs/failure-log.md`). The pitch
must not reintroduce that failure mode.

### Scope is frozen

The core build is done: 292 tests, the held-out number, the mandate teeth, the
audit trail, four failure stories. **No new capabilities.** No XGBoost, no UI, no
extra policies, no refactors of working code.

Remaining work is only: **live Razorpay test-mode slice → demo capture → pitch.**
Anything else risks a clean tree and a clean story for no gain.

## The four judging axes — every decision maps to one

1. **Problem taste** — involuntary churn is a real, quantified leak (~9% of MRR;
   20–40% of subscription churn is involuntary — vendor data, always cite as ranges).
   Razorpay is shipping its own recovery agent, so the problem is pre-validated.
2. **Build quality** — a judge clones and it runs first try. Structured, tested,
   inspectable.
3. **AI judgment** — the right tool in the right place, *and* where we deliberately
   chose not to use AI. Say it out loud in the README.
4. **Failure recovery** — "what broke and how you got out" is the answer they read
   first. `docs/failure-log.md` is written as things break, never retrofitted.

Judges read the work, not the resume. Honest measured metrics beat an ambitious
half-broken demo. "One cherry-picked match proves nothing" is their line — hence
bootstrap confidence intervals on every headline number.

## Locked decisions — do not relitigate

Each has an ADR in `docs/decisions/`.

1. **LLM off the money path** (ADR-0001). The LLM only (a) turns a decline code plus
   context into a human-readable root cause for the ledger, and (b) drafts the
   customer-facing dunning message (Hinglish supported). An LLM output can never
   trigger a money action; only the deterministic policy engine can. Every LLM node
   has a deterministic template fallback and fails closed.
2. **No model training** (ADR-0002). Recovery propensity is a transparent,
   literature-grounded rule table. Training on rule-derived synthetic labels and
   evaluating on the same generator is a circular benchmark. An optional calibrated
   scorer may be added *after* the core loop, labelled in EVAL.md as demonstrating
   calibration, not real-world accuracy. **Not now.**
3. **Explainable, bounded, gated** (ADR-0003). Every ledger row links inputs ->
   propensity -> the exact rule that fired -> LLM rationale -> action -> outcome.
   Every action carries a signed recovery mandate (max amount, currency, expiry,
   allowed method, reason code) modelled on Google AP2; the executor refuses
   anything outside it. Tiered autonomy: low-risk auto-executes, high-risk (refund,
   payout, over threshold, more than N attempts) goes to a human review queue.
4. **Idempotency on every money action** (ADR-0004). Deterministic key
   `subscriptionId:attemptNo:actionType`, checked before any tool call.
5. **Append-only hash-chained ledger and event store** (ADR-0005). Exportable and
   inspectable. This is the audit-trail deliverable.
6. **Eval harness and synthetic generator built FIRST** (ADR-0006). We instrument
   the scoreboard before we play.

## Accepted architectural pushbacks (session 1)

- **The generator is a stochastic world simulator, not a labelled dataset.** Ground
  truth comes from a sampled outcome model with latent factors the rule table does
  *not* encode (payday timing, issuer downtime, tenure) — never from our own rules.
  Otherwise precision/recall is 1.0 by construction. Common random numbers: one
  uniform draw fixed per `(record, attempt_no)` so every policy faces identical luck.
- **The 200+ batch runs on `SimulatedGateway`, not live Razorpay.** A small
  **recorded** 10–20 record run on `LiveTestModeGateway` proves the integration is
  real. `SIMULATION.md` states the split; EVAL.md repeats it in bold.
- **Guardrails AI is cut.** Pydantic strict schemas plus a small `redact.py` plus
  fail-closed LLM output validation gets ~95% of the value with zero install risk.
- **Postgres and Redis are optional.** SQLite (WAL) plus an in-process idempotency
  store is the default so a clean clone runs with no Docker. Compose ships behind a
  profile.
- **Razorpay MCP server is READ_ONLY and off the executor path.** Writes go through
  the Razorpay Python SDK inside the deterministic executor. The model can read
  everything and move nothing.
- Python **3.12** (not 3.13) for wheel coverage. Our own JSONL trace is the source of
  truth; Langfuse is an optional adapter, never a hard dependency.

## Held-out discipline — non-negotiable

**Iterate on `train`. Touch `test` once, for the final number.**

`reclaimos eval` defaults to `--split train`. Scoring the held-out split requires
an explicit `--split test` and appends a timestamped, checksummed line to
`data/runs/held-out-reads.log`, which is committed. Before quoting any held-out
figure, state how many reads the log shows.

This was violated during Phase 1 — `eval` defaulted to `test` and the split was
read dozens of times. Nothing was fitted to it, but a test threshold had been
chosen after seeing the result. Both the lapse and the fix are in
`docs/failure-log.md`. Do not repeat it.

**No ranking claim is repeated until it holds on both splits.** The Phase 1
headline that outreach netted more money than the retry ladder was a 75-record
sampling artifact; it reverses on train. Quote the confidence interval, never the
point estimate.

## Phase 3 acceptance criteria — do not downgrade

Full text in [ADR-0007](docs/decisions/ADR-0007-llm-narrates-never-decides.md);
all three bind:

1. **The decision is computed before the model is called.** Classifier and
   propensity table run first and are passed to the explainer as *inputs*. The
   explainer's return type carries only prose — no `DeclineClass`, no score, no
   `ActionType`, no amount — so a model response has nothing to put a decision
   into. A test asserts the field set, and another asserts the money path still
   produces identical output with the LLM client removed entirely.
2. **Fail-closed is proven by forcing failure.** A stub client returning malformed
   JSON, a wrong schema, an empty string, an over-long response and a raised
   exception must each yield the deterministic template. The template path must
   work with no API key at all, which is CI's normal state. Whether an explanation
   came from the model or the template is recorded in the ledger — an unlabelled
   fallback is a small lie told at scale.
3. **Prompt injection is tested, not assumed.** Hostile text in a customer name or
   a decline description must leave classification, propensity and action
   byte-identical, and must not appear in any stored action.

Ambiguous decline tuples are resolved by the *rules*, flagged low-confidence and
treated conservatively. Ambiguity makes the policy more cautious, never more
aggressive, and is never handed to a model to guess at.

## Phase 4 acceptance criteria — do not downgrade

Written down now so time pressure cannot quietly reduce them to "there's a runtime
check". Full text in
[ADR-0003](docs/decisions/ADR-0003-explainable-bounded-gated.md); all four bind:

1. `src/reclaimos/policy/executor.py` owns enforcement and raises
   `MandateViolation`.
2. Order is **mandate → idempotency → gateway**, so a refused action never burns
   an idempotency key.
3. **Type-level teeth:** `ChargeRequest` is constructible only via
   `Mandate.authorize()` returning a `MandateToken`; the SDK wrapper accepts a
   `ChargeRequest` and nothing else. An unauthorised charge must fail to
   *typecheck*, not merely at runtime.
4. The harness check stays as defence in depth, and a test asserts the executor
   rejects **first**, so we can always say which gate fired.

Today the only mandate gate is in `eval/harness.py`. It blocks, but the harness is
the scorekeeper, not an executor.

## Stack

Python 3.12 with `uv` · Pydantic v2 · Typer and Rich · LangGraph (Phase 4, thin
shell — the policy engine is a pure, unit-testable function library that works
without it) · Razorpay Python SDK for writes, official Razorpay MCP server READ_ONLY
for the analyst surface · Anthropic `claude-sonnet-5` for the two LLM nodes
(configurable via `RECLAIMOS_LLM_MODEL`) · SQLite default, Postgres and Redis
optional · pytest, ruff, mypy strict.

## Hard constraints — respect these or lose days

- Dev environment is **Windows plus PowerShell**, working dir
  `F:\Hackathons\RazorPay-Buildathon`. Keep everything cross-platform; `tasks.ps1`
  mirrors the `Makefile`.
- Razorpay **test mode cannot create disputes or chargebacks** (no `POST /disputes`;
  they originate from banks). This is exactly why we chose recovery over a chargeback
  responder — our whole loop is drivable in test mode via Subscriptions "Charge this
  now" success/failure simulation and the `subscription.*` / `payment.*` webhook
  chain.
- **Never claim NPCI UAP integration.** No public spec, needs RBI approval. Market
  context only.
- Test-mode limits: card tokens valid only about 3 days; RazorpayX payout approval
  states (pending, rejected) unavailable; Smart Collect works for NEFT, IMPS and RTGS
  but not virtual UPI IDs.
- Razorpay docs **disagree on retries-before-halt (3 vs 4)**. We assume 4
  (`RECLAIMOS_MAX_ATTEMPTS`), stated in `config.py` and ADR-0003. Verify against
  current docs when the live slice lands.
- **Secrets are never committed.** Test keys live in `.env`, documented in
  `SECURITY.md`.

## Deliberately cut

Voice recovery · multi-merchant dashboards · real (non-test) payments · Kafka · any
chat or checkout surface. One loop, done rigorously.

## Build order

0. Scaffold, trust-signal files, CLAUDE.md. **done**
1. Synthetic generator and eval harness, with baselines measured **before** the agent
   exists so we can never tune against a baseline we later shaped. **done**
2. Ingestion (signature-verified webhooks), event store, hash-chained ledger,
   idempotency store. **done** — lives in `ingest/` and `store/`; append-only is
   enforced by SQLite triggers, not by convention.
3. Diagnosis: deterministic decline classifier, rule-based propensity table, LLM
   root-cause explainer. **done** — lives in `diagnose/`. The classifier cannot
   reach a model (asserted by parsing its imports); the explainer's return type
   cannot carry a decision; the template path is what CI takes.
4. Policy and decision engine, bounded action catalogue, stopping rules, mandate
   signing, HITL queue, idempotent execution. **done** — lives in `policy/`. All
   four ADR-0003 criteria hold; an unauthorised charge fails to *typecheck*, proven
   by a test that runs mypy. Config frozen untuned at `4ed35761c28b921f`; held-out
   split read once (log shows 2 entries: 1 backfill + 1 real). **Held-out headline:
   agent 68.0% [56.0, 78.7] vs best baseline 37.3% [25.3, 48.0], ceiling 73.3%.**
5. Guardrails, observability, rate limits, PII redaction; deliberately reproduce and
   fix the duplicate-charge race.
6. Full eval run, regenerate EVAL.md, polish README.
7. Demo capture, 5-minute pitch script, and the "what broke and how we got out"
   write-up.

## Working agreement

- Think before building. Keep the tree runnable at every commit. Never over-engineer
  past the brief.
- Prefer determinism and explainability over cleverness. This wins on rigor and
  auditability, not model size.
- When you hit a real bug, surface it and log it in `docs/failure-log.md`. That is
  material, not embarrassment.
- Concise in chat, thorough in code and docs.
- All money is `int` paise. Never a float. Ever.
