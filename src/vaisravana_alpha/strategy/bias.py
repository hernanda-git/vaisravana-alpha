"""BiasEngine — per-tick conviction from MTF EMA + flow + book + risk + breadth.

Redesigned from wave bot's proven 5-component bias:
  - mtf_ema: 40% (multi-timeframe trend)
  - flow_delta: 25% → 35% (order flow / CVD — PRIMARY entry trigger)
  - book_pressure: 20% (real-time liquidity imbalance)
  - risk_regime: 10% (BTC dominance proxy)
  - breadth: 5% (cross-pair participation)

Key changes from alpha v1:
  - CVD/flow_delta boosted from 15% to 35% — it's the PRIMARY entry trigger
  - Universe weight removed from bias (universe ranker now does mean-reversion,
    not momentum chasing; its output is used for pair selection, not direction)
  - Tighter thresholds: MIN_BIAS_STRENGTH=0.30, CONF_ENTRY_FLOOR=0.12
  - BIAS_THRESH lowered to 0.06 so weak-leans still trade
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from vaisravana_alpha.core.models import BiasReading, TickContext
from vaisravana_alpha.strategy.universe_ranker import UniverseRanker

log = logging.getLogger(__name__)

# ── Universe ranker singleton (cached, updated by engine) ─────────────────────

_universe_ranker: UniverseRanker | None = None
_universe_score_cache: dict[str, float] = {}  # pair → total_score (-1..+1)
_universe_last_update: float = 0.0
_UNIVERSE_STALE_S = 120  # treat as stale if >120s old


def set_universe_ranker(ranker: UniverseRanker) -> None:
    """Called by the engine at boot to inject the ranker."""
    global _universe_ranker
    _universe_ranker = ranker


def _get_universe_score(pair: str) -> float:
    """Return cached universe score for a pair (-1.0 weakest to +1.0 strongest).

    Returns 0.0 (neutral) if:
      - Ranker not initialized
      - Pair not in current ranking
      - Cache is stale (>120s)
    """
    global _universe_last_update

    if _universe_ranker is None:
        return 0.0

    # Refresh cache if stale
    now = time.time()
    if now - _universe_last_update > _UNIVERSE_STALE_S:
        _refresh_cache()
        _universe_last_update = now

    return _universe_score_cache.get(pair, 0.0)


def _refresh_cache() -> None:
    """Populate _universe_score_cache from the ranker's latest ranking."""
    global _universe_score_cache, _universe_last_update
    if _universe_ranker is None:
        return

    _universe_score_cache = {}
    for score_obj in _universe_ranker.ranked:
        _universe_score_cache[score_obj.pair] = score_obj.total_score

    _universe_last_update = time.time()


# ── Thresholds (matching wave bot's proven values) ────────────────────────────

BIAS_THRESH = 0.06       # |score| below this → neutral (lowered so weak-leans trade)
BIAS_SATURATE = 0.60     # score magnitude that gives strength=1.0
MIN_BIAS_STRENGTH = 0.30 # minimum strength to act on bias (matching wave bot)
FLIP_STRENGTH = 0.35     # bias strength needed to confirm a flip against wave

CONF_ENTRY_FLOOR = 0.12  # confidence floor for entry (matching wave bot)
CONF_EXIT_FLOOR = 0.25   # confidence below this → early exit
CONF_HOLD_MS = 0.5       # how long conf must stay below floor before exit (ms)

SIZE_MIN = 0.30          # minimum entry size multiplier
SIZE_MAX = 1.00          # maximum entry size multiplier

# ── Component weights (wave bot proven, with CVD boosted to 35%) ──────────────

BIAS_WEIGHTS = {
    "mtf_ema": 0.40,       # multi-timeframe EMA trend
    "flow_delta": 0.35,    # ORDER FLOW / CVD — PRIMARY entry trigger (was 15%)
    "book_pressure": 0.20, # real-time liquidity imbalance
    "risk_regime": 0.05,   # BTC dominance proxy (reduced from 10%)
    "breadth": 0.00,       # removed from bias (universe ranker handles pair selection)
}

