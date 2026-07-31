#!/usr/bin/env python3
"""
Real-time Binance Futures data collector.

Fetches ALL active USDT perpetual futures pairs every 60 seconds and stores:
- 24h ticker (price, change%, volume)
- Best bid/ask (book ticker)
- Mark price (premium index)
- Derived metrics: strength score, volume delta, CVD trend, BTC relative

Uses 4 batch REST endpoints (not per-pair) → ~4 req/cycle, well within 1200/min limit.

Usage:
    python collector.py [--interval 60] [--once]
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone

import httpx

from db import Database

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BINANCE_FUTURES_BASE = "https://fapi.binance.com"

# Endpoints (all public — no auth required)
EXCHANGE_INFO_URL = f"{BINANCE_FUTURES_BASE}/fapi/v1/exchangeInfo"
TICKER_24HR_URL   = f"{BINANCE_FUTURES_BASE}/fapi/v1/ticker/24hr"
BOOK_TICKER_URL   = f"{BINANCE_FUTURES_BASE}/fapi/v1/ticker/bookTicker"
PREMIUM_INDEX_URL = f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex"

REQUEST_TIMEOUT = 30.0     # seconds per HTTP call
MAX_RETRIES = 3
RETRY_DELAY = 2.0

# Logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    force=True,
)
logger = logging.getLogger("collector")

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def fetch_json(client: httpx.AsyncClient, url: str, label: str = "") -> list | dict:
    """Fetch JSON from a Binance endpoint with retries."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            logger.debug("%s: got %s records (attempt %d)", label, _len(data), attempt)
            return data
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_exc = exc
            logger.warning("%s: attempt %d/%d failed: %s", label, attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)
    raise last_exc  # type: ignore[misc]


def _len(obj):
    return len(obj) if isinstance(obj, (list, dict)) else 1


# ---------------------------------------------------------------------------
# Symbol filtering
# ---------------------------------------------------------------------------

def filter_usdt_perps(exchange_info: dict) -> list[str]:
    """Extract active USDT-margined perpetual symbols from exchangeInfo."""
    symbols = []
    for s in exchange_info.get("symbols", []):
        if (
            s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
        ):
            symbols.append(s["symbol"])
    return sorted(symbols)


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def compute_strength_score(
    price_change_24h: float | None,
    quote_volume: float | None,
    volume_delta: float | None,
    bid_qty: float | None,
    ask_qty: float | None,
) -> float:
    """
    Composite strength score [0–100].

    Components (equally weighted):
      - Momentum: tanh(price_change_24h / 5)  →  [-1, 1]
      - Volume depth: tanh(log10(quote_volume + 1) / 3 - 1)  →  [-1, 1]
      - Order-book pressure: tanh(volume_delta / max(bid_qty+ask_qty, 1) * 5)  →  [-1, 1]

    Scaled to [0, 100]: 50 is neutral.
    """
    pc = price_change_24h or 0.0
    qv = quote_volume or 0.0
    vd = volume_delta or 0.0
    bq = bid_qty or 0.0
    aq = ask_qty or 0.0

    # Momentum component
    momentum = math.tanh(pc / 5.0)

    # Volume depth (log scale normalised to ~[-1, 1] for typical ranges)
    vol_depth = math.tanh(math.log10(max(qv, 1.0)) / 3.0 - 1.0)

    # Order-book pressure (volume delta relative to total depth)
    total_depth = bq + aq
    if total_depth > 0:
        ob_pressure = math.tanh((vd / total_depth) * 5.0)
    else:
        ob_pressure = 0.0

    raw = (momentum + vol_depth + ob_pressure) / 3.0  # [-1, 1]
    score = (raw + 1.0) * 50.0  # [0, 100]
    return round(max(0.0, min(100.0, score)), 2)


# ---------------------------------------------------------------------------
# Main collection cycle
# ---------------------------------------------------------------------------

