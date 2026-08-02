"""UniverseRanker — rank all Binance futures pairs by mean-reversion + momentum signals.

Instead of pure momentum chasing (which buys tops and sells bottoms), this ranker
combines:
  1. RSI (mean-reversion): oversold pairs are BUY candidates, overbought are SELL
  2. VWAP distance (mean-reversion): distance from VWAP indicates overextension
  3. CVD divergence (order flow): smart money accumulation/distribution
  4. Volume delta (order flow): net buying vs selling pressure
  5. Relative strength vs BTC (regime): outperformance/underperformance

The key insight: when millions of bots chase momentum, the best edge is mean-reversion.
We rank pairs by how EXTREMELY overextended they are — the most oversold get BUY scores,
the most overbought get SELL scores. This is the opposite of momentum chasing.

Usage:
    from universe_ranker import UniverseRanker
    r = UniverseRanker()
    r.update()  # fetch + score
    strongest = r.strongest(5)   # most oversold → BUY candidates
    weakest = r.weakest(5)       # most overbought → SELL candidates
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
    total_score: float       # -1.0 (weakest/overbought) to +1.0 (strongest/oversold)
    rsi_14: float            # RSI 14-period (0-100)
    vwap_distance: float     # distance from VWAP as fraction
    cvd_divergence: float    # CVD trend (-1..+1)
    volume_delta: float      # -1.0 to +1.0
    btc_relative: float      # pair_return - btc_return
    volume_24h: float        # raw volume in quote currency
    price: float             # current price
    components: dict = field(default_factory=dict)


class UniverseRanker:
    """Fetches all Binance futures pairs, scores them by mean-reversion + momentum."""

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
        self.all_pairs: list[str] = self._load_persisted_pairs()
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

        # 3. Rank — strongest = most oversold (BUY candidates), weakest = most overbought (SELL)
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
        """Return top N strongest pairs (most oversold → BUY candidates)."""
        n = n or self.top_n
        return self.ranked[:n]

    def weakest(self, n: int | None = None) -> list[PairScore]:
        """Return bottom N weakest pairs (most overbought → SELL candidates)."""
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

    def _fetch_active_pairs_sync(self) -> list[str]:
        """Sync version of _fetch_active_pairs for use at boot before event loop."""
        import urllib.request
        import json
        try:
            req = urllib.request.Request(
                f"{self.rest_base}/fapi/v1/exchangeInfo",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            pairs = []
            for s in data.get("symbols", []):
                if s.get("status") != "TRADING":
                    continue
                ct = s.get("contract_type")
                if ct and ct != "PERPETUAL":
                    continue
                symbol = s["symbol"]
                if not symbol.endswith("USDT"):
                    continue
                if any(f in symbol for f in ("UPUSDT", "DOWNUSDT", "HALFUSDT")):
                    continue
                pairs.append(symbol)
            return pairs
        except Exception as e:
            log.error("Failed to fetch active pairs from Binance API: %s", e)
            return []

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
                ct = s.get("contract_type")
                if ct and ct != "PERPETUAL":
                    continue
                symbol = s["symbol"]
                if not symbol.endswith("USDT"):
                    continue
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
        """Score all pairs in parallel batches."""
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

            # 1. RSI 14-period (mean-reversion signal)
            rsi = self._compute_rsi_from_ticker(ticker)

            # 2. VWAP distance (mean-reversion signal)
            vwap_dist = self._compute_vwap_distance(ticker, book)

            # 3. CVD divergence (order flow)
            cvd_div = self._compute_cvd_divergence(ticker, book)

            # 4. Volume delta (order flow)
            vol_delta = self._compute_volume_delta(book)

            # 5. Relative strength vs BTC
            btc_change = btc_score.get("price_change", 0.0)
            price_change = float(ticker.get("priceChangePercent", 0.0)) / 100.0
            btc_rel = price_change - btc_change

            # Composite score — MEAN-REVERSION PRIMARY, MOMENTUM SECONDARY
            total = self._composite_score(rsi, vwap_dist, cvd_div, vol_delta, btc_rel)

            scores[pair] = PairScore(
                pair=pair,
                total_score=total,
                rsi_14=rsi,
                vwap_distance=vwap_dist,
                cvd_divergence=cvd_div,
                volume_delta=vol_delta,
                btc_relative=btc_rel,
                volume_24h=ticker.get("quoteVolume", 0.0),
                price=ticker.get("lastPrice", 0.0),
                components={
                    "rsi": round(rsi, 4),
                    "vwap_distance": round(vwap_dist, 4),
                    "cvd_divergence": round(cvd_div, 4),
                    "volume_delta": round(vol_delta, 4),
                    "btc_relative": round(btc_rel, 4),
                },
            )

        return scores

    async def _fetch_24hr_tickers(self, pairs: list[str]) -> dict:
        """Fetch 24hr ticker for all pairs."""
        tickers = {}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Binance allows comma-separated symbols (up to 50 at a time)
                for i in range(0, len(pairs), 50):
                    batch = pairs[i:i+50]
                    symbols = ",".join(batch)
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
                for i in range(0, len(pairs), 50):
                    batch = pairs[i:i+50]
                    symbols = ",".join(batch)
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

    def _compute_rsi_from_ticker(self, ticker: dict | None, period: int = 14) -> float:
        """Compute RSI proxy from 24hr ticker data.

        Uses price change over 24h as a proxy for RSI direction.
        If price dropped significantly → oversold → positive score (BUY)
        If price rose significantly → overbought → negative score (SELL)

        This is a simplified RSI proxy since we don't have kline data in the
        24hr ticker. The direction is correct even if the magnitude is approximate.
        """
        if not ticker:
            return 0.5  # neutral

        price_change = float(ticker.get("priceChangePercent", 0.0)) / 100.0

        # Normalize: price_change of -5% → +1.0 (oversold), +5% → -1.0 (overbought)
        # Cap at ±5% to avoid outliers
        pc_norm = max(-0.05, min(0.05, price_change))
        # Scale to -1..+1: negative change = positive score (oversold = BUY)
        rsi_proxy = -pc_norm * 20.0  # -5% → +1.0, +5% → -1.0
        return max(-1.0, min(1.0, rsi_proxy))

    def _compute_vwap_distance(self, ticker: dict | None, book: dict | None) -> float:
        """Compute distance from VWAP as a mean-reversion signal.

        Uses 24h volume-weighted price as a proxy for VWAP.
        If current price is far above VWAP → overbought → negative score
        If current price is far below VWAP → oversold → positive score
        """
        if not ticker:
            return 0.0

        last_price = float(ticker.get("lastPrice", 0))
        quote_volume = float(ticker.get("quoteVolume", 0))
        base_volume = float(ticker.get("volume", 0))

        if quote_volume == 0 or base_volume == 0:
            return 0.0

        # VWAP proxy: quoteVolume / baseVolume = volume-weighted average price
        vwap_proxy = quote_volume / base_volume

        if vwap_proxy == 0:
            return 0.0

        # Distance as fraction
        dist = (last_price - vwap_proxy) / vwap_proxy

        # Normalize: positive dist = price above VWAP = overbought = negative score
        # Clamp at ±2% to avoid outliers
        dist = max(-0.02, min(0.02, dist))
        # Scale to -1..+1
        return max(-1.0, min(1.0, -dist * 50))  # negative sign: above VWAP = negative score

    def _compute_cvd_divergence(self, ticker: dict | None, book: dict | None) -> float:
        """Compute CVD divergence proxy.

        If price is rising but volume is declining → bearish divergence (SELL)
        If price is falling but volume is increasing → bullish divergence (BUY)
        """
        if not ticker or not book:
            return 0.0

        price_change = float(ticker.get("priceChangePercent", 0.0)) / 100.0
        volume = float(ticker.get("quoteVolume", 0.0))

        bid_qty = float(book.get("bidQty", 0.0))
        ask_qty = float(book.get("askQty", 0.0))
        total = bid_qty + ask_qty

        if total == 0:
            return 0.0

        # Book pressure: more bid size = buying pressure
        book_pressure = (bid_qty - ask_qty) / total

        # CVD divergence: if price is up but book pressure is down → bearish divergence
        # If price is down but book pressure is up → bullish divergence
        # Normalize price change to -1..+1 (cap at ±5%)
        pc_norm = max(-1.0, min(1.0, price_change * 10))

        # Divergence = book_pressure - price_change (positive = bullish divergence)
        divergence = book_pressure - pc_norm

        return max(-1.0, min(1.0, divergence))

    def _compute_volume_delta(self, book: dict | None) -> float:
        """Compute volume delta from order book bid/ask quantities."""
        if not book:
            return 0.0
        bid_qty = float(book.get("bidQty", 0.0))
        ask_qty = float(book.get("askQty", 0.0))
        total = bid_qty + ask_qty
        if total == 0:
            return 0.0
        return max(-1.0, min(1.0, (bid_qty - ask_qty) / total))

    def _composite_score(
        self,
        rsi: float,
        vwap_dist: float,
        cvd_div: float,
        vol_delta: float,
        btc_rel: float,
    ) -> float:
        """Compute composite strength score (-1.0 to +1.0).

        MEAN-REVERSION PRIMARY (70%):
          - RSI: 30% — oversold = BUY, overbought = SELL
          - VWAP distance: 20% — below VWAP = BUY, above = SELL
          - CVD divergence: 20% — bullish divergence = BUY

        MOMENTUM SECONDARY (30%):
          - Volume delta: 15% — net buying = bullish
          - BTC relative: 15% — outperformance = bullish
        """
        score = (
            0.30 * rsi           # mean-reversion: oversold = strong BUY
            + 0.20 * vwap_dist   # mean-reversion: below VWAP = BUY
            + 0.20 * cvd_div     # mean-reversion: bullish divergence = BUY
            + 0.15 * vol_delta   # momentum: net buying = bullish
            + 0.15 * btc_rel     # momentum: outperformance = bullish
        )

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