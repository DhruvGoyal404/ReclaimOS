"""Renders EVAL.md from a persisted evaluation run.

EVAL.md is generated, never hand-edited. That is not tidiness: a metrics page a
human can touch is a metrics page a human can quietly improve.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from reclaimos.eval.metrics import PolicyMetrics
from reclaimos.eval.runner import DEV_SPLIT, HELD_OUT_SPLIT, ORACLE_NAME, EvalRun
from reclaimos.money import Paise, format_inr

EXCEPTION_PREVIEW_ROWS = 15


def _money(paise: int | float) -> str:
    return format_inr(Paise(round(paise)))


def _hours(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}h"


def _label(m: PolicyMetrics) -> str:
    return f"**{m.name}**" if m.name == ORACLE_NAME else m.name


def render(run: EvalRun, holdout_reads: int = 0) -> str:
    """Render the whole of EVAL.md."""
    lines: list[str] = []
    add = lines.append

    add("# EVAL.md — measured results")
    add("")
    add("> **This file is generated.** Do not edit by hand — run")
    add("> `uv run reclaimos report` and commit the result.")
    add("")
    add(
        f"_Generated {run.created_at:%Y-%m-%d %H:%M %Z} · split `{run.split}` · "
        f"{run.manifest.summary()}_"
    )
    add("")

    # --- the disclaimer, first, in bold -----------------------------------
    add("## What these numbers are")
    add("")
    add(
        "**Every rupee figure below is simulated INR under a declared recovery model, "
        "not money moved through Razorpay.** The batch runs against a stochastic world "
        "simulator because test mode cannot model whether a retry three days later "
        "would have succeeded — measuring policy quality there would produce a number "
        "that looks live and means nothing. A separate small live test-mode slice "
        "proves the integration is real. [SIMULATION.md](SIMULATION.md) draws the line "
        "precisely."
    )
    add("")
    add(
        "The comparison that carries the weight is **relative**: each policy against the "
        "others and against a truth-reading ceiling, all facing **identical random "
        "draws**. Confidence intervals are percentile bootstrap intervals over the "
        "record population; they quantify sampling variation across subscriptions and "
        "say nothing about whether the world model's assumptions are right."
    )
    add("")

    # --- the held-out seal, stated before any number -----------------------
    add("## Which split this is, and how often we have looked")
    add("")
    if run.split == DEV_SPLIT:
        add(
            f"These numbers come from the **{DEV_SPLIT}** split. Development happens here, "
            f"and it is free to be re-run as often as we like. The **{HELD_OUT_SPLIT}** split "
            "is held back for the final measurement once ReclaimOS's own agent exists."
        )
    else:
        add(
            f"These numbers come from the **held-out {HELD_OUT_SPLIT} split**. This is the "
            "measurement that counts."
        )
    add("")
    add(
        f"Held-out reads to date, per `data/runs/held-out-reads.log`: **{holdout_reads}**. "
        "`reclaimos eval` defaults to the development split; scoring the held-out split "
        "requires an explicit `--split test` and appends a timestamped, checksummed line "
        "to that log. A checksum proves *which* data was scored — it says nothing about "
        "how many times we looked, which is what actually corrupts a held-out set."
    )
    add("")
    add(
        "**Disclosure.** During Phase 1 this discipline was not yet enforced, and the "
        "held-out split was read repeatedly while the harness was being built — the eval "
        "command defaulted to it. No parameter was fitted to a held-out result (the "
        "baselines have no free parameters, and the world constants were authored before "
        "the first run and never revised), but one test did encode a threshold chosen "
        "after seeing the held-out gap. That assertion has been moved to the development "
        "split and the defaults inverted. The lapse is recorded in "
        "[docs/failure-log.md](docs/failure-log.md) rather than quietly corrected."
    )
    add("")

    # --- provenance --------------------------------------------------------
    add("## Dataset")
    add("")
    add(f"- Generator `{run.manifest.generator_version}`, seed `{run.manifest.seed}`")
    add(
        f"- {run.manifest.n_total} records — {run.manifest.n_train} train / "
        f"{run.manifest.n_test} test (held out, never seen during tuning)"
    )
    add(f"- Test split SHA-256: `{run.manifest.sha256.get('test.jsonl', 'n/a')}`")
    add(f"- Attempt cap: {run.max_attempts}")
    frozen = "frozen" if run.agent_config_frozen else "a-priori defaults, not yet tuned"
    add(f"- Agent config: `{run.agent_config_fingerprint[:16]}…` ({frozen})")
    add(
        f"- Declared costs: {_money(run.charge_attempt_cost_paise)} per charge attempt, "
        f"{_money(run.contact_cost_paise)} per customer contact"
    )
    add("")
    add("Realised failure mix:")
    add("")
    add("| family | share | class | share |")
    add("| --- | ---: | --- | ---: |")
    families = list(run.manifest.family_mix.items())
    classes = list(run.manifest.class_mix.items())
    for i in range(max(len(families), len(classes))):
        fam = f"{families[i][0]} | {families[i][1]:.1%}" if i < len(families) else " | "
        cls = f"`{classes[i][0]}` | {classes[i][1]:.1%}" if i < len(classes) else " | "
        add(f"| {fam} | {cls} |")
    add("")

    # --- headline ----------------------------------------------------------
    add("## Money recovered")
    add("")
    add("| policy | recovery rate | 95% CI | gross | cost | net | 95% CI (net) | % of achievable |")
    add("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for m in run.metrics:
        add(
            f"| {_label(m)} | {m.recovery_rate:.1f}% | {m.recovery_rate_ci} | "
            f"{_money(m.gross_recovered_paise)} | {_money(m.cost_paise)} | "
            f"{_money(m.net_recovered_paise)} | "
            f"{_money(m.net_recovered_ci_paise.low)}–{_money(m.net_recovered_ci_paise.high)} | "
            f"{m.money_capture_rate:.1f}% |"
        )
    add("")
    add(
        f"`{ORACLE_NAME}` is **not a policy**. It reads the sealed latent state "
        "(when a short balance recovers, how long an issuer outage lasts, how willing "
        "a customer is to pay) that no record exposes. It is the ceiling a perfect "
        "policy could reach on this data, and it exists so that "
        '"% of achievable" has an honest denominator.'
    )
    add("")

    _separation(run, add)

    _baseline_delta(run, add)

    # --- efficiency --------------------------------------------------------
    add("## Efficiency")
    add("")
    add("| policy | charge attempts | contacts | actions per recovery | median time | p90 time |")
    add("| --- | ---: | ---: | ---: | ---: | ---: |")
    for m in run.metrics:
        add(
            f"| {_label(m)} | {m.charge_attempts} | {m.contact_actions} | "
            f"{m.attempts_per_recovery:.2f} | {_hours(m.median_hours_to_recovery)} | "
            f"{_hours(m.p90_hours_to_recovery)} |"
        )
    add("")

    # --- safety ------------------------------------------------------------
    add("## Safety invariants")
    add("")
    add(
        "Retries against hard declines and mandate violations are **false actions**: "
        "money and customer patience spent where no policy could have recovered "
        "anything. The target for mandate violations is zero, always."
    )
    add("")
    add(
        "| policy | hard-decline retries | as % of attempts | mandate violations | "
        "escalated | gated | self-halted | wasted cost |"
    )
    add("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for m in run.metrics:
        add(
            f"| {_label(m)} | {m.hard_decline_retries} | {m.hard_decline_retry_rate:.1f}% | "
            f"{m.mandate_violations} | {m.escalations} | {_money(m.gated_paise)} | "
            f"{m.self_halt_rate:.1f}% | {_money(m.wasted_cost_paise)} |"
        )
    add("")
    add("### How escalations are resolved")
    add("")
    add(
        "**They are not.** No simulated reviewer approves anything. An escalated "
        "subscription terminates as `escalated_to_human`, executes no action, and "
        "counts as **not recovered**; the money involved appears in the *gated* "
        "column and nowhere else."
    )
    add("")
    add(
        "That is the unflattering choice, on purpose. If escalations were "
        "auto-approved, a policy could buy a perfect safety record by escalating "
        "everything and still collect the recovery. Counting them as losses means "
        "over-escalating costs the headline number, so the safety metrics cannot "
        "be gamed from that direction. The cost is that our reported recovery rate "
        "is a floor: a real deployment with a human reviewer would recover some of "
        "the gated amount."
    )
    add("")

    # --- classification ----------------------------------------------------
    add("## Predicting `recoverable`")
    add("")
    add(
        "Ground truth is the world's sampled outcome under the ceiling policy — not a "
        "rule, and not anything the classifier can read. Some gateway `(code, reason)` "
        "tuples are emitted by more than one true class, so a perfect score is "
        "impossible by construction; that error floor is deliberate "
        "([ADR-0006](docs/decisions/ADR-0006-eval-first-stochastic-world.md))."
    )
    add("")
    add("| policy | precision | recall | F1 | TP | FP | TN | FN |")
    add("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for m in run.metrics:
        c = m.confusion
        add(
            f"| {_label(m)} | {c.precision:.3f} | {c.recall:.3f} | {c.f1:.3f} | "
            f"{c.tp} | {c.fp} | {c.tn} | {c.fn} |"
        )
    add("")

    # --- per class ---------------------------------------------------------
    add("## Recovery rate by true decline class")
    add("")
    header = "| decline class | n | " + " | ".join(_label(m) for m in run.metrics) + " |"
    add(header)
    add("| --- | ---: |" + " ---: |" * len(run.metrics))
    all_classes = sorted({k for m in run.metrics for k in m.by_class})
    for cls in all_classes:
        n = next((m.by_class[cls][1] for m in run.metrics if cls in m.by_class), 0)
        cells = []
        for m in run.metrics:
            got, total = m.by_class.get(cls, (0, 0))
            cells.append(f"{100.0 * got / total:.0f}%" if total else "—")
        add(f"| `{cls}` | {n} | " + " | ".join(cells) + " |")
    add("")

    # --- exceptions --------------------------------------------------------
    _exceptions(run, add)

    # --- the claim that replicates -----------------------------------------
    _waste(run, add)

    # --- agent status ------------------------------------------------------
    add("## The agent column is deliberately empty")
    add("")
    add(
        "ReclaimOS's own policy does not exist yet. The harness, the baselines and every "
        "metric above were built first, on purpose "
        "([ADR-0006](docs/decisions/ADR-0006-eval-first-stochastic-world.md)): a "
        "scoreboard built after the player is a scoreboard shaped around the player. "
        "When the agent lands in Phase 4 it implements the same `Policy` interface and "
        "is scored by this same code path — there is no gentler route available to it."
    )
    add("")

    add("## Assumptions a reader may want to disagree with")
    add("")
    add(
        "- Base recovery probabilities per decline class, and the timing, decay, "
        "method and tenure multipliers — all in "
        "`src/reclaimos/generator/outcome_model.py`, each cited at its use site."
    )
    add(
        f"- Cost per charge attempt ({_money(run.charge_attempt_cost_paise)}) and per "
        f"contact ({_money(run.contact_cost_paise)}) — `src/reclaimos/eval/costs.py`. "
        "Real acquirer economics are not public."
    )
    add(
        "- The failure mix, conditioned on payment rail — "
        "`src/reclaimos/generator/profiles.py`, anchored on published vendor ranges "
        "for involuntary churn, used as ranges."
    )
    add(
        "- The ceiling is greedy, not optimal: it maximises probability within each "
        "slot, which is exact for a single slot and mildly suboptimal across slots. It "
        "is therefore a ceiling *estimate*, and a very slightly conservative one."
    )
    add("")
    return "\n".join(lines) + "\n"


def _separation(run: EvalRun, emit: Callable[[str], None]) -> None:
    """State what the intervals actually separate, and what they do not.

    Computed rather than written, for the same reason the waste section is: the
    one headline this project got wrong survived because it lived in a sentence
    nobody recomputed (docs/failure-log.md). A claim assembled from the numbers
    cannot drift away from them.
    """
    agent = run.by_name("reclaimos_agent")
    ceiling = run.by_name(ORACLE_NAME)
    baselines = [
        m for m in run.metrics if m.name not in (ORACLE_NAME, "reclaimos_agent", "do_nothing")
    ]
    if agent is None or ceiling is None or not baselines:
        return

    best = max(baselines, key=lambda m: m.recovery_rate)
    separated = agent.recovery_rate_ci.low > best.recovery_rate_ci.high
    overlaps_ceiling = agent.recovery_rate_ci.high > ceiling.recovery_rate_ci.low

    emit("### What these intervals separate, and what they do not")
    emit("")
    if separated:
        emit(
            f"**The agent is cleanly ahead of the best baseline.** Its 95% interval "
            f"{agent.recovery_rate_ci} does not overlap `{best.name}`'s "
            f"{best.recovery_rate_ci}. On {agent.n} records that separation is real, "
            "not a sampling artifact."
        )
    else:
        emit(
            f"**The agent is ahead on the point estimate but the intervals overlap** "
            f"({agent.recovery_rate_ci} against `{best.name}`'s {best.recovery_rate_ci}). "
            f"On {agent.n} records that is not yet a separation, and it should not be "
            "quoted as one."
        )
    emit("")
    if overlaps_ceiling:
        emit(
            f"**The agent cannot be distinguished from the ceiling here, and that is "
            f"not the same as matching it.** Its interval {agent.recovery_rate_ci} "
            f"overlaps the ceiling's {ceiling.recovery_rate_ci}, so at this sample size "
            f"the remaining gap ({ceiling.recovery_rate - agent.recovery_rate:.1f} points) "
            "is inside the noise. The honest claim is that we cannot measure the "
            "shortfall from here — not that there is none."
        )
    else:
        emit(
            f"**A measurable gap to the ceiling remains**: {agent.recovery_rate_ci} "
            f"against {ceiling.recovery_rate_ci}."
        )
    emit("")


def _waste(run: EvalRun, emit: Callable[[str], None]) -> None:
    """The finding that holds on both splits, computed from this run.

    Written as generated output rather than prose on purpose: an earlier headline
    -- that outreach netted more money than the retry ladder -- was a small-sample
    artifact that reversed on the other split, and it survived as long as it did
    because it lived in prose nobody recomputed. This paragraph cannot drift from
    the numbers because it is made of them.
    """
    ladder = run.by_name("retry_3x_fixed")
    outreach = run.by_name("contact_once")
    if ladder is None or outreach is None:
        return

    emit("## What a blind retry ladder actually buys")
    emit("")
    emit(
        f"On this split `retry_3x_fixed` recovers {ladder.recovery_rate:.1f}% "
        f"{ladder.recovery_rate_ci} against `contact_once` at "
        f"{outreach.recovery_rate:.1f}% {outreach.recovery_rate_ci}. But it pays for "
        "that recovery with actions that could never have worked:"
    )
    emit("")
    emit("| | retry_3x_fixed | contact_once |")
    emit("| --- | ---: | ---: |")
    emit(
        f"| Retries against hard declines | {ladder.hard_decline_retries} "
        f"| {outreach.hard_decline_retries} |"
    )
    emit(
        f"| Debits attempted against expired consent | {ladder.mandate_violations} "
        f"| {outreach.mandate_violations} |"
    )
    emit(
        f"| Cost burned on unreachable records | {_money(ladder.wasted_cost_paise)} "
        f"| {_money(outreach.wasted_cost_paise)} |"
    )
    emit("")
    emit(
        "A hard decline — a card reported stolen, an issuer refusing outright, a "
        "revoked mandate -- will not authorise on the second attempt or the tenth. "
        "A mandate violation is worse than wasted: it is a debit attempted without "
        "live consent, which our own executor is built to refuse "
        "([ADR-0003](docs/decisions/ADR-0003-explainable-bounded-gated.md))."
    )
    emit("")
    emit(
        "**This is the target ReclaimOS is aimed at: the ladder's recovery rate "
        "without the ladder's waste.** Unlike a money-ranking between these two "
        "baselines, it holds on both splits — which is the only reason it is stated "
        "here at all (see [docs/failure-log.md](docs/failure-log.md))."
    )
    emit("")


def _baseline_delta(run: EvalRun, emit: Callable[[str], None]) -> None:
    """Money recovered above the do-nothing floor, per policy."""
    floor = run.by_name("do_nothing")
    if floor is None:
        return
    emit("### Against doing nothing")
    emit("")
    emit("| policy | net above floor | per record |")
    emit("| --- | ---: | ---: |")
    for m in run.metrics:
        if m.name == floor.name:
            continue
        delta = m.net_recovered_paise - floor.net_recovered_paise
        per_record = delta / m.n if m.n else 0
        emit(f"| {_label(m)} | {_money(delta)} | {_money(per_record)} |")
    emit("")


def _exceptions(run: EvalRun, emit: Callable[[str], None]) -> None:
    """The exception list for the best-performing baseline."""
    candidates = [m for m in run.metrics if m.name not in (ORACLE_NAME, "do_nothing")]
    if not candidates:
        return
    # Ranked by recovery rate, not net money: net ordering at small n is
    # sampling noise (see docs/failure-log.md), recovery-rate ordering is not.
    best = max(candidates, key=lambda m: m.recovery_rate)
    rows = run.exceptions.get(best.name, [])
    missed = [r for r in rows if r.was_recoverable]

    emit("## Exception list")
    emit("")
    emit(
        f"Everything `{best.name}` (the baseline with the highest recovery rate) left "
        f"unrecovered: **{len(rows)} records**, of which **{len(missed)} were reachable "
        "by the ceiling** and are therefore genuine misses rather than impossible cases. "
        "Misses are listed first, largest amount first."
    )
    emit("")
    emit("| subscription | true class | ended as | amount | charges | contacts | was reachable |")
    emit("| --- | --- | --- | ---: | ---: | ---: | :---: |")
    for row in rows[:EXCEPTION_PREVIEW_ROWS]:
        emit(
            f"| `{row.subscription_id}` | `{row.true_class}` | `{row.terminal_reason}` | "
            f"{_money(row.amount_paise)} | {row.charge_attempts} | {row.contact_actions} | "
            f"{'yes' if row.was_recoverable else 'no'} |"
        )
    emit("")
    if len(rows) > EXCEPTION_PREVIEW_ROWS:
        emit(
            f"_{len(rows) - EXCEPTION_PREVIEW_ROWS} further rows omitted. The complete "
            f"list for every policy is written to "
            f"`data/runs/exceptions-{run.split}-<policy>.csv`._"
        )
        emit("")


def write(run: EvalRun, path: Path, holdout_reads: int = 0) -> Path:
    path.write_text(render(run, holdout_reads), encoding="utf-8", newline="\n")
    return path
