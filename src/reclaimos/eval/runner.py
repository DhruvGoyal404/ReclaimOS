"""Orchestrates a full evaluation: load a split, run every policy, reduce, persist.

One entry point, used identically by the CLI, the tests and CI. There is no
alternative path by which our own agent could be evaluated more gently than the
baselines were.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from reclaimos.config import settings
from reclaimos.domain import IST, RecordOutcome, SubscriptionRecord
from reclaimos.eval import metrics as metrics_mod
from reclaimos.eval.baselines import all_baselines
from reclaimos.eval.harness import compute_oracles, run_oracle, run_policy
from reclaimos.eval.metrics import ExceptionRow, PolicyMetrics
from reclaimos.eval.policy import Policy
from reclaimos.generator import Manifest, read_manifest, read_records, read_world
from reclaimos.policy.agent import ReclaimAgent
from reclaimos.policy.config import FROZEN_CONFIG_PATH, AgentConfig

#: Development happens here. Everything is free to be run, re-run and iterated on.
DEV_SPLIT = "train"

#: Read once, for the final number. See ``record_holdout_read``.
HELD_OUT_SPLIT = "test"

#: Append-only log of every held-out read, so the seal is auditable rather than
#: asserted. A checksum in EVAL.md proves *which* data was scored; it says nothing
#: about how many times we looked. This does.
HOLDOUT_LOG = "held-out-reads.log"

#: Reported after the baselines, always labelled as reading sealed truth.
ORACLE_NAME = "oracle_ceiling"
ORACLE_DESCRIPTION = "Ceiling estimate — reads sealed truth, not a policy"


class EvalRun(BaseModel):
    """A complete evaluation, serialisable so `report` need not recompute it."""

    model_config = ConfigDict(frozen=True)

    created_at: datetime
    split: str
    manifest: Manifest
    max_attempts: int
    charge_attempt_cost_paise: int
    contact_cost_paise: int
    metrics: list[PolicyMetrics]
    exceptions: dict[str, list[ExceptionRow]]

    #: Content hash of the agent configuration these numbers came from. Quoted
    #: in EVAL.md so a reader can tell frozen results from a local edit.
    agent_config_fingerprint: str = ""
    agent_config_frozen: bool = False

    def by_name(self, name: str) -> PolicyMetrics | None:
        return next((m for m in self.metrics if m.name == name), None)


def _values(records: list[SubscriptionRecord]) -> dict[str, int]:
    return {r.subscription_id: r.plan_amount_paise for r in records}


def evaluate(
    data_dir: Path,
    split: str = DEV_SPLIT,
    policies: list[Policy] | None = None,
    max_attempts: int | None = None,
) -> EvalRun:
    """Run every baseline plus the oracle ceiling against one split."""
    from reclaimos.eval.costs import CHARGE_ATTEMPT_COST, CONTACT_COST

    attempts = max_attempts if max_attempts is not None else settings.max_attempts
    manifest = read_manifest(data_dir)
    records = read_records(data_dir / f"{split}.jsonl")
    world = read_world(data_dir / f"{split}.world.json")
    values = _values(records)

    agent_config = AgentConfig.frozen_or_default()
    chosen: list[Policy] = (
        list(policies)
        if policies is not None
        else [*all_baselines(), ReclaimAgent(config=agent_config)]
    )
    oracles = compute_oracles(records, world, attempts)

    collected: list[PolicyMetrics] = []
    exception_rows: dict[str, list[ExceptionRow]] = {}

    for policy in chosen:
        outcomes = run_policy(policy, records, world, attempts, oracles)
        collected.append(metrics_mod.compute(policy.name, policy.description, outcomes, values))
        exception_rows[policy.name] = metrics_mod.exceptions(outcomes, values)

    oracle_outcomes: list[RecordOutcome] = run_oracle(records, world, attempts, oracles)
    collected.append(metrics_mod.compute(ORACLE_NAME, ORACLE_DESCRIPTION, oracle_outcomes, values))
    exception_rows[ORACLE_NAME] = metrics_mod.exceptions(oracle_outcomes, values)

    return EvalRun(
        created_at=datetime.now(tz=IST),
        split=split,
        manifest=manifest,
        max_attempts=attempts,
        charge_attempt_cost_paise=int(CHARGE_ATTEMPT_COST),
        contact_cost_paise=int(CONTACT_COST),
        metrics=collected,
        exceptions=exception_rows,
        agent_config_fingerprint=agent_config.fingerprint(),
        agent_config_frozen=FROZEN_CONFIG_PATH.exists(),
    )


def record_holdout_read(run_dir: Path, manifest: Manifest, reason: str) -> Path:
    """Append one line to the held-out read log.

    The discipline is: iterate on train, touch test once for the final number.
    Discipline that is not recorded is not discipline, so every held-out read
    appends here -- timestamp, dataset checksum, and a stated reason. The file is
    append-only and committed, which makes an undisclosed sixth look at the test
    split into a visible act rather than a private one.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / HOLDOUT_LOG
    stamp = datetime.now(tz=IST).isoformat(timespec="seconds")
    checksum = manifest.sha256.get("test.jsonl", "unknown")
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{stamp}\tseed={manifest.seed}\tsha256={checksum}\treason={reason}\n")
    return path


def holdout_read_count(run_dir: Path) -> int:
    """How many times the held-out split has been scored, per the log.

    Comment lines are skipped. The header was being counted as two reads, which
    would have inflated the figure EVAL.md prints -- a number that exists
    precisely to be trusted, so an off-by-two there is worse than no number.
    """
    path = run_dir / HOLDOUT_LOG
    if not path.exists():
        return 0
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def write_run(run: EvalRun, run_dir: Path) -> Path:
    """Persist the evaluation, plus a CSV exception list per policy."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"eval-{run.split}.json"
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8", newline="\n")

    for name, rows in run.exceptions.items():
        csv_path = run_dir / f"exceptions-{run.split}-{name}.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "subscription_id",
                    "true_class",
                    "terminal_reason",
                    "amount_paise",
                    "charge_attempts",
                    "contact_actions",
                    "was_recoverable",
                ]
            )
            for row in rows:
                writer.writerow(
                    [
                        row.subscription_id,
                        row.true_class,
                        row.terminal_reason,
                        row.amount_paise,
                        row.charge_attempts,
                        row.contact_actions,
                        row.was_recoverable,
                    ]
                )
    return path


def read_run(run_dir: Path, split: str = DEV_SPLIT) -> EvalRun:
    return EvalRun.model_validate_json((run_dir / f"eval-{split}.json").read_text(encoding="utf-8"))