CONF_WEIGHTS = {
    "bias_strength": 0.35,
    "structure_score": 0.25,
    "mtf_confluence": 0.20,
    "vol_confirm": 0.10,
    "recency": 0.10,
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _ema_cross_strength(ema_fast: float, ema_slow: float) -> float:
    """Normalised EMA cross slope: +1..-1.

    positive = fast above slow (bullish)
    negative = fast below slow (bearish)
    magnitude = distance as fraction of slow
    """
    if ema_slow == 0:
        return 0.0
    pct_diff = (ema_fast - ema_slow) / ema_slow
    # Saturate at ±0.5% diff; scale to ±1
    return _clamp(pct_diff / 0.005, -1.0, 1.0)


def _flow_delta_norm(flow_delta: float, flow_volume: float) -> float:
    """Normalise signed volume delta to -1..+1.

    Positive = net buying pressure.
    """
    if flow_volume == 0:
        return 0.0
    ratio = flow_delta / flow_volume
    return _clamp(ratio, -1.0, 1.0)


def _book_pressure(bid: float, ask: float, bid_qty: float = 0.0, ask_qty: float = 0.0) -> float:
    """Book imbalance from best bid/ask. -1..+1.

    iter-D: when real top-of-book SIZES are available (bookTicker bidQty/
    askQty), use true liquidity imbalance:
        (bid_qty - ask_qty) / (bid_qty + ask_qty)
    Positive = more resting bid size = demand pressure (bullish).
    Falls back to the legacy price-distance proxy (~0) when sizes absent,
    so the component degrades to its old behavior instead of breaking.
    """
    if bid == 0 or ask == 0:
        return 0.0
    if bid_qty > 0 and ask_qty > 0:
        total = bid_qty + ask_qty
        return _clamp((bid_qty - ask_qty) / total, -1.0, 1.0)
    spread = ask - bid
    if spread == 0:
        return 0.0
    # bid_liq / (bid_liq + ask_liq) → 0..1; normalise to -1..+1
    # Without size, use price distance
    mid = (bid + ask) / 2
    imba = (mid - bid) / spread  # 0..1 where 0.5=neutral
    return _clamp((imba - 0.5) * 2, -1.0, 1.0)


def _recency_factor(signal_age_s: float, half_life_s: float = 300.0) -> float:
    """Exponential decay: 1.0 fresh → 0.0 stale."""
    if signal_age_s <= 0:
        return 1.0
    return max(0.0, 2.0 ** (-signal_age_s / half_life_s) - 0.01)
    # floor at ~ -0.01 to avoid negative


# ── BiasEngine ────────────────────────────────────────────────────────────────


def read_bias(pair: str, tick, ctx: TickContext) -> BiasReading:
    """Compute per-tick bias from MTF EMA + flow + book + risk + breadth.

    Pure function — all inputs are in tick/ctx.

    CVD/flow_delta is the PRIMARY entry trigger (35% weight). When order flow
    agrees with price direction, the signal is strong. When it diverges,
    the signal is weak or reversed.
    """
    # 1. MTF EMA — robust blend of TREND (fast vs slow EMA) and MOMENTUM
    #    (price vs fast EMA). Using price-vs-ema_15m alone was laggy: ema_15m
    #    stayed above price during up-moves, so the bot kept SELLing into
    #    rallies. Now trend (ema_15m vs ema_1h) defines direction; momentum
    #    (price vs ema_15m) confirms. Falls back to price if ema_1h unpopulated.
    if ctx.ema_1h:
        trend = _ema_cross_strength(ctx.ema_15m, ctx.ema_1h)
    else:
        trend = _ema_cross_strength(ctx.ema_15m, ctx.price)
    momentum = _ema_cross_strength(ctx.price, ctx.ema_15m)
    mtf_ema = 0.6 * trend + 0.4 * momentum

    # 2. Order-flow delta (CVD) — PRIMARY ENTRY TRIGGER (35% weight)
    #    This is the most important component. Real-time taker buy/sell volume
    #    delta shows where the actual money is flowing.
    flow_delta = _flow_delta_norm(ctx.flow_delta, ctx.flow_volume)

    # 3. Book pressure (iter-D: real top-of-book size imbalance when available)
    book_pressure = _book_pressure(ctx.bid, ctx.ask, ctx.bid_qty, ctx.ask_qty)

    # 4. Risk regime (from context)
    risk_regime = _clamp(ctx.risk_regime, -1.0, 1.0)

    # 5. Universe ranking — used as a CONFIDENCE BOOSTER, not a direction signal.
    #    The universe ranker now does mean-reversion (oversold=BUY, overbought=SELL),
    #    so it aligns with the bias direction. We add it as a small confidence boost.
    universe_score = _get_universe_score(pair)

    # Weighted blend
    score = (
        BIAS_WEIGHTS["mtf_ema"] * mtf_ema
        + BIAS_WEIGHTS["flow_delta"] * flow_delta
        + BIAS_WEIGHTS["book_pressure"] * book_pressure
        + BIAS_WEIGHTS["risk_regime"] * risk_regime
        + 0.05 * universe_score  # small universe boost (5%)
    )

    # Direction
    if score > BIAS_THRESH:
        direction = "bullish"
    elif score < -BIAS_THRESH:
        direction = "bearish"
    else:
        direction = "neutral"

    # Strength (0..1)
    strength = _clamp(abs(score) / BIAS_SATURATE, 0.0, 1.0)

    return BiasReading(
        direction=direction,
        strength=strength,
        components={
            "mtf_ema": round(mtf_ema, 4),
            "flow_delta": round(flow_delta, 4),
            "book_pressure": round(book_pressure, 4),
            "risk_regime": round(risk_regime, 4),
            "universe": round(universe_score, 4),
            "score": round(score, 4),
        },
        ts=tick.ts if hasattr(tick, "ts") else 0.0,
    )


# ── Confidence ────────────────────────────────────────────────────────────────


def read_confidence(
    bias: BiasReading,
    ctx: TickContext,
    structure_score: float,
    vol_confirm: float,
    signal_age_s: float,
) -> float:
    """Compute live 0..1 conviction from bias strength + structure + confluence.

    Pure function.
    """
    c = (
        CONF_WEIGHTS["bias_strength"] * bias.strength
        + CONF_WEIGHTS["structure_score"] * _clamp(structure_score, 0.0, 1.0)
        + CONF_WEIGHTS["mtf_confluence"] * (1.0 if ctx.mtf_confluence else 0.0)
        + CONF_WEIGHTS["vol_confirm"] * _clamp(vol_confirm, 0.0, 1.0)
        + CONF_WEIGHTS["recency"] * _recency_factor(signal_age_s)
    )
    return _clamp(c, 0.0, 1.0)


# ── Confidence-driven sizing ──────────────────────────────────────────────────


def size_from_confidence(base_size: float, confidence: float) -> float:
    """Scale entry size by confidence: monotonic, clamped [SIZE_MIN, SIZE_MAX].

    Low conviction → small position (min 30% of base).
    High conviction → full position (max 100% of base).
    SL distance is UNCHANGED — we scale size, not risk.
    """
    if base_size <= 0 or confidence <= 0:
        return 0.0
    multiplier = SIZE_MIN + (SIZE_MAX - SIZE_MIN) * _clamp(confidence, 0.0, 1.0)
    return base_size * multiplier