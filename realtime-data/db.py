"""
SQLite database layer for the real-time Binance futures data pipeline.

Provides:
- Schema initialization from schema.sql
- Async batch inserts for pair_metrics, universe_snapshot, btc_benchmark
- CVD trend tracking via an in-memory EMA accumulator
"""

import json
import logging
import os
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "futures_data.db"


class Database:
    """Async SQLite database manager with schema bootstrapping."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or os.environ.get("RT_DB_PATH", DEFAULT_DB_PATH))
        self._cvd_ema: dict[str, float] = {}  # pair -> last EMA value
        self._cvd_alpha: float = 2.0 / (12 + 1)  # 12-period EMA smoothing factor

    async def init_schema(self) -> None:
        """Create tables and indexes from schema.sql."""
        schema = SCHEMA_SQL.read_text()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(schema)
            await db.commit()
        logger.info("Schema initialized at %s", self.db_path)

    async def insert_snapshot(
        self,
        ts: str,
        pairs: list[dict],
        btc: dict | None,
    ) -> int:
        """
        Insert a full snapshot: pair_metrics rows + universe_snapshot + btc_benchmark.

        Args:
            ts: ISO-8601 timestamp string.
            pairs: List of per-pair dicts with keys:
                   pair, price, price_change_24h, quote_volume,
                   bid_qty, ask_qty, bid_price, ask_price, mark_price,
                   volume_delta, strength_score, btc_relative
            btc: BTC benchmark dict with keys price, price_change_24h, quote_volume
                 (or None if BTC data unavailable).

        Returns:
            Number of pair_metrics rows inserted.
        """
        pair_count = len(pairs)

        # --- Compute CVD trend (12-period EMA of volume_delta) ---
        for p in pairs:
            symbol = p["pair"]
            vd = p.get("volume_delta", 0.0) or 0.0
            prev = self._cvd_ema.get(symbol)
            if prev is None:
                self._cvd_ema[symbol] = vd
                p["cvd_trend"] = vd
            else:
                ema = self._cvd_alpha * vd + (1 - self._cvd_alpha) * prev
                self._cvd_ema[symbol] = ema
                p["cvd_trend"] = round(ema, 6)

        # --- Top/Bottom 5 by strength_score ---
        scored = sorted(pairs, key=lambda x: x.get("strength_score", 0) or 0, reverse=True)
        strongest = scored[:5]
        weakest = scored[-5:] if len(scored) >= 5 else scored[:0]

        strongest_json = json.dumps([
            {"pair": s["pair"], "score": s.get("strength_score")} for s in strongest
        ])
        weakest_json = json.dumps([
            {"pair": s["pair"], "score": s.get("strength_score")} for s in weakest
        ])

        async with aiosqlite.connect(self.db_path) as db:
            # Insert pair_metrics
            rows = [
                (
                    ts,
                    p["pair"],
                    p.get("price"),
                    p.get("price_change_24h"),
                    p.get("quote_volume"),
                    p.get("bid_qty"),
                    p.get("ask_qty"),
                    p.get("bid_price"),
                    p.get("ask_price"),
                    p.get("mark_price"),
                    p.get("volume_delta"),
                    p.get("cvd_trend"),
                    p.get("strength_score"),
                    p.get("btc_relative"),
                )
                for p in pairs
            ]
            await db.executemany(
                """INSERT INTO pair_metrics
                   (ts, pair, price, price_change_24h, quote_volume,
                    bid_qty, ask_qty, bid_price, ask_price, mark_price,
                    volume_delta, cvd_trend, strength_score, btc_relative)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )

            # Insert universe_snapshot
            await db.execute(
                """INSERT INTO universe_snapshot (ts, pair_count, strongest_pairs_json, weakest_pairs_json)
                   VALUES (?, ?, ?, ?)""",
                (ts, pair_count, strongest_json, weakest_json),
            )

            # Insert btc_benchmark
            if btc:
                await db.execute(
                    """INSERT INTO btc_benchmark (ts, price, price_change_24h, quote_volume)
                       VALUES (?, ?, ?, ?)""",
                    (ts, btc["price"], btc.get("price_change_24h"), btc.get("quote_volume")),
                )

            await db.commit()

        logger.info(
            "Snapshot inserted: ts=%s pairs=%d strongest=%s",
            ts, pair_count, [s["pair"] for s in strongest],
        )
        return pair_count

    async def get_recent_ts(self, limit: int = 5) -> list[str]:
        """Return the most recent snapshot timestamps."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT DISTINCT ts FROM pair_metrics ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [r["ts"] for r in rows]

    async def row_count(self, table: str = "pair_metrics") -> int:
        """Return the number of rows in a table."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cursor.fetchone()
            return row[0] if row else 0
