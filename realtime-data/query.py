#!/usr/bin/env python3
"""
Convenience query interface for the real-time Binance futures data pipeline.

All queries are read-only and designed for agentic consumption — they return
plain dicts/lists ready for JSON serialisation.

Usage:
    python query.py [--db PATH] <command> [args...]

Commands:
    strongest [n=10] [--ts LATEST]
        Top N pairs by strength score at the most recent snapshot.

    weakest [n=10] [--ts LATEST]
        Bottom N pairs by strength score.

    pair <SYMBOL> [--ts LATEST] [--limit 20]
        Recent metrics for a single pair (e.g. BTCUSDT).

    snapshot [--ts LATEST]
        Most recent universe snapshot summary.

    btc
        Most recent BTC benchmark.

    recent [--limit 5]
        List recent snapshot timestamps with pair counts.

    top-momentum [n=10]
        Top pairs by 24h price change (momentum).

    top-volume [n=10]
        Top pairs by 24h quote volume.

    cvd [n=10]
        Top/bottom pairs by CVD trend (cumulative volume delta EMA).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "futures_data.db"


def get_db(path: str | None = None) -> sqlite3.Connection:
    db_path = path or os.environ.get("RT_DB_PATH", str(DEFAULT_DB))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_ts(conn: sqlite3.Connection, ts: str | None) -> str:
    """Resolve 'LATEST' or a specific timestamp."""
    if ts is None or ts.upper() == "LATEST":
        row = conn.execute(
            "SELECT ts FROM pair_metrics ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if not row:
            raise SystemExit("No data in database.")
        return row["ts"]
    return ts


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

def strongest(conn: sqlite3.Connection, n: int = 10, ts: str | None = None):
    ts = _resolve_ts(conn, ts)
    rows = conn.execute(
        """SELECT pair, strength_score, price, price_change_24h, quote_volume,
                  bid_qty, ask_qty, volume_delta, cvd_trend, btc_relative, mark_price
           FROM pair_metrics
           WHERE ts = ?
           ORDER BY strength_score DESC
           LIMIT ?""",
        (ts, n),
    ).fetchall()
    return [dict(r) for r in rows]


def weakest(conn: sqlite3.Connection, n: int = 10, ts: str | None = None):
    ts = _resolve_ts(conn, ts)
    rows = conn.execute(
        """SELECT pair, strength_score, price, price_change_24h, quote_volume,
                  bid_qty, ask_qty, volume_delta, cvd_trend, btc_relative, mark_price
           FROM pair_metrics
           WHERE ts = ?
           ORDER BY strength_score ASC
           LIMIT ?""",
        (ts, n),
    ).fetchall()
    return [dict(r) for r in rows]


def pair_history(conn: sqlite3.Connection, symbol: str, ts: str | None = None, limit: int = 20):
    if ts is None or ts.upper() == "LATEST":
        rows = conn.execute(
            """SELECT * FROM pair_metrics
               WHERE pair = ?
               ORDER BY ts DESC
               LIMIT ?""",
            (symbol, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM pair_metrics
               WHERE pair = ? AND ts <= ?
               ORDER BY ts DESC
               LIMIT ?""",
            (symbol, ts, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def snapshot(conn: sqlite3.Connection, ts: str | None = None):
    ts = _resolve_ts(conn, ts)
    row = conn.execute(
        "SELECT * FROM universe_snapshot WHERE ts = ? ORDER BY id DESC LIMIT 1",
        (ts,),
    ).fetchone()
    if not row:
        return {"error": "No snapshot found"}
    d = dict(row)
    d["strongest_pairs"] = json.loads(d["strongest_pairs_json"] or "[]")
    d["weakest_pairs"] = json.loads(d["weakest_pairs_json"] or "[]")
    del d["strongest_pairs_json"], d["weakest_pairs_json"]
    return d


def btc_benchmark(conn: sqlite3.Connection, ts: str | None = None):
    if ts is None or ts.upper() == "LATEST":
        row = conn.execute(
            "SELECT * FROM btc_benchmark ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM btc_benchmark WHERE ts = ? ORDER BY id DESC LIMIT 1",
            (ts,),
        ).fetchone()
    return dict(row) if row else {"error": "No BTC data"}


def recent_snapshots(conn: sqlite3.Connection, limit: int = 5):
    rows = conn.execute(
        """SELECT ts, pair_count,
                   (SELECT price FROM btc_benchmark WHERE ts = u.ts ORDER BY id DESC LIMIT 1) as btc_price
            FROM universe_snapshot u
            ORDER BY ts DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def top_momentum(conn: sqlite3.Connection, n: int = 10, ts: str | None = None):
    ts = _resolve_ts(conn, ts)
    rows = conn.execute(
        """SELECT pair, price_change_24h, price, quote_volume, strength_score
           FROM pair_metrics
           WHERE ts = ?
           ORDER BY price_change_24h DESC
           LIMIT ?""",
        (ts, n),
    ).fetchall()
    return [dict(r) for r in rows]


def top_volume(conn: sqlite3.Connection, n: int = 10, ts: str | None = None):
    ts = _resolve_ts(conn, ts)
    rows = conn.execute(
        """SELECT pair, quote_volume, price, price_change_24h, strength_score
           FROM pair_metrics
           WHERE ts = ?
           ORDER BY quote_volume DESC
           LIMIT ?""",
        (ts, n),
    ).fetchall()
    return [dict(r) for r in rows]


def top_cvd(conn: sqlite3.Connection, n: int = 10, ts: str | None = None):
    ts = _resolve_ts(conn, ts)
    rows = conn.execute(
        """SELECT pair, cvd_trend, volume_delta, bid_qty, ask_qty, strength_score
           FROM pair_metrics
           WHERE ts = ?
           ORDER BY cvd_trend DESC
           LIMIT ?""",
        (ts, n),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

COMMANDS = {
    "strongest": strongest,
    "weakest": weakest,
    "pair": pair_history,
    "snapshot": snapshot,
    "btc": btc_benchmark,
    "recent": recent_snapshots,
    "top-momentum": top_momentum,
    "top-volume": top_volume,
    "cvd": top_cvd,
}


def main():
    parser = argparse.ArgumentParser(
        description="Query the Binance Futures real-time data pipeline",
    )
    parser.add_argument("--db", type=str, default=None, help="SQLite database path")
    parser.add_argument("command", nargs="?", choices=list(COMMANDS), help="Query command")
    parser.add_argument("args", nargs="*", help="Additional arguments for the command")
    parser.add_argument("--ts", type=str, default=None, help="Timestamp (default: LATEST)")
    parser.add_argument("--limit", type=int, default=None, help="Limit for pair/recent")
    parser.add_argument("-n", type=int, default=10, help="Number of results (default: 10)")
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    conn = get_db(args.db)

    try:
        cmd = args.command
        if cmd == "pair":
            if not args.args:
                print("Usage: query.py pair <SYMBOL> [--limit N]", file=sys.stderr)
                sys.exit(1)
            result = COMMANDS[cmd](conn, args.args[0], ts=args.ts, limit=args.limit or 20)
        elif cmd == "recent":
            result = COMMANDS[cmd](conn, limit=args.limit or 5)
        elif cmd in ("snapshot", "btc"):
            result = COMMANDS[cmd](conn, ts=args.ts)
        elif cmd in ("strongest", "weakest", "top-momentum", "top-volume", "cvd"):
            result = COMMANDS[cmd](conn, n=args.n, ts=args.ts)
        else:
            result = COMMANDS[cmd](conn)

        print(json.dumps(result, indent=2, default=str))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
