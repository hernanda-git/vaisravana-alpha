"""Agentic workflow schema -- the database an autonomous loop reasons over.

The trading tables record what happened. These tables record *why a change
was made and whether it worked*, which is the part an improvement loop
cannot function without.

The predecessor's loop kept its reasoning in markdown logs. That made three
questions unanswerable by query, and all three matter:

  1. "Did iteration 17 actually beat iteration 16, or did the market change?"
     Needs run isolation with the exact surface attached to each trade.
  2. "Why did we take zero trades for six hours?"
     Needs rejection telemetry. Silence and a vetoing gate look identical in
     a trade table, and wave once spent a day with a gate rejecting 100% of
     candidates while the logs showed a healthy, busy bot.
  3. "Have we tried this before?"
     Needs a hypothesis ledger with lineage, or the loop rediscovers the same
     dead end forever.

Design rules:

  * Append-only. Nothing is updated in place except a run's terminal status.
    An agent that can rewrite its own history cannot be audited.
  * Every trade carries its `run_id`, so a result can never be silently
    attributed to the wrong parameter set.
  * Rejections are counted, not just logged, because "we took no trades"
    and "we were blocked 4,000 times by one gate" demand opposite responses.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

AGENTIC_SCHEMA = """
-- ── runs ──────────────────────────────────────────────────────────────
-- One row per engine start. The unit of comparison for the whole loop:
-- a verdict is always "this run versus that run", never a floating average
-- over a window whose parameters changed halfway through.
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    started_ts       REAL NOT NULL,
    ended_ts         REAL,
    status           TEXT NOT NULL DEFAULT 'running',  -- running|completed|crashed|halted
    halt_reason      TEXT DEFAULT '',

    surface_revision INTEGER NOT NULL DEFAULT 0,
    surface_json     TEXT NOT NULL,     -- full surface, so a run is reproducible
    surface_hash     TEXT NOT NULL,     -- identical config across runs is detectable

    iteration_id     TEXT DEFAULT '',   -- links to the hypothesis under test
    mode             TEXT NOT NULL DEFAULT 'paper',
    pairs            TEXT NOT NULL DEFAULT '',
    git_sha          TEXT DEFAULT '',   -- code identity; surface alone is not enough

    start_balance    REAL NOT NULL DEFAULT 0,
    end_balance      REAL,
    ticks            INTEGER DEFAULT 0,
    opens            INTEGER DEFAULT 0,
    closes           INTEGER DEFAULT 0,
    notes            TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_started   ON runs(started_ts DESC);
CREATE INDEX IF NOT EXISTS idx_runs_iteration ON runs(iteration_id);
CREATE INDEX IF NOT EXISTS idx_runs_hash      ON runs(surface_hash);

-- ── iterations ────────────────────────────────────────────────────────
-- The hypothesis ledger. `parent_id` gives lineage, so the loop can walk
-- back from a winner and see the path that produced it -- and, more
-- usefully, avoid re-walking paths already known to be dead.
CREATE TABLE IF NOT EXISTS iterations (
    iteration_id  TEXT PRIMARY KEY,
    created_ts    REAL NOT NULL,
    parent_id     TEXT DEFAULT '',

    hypothesis    TEXT NOT NULL,   -- what we believe and why
    change_kind   TEXT NOT NULL,   -- surface|gate|risk|code
    change_json   TEXT NOT NULL,   -- exact diff applied
    predicted     TEXT DEFAULT '', -- stated BEFORE the run, to expose hindsight bias

    status        TEXT NOT NULL DEFAULT 'proposed',
        -- proposed|running|evaluated|promoted|rejected|inconclusive
    verdict       TEXT DEFAULT '',
    decided_ts    REAL,
    decided_by    TEXT DEFAULT ''  -- agent id or 'human'
);
CREATE INDEX IF NOT EXISTS idx_iter_status ON iterations(status);
CREATE INDEX IF NOT EXISTS idx_iter_parent ON iterations(parent_id);