async def collect_once(
    client: httpx.AsyncClient,
    db: Database,
    active_symbols: set[str] | None = None,
) -> dict:
    """
    Run one collection cycle.

    Returns a summary dict or raises on fatal error.
    """
    ts = datetime.now(timezone.utc).isoformat()

    # --- 1. Fetch all endpoints in parallel ---
    tickers_task = fetch_json(client, TICKER_24HR_URL, "24h tickers")
    books_task   = fetch_json(client, BOOK_TICKER_URL, "book tickers")
    marks_task   = fetch_json(client, PREMIUM_INDEX_URL, "premium index")

    tickers_raw, books_raw, marks_raw = await asyncio.gather(
        tickers_task, books_task, marks_task,
    )

    # --- 2. Index by symbol ---
    tickers: dict[str, dict] = {}
    for t in tickers_raw:
        sym = t.get("symbol", "")
        if active_symbols is None or sym in active_symbols:
            tickers[sym] = t

    books: dict[str, dict] = {}
    for b in books_raw:
        sym = b.get("symbol", "")
        if active_symbols is None or sym in active_symbols:
            books[sym] = b

    marks: dict[str, dict] = {}
    for m in marks_raw:
        sym = m.get("symbol", "")
        if active_symbols is None or sym in active_symbols:
            marks[sym] = m

    # --- 3. Extract BTC benchmark ---
    btc_ticker = tickers.get("BTCUSDT", {})
    btc = None
    if btc_ticker:
        btc = {
            "price": _float(btc_ticker.get("lastPrice")),
            "price_change_24h": _float(btc_ticker.get("priceChangePercent")),
            "quote_volume": _float(btc_ticker.get("quoteVolume")),
        }

    btc_pct = btc["price_change_24h"] if btc else 0.0

    # --- 4. Build per-pair metrics ---
    pairs = []
    symbols = list(active_symbols) if active_symbols else sorted(tickers.keys())

    for sym in symbols:
        t = tickers.get(sym, {})
        b = books.get(sym, {})
        m = marks.get(sym, {})

        price = _float(t.get("lastPrice"))
        price_change_24h = _float(t.get("priceChangePercent"))
        quote_volume = _float(t.get("quoteVolume"))
        bid_price = _float(b.get("bidPrice"))
        ask_price = _float(b.get("askPrice"))
        bid_qty = _float(b.get("bidQty"))
        ask_qty = _float(b.get("askQty"))
        mark_price = _float(m.get("markPrice"))

        # Volume delta: net bid - ask quantity at best levels
        volume_delta = round((bid_qty or 0.0) - (ask_qty or 0.0), 8)

        # BTC relative: how this pair is doing vs BTC
        btc_relative = round(price_change_24h - btc_pct, 4) if btc else None

        # Strength score
        strength_score = compute_strength_score(
            price_change_24h, quote_volume, volume_delta, bid_qty, ask_qty
        )

        pairs.append({
            "pair": sym,
            "price": price,
            "price_change_24h": price_change_24h,
            "quote_volume": quote_volume,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "mark_price": mark_price,
            "volume_delta": volume_delta,
            "strength_score": strength_score,
            "btc_relative": btc_relative,
        })

    # --- 5. Store in DB ---
    count = await db.insert_snapshot(ts, pairs, btc)

    # --- 6. Summary ---
    scored = sorted(pairs, key=lambda x: x["strength_score"], reverse=True)
    summary = {
        "ts": ts,
        "pairs": count,
        "btc_price": btc["price"] if btc else None,
        "btc_change_24h": btc_pct,
        "strongest": [(s["pair"], s["strength_score"]) for s in scored[:5]],
        "weakest": [(s["pair"], s["strength_score"]) for s in scored[-5:]],
    }
    return summary


async def run_collector(
    interval: int = 60,
    once: bool = False,
    db_path: str | None = None,
):
    """Main loop: initialise, then collect every `interval` seconds."""
    db = Database(db_path)
    await db.init_schema()

    # --- Discover active symbols once ---
    async with httpx.AsyncClient() as client:
        logger.info("Fetching exchangeInfo to discover active USDT perps...")
        exchange_info = await fetch_json(client, EXCHANGE_INFO_URL, "exchangeInfo")
        active_symbols = set(filter_usdt_perps(exchange_info))
        logger.info("Discovered %d active USDT perpetual pairs", len(active_symbols))

        if once:
            summary = await collect_once(client, db, active_symbols)
            logger.info("Single collection done: %s", json.dumps(summary))
            return summary

        # --- Continuous loop ---
        cycle = 0
        while True:
            cycle += 1
            start = time.monotonic()
            try:
                summary = await collect_once(client, db, active_symbols)
                elapsed = time.monotonic() - start
                logger.info(
                    "Cycle %d: %d pairs in %.1fs | strongest: %s | weakest: %s",
                    cycle, summary["pairs"], elapsed,
                    [p[0] for p in summary["strongest"]],
                    [p[0] for p in summary["weakest"]],
                )
            except Exception:
                logger.exception("Cycle %d failed — will retry after interval", cycle)

            wait = max(0.0, interval - (time.monotonic() - start))
            logger.debug("Sleeping %.1fs until next cycle", wait)
            await asyncio.sleep(wait)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _float(v) -> float | None:
    """Parse a string/number to float, returning None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Binance Futures real-time data collector")
    parser.add_argument("--interval", type=int, default=60, help="Collection interval in seconds (default: 60)")
    parser.add_argument("--once", action="store_true", help="Run one collection cycle and exit")
    parser.add_argument("--db", type=str, default=None, help="SQLite database path")
    args = parser.parse_args()

    try:
        asyncio.run(run_collector(interval=args.interval, once=args.once, db_path=args.db))
    except KeyboardInterrupt:
        logger.info("Shutting down on user interrupt")
        sys.exit(0)
