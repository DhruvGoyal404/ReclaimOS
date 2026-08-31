"""Metrics arithmetic, the bootstrap, and the end-to-end CLI path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reclaimos.cli import _force_utf8_stdio, app
from reclaimos.domain import DeclineClass, RecordOutcome, TerminalReason
from reclaimos.eval import metrics as metrics_mod
from reclaimos.eval.metrics import BOOTSTRAP_RESAMPLES, Confusion
from reclaimos.eval.runner import (
    DEV_SPLIT,
    HELD_OUT_SPLIT,
    ORACLE_NAME,
    EvalRun,
    evaluate,
    holdout_read_count,
    read_run,
    record_holdout_read,
    write_run,
)
from reclaimos.generator import build_dataset

runner = CliRunner()


def _outcome(
    sid: str,
    *,
    recovered: bool,
    amount: int = 0,
    cost: int = 0,
    predicted: bool = True,
    truly: bool = True,
    terminal: TerminalReason = TerminalReason.RECOVERED,
) -> RecordOutcome:
    return RecordOutcome(
        subscription_id=sid,
        recovered=recovered,
        amount_recovered_paise=amount if recovered else 0,
        cost_paise=cost,
        terminal_reason=terminal,
        predicted_recoverable=predicted,
        true_recoverable=truly,
        true_class=DeclineClass.SOFT_INSUFFICIENT_FUNDS,
    )


# --- confusion arithmetic ----------------------------------------------------


def test_confusion_matrix_arithmetic() -> None:
    c = Confusion(tp=6, fp=2, tn=5, fn=4)
    assert c.precision == 0.75  # 6 / (6 + 2)
    assert c.recall == 0.6  # 6 / (6 + 4)
    assert abs(c.f1 - 2 * 0.75 * 0.6 / 1.35) < 1e-12


def test_confusion_degenerate_cases_do_not_divide_by_zero() -> None:
    empty = Confusion(tp=0, fp=0, tn=0, fn=0)
    assert (empty.precision, empty.recall, empty.f1) == (0.0, 0.0, 0.0)


# --- reduction ---------------------------------------------------------------


def test_metrics_reduce_outcomes_correctly() -> None:
    outcomes = [
        _outcome("a", recovered=True, amount=50_000, cost=200),
        _outcome("b", recovered=False, cost=400, terminal=TerminalReason.POLICY_STOPPED),
        _outcome(
            "c",
            recovered=False,
            cost=200,
            predicted=False,
            truly=False,
            terminal=TerminalReason.POLICY_STOPPED,
        ),
    ]
    values = {"a": 50_000, "b": 30_000, "c": 20_000}
    m = metrics_mod.compute("t", "test", outcomes, values)

    assert m.n == 3
    assert m.recovered == 1
    assert abs(m.recovery_rate - 100 / 3) < 1e-9
    assert m.gross_recovered_paise == 50_000
    assert m.cost_paise == 800
    assert m.net_recovered_paise == 49_200
    # The achievable denominator must include the record we missed.
    assert m.recoverable_money_paise == 80_000
    # Cost burned on a record no policy could have recovered.
    assert m.wasted_cost_paise == 200
    # 'a' and 'b' were both predicted recoverable and both genuinely were, so
    # both are true positives -- the prediction is about reachability, not about
    # whether this particular policy managed it. 'c' is a true negative.
    assert m.confusion == Confusion(tp=2, fp=0, tn=1, fn=0)


def test_by_class_counts_recoveries_and_totals() -> None:
    outcomes = [
        _outcome("a", recovered=True, amount=100),
        _outcome("b", recovered=False, terminal=TerminalReason.POLICY_STOPPED),
    ]
    m = metrics_mod.compute("t", "test", outcomes, {"a": 100, "b": 100})
    assert m.by_class[DeclineClass.SOFT_INSUFFICIENT_FUNDS.value] == (1, 2)


# --- the bootstrap -----------------------------------------------------------


def test_bootstrap_is_deterministic_and_brackets_the_point_estimate() -> None:
    outcomes = [_outcome(f"s{i}", recovered=i % 3 == 0, amount=10_000, cost=100) for i in range(60)]
    values = {o.subscription_id: 10_000 for o in outcomes}
    first = metrics_mod.compute("t", "test", outcomes, values)
    second = metrics_mod.compute("t", "test", outcomes, values)

    assert first.recovery_rate_ci == second.recovery_rate_ci
    assert first.recovery_rate_ci.low <= first.recovery_rate <= first.recovery_rate_ci.high
    assert first.net_recovered_ci_paise.low <= first.net_recovered_paise
    assert first.net_recovered_paise <= first.net_recovered_ci_paise.high


def test_bootstrap_of_a_constant_outcome_has_zero_width() -> None:
    outcomes = [
        _outcome(f"s{i}", recovered=False, terminal=TerminalReason.NO_ACTION_TAKEN)
        for i in range(20)
    ]
    m = metrics_mod.compute("t", "test", outcomes, {o.subscription_id: 100 for o in outcomes})
    assert m.recovery_rate_ci.low == m.recovery_rate_ci.high == 0.0
    assert BOOTSTRAP_RESAMPLES >= 1_000


# --- the exception list ------------------------------------------------------


def test_exception_list_puts_real_misses_first_then_largest_amount() -> None:
    outcomes = [
        _outcome("cheap_miss", recovered=False, truly=True, terminal=TerminalReason.POLICY_STOPPED),
        _outcome(
            "impossible", recovered=False, truly=False, terminal=TerminalReason.POLICY_STOPPED
        ),
        _outcome("big_miss", recovered=False, truly=True, terminal=TerminalReason.POLICY_STOPPED),
        _outcome("won", recovered=True, amount=100),
    ]
    values = {"cheap_miss": 10_000, "impossible": 90_000, "big_miss": 50_000, "won": 100}
    rows = metrics_mod.exceptions(outcomes, values)

    assert [r.subscription_id for r in rows] == ["big_miss", "cheap_miss", "impossible"]
    assert rows[0].was_recoverable and not rows[-1].was_recoverable


# --- end to end --------------------------------------------------------------


@pytest.fixture(scope="module")
def full_run(tmp_path_factory: pytest.TempPathFactory) -> EvalRun:
    """One 200-record evaluation on the *development* split.

    The suite is development, so it runs on train. Every assertion below that
    encodes a threshold -- the headroom check in particular -- would otherwise be
    knowledge of the held-out split smuggled into the repository.
    """
    out = tmp_path_factory.mktemp("full")
    build_dataset(out, n=200, seed=42)
    return evaluate(out, split=DEV_SPLIT)


def test_full_pipeline_runs_and_ranks_policies_sensibly(full_run: EvalRun) -> None:
    run = full_run

    names = [m.name for m in run.metrics]
    assert names[-1] == ORACLE_NAME
    assert "do_nothing" in names

    floor = run.by_name("do_nothing")
    ceiling = run.by_name(ORACLE_NAME)
    assert floor is not None and ceiling is not None
    assert floor.recovery_rate == 0.0
    assert floor.net_recovered_paise == 0

    # The ceiling must dominate every real policy, or it is not a ceiling.
    for m in run.metrics:
        if m.name != ORACLE_NAME:
            assert m.recovery_rate <= ceiling.recovery_rate

    # The BASELINES must leave real headroom, or the problem is not interesting.
    # This assertion predates the agent; it is scoped to the baselines on purpose,
    # because the agent closing the gap is the goal rather than a regression.
    best_baseline = max(
        (m for m in run.metrics if m.name not in (ORACLE_NAME, "reclaimos_agent")),
        key=lambda m: m.recovery_rate,
    )
    assert ceiling.recovery_rate - best_baseline.recovery_rate > 10.0

    # And the agent must close most of that gap without exceeding the ceiling.
    agent = run.by_name("reclaimos_agent")
    assert agent is not None
    assert agent.recovery_rate > best_baseline.recovery_rate
    assert agent.recovery_rate <= ceiling.recovery_rate, "a policy beat the ceiling"


def test_the_agent_is_safer_than_every_baseline_that_moves_money(full_run: EvalRun) -> None:
    """The claim the whole project is aimed at, checked on the development split."""
    agent = full_run.by_name("reclaimos_agent")
    ladder = full_run.by_name("retry_3x_fixed")
    assert agent is not None and ladder is not None

    assert agent.mandate_violations == 0
    assert agent.hard_decline_retries < ladder.hard_decline_retries / 3
    assert agent.recovery_rate > ladder.recovery_rate
    assert agent.self_halt_rate == 100.0, "the harness had to stop the agent"


def test_escalated_money_is_reported_and_not_counted_as_recovered(
    full_run: EvalRun,
) -> None:
    agent = full_run.by_name("reclaimos_agent")
    assert agent is not None
    if agent.escalations:
        assert agent.gated_paise > 0
        assert agent.by_terminal.get("escalated_to_human") == agent.escalations


def test_blind_retry_ladders_burn_attempts_on_hard_declines(full_run: EvalRun) -> None:
    """The cost the diagnosis step exists to avoid, measured before it is built."""
    run = full_run

    ladder = run.by_name("retry_3x_fixed")
    outreach = run.by_name("contact_once")
    assert ladder is not None and outreach is not None
    assert ladder.hard_decline_retries > 0
    assert outreach.hard_decline_retries == 0
    assert ladder.mandate_violations > 0  # expired mandates it retried blindly
    assert outreach.mandate_violations == 0


def test_run_round_trips_through_disk(tmp_path: Path) -> None:
    build_dataset(tmp_path, n=80, seed=42)
    run = evaluate(tmp_path, split=DEV_SPLIT)
    write_run(run, tmp_path / "runs")
    reloaded = read_run(tmp_path / "runs", DEV_SPLIT)

    assert reloaded.manifest.sha256 == run.manifest.sha256
    assert [m.name for m in reloaded.metrics] == [m.name for m in run.metrics]
    assert (tmp_path / "runs" / f"exceptions-{DEV_SPLIT}-do_nothing.csv").exists()


# --- the CLI -----------------------------------------------------------------


def test_cli_gen_eval_report_on_a_clean_directory(tmp_path: Path) -> None:
    data, runs = tmp_path / "data", tmp_path / "runs"
    out = tmp_path / "EVAL.md"

    assert (
        runner.invoke(app, ["gen", "--n", "80", "--seed", "42", "--out", str(data)]).exit_code == 0
    )
    assert (
        runner.invoke(
            app, ["eval", "--policy", "all", "--data", str(data), "--out", str(runs)]
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["report", "--runs", str(runs), "--out", str(out)]).exit_code == 0

    text = out.read_text(encoding="utf-8")
    assert "₹" in text, "the rupee sign must survive the whole pipeline"
    assert "simulated INR under a declared recovery model" in text
    assert "The agent column is deliberately empty" in text
    assert "Test split SHA-256" in text


def test_eval_defaults_to_the_development_split(tmp_path: Path) -> None:
    """Held-out data must never be scored by accident."""
    data, runs = tmp_path / "data", tmp_path / "runs"
    runner.invoke(app, ["gen", "--n", "60", "--seed", "42", "--out", str(data)])
    runner.invoke(app, ["eval", "--data", str(data), "--out", str(runs)])

    assert (runs / f"eval-{DEV_SPLIT}.json").exists()
    assert not (runs / f"eval-{HELD_OUT_SPLIT}.json").exists()
    assert holdout_read_count(runs) == 0


def test_scoring_the_held_out_split_is_recorded(tmp_path: Path) -> None:
    """A checksum proves which data was scored. It says nothing about how often we
    looked -- so every held-out read appends to an audit log."""
    data, runs = tmp_path / "data", tmp_path / "runs"
    runner.invoke(app, ["gen", "--n", "60", "--seed", "42", "--out", str(data)])

    assert holdout_read_count(runs) == 0
    result = runner.invoke(
        app,
        ["eval", "--split", HELD_OUT_SPLIT, "--data", str(data), "--out", str(runs)],
    )
    assert result.exit_code == 0
    assert holdout_read_count(runs) == 1

    result = runner.invoke(
        app,
        ["eval", "--split", HELD_OUT_SPLIT, "--data", str(data), "--out", str(runs)],
    )
    assert result.exit_code == 0
    assert holdout_read_count(runs) == 2, "a second look must be visible, not silent"

    log = (runs / "held-out-reads.log").read_text(encoding="utf-8")
    manifest_line = log.splitlines()[0]
    assert "sha256=" in manifest_line and "reason=" in manifest_line


def test_the_read_log_is_append_only_in_practice(tmp_path: Path) -> None:
    from reclaimos.generator import read_manifest

    build_dataset(tmp_path, n=40, seed=42)
    manifest = read_manifest(tmp_path)
    runs = tmp_path / "runs"
    for i in range(3):
        record_holdout_read(runs, manifest, reason=f"test-{i}")
    assert holdout_read_count(runs) == 3


def test_cli_refuses_to_evaluate_without_a_dataset(tmp_path: Path) -> None:
    result = runner.invoke(app, ["eval", "--data", str(tmp_path / "nothing")])
    assert result.exit_code != 0


def test_cli_rejects_a_policy_it_does_not_have_yet(tmp_path: Path) -> None:
    result = runner.invoke(app, ["eval", "--policy", "reclaimos_agent"])
    assert result.exit_code != 0


def test_stdio_is_utf8_so_the_rupee_sign_cannot_crash_a_run() -> None:
    """Regression: Windows consoles default to cp1252, which cannot encode U+20B9.
    See docs/failure-log.md."""
    _force_utf8_stdio()
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        assert encoding in {"utf8", ""}, encoding


def test_the_read_log_does_not_count_its_own_header(tmp_path: Path) -> None:
    """Regression: the two comment lines at the top of the log were counted as
    reads, inflating the figure EVAL.md prints. A number that exists to be
    trusted is worse wrong than absent."""
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "held-out-reads.log").write_text(
        "# a comment\n#\tanother\n2026-08-31T01:00:00+05:30\tseed=42\tsha256=x\treason=y\n",
        encoding="utf-8",
    )
    assert holdout_read_count(runs) == 1
