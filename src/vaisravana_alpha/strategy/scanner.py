"""Wave scanner — tick-driven entry candidate generator."""
from __future__ import annotations

import logging
import time
from typing import Optional

from vaisravana_alpha.core.models import BiasReading, Candidate, TickContext
from vaisravana_alpha.strategy.smc import SMCZoneCache
from vaisravana_alpha.strategy.gate import wave_quality_pass
from vaisravana_alpha.strategy.structure import detect_structure

log = logging.getLogger(__name__)


def scan(
    pair: str,
    side: str,
    tf: str,
    bias: BiasReading,
    confidence: float,
    ctx: TickContext,
    zone_cache: SMCZoneCache,
    adx: float = 25.0,
) -> Optional[Candidate]:
    """Tick-driven scan: produce a Candidate if bias+conf+gate align.

    Called on every tick per (pair, side, profile). Returns None -> stay SCANNING.
    """
    # Quick pre-check: bias direction must agree
    if side == "BUY" and bias.direction != "bullish":
        return None
    if side == "SELL" and bias.direction != "bearish":
        return None

    # Derive structure
    sr = detect_structure(ctx, zone_cache, ctx.price)

    # Quality gate
    passed, reason = wave_quality_pass(
        side=side,
        bias=bias,
        confidence=confidence,
        structure_score=sr.structure_score,
        ctx=ctx,
        zone_cache=zone_cache,
        adx=adx,
    )
    if not passed:
        return None

    return Candidate(
        pair=pair,
        tf=tf,
        side=side,
        strength=confidence * bias.strength,
        ts=time.time(),
        reason="pass",
    )
