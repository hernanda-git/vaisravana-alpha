"""Structure helpers — EMA tick-recursive, BOS/CHoCH, swing, liquidity sweep."""
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


def detect_swing(klines: list[dict], i: int) -> tuple[str, Optional[float]]:
    """Detect if bar i is a swing high or low.

    Returns ('high', price) or ('low', price) or ('', None).
    Uses local window of 3 bars on each side.
    """
    if i < 3 or i >= len(klines) - 3:
        return '', None
    bar = klines[i]
    # Swing high
    if all(bar['high'] >= klines[j]['high'] for j in range(i-3, i+4) if j != i):
        return 'high', bar['high']
    # Swing low
    if all(bar['low'] <= klines[j]['low'] for j in range(i-3, i+4) if j != i):
        return 'low', bar['low']
    return '', None


def detect_structure(
    ctx: TickContext,
    zone_cache: SMCZoneCache,
    price: float,
) -> StructureReading:
    """Derive structure reading from current tick context + SMC zones.

    Called every tick — uses cached zones, never recomputes SMC.
    """
    sd = StructureReading()
    sd.ema_15m = ctx.ema_15m
    sd.ema_1h = ctx.ema_1h
    sd.ema_slope = compute_ema_slope(ctx.ema_15m, ctx.ema_1h)

    # Score: blend of EMA slope + structure quality
    ema_component = abs(sd.ema_slope)
    sd.structure_score = min(1.0, ema_component * 2.0)

    # MTF confluence: decision-TF EMA direction agrees with HTF
    sd.mtf_confluence = (sd.ema_slope > 0.2) or (sd.ema_slope < -0.2)

    # SMC zone checks (cheap, cached)
    zone = zone_cache.point_in_zone(ctx.pair, price) if zone_cache else None
    if zone:
        if zone.zone_type.value == 'fvg':
            sd.fvg_hit = True
        if zone.bias == 'bullish':
            sd.in_demand_zone = True
        elif zone.bias == 'bearish':
            sd.in_supply_zone = True
        # Boost structure score when price is in a zone
        sd.structure_score = min(1.0, sd.structure_score + 0.15)

    # Liquidity sweep detection from recent klines
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

    return sd
