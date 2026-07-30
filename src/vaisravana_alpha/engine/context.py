"""Per-pair live context -- the engine's working memory.

`ContextStore` holds one `TickContext` per pair and is the single place
where indicator state is mutated. Centralising it prevents the class of bug
that plagued the legacy engine, where two code paths updated the same EMA on
different schedules and the slower one silently froze.

Two rules are enforced here by construction:

1. Indicator updates from REST are DETERMINISTIC RECOMPUTES, not incremental
   folds. A REST fetch returns the same closed candles every poll, so folding
   them in repeatedly would drift the value. Recomputing from the window is
   idempotent.

2. Kline history is bounded. An unbounded list is a slow memory leak in a
   process meant to run for weeks.
"""

from __future__ import annotations

import logging

from vaisravana_alpha.core.models import Tick, TickContext

log = logging.getLogger(__name__)

# Keep enough history for indicator windows without growing without bound.
MAX_KLINES_PER_TF = 200

# Normalisation constant for trend strength: a 0.5% EMA spread saturates the
# signal. Shared by breadth and risk regime so their units are comparable.
TREND_SATURATION = 0.005


def ema_from_closes(closes: list[float], period: int) -> float:
    """EMA over a fixed window, seeded from the first close.

    Deterministic by design: the same input window always yields the same
    output, which is what makes it safe to call on every REST refetch.
    """
    if not closes:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    ema = float(closes[0])
    for close in closes[1:]:
        ema = alpha * float(close) + (1.0 - alpha) * ema
    return ema


def _normalised_trend(fast: float, slow: float) -> float:
    """Signed EMA spread clamped to [-1, +1]."""
    if not slow:
        return 0.0
    return max(-1.0, min(1.0, (fast - slow) / slow / TREND_SATURATION))


class ContextStore:
    """Owns every pair's live `TickContext`."""

    def __init__(self, pairs: list[str], timeframes: list[str]) -> None:
        self._contexts: dict[str, TickContext] = {}
        self._timeframes = list(timeframes)
        for pair in pairs:
            ctx = TickContext(pair=pair)
            ctx.klines = {tf: [] for tf in timeframes}
            self._contexts[pair] = ctx

    @property
    def pairs(self) -> list[str]:
        return list(self._contexts)

    def get(self, pair: str) -> TickContext | None:
        return self._contexts.get(pair)

    def update_from_tick(self, tick: Tick) -> TickContext:
        """Fold a live tick into its pair's context and return it."""
        ctx = self._contexts.get(tick.pair)
        if ctx is None:
            ctx = TickContext(pair=tick.pair)
            ctx.klines = {tf: [] for tf in self._timeframes}
            self._contexts[tick.pair] = ctx

        if tick.price:
            ctx.price = tick.price
        if tick.mark:
            ctx.mark = tick.mark
        if tick.bid:
            ctx.bid = tick.bid
        if tick.ask:
            ctx.ask = tick.ask
        if tick.bid_qty or tick.ask_qty:
            ctx.bid_qty = tick.bid_qty
            ctx.ask_qty = tick.ask_qty
            total = tick.bid_qty + tick.ask_qty
            ctx.book_imbalance = (
                (tick.bid_qty - tick.ask_qty) / total if total > 0 else 0.0
            )
        ctx.last_tick_ts = tick.ts
        return ctx

    def append_kline(self, pair: str, timeframe: str, kline: dict) -> None:
        """Insert or replace a candle, keyed by its open time.

        Deduplication is by timestamp rather than by position. The REST
        poller re-delivers the same window of closed candles on every cycle,
        so a naive append would stack hundreds of duplicates and skew every
        indicator computed from the window. Matching on `ts` makes a repeated
        fetch a no-op, which is what makes the two feeds safely redundant.
        """
        ctx = self._contexts.get(pair)
        if ctx is None:
            return

        bucket = ctx.klines.setdefault(timeframe, [])
        ts = kline.get("ts")

        if ts is not None:
            # Scan from the end: a repeat almost always lands in the newest
            # few entries, so this stays effectively constant time.
            for i in range(len(bucket) - 1, -1, -1):
                if bucket[i].get("ts") == ts:
                    bucket[i] = kline
                    break
            else:
                bucket.append(kline)
                # Feeds can interleave, so keep the window ordered by time
                # rather than by arrival.
                if len(bucket) > 1 and bucket[-2].get("ts", 0) > ts:
                    bucket.sort(key=lambda k: k.get("ts", 0))
        else:
            bucket.append(kline)

        if len(bucket) > MAX_KLINES_PER_TF:
            del bucket[:-MAX_KLINES_PER_TF]
        if not kline.get("is_final"):
            ctx.forming_candle = kline

    def refresh_indicators(self, pair: str, timeframe: str) -> None:
        """Recompute EMAs for a pair from its stored candle window.

        Called after final candles land. Uses a full recompute rather than an
        incremental update, so a REST poll that re-delivers the same window
        cannot drift the value.
        """
        ctx = self._contexts.get(pair)
        if ctx is None:
            return
        closes = [
            float(k["close"]) for k in ctx.klines.get(timeframe, []) if "close" in k
        ]
        if not closes:
            return
        if timeframe == "15m":
            ctx.ema_15m = ema_from_closes(closes, 20)
        elif timeframe == "1h":
            ctx.ema_1h = ema_from_closes(closes, 20)
        if ctx.ema_15m and ctx.ema_1h:
            ctx.ema_slope = _normalised_trend(ctx.ema_15m, ctx.ema_1h)

    def refresh_flow(self, pair: str, timeframe: str = "15m",
                     lookback: int = 3) -> None:
        """Derive signed taker flow from recent candles.

        Binance reports taker-buy volume per candle; taker sells are the
        remainder. Signed flow is therefore `2 * buys - total`. Assigned, not
        accumulated, so repeated REST fetches of the same window are safe.
        """
        ctx = self._contexts.get(pair)
        if ctx is None:
            return
        recent = [k for k in ctx.klines.get(timeframe, []) if k.get("is_final")]
        recent = recent[-lookback:]
        if not recent:
            return
        volume = sum(float(k.get("volume", 0.0)) for k in recent)
        if volume <= 0 or not any("taker_buy_volume" in k for k in recent):
            return
        buys = sum(float(k.get("taker_buy_volume", 0.0)) for k in recent)
        ctx.flow_delta = 2.0 * buys - volume
        ctx.flow_volume = volume

    def refresh_regimes(self, leader: str = "BTCUSDT") -> None:
        """Recompute cross-pair breadth and the leader-driven risk regime.

        Breadth is the mean trend across every pair with both EMAs live.
        Risk regime is the market leader's trend alone, a standard risk-on /
        risk-off proxy. They are distinct signals and both are cheap, since
        the EMAs they read are already maintained.
        """
        trends = [
            _normalised_trend(ctx.ema_15m, ctx.ema_1h)
            for ctx in self._contexts.values()
            if ctx.ema_15m and ctx.ema_1h
        ]
        if trends:
            breadth = sum(trends) / len(trends)
            for ctx in self._contexts.values():
                ctx.alt_breadth = breadth

        leader_ctx = self._contexts.get(leader)
        if leader_ctx and leader_ctx.ema_15m and leader_ctx.ema_1h:
            regime = _normalised_trend(leader_ctx.ema_15m, leader_ctx.ema_1h)
            for ctx in self._contexts.values():
                ctx.risk_regime = regime

    def seeded_pairs(self) -> int:
        """How many pairs have both EMAs live -- a readiness signal at boot."""
        return sum(
            1 for ctx in self._contexts.values() if ctx.ema_15m and ctx.ema_1h
        )