-- ── trades ────────────────────────────────────────────────────────────
-- Immutable closed-trade facts. Gross, fees, and net are stored separately
-- and never derived at read time: a positive gross with a negative net is
-- the single most important distinction in this whole system, and
-- recomputing it later invites the two to drift apart.
CREATE TABLE IF NOT EXISTS trades (
    trade_id     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    wave_id      TEXT DEFAULT '',

    pair         TEXT NOT NULL,
    side         TEXT NOT NULL,
    tf           TEXT DEFAULT '',

    open_ts      REAL NOT NULL,
    close_ts     REAL,
    hold_s       REAL,

    entry_price  REAL NOT NULL,
    exit_price   REAL,
    anchor       REAL,
    size         REAL,
    notional     REAL,
    leverage     INTEGER DEFAULT 1,

    gross_usd    REAL DEFAULT 0,
    open_fee     REAL DEFAULT 0,
    close_fee    REAL DEFAULT 0,
    net_usd      REAL DEFAULT 0,
    r_multiple   REAL DEFAULT 0,
    peak_r       REAL DEFAULT 0,

    entry_conf   REAL DEFAULT 0,
    entry_bias   TEXT DEFAULT '',
    close_reason TEXT DEFAULT '',
    balance_after REAL,

    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_trades_run   ON trades(run_id);
CREATE INDEX IF NOT EXISTS idx_trades_pair  ON trades(pair);
CREATE INDEX IF NOT EXISTS idx_trades_close ON trades(close_ts);

-- ── gate_rejections ───────────────────────────────────────────────────
-- Counted, not logged. Aggregated per (run, gate, pair) so a six-hour
-- silence resolves to a specific gate name and a number, instead of an
-- absence of evidence.
CREATE TABLE IF NOT EXISTS gate_rejections (
    run_id      TEXT NOT NULL,
    gate        TEXT NOT NULL,
    pair        TEXT NOT NULL DEFAULT '',
    count       INTEGER NOT NULL DEFAULT 0,
    last_ts     REAL,
    last_detail TEXT DEFAULT '',
    PRIMARY KEY (run_id, gate, pair)
);
CREATE INDEX IF NOT EXISTS idx_rej_run ON gate_rejections(run_id);

-- ── evaluations ───────────────────────────────────────────────────────
-- One row per evaluation layer per run. Layers are stored separately
-- rather than collapsed into a single score, because "profitable but
-- statistically meaningless" and "sound but unprofitable" require
-- completely different responses and a single number hides which one
-- you are looking at.
CREATE TABLE IF NOT EXISTS evaluations (
    eval_id    TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    layer      INTEGER NOT NULL,   -- 0..5
    layer_name TEXT NOT NULL,
    verdict    TEXT NOT NULL,      -- pass|warn|fail|insufficient_data
    score      REAL,
    metrics    TEXT NOT NULL DEFAULT '{}',
    reasons    TEXT NOT NULL DEFAULT '[]',
    created_ts REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_eval_run   ON evaluations(run_id, layer);
CREATE INDEX IF NOT EXISTS idx_eval_layer ON evaluations(layer, verdict);

-- ── decisions ─────────────────────────────────────────────────────────
-- The audit trail. Every promote/reject/rollback, with the evidence that
-- justified it. This is what makes an autonomous loop reviewable after the
-- fact rather than merely observable while it runs.
CREATE TABLE IF NOT EXISTS decisions (
    decision_id  TEXT PRIMARY KEY,
    created_ts   REAL NOT NULL,
    run_id       TEXT DEFAULT '',
    iteration_id TEXT DEFAULT '',

    action       TEXT NOT NULL,   -- promote|reject|rollback|hold|escalate
    rationale    TEXT NOT NULL,
    evidence     TEXT NOT NULL DEFAULT '{}',
    actor        TEXT NOT NULL DEFAULT 'agent',
    reverted_by  TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dec_created ON decisions(created_ts DESC);

-- ── schema_meta ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init_agentic_db(db_path: str) -> sqlite3.Connection:
    """Open the agentic database, creating tables if needed.

    WAL is enabled so the evaluator can read a run while the engine is still
    writing it. Under the default rollback journal those two block each
    other, and an evaluation that has to stop the bot to run is an
    evaluation that will not get run.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(AGENTIC_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    log.info("agentic database ready at %s (schema v%d)", db_path, SCHEMA_VERSION)
    return conn


def new_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:12]
    return f"{prefix}-{short}" if prefix else short


def surface_hash(surface_json: str) -> str:
    """Stable hash of a surface, so identical configs are detectable.

    Keys are sorted before hashing: two surfaces that differ only in JSON
    key order are the same experiment and must not look like two.
    """
    import hashlib

    try:
        canonical = json.dumps(json.loads(surface_json), sort_keys=True,
                               separators=(",", ":"))
    except Exception:
        canonical = surface_json
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ── run lifecycle ────────────────────────────────────────────────────────


def start_run(
    conn: sqlite3.Connection,
    surface,
    mode: str,
    pairs: list[str],
    start_balance: float,
    iteration_id: str = "",
    git_sha: str = "",
) -> str:
    """Record the start of a run and return its id."""
    run_id = new_id("run")
    surface_json = json.dumps(surface.as_dict(), sort_keys=True)
    conn.execute(
        """INSERT INTO runs (run_id, started_ts, status, surface_revision,
               surface_json, surface_hash, iteration_id, mode, pairs, git_sha,
               start_balance)
           VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, time.time(), surface.revision, surface_json,
         surface_hash(surface_json), iteration_id, mode, ",".join(pairs),
         git_sha, start_balance),
    )
    conn.commit()
    log.info("run %s started (surface rev=%d)", run_id, surface.revision)
    return run_id


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    end_balance: float,
    ticks: int,
    opens: int,
    closes: int,
    halt_reason: str = "",
) -> None:
    """Close out a run. The only non-append-only write in the schema."""
    conn.execute(
        """UPDATE runs SET ended_ts=?, status=?, end_balance=?, ticks=?,
               opens=?, closes=?, halt_reason=? WHERE run_id=?""",
        (time.time(), status, end_balance, ticks, opens, closes,
         halt_reason, run_id),
    )
    conn.commit()
    log.info("run %s finished: %s, balance %.4f", run_id, status, end_balance)


