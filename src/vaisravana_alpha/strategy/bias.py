"""BiasEngine — per-tick conviction from MTF EMA + flow + book + risk + breadth.

The master switch: jump-IN when bias aligns + confidence ≥ floor;
jump-OUT when bias flips against the wave.
"""
from __future__ import annotations

import logging
from typing import Optional

from vaisravana_alpha.core.models import BiasReading, TickContext

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

BIAS_THRESH = 0.06       # |score| below this → neutral (lowered hard so the bot surfs weak-leans / flat tape instead of sitting fully flat)
BIAS_SATURATE = 0.60     # score magnitude that gives strength=1.0
MIN_BIAS_STRENGTH = 0.40 # minimum strength to act on bias
FLIP_STRENGTH = 0.35     # bias strength needed to confirm a flip against wave

CONF_ENTRY_FLOOR = 0.18  # lowered: bias+struct alone must clear the floor (REST-poll mode has 0 vol_confirm/recency)
CONF_EXIT_FLOOR = 0.25   # confidence below this → early exit
CONF_HOLD_MS = 0.5       # how long conf must stay below floor before exit (ms)

SIZE_MIN = 0.30          # minimum entry size multiplier
SIZE_MAX = 1.00          # maximum entry size multiplier

# ── Component weights (design doc §3.2) ──────────────────────────────────────

BIAS_WEIGHTS = {
    "mtf_ema": 0.40,
    "flow_delta": 0.25,
    "book_pressure": 0.20,
    "risk_regime": 0.10,
    "breadth": 0.05,
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

    # 2. Order-flow delta
    flow_delta = _flow_delta_norm(ctx.flow_delta, ctx.flow_volume)

    # 3. Book pressure (iter-D: real top-of-book size imbalance when available)
    book_pressure = _book_pressure(ctx.bid, ctx.ask, ctx.bid_qty, ctx.ask_qty)

    # 4. Risk regime (from context)
    risk_regime = _clamp(ctx.risk_regime, -1.0, 1.0)

    # 5. Breadth
    breadth = _clamp(ctx.alt_breadth, -1.0, 1.0)

    # Weighted blend
    score = (
        BIAS_WEIGHTS["mtf_ema"] * mtf_ema
        + BIAS_WEIGHTS["flow_delta"] * flow_delta
        + BIAS_WEIGHTS["book_pressure"] * book_pressure
        + BIAS_WEIGHTS["risk_regime"] * risk_regime
        + BIAS_WEIGHTS["breadth"] * breadth
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
            "breadth": round(breadth, 4),
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
