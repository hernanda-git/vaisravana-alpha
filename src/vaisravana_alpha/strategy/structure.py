"""Structure helpers — EMA tick-recursive, real SMC zone detection.

Redesigned from wave bot's proven SMC zone cache. The key change:
- structure_score is now derived from REAL SMC zones (order blocks, FVGs, liquidity pools)
- NOT from EMA slope × 2 (which was meaningless)
- SMC zones are computed on closed HTF klines and cached for sub-ms per-tick reads
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from vaisravana_alpha.core.models import SMCZone, TickContext
from vaisravana_alpha.strategy.smc import SMCZoneCache

log = logging.getLogger(__name__)


@dataclass
class StructureReading:
    """Per-tick structure context derived from EMA + candles + SMC."""
    ema_15m: float = 0.0
    ema_1h: float = 0.0
    ema_slope: float = 0.0          # normalised cross -1..+1
    hh: bool = False                 # higher high formed
    hl: bool = False                 # higher low formed
    lh: bool = False                 # lower high formed (CHoCH)
    ll: bool = False                 # lower low formed
    bos: bool = False                # break of structure
    choch: bool = False              # change of character
    liquidity_sweep: bool = False    # sweep of recent swing high/low
    fvg_hit: bool = False            # price inside an FVG
    in_demand_zone: bool = False     # in a demand OB
    in_supply_zone: bool = False     # in a supply OB
    structure_score: float = 0.0     # 0..1 composite
    mtf_confluence: bool = False     # decision TF agrees with HTF


def ema_update(prev_ema: float, price: float, period: int = 20) -> float:
    """Tick-recursive EMA: ema = price * k + ema_prev * (1 - k).

    Sub-microsecond per tick. No window needed.
    """
    if prev_ema == 0:
        return price
    k = 2.0 / (period + 1)
    return price * k + prev_ema * (1 - k)


def compute_ema_slope(ema_15m: float, ema_1h: float) -> float:
    """Normalised EMA cross slope -1..+1.

    Positive = fast above slow (bullish).
    """
    if ema_1h == 0:
        return 0.0
    pct = (ema_15m - ema_1h) / ema_1h
    return max(-1.0, min(1.0, pct / 0.005))


def detect_structure(
    ctx: TickContext,
    zone_cache: SMCZoneCache,
    price: float,
) -> StructureReading:
    """Derive structure reading from current tick context + SMC zones.

    Called every tick — uses cached zones, never recomputes SMC.

    Key change from v1: structure_score is now derived from REAL SMC zones
    (order blocks, FVGs, liquidity pools) instead of EMA slope × 2.
    """
    sd = StructureReading()
    sd.ema_15m = ctx.ema_15m
    sd.ema_1h = ctx.ema_1h
    sd.ema_slope = compute_ema_slope(ctx.ema_15m, ctx.ema_1h)

    # ── Real SMC zone scoring ────────────────────────────────────────────
    # The structure score is now derived from REAL SMC zones with
    # proximity-based granularity instead of binary 0.6/0.2.
    #
    # Key insight from data: wins had avg structure_score=0.1209 vs
    # losses=0.1081 — the difference was negligible because the score
    # was binary. Now we make it continuous and proximity-weighted.

    zone = zone_cache.point_in_zone(ctx.pair, price) if zone_cache else None
    if zone:
        if zone.zone_type.value == 'fvg':
            sd.fvg_hit = True
        if zone.bias == 'bullish':
            sd.in_demand_zone = True
        elif zone.bias == 'bearish':
            sd.in_supply_zone = True

        # Proximity-based scoring: how close is price to the zone center?
        zone_center = (zone.lo + zone.hi) / 2
        zone_width = zone.hi - zone.lo
        if zone_width > 0:
            # Distance from price to zone center, normalized by zone width
            dist = abs(price - zone_center) / zone_width
            # At center (dist=0) → score=1.0; at 2x width (dist=2) → score=0.0
            proximity = max(0.0, 1.0 - dist * 0.5)
        else:
            proximity = 1.0

        # Base score by zone type, modulated by proximity
        type_scores = {
            'order_block': 0.7,
            'fvg': 0.6,
            'liquidity_pool': 0.4,
            'bos': 0.35,
            'choch': 0.35,
        }
        base = type_scores.get(zone.zone_type.value, 0.2)
        sd.structure_score = base * proximity

        # Check for matured BOS/CHoCH zones
        if zone_cache:
            matured_bullish = zone_cache.get_matured_bos_choch(ctx.pair, "bullish")
            matured_bearish = zone_cache.get_matured_bos_choch(ctx.pair, "bearish")
            if matured_bullish:
                sd.bos = True
            if matured_bearish:
                sd.choch = True
    else:
        # No zones cached (REST-poll mode, or zones not yet refreshed)
        # Fall back to EMA slope as a weak signal
        ema_component = abs(sd.ema_slope)
        sd.structure_score = min(0.3, ema_component * 0.6)  # max 0.3 without zones

    # ── MTF confluence ───────────────────────────────────────────────────
    # Decision-TF EMA direction agrees with HTF.
    # Lowered from 0.2 to 0.05 — most pairs oscillate around 0.05-0.15
    # spread; 0.2 required 1% spread which almost never sustained.
    sd.mtf_confluence = (sd.ema_slope > 0.05) or (sd.ema_slope < -0.05)

    # ── Liquidity sweep detection from recent klines ─────────────────────
    klines_list = ctx.klines.get(ctx.tf if hasattr(ctx, 'tf') and ctx.tf else '15m', [])
    if len(klines_list) >= 6:
        recent = klines_list[-6:]
        recent_highs = [k['high'] for k in recent]
        recent_lows = [k['low'] for k in recent]
        max_high = max(recent_highs)
        min_low = min(recent_lows)
        # Sweep of high from above
        if price > max_high * 1.001:
            sd.liquidity_sweep = True
        # Sweep of low from below
        if price < min_low * 0.999:
            sd.liquidity_sweep = True

    # Also propagate to the context so the confidence calculation reads it.
    ctx.mtf_confluence = sd.mtf_confluence
    ctx.structure_score = sd.structure_score
    return sd