def heartbeat_run(conn: sqlite3.Connection, run_id: str, ticks: int,
                  opens: int, closes: int) -> None:
    """Update counters mid-run so a crashed run still shows its progress.

    Without this a process killed by OOM leaves a row claiming zero ticks,
    and the loop cannot distinguish "crashed immediately" from "ran well for
    six hours then died".
    """
    conn.execute(
        "UPDATE runs SET ticks=?, opens=?, closes=? WHERE run_id=?",
        (ticks, opens, closes, run_id),
    )
    conn.commit()


# ── trades ───────────────────────────────────────────────────────────────


def record_trade(conn: sqlite3.Connection, run_id: str, wave, econ: dict) -> str:
    """Append one closed trade.

    Gross is derived once, here, as `net + fees`, and then stored. Later
    readers never recompute it, so the fee accounting cannot drift between
    the card the owner reads and the number the evaluator scores.
    """
    trade_id = new_id("t")
    open_fee = float(getattr(wave, "_open_fee", 0.0) or 0.0)
    close_fee = float(econ.get("close_fee", 0.0) or 0.0)
    net = float(econ.get("net", 0.0) or 0.0)
    open_ts = float(getattr(wave, "open_ts", 0.0) or 0.0)
    close_ts = time.time()

    conn.execute(
        """INSERT INTO trades (trade_id, run_id, wave_id, pair, side, tf,
               open_ts, close_ts, hold_s, entry_price, exit_price, anchor,
               size, notional, leverage, gross_usd, open_fee, close_fee,
               net_usd, r_multiple, peak_r, entry_conf, entry_bias,
               close_reason, balance_after)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            trade_id, run_id, getattr(wave, "wave_id", ""),
            wave.pair, wave.side, getattr(wave, "tf", ""),
            open_ts, close_ts, max(0.0, close_ts - open_ts) if open_ts else 0.0,
            wave.entry_price, float(econ.get("exit_price", 0.0) or 0.0),
            getattr(wave, "anchor", 0.0),
            getattr(wave, "size", 0.0), getattr(wave, "notional", 0.0),
            getattr(wave, "leverage", 1),
            net + open_fee + close_fee, open_fee, close_fee, net,
            getattr(wave, "live_r", 0.0), getattr(wave, "peak_r", 0.0),
            getattr(wave, "confidence", 0.0), str(getattr(wave, "bias", "")),
            getattr(wave, "close_reason", ""),
            float(econ.get("balance_after", 0.0) or 0.0),
        ),
    )
    conn.commit()
    return trade_id


# ── rejection telemetry ──────────────────────────────────────────────────


def record_rejection(conn: sqlite3.Connection, run_id: str, gate: str,
                     pair: str = "", detail: str = "") -> None:
    """Increment a gate's rejection counter.

    An upsert rather than an insert: one row per (run, gate, pair) keeps the
    table small enough to survive a bot that rejects thousands of candidates
    an hour, while still answering "which gate is blocking us".
    """
    conn.execute(
        """INSERT INTO gate_rejections (run_id, gate, pair, count, last_ts,
               last_detail)
           VALUES (?, ?, ?, 1, ?, ?)
           ON CONFLICT(run_id, gate, pair) DO UPDATE SET
               count = count + 1, last_ts = excluded.last_ts,
               last_detail = excluded.last_detail""",
        (run_id, gate, pair, time.time(), detail[:200]),
    )


def rejection_summary(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """Rejections for a run, worst gate first."""
    rows = conn.execute(
        """SELECT gate, SUM(count) AS total, MAX(last_ts) AS last_ts,
                  MAX(last_detail) AS detail
           FROM gate_rejections WHERE run_id=? GROUP BY gate
           ORDER BY total DESC""",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── iterations and decisions ─────────────────────────────────────────────


def create_iteration(conn: sqlite3.Connection, hypothesis: str,
                     change_kind: str, change: dict, predicted: str = "",
                     parent_id: str = "") -> str:
    """Register a hypothesis before the run that tests it.

    `predicted` is recorded up front on purpose. A prediction written after
    seeing the result is a story, not a test.
    """
    iteration_id = new_id("iter")
    conn.execute(
        """INSERT INTO iterations (iteration_id, created_ts, parent_id,
               hypothesis, change_kind, change_json, predicted, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed')""",
        (iteration_id, time.time(), parent_id, hypothesis, change_kind,
         json.dumps(change, sort_keys=True), predicted),
    )
    conn.commit()
    return iteration_id


def settle_iteration(conn: sqlite3.Connection, iteration_id: str,
                     status: str, verdict: str, decided_by: str = "agent") -> None:
    conn.execute(
        """UPDATE iterations SET status=?, verdict=?, decided_ts=?, decided_by=?
           WHERE iteration_id=?""",
        (status, verdict, time.time(), decided_by, iteration_id),
    )
    conn.commit()


def record_decision(conn: sqlite3.Connection, action: str, rationale: str,
                    evidence: dict, run_id: str = "", iteration_id: str = "",
                    actor: str = "agent") -> str:
    decision_id = new_id("dec")
    conn.execute(
        """INSERT INTO decisions (decision_id, created_ts, run_id, iteration_id,
               action, rationale, evidence, actor)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (decision_id, time.time(), run_id, iteration_id, action, rationale,
         json.dumps(evidence, sort_keys=True, default=str), actor),
    )
    conn.commit()
    return decision_id


# ── evaluation storage ───────────────────────────────────────────────────


def record_evaluation(conn: sqlite3.Connection, run_id: str, layer: int,
                      layer_name: str, verdict: str, score: float | None,
                      metrics: dict, reasons: list[str]) -> str:
    eval_id = new_id("ev")
    conn.execute(
        """INSERT INTO evaluations (eval_id, run_id, layer, layer_name,
               verdict, score, metrics, reasons, created_ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (eval_id, run_id, layer, layer_name, verdict, score,
         json.dumps(metrics, sort_keys=True, default=str),
         json.dumps(reasons), time.time()),
    )
    conn.commit()
    return eval_id


# ── queries the loop needs ───────────────────────────────────────────────


def get_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def latest_run(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM runs ORDER BY started_ts DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def run_trades(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM trades WHERE run_id=? ORDER BY close_ts", (run_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def compare_runs(conn: sqlite3.Connection, run_a: str, run_b: str) -> dict:
    """Head-to-head summary of two runs.

    The loop's core question. Returns raw aggregates only; judging whether a
    difference is *meaningful* is layer 2's job, not this function's.
    """
    def agg(run_id: str) -> dict:
        row = conn.execute(
            """SELECT COUNT(*) AS n,
                      COALESCE(SUM(net_usd), 0) AS net,
                      COALESCE(SUM(open_fee + close_fee), 0) AS fees,
                      COALESCE(AVG(r_multiple), 0) AS avg_r,
                      COALESCE(SUM(CASE WHEN net_usd > 0 THEN 1 ELSE 0 END), 0) AS wins
               FROM trades WHERE run_id=?""",
            (run_id,),
        ).fetchone()
        data = dict(row)
        data["win_rate"] = (data["wins"] / data["n"]) if data["n"] else 0.0
        data["net_per_trade"] = (data["net"] / data["n"]) if data["n"] else 0.0
        return data

    a, b = agg(run_a), agg(run_b)
    return {
        "run_a": run_a, "run_b": run_b, "a": a, "b": b,
        "delta_net": b["net"] - a["net"],
        "delta_net_per_trade": b["net_per_trade"] - a["net_per_trade"],
        "delta_win_rate": b["win_rate"] - a["win_rate"],
    }
