"""ReclaimOS command line.

Three commands carry Phase 1: ``gen`` builds a dataset, ``eval`` runs every policy
against the held-out split, ``report`` renders EVAL.md. CI runs all three on a
clean checkout, so "clone and it works" is verified by machine.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from reclaimos import __version__
from reclaimos.config import REPO_ROOT, settings
from reclaimos.eval import report as report_mod
from reclaimos.eval.runner import (
    DEV_SPLIT,
    HELD_OUT_SPLIT,
    ORACLE_NAME,
    evaluate,
    holdout_read_count,
    read_run,
    record_holdout_read,
    write_run,
)
from reclaimos.generator import build_dataset
from reclaimos.money import Paise, format_inr
from reclaimos.store import Database, DecisionLedger, EventStore
from reclaimos.store.idempotency import SqliteIdempotencyStore


def _force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 before anything prints a rupee sign.

    Windows consoles default to a legacy code page (cp1252 here), which cannot
    encode U+20B9 INR SIGN -- so ``reclaimos eval`` died with a UnicodeEncodeError
    on the primary dev machine the first time it printed a money column. Writing
    "INR" instead would have hidden the problem rather than fixed it: EVAL.md and
    every customer-facing message are going to carry the symbol too.

    ``errors="replace"`` is a deliberate second line of defence: a console that
    still cannot render a glyph should show a placeholder, never crash a run.
    See docs/failure-log.md.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()

app = typer.Typer(
    name="reclaimos",
    help="Auditable recovery of revenue lost to failed recurring payments.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def version() -> None:
    """Print the ReclaimOS version."""
    console.print(f"reclaimos {__version__}")


@app.command()
def config() -> None:
    """Show the effective configuration (secrets are never printed)."""
    table = Table(title="ReclaimOS settings", header_style="bold")
    table.add_column("setting")
    table.add_column("value")
    for name, value in settings.model_dump().items():
        table.add_row(name, str(value))
    console.print(table)


@app.command()
def gen(
    n: Annotated[int, typer.Option("--n", help="Number of failed subscriptions.")] = 250,
    seed: Annotated[int, typer.Option("--seed", help="Generator seed.")] = 42,
    test_fraction: Annotated[float, typer.Option("--test-fraction", help="Held-out share.")] = 0.30,
    out: Annotated[Path | None, typer.Option("--out", help="Output directory.")] = None,
) -> None:
    """Generate synthetic subscriptions plus the sealed world model."""
    out_dir = out or settings.data_dir
    manifest = build_dataset(out_dir, n=n, seed=seed, test_fraction=test_fraction)

    console.print(f"[bold green]wrote[/] {manifest.summary()} -> {out_dir}")
    table = Table(title="Realised failure mix", header_style="bold")
    table.add_column("family")
    table.add_column("share", justify="right")
    for family, share in manifest.family_mix.items():
        table.add_row(family, f"{share:.1%}")
    console.print(table)
    console.print(f"test split sha256: [dim]{manifest.sha256['test.jsonl']}[/]")


@app.command(name="eval")
def eval_cmd(
    split: Annotated[
        str,
        typer.Option(
            "--split",
            help="Which split to score. Defaults to train; scoring test is a deliberate act.",
        ),
    ] = DEV_SPLIT,
    data: Annotated[Path | None, typer.Option("--data", help="Dataset directory.")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Run output directory.")] = None,
    policy: Annotated[
        str, typer.Option("--policy", help="Only 'all' is available until Phase 4.")
    ] = "all",
) -> None:
    """Score every policy against a split and persist the run."""
    if policy != "all":
        raise typer.BadParameter(
            "Only --policy all is available: ReclaimOS's own agent lands in Phase 4. "
            "The baselines are deliberately measured first (ADR-0006)."
        )

    data_dir = data or settings.data_dir
    run_dir = out or settings.run_dir
    if not (data_dir / f"{split}.jsonl").exists():
        raise typer.BadParameter(f"no dataset at {data_dir}. Run: reclaimos gen --n 250 --seed 42")

    run = evaluate(data_dir, split=split)
    path = write_run(run, run_dir)

    if split == HELD_OUT_SPLIT:
        # Iterate on train; touch test once, for the final number. Recording the
        # read is what makes that a rule rather than an intention.
        record_holdout_read(run_dir, run.manifest, reason=f"cli eval --split {split}")
        console.print(
            f"[bold yellow]held-out split scored[/] — read #{holdout_read_count(run_dir)}, "
            f"logged to {run_dir / 'held-out-reads.log'}"
        )

    table = Table(
        title=(
            f"ReclaimOS · {split} split · "
            f"{run.metrics[0].n if run.metrics else 0} records · simulated INR"
        ),
        header_style="bold",
    )
    table.add_column("policy")
    table.add_column("recovery", justify="right")
    table.add_column("95% CI", justify="right")
    table.add_column("net", justify="right")
    table.add_column("hard retries", justify="right")
    table.add_column("mandate viol.", justify="right")
    table.add_column("escalated", justify="right")
    for m in run.metrics:
        style = "bold cyan" if m.name == ORACLE_NAME else ""
        table.add_row(
            m.name,
            f"{m.recovery_rate:.1f}%",
            str(m.recovery_rate_ci),
            format_inr(Paise(m.net_recovered_paise)),
            str(m.hard_decline_retries),
            str(m.mandate_violations),
            str(m.escalations),
            style=style,
        )
    console.print(table)
    console.print(
        f"[dim]{ORACLE_NAME} reads sealed truth and is not a policy — it is the ceiling.[/]"
    )
    console.print(f"[bold green]wrote[/] {path}")


@app.command()
def report(
    split: Annotated[str, typer.Option("--split", help="Which run to render.")] = DEV_SPLIT,
    runs: Annotated[Path | None, typer.Option("--runs", help="Run directory.")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Output markdown path.")] = None,
) -> None:
    """Regenerate EVAL.md from the persisted run."""
    run_dir = runs or settings.run_dir
    target = out or (REPO_ROOT / "EVAL.md")
    if not (run_dir / f"eval-{split}.json").exists():
        raise typer.BadParameter(f"no run at {run_dir}. Run: reclaimos eval --policy all")
    written = report_mod.write(read_run(run_dir, split), target, holdout_read_count(run_dir))
    console.print(f"[bold green]wrote[/] {written}")


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

ledger_app = typer.Typer(
    name="ledger",
    help="Inspect the append-only, hash-chained audit trail.",
    no_args_is_help=True,
)
app.add_typer(ledger_app)


def _database(url: str | None) -> Database:
    return Database.from_url(url or settings.database_url)


@ledger_app.command("verify")
def ledger_verify(
    database: Annotated[
        str | None, typer.Option("--database", help="Database URL. Defaults to settings.")
    ] = None,
) -> None:
    """Recompute every hash in the chain and report any break.

    This is the whole audit-trail claim in one command: if a decision row was
    altered, its hash no longer matches its payload and every later link fails.
    Exits non-zero on a break so CI or a cron job can act on it.
    """
    report = DecisionLedger(_database(database)).verify()
    if report.ok:
        console.print(f"[bold green]{report.summary()}[/]")
        return

    console.print(f"[bold red]{report.summary()}[/]")
    table = Table(title="Chain breaks", header_style="bold red")
    table.add_column("entry", justify="right")
    table.add_column("reason")
    table.add_column("expected")
    table.add_column("found")
    for brk in report.breaks:
        table.add_row(str(brk.seq), brk.reason, brk.expected[:16] + "…", brk.found[:16] + "…")
    console.print(table)
    raise typer.Exit(code=1)


@ledger_app.command("export")
def ledger_export(
    out: Annotated[Path, typer.Option("--out", help="Where to write the chain.")] = Path(
        "data/runs/ledger.jsonl"
    ),
    database: Annotated[str | None, typer.Option("--database")] = None,
) -> None:
    """Export the full chain as JSONL for inspection outside this codebase."""
    ledger = DecisionLedger(_database(database))
    written = ledger.export_jsonl(out)
    console.print(f"[bold green]wrote[/] {ledger.count()} entries -> {written}")


@ledger_app.command("stats")
def ledger_stats(
    database: Annotated[str | None, typer.Option("--database")] = None,
) -> None:
    """Counts across the event store, the ledger and the idempotency claims."""
    db = _database(database)
    events = EventStore(db)
    ledger = DecisionLedger(db)
    verified = events.count(verified_only=True)

    table = Table(title=f"ReclaimOS audit trail · {db.path}", header_style="bold")
    table.add_column("store")
    table.add_column("rows", justify="right")
    table.add_row("events (signature verified)", str(verified))
    table.add_row("events (rejected)", str(events.count() - verified))
    table.add_row("ledger entries", str(ledger.count()))
    table.add_row("idempotency claims", str(SqliteIdempotencyStore(db).count()))
    console.print(table)
    console.print(f"chain head: [dim]{ledger.head()}[/]")


@ledger_app.command("demo")
def ledger_demo(
    database: Annotated[str | None, typer.Option("--database")] = None,
) -> None:
    """Seed a small, verifiable audit trail end to end.

    Exists for two reasons. It is the thing to run on camera -- a judge can watch
    a signed webhook arrive, a forged one be refused, a replay lose, and the chain
    verify. And it gives CI something real to run `ledger verify` against, so the
    tamper-evidence claim is exercised by the build rather than only by unit
    tests. A verification nothing invokes is not a guarantee.
    """
    import json as _json

    from reclaimos.domain import ActionType
    from reclaimos.ingest import compute_signature, ingest
    from reclaimos.store import idempotency_key

    secret = "whsec_demo_not_a_real_secret"
    db = _database(database)
    events, ledger = EventStore(db), DecisionLedger(db)
    idem = SqliteIdempotencyStore(db)

    body = _json.dumps(
        {
            "entity": "event",
            "event": "payment.failed",
            "created_at": 1_780_000_000,
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_DEMO01",
                        "amount": 49900,
                        "currency": "INR",
                        "error_code": "BAD_REQUEST_ERROR",
                        "error_reason": "insufficient_funds",
                    }
                },
                "subscription": {"entity": {"id": "sub_DEMO01"}},
            },
        }
    ).encode("utf-8")
    signature = compute_signature(body, secret)

    for _ in range(5):
        delivery = ingest(body, signature, secret, events)
    forged = ingest(body.replace(b"49900", b"9900000"), signature, secret, events)

    key = idempotency_key("sub_DEMO01", 1, ActionType.RETRY_CHARGE)
    won = idem.claim(key)
    replayed = idem.claim(key)

    for step in ("ingested", "diagnosed", "halted"):
        ledger.append(
            {
                "subscription_id": "sub_DEMO01",
                "step": step,
                "amount_paise": 49900,
                "rule_id": f"demo.{step}",
            }
        )

    table = Table(title="Audit trail demo", header_style="bold")
    table.add_column("what happened")
    table.add_column("result")
    table.add_row(
        "5 deliveries of one signed webhook", f"{events.count(verified_only=True)} event row"
    )
    table.add_row("last delivery recognised as a redelivery", str(delivery.duplicate))
    table.add_row("forged delivery accepted", str(forged.accepted))
    table.add_row("forged body stored", "no — digest and reason only")
    table.add_row("idempotency claim / replay", f"{won} / {replayed}")
    table.add_row("ledger entries", str(ledger.count()))
    console.print(table)
    console.print(f"[dim]{db.path}[/] — now run: reclaimos ledger verify")


if __name__ == "__main__":  # pragma: no cover
    app()
