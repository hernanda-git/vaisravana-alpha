"""SQLite schema and writers for wave_log, wave_telemetry, and smc_zones.

`init_db(path)` owns connection creation so no caller has to know how the
database is opened. Rows are written with `row_factory` set, so reads come
back as mappings rather than positional tuples -- adding a column later
cannot silently shift the meaning of an index somewhere else.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time

log = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wave_log (
    wave_id TEXT PRIMARY KEY,
    pair TEXT, tf TEXT, side TEXT,
    state TEXT,
    entry_price REAL, anchor REAL, sl_price REAL, tp_price REAL,
    size REAL, leverage INT,
    structure_score REAL, mtf_confluence INT,
    opened_ts TEXT, closed_ts TEXT,
    close_reason TEXT,
    peak_r REAL, final_r REAL,
    fees_usd REAL, pnl_usd REAL,
    config_ver TEXT
);

CREATE TABLE IF NOT EXISTS wave_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wave_id TEXT,
    ts TEXT,
    mark_price REAL,
    live_r REAL,
    structure_score REAL,
    distance_to_invalidation REAL,
    sl_price REAL,
    partials_taken INT,
    bias TEXT,
    confidence REAL,
    ema_slope REAL,
    FOREIGN KEY(wave_id) REFERENCES wave_log(wave_id)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_wave_ts
    ON wave_telemetry(wave_id, ts);

CREATE TABLE IF NOT EXISTS smc_zones (
    id TEXT PRIMARY KEY,
    pair TEXT, tf TEXT,
    type TEXT,
    lo REAL, hi REAL,
    bias TEXT,
    matured INT DEFAULT 0,
    ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_smc_lookup
    ON smc_zones(pair, tf, type);

-- Trades table: persists trade data across restarts (wave_log only has structure, not economics)
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wave_id TEXT UNIQUE,
    pair TEXT, tf TEXT, side TEXT,
    entry_price REAL, exit_price REAL,
    quantity REAL, leverage INT,
    pnl REAL, fee REAL,
    confidence REAL,
    close_reason TEXT,
    opened_at TEXT, closed_at TEXT,
    FOREIGN KEY(wave_id) REFERENCES wave_log(wave_id)
);
CREATE INDEX IF NOT EXISTS idx_trades_pair
    ON trades(pair, side);
CREATE INDEX IF NOT EXISTS idx_trades_closed
    ON trades(closed_at);
"""


# ── Init ──────────────────────────────────────────────────────────────────────


def init_db(db_path: str) -> sqlite3.Connection:
    """Open (creating if needed) the database and ensure every table exists.

    Idempotent: re-running never drops or alters existing data. The parent
    directory is created so a fresh container with an empty volume works
    without a provisioning step.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    log.info("database ready at %s", db_path)
    return conn


def init_wave_db(conn: sqlite3.Connection) -> None:
    """Create the tables on an already-open connection."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Check if a table exists in the DB."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone()
    return row is not None


# ── wave_log writers ──────────────────────────────────────────────────────────


