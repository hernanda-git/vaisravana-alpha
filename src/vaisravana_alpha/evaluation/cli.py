"""CLI for the multi-layer evaluator.

One command, two subcommands:

    alpha-eval report  [DB] [--run RUN_ID] [--last N]
    alpha-eval promote [DB] [--run RUN_ID] [--dry-run]

`report` runs all six layers on a run and prints them. `promote` runs the
report, then records a decision row and (unless --dry-run) settles the
iteration. Both read only; nothing here can start or stop the engine.

The point of a CLI rather than `import` is that the improvement loop and a
human both need to trigger evaluation the same way, and a subprocess with a
fixed contract is the most stable interface between them.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from vaisravana_alpha.evaluation.layers import evaluate_run, format_report
from vaisravana_alpha.storage import agentic


def _open(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_run(conn: sqlite3.Connection, run_id: str | None,
                 last: int) -> str | None:
    if run_id:
        return run_id
    rows = conn.execute(
        "SELECT run_id FROM runs ORDER BY started_ts DESC LIMIT ?",
        (last,),
    ).fetchall()
    return rows[-1]["run_id"] if rows else None


def cmd_report(args: argparse.Namespace) -> int:
    conn = _open(args.db)
    run_id = _resolve_run(conn, args.run, max(1, args.last))
    if not run_id:
        print("no runs found", file=sys.stderr)
        return 2

    run = agentic.get_run(conn, run_id)
    if not run:
        print(f"run {run_id} not found", file=sys.stderr)
        return 2

    trades = agentic.run_trades(conn, run_id)
    rejections = agentic.rejection_summary(conn, run_id)
    report = evaluate_run(run, trades, rejections, n_trials=args.trials)

    print(format_report(report))

    # Persistence of the layer verdicts: this is the auditable record, so
    # write it even when only printing to stdout.
    for layer in report.layers:
        agentic.record_evaluation(
            conn, run_id, layer.layer, layer.name, layer.verdict,
            layer.score, layer.metrics, layer.reasons,
        )

    print()
    print(f"PROMOTE: {'yes' if report.promote else 'no'}")
    return 0 if report.promote else 1


def cmd_promote(args: argparse.Namespace) -> int:
    conn = _open(args.db)
    run_id = _resolve_run(conn, args.run, 1)
    if not run_id:
        print("no runs found", file=sys.stderr)
        return 2

    run = agentic.get_run(conn, run_id)
    trades = agentic.run_trades(conn, run_id)
    rejections = agentic.rejection_summary(conn, run_id)
    report = evaluate_run(run, trades, rejections, n_trials=args.trials)

    for layer in report.layers:
        agentic.record_evaluation(
            conn, run_id, layer.layer, layer.name, layer.verdict,
            layer.score, layer.metrics, layer.reasons,
        )

    iteration_id = run.get("iteration_id") or ""
    evidence = {
        "run_id": run_id,
        "net_usd": report.layer(3).metrics.get("net_usd") if report.layer(3) else None,
        "expectancy": report.layer(3).metrics.get("expectancy") if report.layer(3) else None,
        "dsr": report.layer(2).metrics.get("dsr") if report.layer(2) else None,
        "max_drawdown": (
            report.layer(5).metrics.get("max_drawdown") if report.layer(5) else None
        ),
    }

    if report.promote:
        action = "promote"
        rationale = "All gating layers passed; safe to promote."
    else:
        action = "reject"
        rationale = report.summary

    if args.dry_run:
        print(f"[dry-run] would {action} {run_id}: {rationale}")
        print(format_report(report))
        return 0 if report.promote else 1

    decision_id = agentic.record_decision(
        conn, action=action, rationale=rationale,
        evidence=evidence, run_id=run_id, iteration_id=iteration_id,
    )
    if iteration_id:
        agentic.settle_iteration(
            conn, iteration_id,
            status="promoted" if report.promote else "rejected",
            verdict=rationale,
        )
    print(f"{action} recorded: {decision_id}")
    return 0 if report.promote else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alpha-eval", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="run all evaluation layers on a run")
    r.add_argument("db", nargs="?", default="/data/alpha-agentic.db")
    r.add_argument("--run", default=None, help="run id (default: most recent)")
    r.add_argument("--last", type=int, default=1, help="which run back (1 = newest)")
    r.add_argument("--trials", type=int, default=1, help="variants tried (for DSR)")
    r.set_defaults(func=cmd_report)

    pr = sub.add_parser("promote", help="evaluate and record a promote/reject decision")
    pr.add_argument("db", nargs="?", default="/data/alpha-agentic.db")
    pr.add_argument("--run", default=None)
    pr.add_argument("--trials", type=int, default=1)
    pr.add_argument("--dry-run", action="store_true")
    pr.set_defaults(func=cmd_promote)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
