"""UniverseRanker — rank all Binance futures pairs by strength/weakness.

Fetches every active futures contract, scores it on:
  1. 24h price change (momentum)
  2. Volume delta (buying vs selling pressure)
  3. CVD divergence (cumulative volume delta trend)
  4. Relative strength vs BTC (outperformance)

Returns ranked lists: top N strongest, bottom N weakest.
Both bots use this to SELECT pairs AND DIRECTION instead of guessing.

Usage:
    from universe_ranker import UniverseRanker
    r = UniverseRanker()
    r.update()  # fetch + score
    strongest = r.strongest(5)   # [(pair, score), ...]
    weakest = r.weakest(5)       # [(pair, score), ...]
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

DEFAULT_PAIRS_FILE = "/data/universe_pairs.json"
DEFAULT_SCORES_FILE = "/data/universe_scores.json"
REFRESH_INTERVAL_S = 60  # re-fetch every 60s


@dataclass
class PairScore:
    """Single pair's strength score and components."""
    pair: str
    total_score: float       # -1.0 (weakest) to +1.0 (strongest)
    price_change_24h: float  # raw 24h % change
    volume_delta: float      # -1.0 to +1.0
    cvd_trend: float         # -1.0 to +1.0
    btc_relative: float      # pair_return - btc_return
    volume_24h: float        # raw volume in quote currency
    price: float             # current price
    components: dict = field(default_factory=dict)