def log_wave_open(conn: sqlite3.Connection, wave) -> None:
    """Insert a new wave_log row (state=ENTERED/initial)."""
    conn.execute("""
        INSERT OR REPLACE INTO wave_log
            (wave_id, pair, tf, side, state, entry_price, anchor, sl_price,
             tp_price, size, leverage, structure_score, mtf_confluence,
             opened_ts, config_ver)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        wave.wave_id, wave.pair, wave.tf, wave.side, wave.state.value,
        wave.entry_price, wave.anchor, wave.sl_price,
        wave.tp_price, wave.size, wave.leverage,
        wave.structure_score, int(wave.mtf_confluence),
        str(wave.opened_ts), "",
    ))
    conn.commit()


def log_wave_close(conn: sqlite3.Connection, wave, econ: dict = None) -> None:
    """Update the wave_log row on close (state=CLOSED + metrics)."""
    if econ is None:
        econ = {"close_fee": 0.0, "net": 0.0}
    conn.execute("""
        UPDATE wave_log SET
            state=?, closed_ts=?, close_reason=?,
            peak_r=?, final_r=?, fees_usd=?, pnl_usd=?
        WHERE wave_id=?
    """, (
        wave.state.value,
        str(wave.closed_ts or time.time()),
        wave.close_reason,
        wave.peak_r, wave.live_r,
        round(float(econ.get("close_fee", 0.0)), 4),
        round(float(econ.get("net", 0.0)), 4),
        wave.wave_id,
    ))
    conn.commit()


def wave_exists(conn: sqlite3.Connection, wave_id: str) -> bool:
    """Check if a wave_log entry exists."""
    row = conn.execute(
        "SELECT 1 FROM wave_log WHERE wave_id=?", (wave_id,)
    ).fetchone()
    return row is not None


# ── telemetry writer ──────────────────────────────────────────────────────────


def append_telemetry(conn: sqlite3.Connection, telemetry: dict) -> None:
    """Append one telemetry row."""
    conn.execute("""
        INSERT INTO wave_telemetry
            (wave_id, ts, mark_price, live_r, structure_score,
             distance_to_invalidation, sl_price, partials_taken,
             bias, confidence, ema_slope)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        telemetry["wave_id"], telemetry["ts"], telemetry.get("mark_price", 0),
        telemetry.get("live_r", 0), telemetry.get("structure_score", 0),
        telemetry.get("distance_to_invalidation", 0),
        telemetry.get("sl_price", 0), telemetry.get("partials_taken", 0),
        telemetry.get("bias", ""), telemetry.get("confidence", 0),
        telemetry.get("ema_slope", 0),
    ))
    conn.commit()


# ── smc_zones writer ──────────────────────────────────────────────────────────


def upsert_zone(conn: sqlite3.Connection, zone) -> None:
    """Insert or replace an SMC zone."""
    conn.execute("""
        INSERT OR REPLACE INTO smc_zones
            (id, pair, tf, type, lo, hi, bias, matured, ts)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        zone.id, zone.pair, zone.tf, zone.zone_type.value,
        zone.lo, zone.hi, zone.bias, int(zone.matured),
        str(zone.ts),
    ))
    conn.commit()


def clear_zones(conn: sqlite3.Connection, pair: str, tf: str) -> None:
    """Remove all zones for a pair+tf (to re-seed on close)."""
    conn.execute("DELETE FROM smc_zones WHERE pair=? AND tf=?", (pair, tf))
    conn.commit()


# ── Queries ────────────────────────────────────────────────────────────────────


def get_open_waves(conn: sqlite3.Connection) -> list[dict]:
    """Return all SURFING/ENTERED waves."""
    rows = conn.execute(
        "SELECT * FROM wave_log WHERE state IN ('ENTERED','SURFING')"
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_closed(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Return recent CLOSED waves."""
    rows = conn.execute(
        "SELECT * FROM wave_log WHERE state NOT IN ('ENTERED','SURFING') "
        "ORDER BY closed_ts DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── trades table writers ──────────────────────────────────────────────────────


def log_trade(conn: sqlite3.Connection, wave, econ: dict) -> None:
    """Insert a trade row for persistence across restarts."""
    conn.execute("""
        INSERT OR REPLACE INTO trades
            (wave_id, pair, tf, side, entry_price, exit_price,
             quantity, leverage, pnl, fee, confidence, close_reason,
             opened_at, closed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        wave.wave_id, wave.pair, wave.tf, wave.side,
        wave.entry_price, econ.get("exit_price", 0.0),
        wave.size, wave.leverage,
        econ.get("net", 0.0), econ.get("close_fee", 0.0),
        wave.confidence, wave.close_reason,
        str(wave.opened_ts), str(wave.closed_ts or time.time()),
    ))
    conn.commit()
