# Contributing

## Setup

```bash
uv sync --extra dev        # installs Python 3.12 + all deps from the lockfile
uv run pytest              # should be green immediately
uv run reclaimos --help
```

No `.env`, no Docker and no API keys are needed for the generator, the eval harness
or the test suite. Credentials are only required for the live Razorpay test-mode
slice.

## Before you commit

```bash
uv run ruff format .
uv run ruff check --fix .
uv run mypy
uv run pytest
```

Or `./tasks.ps1 check` on Windows, `make check` elsewhere. CI runs exactly these.

## House rules

- **Money is `int` paise. Never a float.** A test enforces this statically; if it
  fails, fix the code, not the test.
- **Timestamps are timezone-aware IST.** Billing cycles and payday effects are local
  calendar phenomena; a naive datetime silently corrupts them.
- **The LLM never selects an action.** If a change would let model output influence
  which action fires, it is wrong regardless of how well it works. See ADR-0001.
- **Iterate on `train`; touch `test` once.** `eval` defaults to the development
  split. Scoring held-out data needs an explicit `--split test` and is logged to
  `data/runs/held-out-reads.log`. Never tune against a number that came from there.
- **Never quote a ranking that holds on only one split**, and quote the confidence
  interval rather than the point estimate.
- **Keep the tree runnable at every commit.** No commit should leave `pytest` red.
- **Small, logical commits** with messages that say why, not what.
- **New architectural decisions get an ADR** in `docs/decisions/`. Six exist; follow
  their shape.
- **When something breaks, log it** in `docs/failure-log.md` while it is fresh —
  what broke, how it was found, what fixed it. That file is a deliverable.

## Testing conventions

- Unit tests are fast and deterministic; seed every generator.
- Anything touching a gateway uses the `SimulatedGateway`, never the network.
- Eval runs longer than a second are marked `@pytest.mark.slow`.
- A bug fix arrives with the regression test that would have caught it.

## Project layout

```
src/reclaimos/
  domain/      typed vocabulary shared by everything
  generator/   synthetic subscriptions + the stochastic world model
  eval/        metrics, baselines, harness, report rendering
  obs/         JSONL trace emitter (our source of truth for traces)
docs/decisions/  ADRs — the locked decisions
```