class UniverseRanker:
    """Fetches all Binance futures pairs, scores them, ranks strongest/weakest."""

    def __init__(
        self,
        rest_base: str = "https://fapi.binance.com",
        pairs_file: str = DEFAULT_PAIRS_FILE,
        scores_file: str = DEFAULT_SCORES_FILE,
        top_n: int = 5,
        bottom_n: int = 5,
    ):
        self.rest_base = rest_base.rstrip("/")
        self.pairs_file = pairs_file
        self.scores_file = scores_file
        self.top_n = top_n
        self.bottom_n = bottom_n

        # State
        self.all_pairs: list[str] = []
        self.scores: dict[str, PairScore] = {}
        self.ranked: list[PairScore] = []
        self.last_update: float = 0.0
        self._client: httpx.AsyncClient | None = None

    # ── Public API ─────────────────────────────────────────────────────────

    async def update(self) -> None:
        """Fetch all pairs, compute scores, rank. Call every REFRESH_INTERVAL_S."""
        log.info("universe_ranker: starting update cycle")

        # 1. Get all active futures symbols
        if not self.all_pairs:
            self.all_pairs = await self._fetch_active_pairs()
            log.info("universe_ranker: %d active futures pairs", len(self.all_pairs))

        # 2. Score each pair
        self.scores = await self._score_all_pairs(self.all_pairs)

        # 3. Rank
        self.ranked = sorted(self.scores.values(), key=lambda s: s.total_score, reverse=True)

        # 4. Persist
        self._persist()
        self.last_update = time.time()

        log.info(
            "universe_ranker: done — strongest=%s weakest=%s",
            self.strongest(3),
            self.weakest(3),
        )

    def strongest(self, n: int | None = None) -> list[PairScore]:
        """Return top N strongest pairs."""
        n = n or self.top_n
        return self.ranked[:n]

    def weakest(self, n: int | None = None) -> list[PairScore]:
        """Return bottom N weakest pairs."""
        n = n or self.bottom_n
        return self.ranked[-n:]

    def get_universe(self) -> tuple[list[str], list[str]]:
        """Return (strongest_pairs, weakest_pairs) for bot selection."""
        s = [p.pair for p in self.strongest(self.top_n)]
        w = [p.pair for p in self.weakest(self.bottom_n)]
        return s, w

    def get_active_pairs(self) -> list[str]:
        """Return the full list of active futures pairs."""
        return list(self.all_pairs)

    def get_score(self, pair: str) -> Optional[PairScore]:
        """Get score for a specific pair."""
        return self.scores.get(pair)

    # ── Data fetching ──────────────────────────────────────────────────────

    async def _fetch_active_pairs(self) -> list[str]:
        """Fetch all active Binance futures symbols."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.rest_base}/fapi/v1/exchangeInfo",
                )
                resp.raise_for_status()
                data = resp.json()

            pairs = []
            for s in data.get("symbols", []):
                if s.get("status") != "TRADING":
                    continue
                # Accept PERPETUAL or None (spot/other USDT pairs)
                ct = s.get("contract_type")
                if ct and ct != "PERPETUAL":
                    continue
                symbol = s["symbol"]
                # Only USDT-margined pairs for simplicity
                if not symbol.endswith("USDT"):
                    continue
                # Filter out low-quality pairs
                if any(f in symbol for f in ("UPUSDT", "DOWNUSDT", "HALFUSDT")):
                    continue
                pairs.append(symbol)

            # Persist the pair list
            os.makedirs(os.path.dirname(self.pairs_file) or ".", exist_ok=True)
            with open(self.pairs_file, "w") as f:
                json.dump({"pairs": pairs, "updated": time.time()}, f)

            return pairs

        except Exception as exc:
            log.error("universe_ranker: failed to fetch pairs: %s", exc)
            # Fallback: try persisted list
            return self._load_persisted_pairs()

    def _load_persisted_pairs(self) -> list[str]:
        """Load previously saved pair list."""
        try:
            with open(self.pairs_file) as f:
                data = json.load(f)
            return data.get("pairs", [])
        except Exception:
            return []

    async def _score_all_pairs(self, pairs: list[str]) -> dict[str, PairScore]:
        """Score all pairs in parallel."""
        # Batch fetch 24hr tickers
        tickers = await self._fetch_24hr_tickers(pairs)

        # Batch fetch order book for volume delta proxy
        books = await self._fetch_order_books(pairs)

        # Fetch BTC for relative strength
        btc_score = await self._fetch_btc_score()

        scores = {}
        for pair in pairs:
            ticker = tickers.get(pair)
            book = books.get(pair)
            if not ticker:
                continue

            # 1. Price change 24h
            price_change = float(ticker.get("priceChangePercent", 0.0)) / 100.0

            # 2. Volume delta proxy (bidQty vs askQty from bookTicker)
            vol_delta = self._compute_volume_delta(book)

            # 3. CVD trend proxy (volume-weighted price direction)
            cvd_trend = self._compute_cvd_trend(ticker, book)

            # 4. Relative strength vs BTC
            btc_change = btc_score.get("price_change", 0.0)
            btc_rel = price_change - btc_change

            # Composite score (normalized to -1..+1)
            total = self._composite_score(price_change, vol_delta, cvd_trend, btc_rel)

            scores[pair] = PairScore(
                pair=pair,
                total_score=total,
                price_change_24h=price_change,
                volume_delta=vol_delta,
                cvd_trend=cvd_trend,
                btc_relative=btc_rel,
                volume_24h=ticker.get("quoteVolume", 0.0),
                price=ticker.get("lastPrice", 0.0),
                components={
                    "price_change": round(price_change, 4),
                    "volume_delta": round(vol_delta, 4),
                    "cvd_trend": round(cvd_trend, 4),
                    "btc_relative": round(btc_rel, 4),
                },
            )

        return scores

    async def _fetch_24hr_tickers(self, pairs: list[str]) -> dict:
        """Fetch 24hr ticker for all pairs."""
        tickers = {}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Binance allows comma-separated symbols
                symbols = ",".join(pairs[:50])  # batch limit
                resp = await client.get(
                    f"{self.rest_base}/fapi/v1/ticker/24hr",
                    params={"symbols": symbols},
                )
                resp.raise_for_status()
                for t in resp.json():
                    tickers[t["symbol"]] = t
        except Exception as exc:
            log.warning("universe_ranker: ticker fetch failed: %s", exc)
        return tickers

    async def _fetch_order_books(self, pairs: list[str]) -> dict:
        """Fetch best bid/ask for volume delta proxy."""
        books = {}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                symbols = ",".join(pairs[:50])
                resp = await client.get(
                    f"{self.rest_base}/fapi/v1/ticker/bookTicker",
                    params={"symbols": symbols},
                )
                resp.raise_for_status()
                for b in resp.json():
                    books[b["symbol"]] = b
        except Exception as exc:
            log.warning("universe_ranker: bookTicker fetch failed: %s", exc)
        return books

    async def _fetch_btc_score(self) -> dict:
        """Fetch BTC USDT 24hr data for relative strength."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.rest_base}/fapi/v1/ticker/24hr",
                    params={"symbol": "BTCUSDT"},
                )
                resp.raise_for_status()
                data = resp.json()
                # When querying a single symbol, Binance returns a dict, not a list
                if isinstance(data, list):
                    data = data[0]
                pc = data.get("priceChangePercent", "0")
                vol = data.get("quoteVolume", "0")
                return {
                    "price_change": float(pc) / 100.0,
                    "volume": float(vol),
                }
        except Exception as exc:
            log.warning("universe_ranker: BTC fetch failed: %s", exc)
            return {"price_change": 0.0, "volume": 0.0}

    # ── Scoring ────────────────────────────────────────────────────────────

    def _compute_volume_delta(self, book: dict | None) -> float:
        """Compute volume delta from order book bid/ask quantities.

        Higher bidQty relative to askQty = buying pressure = bullish.
        """
        if not book:
            return 0.0
        bid_qty = float(book.get("bidQty", 0.0))
        ask_qty = float(book.get("askQty", 0.0))
        total = bid_qty + ask_qty
        if total == 0:
            return 0.0
        # Normalize to -1..+1
        return max(-1.0, min(1.0, (bid_qty - ask_qty) / total))

    def _compute_cvd_trend(self, ticker: dict | None, book: dict | None) -> float:
        """Compute CVD trend proxy.

        Uses 24h volume + price change direction + book pressure.
        If price is rising AND volume is heavy → strong bullish CVD.
        If price is falling AND volume is heavy → strong bearish CVD.
        """
        if not ticker:
            return 0.0

        price_change = float(ticker.get("priceChangePercent", 0.0)) / 100.0
        volume = float(ticker.get("quoteVolume", 0.0))

        # Normalize volume to a 0..1 scale (log scale to handle outliers)
        vol_norm = min(1.0, volume / 1e9)  # 1B USDT = max

        # CVD trend: price direction weighted by volume
        # Positive = buying pressure dominating
        # Negative = selling pressure dominating
        cvd = price_change * (1.0 + vol_norm)

        # Normalize to -1..+1
        return max(-1.0, min(1.0, cvd * 10))  # scale factor

    def _composite_score(
        self,
        price_change: float,
        vol_delta: float,
        cvd_trend: float,
        btc_relative: float,
    ) -> float:
        """Compute composite strength score (-1.0 to +1.0).

        Weights:
          - price_change_24h: 0.30 (momentum)
          - volume_delta: 0.20 (order flow)
          - cvd_trend: 0.25 (cumulative volume delta)
          - btc_relative: 0.25 (relative strength)
        """
        # Normalize price_change to -1..+1 (cap at ±5% to avoid outliers)
        pc_norm = max(-1.0, min(1.0, price_change * 10))

        score = (
            0.30 * pc_norm
            + 0.20 * vol_delta
            + 0.25 * cvd_trend
            + 0.25 * btc_relative
        )

        # Final clamp
        return max(-1.0, min(1.0, score))

    # ── Persistence ────────────────────────────────────────────────────────

    def _persist(self) -> None:
        """Save scores to disk for debugging/analysis."""
        try:
            os.makedirs(os.path.dirname(self.scores_file) or ".", exist_ok=True)
            data = {
                "updated": self.last_update,
                "strongest": [
                    {
                        "pair": s.pair,
                        "score": round(s.total_score, 4),
                        "components": s.components,
                    }
                    for s in self.strongest(10)
                ],
                "weakest": [
                    {
                        "pair": s.pair,
                        "score": round(s.total_score, 4),
                        "components": s.components,
                    }
                    for s in self.weakest(10)
                ],
            }
            with open(self.scores_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            log.debug("universe_ranker: persist failed: %s", exc)