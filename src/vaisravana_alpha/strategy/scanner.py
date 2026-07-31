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
    regime_label: str = "range",
) -> Optional[Candidate]:
    """Tick-driven scan: produce a Candidate if bias+conf+gate align.

    Called on every tick per (pair, side, profile). Returns None -> stay SCANNING.

    v0.1: CVD divergence as entry amplifier — when flow_delta (CVD) is
    declining while price moves in the trade direction, smart money is
    distributing/accumulating against the move. This is a real edge
    that most bots miss because they only use CVD as an exit signal.

    regime_label is passed through to the Candidate so the manager
    can adapt TP width (wider in trends, tighter in ranges).
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

    # v0.1: CVD divergence entry amplifier — flow_delta declining against
    # price direction signals institutional distribution/accumulation.
    # When CVD disagrees with price, that is a real entry edge because
    # retail is chasing while smart money is fading the move.
    cvd = getattr(ctx, "flow_delta", 0) or 0
    cvd_prev = getattr(ctx, "flow_delta_prev", 0) or 0
    cvd_divergence_bonus = 0.0
    if cvd_prev > 0 and cvd < cvd_prev * 0.5 and side == "SELL":
        # Price up but CVD collapsing — smart money distributing, good SELL entry
        cvd_divergence_bonus = 0.15
    elif cvd_prev < 0 and cvd > cvd_prev * 0.5 and side == "BUY":
        # Price down but CVD improving — smart money accumulating, good BUY entry
        cvd_divergence_bonus = 0.15

    return Candidate(
        pair=pair,
        tf=tf,
        side=side,
        strength=confidence * bias.strength * (1.0 + cvd_divergence_bonus),
        ts=time.time(),
        reason="pass",
        regime_label=regime_label,
    )
