"""Wave gate — wave-quality whipsaw guard (bias+conf+SMC)."""
from __future__ import annotations

import logging
from typing import Optional

from vaisravana_alpha.core.models import BiasReading, TickContext
from vaisravana_alpha.strategy.smc import SMCZoneCache

log = logging.getLogger(__name__)

# Thresholds
MIN_BIAS_STRENGTH = 0.10 # very low so weak leans on choppy BONK tape still trade
CONF_ENTRY_FLOOR = 0.10  # lower so flat-tape candidates still pass
                          # (0 opens in a full run after warmup). Keep iter-9
                          # selectivity (net break-even); the standalone warmup
                          # already removed the dead 90s burst.
ADX_FLOOR = 15           # allow more regimes through (mean-revert included)
STRUCTURE_SCORE_FLOOR = 0.10  # allow thinner structure through


def wave_quality_pass(
    side: str,
    bias: BiasReading,
    confidence: float,
    structure_score: float,
    ctx: TickContext,
    zone_cache: SMCZoneCache,
    adx: float = 25.0,
) -> tuple[bool, str]:
    """Check if a candidate wave passes the quality gate.

    All AND conditions — single fail rejects.
    Returns (pass, reason).
    """
    # 1. Bias direction agrees with intended side
    if side == "BUY" and bias.direction != "bullish":
        return False, f"bias={bias.direction} rejects BUY"
    if side == "SELL" and bias.direction != "bearish":
        return False, f"bias={bias.direction} rejects SELL"

    # 2. Bias strength floor
    if bias.strength < MIN_BIAS_STRENGTH:
        return False, f"bias_strength={bias.strength:.2f} < {MIN_BIAS_STRENGTH}"

    # 3. Confidence floor
    if confidence < CONF_ENTRY_FLOOR:
        return False, f"confidence={confidence:.2f} < {CONF_ENTRY_FLOOR}"

    # 4. Structure score floor
    if structure_score < STRUCTURE_SCORE_FLOOR:
        return False, f"structure={structure_score:.2f} < {STRUCTURE_SCORE_FLOOR}"

    # 5. ADX floor (non-chop)
    if adx < ADX_FLOOR:
        return False, f"adx={adx:.0f} < {ADX_FLOOR}"

    # 6. SMC zone check — only if zones are actually seeded.
    # In REST-poll / WS-down mode the zone cache is empty, so we
    # must NOT reject on a missing zone (that would block all trades).
    if zone_cache and zone_cache.get_zones(ctx.pair):
        zone = zone_cache.point_in_zone(ctx.pair, ctx.price)
        if side == "BUY" and (not zone or zone.bias != "bullish"):
            return False, f"no bullish zone at {ctx.price:.1f}"
        if side == "SELL" and (not zone or zone.bias != "bearish"):
            return False, f"no bearish zone at {ctx.price:.1f}"

    # 7. SMC invalidation check — only if zones are seeded.
    if zone_cache and zone_cache.get_zones(ctx.pair):
        check_bias = "bearish" if side == "BUY" else "bullish"
        opposing = zone_cache.get_matured_bos_choch(ctx.pair, check_bias)
        if opposing:
            return False, f"matured {opposing[0].zone_type.value} against {side}"

    return True, "pass"
