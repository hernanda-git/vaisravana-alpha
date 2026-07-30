"""Survival gates — additive, fee-aware trade-quality gates (iter-13).

These run BEFORE a wave is opened. They are pure admission control: they
can only REDUCE trade frequency and block obviously-unprofitable entries.
They never change the engine's signal logic or StrategyProfile, so they
respect the Sentinel constraint (ParameterSurface-only mutation) and cannot
introduce new risk — only cut bleed.

Gates (each independently tunable via env / ParameterSurface later):
  1. Fee-aware EV gate — block unless expected favorable move clears
     k x (round-trip fee + spread + slip). On a ~breakeven signal this
     vetoes the majority of trades that net ~0 gross but pay full fees.
  2. Trade-frequency throttle — global cap ~TRADES_PER_HOUR, per-pair
     spacing PAIR_SPACING_MIN. Cuts churn 75%+ (fees scale with count).
  3. Spread gate — skip pairs whose current spread_bps exceeds SPREAD_GATE_BPS.
     Spread is paid twice per round trip; on thin alts it dwarfs any edge.
  4. Session block — ban low-liquidity UTC windows (00-05) where the bot
     trades noise / gets adverse-selected.

Design: stateless-ish module with a small rolling counter. Safe under the
async loop (single-threaded per event loop, so no lock needed for counters
updated only from on_tick).
"""
from __future__ import annotations

import os
import time
import math
import json
import logging
import threading

log = logging.getLogger(__name__)

# ── Tunable thresholds (ParameterSurface candidates; env-overridable) ──
# Adaptive throttle: the cap is no longer a fixed constant. It floats between
# TPH_FLOOR and TPH_CEIL based on the bot's own recent realized expectancy
# (per-trade net PnL). When expectancy is positive the cap RISES so the loop
# can gather more samples and compound; when it is negative the cap FALLS to
# cut bleed. The floor always stays > 0 so the bot never fully stalls (0 opens
# = 0 growth = a rejected dead regime, per iter-10 lesson).
TPH_FLOOR = int(os.getenv("VAISRAVANA_TPH_FLOOR", "4"))
TPH_CEIL = int(os.getenv("VAISRAVANA_TPH_CEIL", "20"))
TPH_START = int(os.getenv("VAISRAVANA_TRADES_PER_HOUR", "6"))  # env keeps meaning as start
PAIR_SPACING_MIN = float(os.getenv("VAISRAVANA_PAIR_SPACING_MIN", "20"))
SPREAD_GATE_BPS = float(os.getenv("VAISRAVANA_SPREAD_GATE_BPS", "5.0"))
# iter-A (gate recalibration, 2026-07-29): K=2.5 with FEE_RT=8 demanded
# 25-49bps expected move while the tape's exp_move (conf x ATR15m) measured
# only 6-20bps -> 4549 VETO / 0 PASS in 60m = fully suppressed bot (0 trades
# = 0 growth; per the iter-10 lesson, 0 opens is a REJECT, not selectivity).
# Recal to val's stated fee model: maker 0.02% open + taker 0.04% close
# = 6bps RT; slip 1bps for limit-style entry at 5s REST cadence. K=1.4 keeps
# a real EV margin (required ~= 12.6-18bps on tight pairs) so only the top
# slice of candidates passes. Env-overridable as before.
EV_GATE_K = float(os.getenv("VAISRAVANA_EV_GATE_K", "1.4"))  # require E[move] >= K x cost
FEE_BPS_ROUNDTRIP = float(os.getenv("VAISRAVANA_FEE_BPS_RT", "6.0"))  # 0.02% maker open + 0.04% taker close
SLIP_BPS = float(os.getenv("VAISRAVANA_SLIP_BPS", "1.0"))  # est execution slip (limit-style entry)
# session block (UTC hours, inclusive low, exclusive high)
SESSION_BLOCK_UTC = os.getenv("VAISRAVANA_SESSION_BLOCK", "0-5")  # "0-5" or "" to disable


def _session_blocked_utc() -> bool:
    if not SESSION_BLOCK_UTC:
        return False
    try:
        lo, hi = SESSION_BLOCK_UTC.split("-")
        lo_h, hi_h = int(lo), int(hi)
    except ValueError:
        return False
    hour = time.gmtime().tm_hour
    return lo_h <= hour < hi_h


class _RateTracker:
    """Adaptive per-hour trade throttle.

    The cap is NOT fixed. Every hour it recomputes a float target from the
    bot's OWN realized expectation (per-trade net PnL) over the last window:

        exp = mean(net_pnl per closed trade) / risk_unit
        cap = clamp(round(START + k * exp), FLOOR, CEIL)

    where risk_unit is a reference $/R (e.g. $0.05). Positive expectancy ->
    more trades (more samples, compound); negative -> fewer (cut bleed). The
    floor guarantees the bot never fully halts (0 opens == dead regime).
    """
    def __init__(self):
        self._hour_start = time.time()
        self._count = 0
        self._last_open_ts: dict[str, float] = {}
        self._lock = threading.Lock()
        # realized net PnL of recently closed trades (ts, net) for expectancy
        self._net_samples: list[tuple[float, float]] = []
        self._cap = max(TPH_FLOOR, min(TPH_CEIL, TPH_START))
        self._exp_window = 3600.0  # keep ~1h of closed-trade samples
        self._exp_last_ts = time.time()

    def record_open(self, pair: str) -> None:
        with self._lock:
            now = time.time()
            if now - self._hour_start >= 3600:
                self._hour_start = now
                self._count = 0
            self._count += 1
            self._last_open_ts[pair] = now

    def record_close(self, net_pnl: float) -> None:
        """Feed realized net PnL of a closed wave so the cap can adapt."""
        with self._lock:
            now = time.time()
            self._net_samples.append((now, net_pnl))
            # prune older than window
            self._net_samples = [(t, v) for (t, v) in self._net_samples
                                 if now - t <= self._exp_window]
            # recompute cap at most once/minute to avoid jitter
            if now - self._exp_last_ts >= 60.0:
                self._recompute_cap()
                self._exp_last_ts = now

    def _recompute_cap(self) -> None:
        if not self._net_samples:
            return
        vals = [v for (_, v) in self._net_samples]
        mean_net = sum(vals) / len(vals)
        # reference risk unit: a 1R move on a ~$5 notional wave ≈ $0.05
        risk_unit = 0.05
        exp_r = mean_net / risk_unit if risk_unit else 0.0
        # +0.10R expectancy -> ~ +1 trade; -0.10R -> -1 trade (clamped)
        target = TPH_START + round(exp_r * 10.0)
        new_cap = max(TPH_FLOOR, min(TPH_CEIL, target))
        if new_cap != self._cap:
            log.info("adaptive_throttle cap %d -> %d (exp_r=%.3f, n=%d)",
                     self._cap, new_cap, exp_r, len(vals))
            self._cap = new_cap

    def allowed(self, pair: str) -> tuple[bool, str]:
        with self._lock:
            now = time.time()
            if now - self._hour_start >= 3600:
                self._hour_start = now
                self._count = 0
            if self._count >= self._cap:
                return False, f"global_rate:{self._count}/{self._cap}per_h"
            last = self._last_open_ts.get(pair)
            if last is not None and (now - last) < PAIR_SPACING_MIN * 60:
                return False, f"pair_spacing:{PAIR_SPACING_MIN}m"
            return True, "ok"


_TRACKER = _RateTracker()


def spread_bps(ctx) -> float:
    """Spread in basis points from best bid/ask. 0 if unavailable."""
    if ctx.bid and ctx.ask and ctx.bid > 0 and ctx.ask > 0:
        mid = (ctx.bid + ctx.ask) / 2.0
        if mid > 0:
            return abs(ctx.ask - ctx.bid) / mid * 1e4
    return 0.0


def survival_gate(pair: str, ctx, confidence: float, expected_move_bps: float) -> tuple[bool, str]:
    """Admission control before opening a wave.

    Args:
        pair: symbol
        ctx: TickContext (needs bid/ask for spread)
        confidence: 0..1 signal confidence
        expected_move_bps: model's expected favorable move in bps over hold.
            Caller computes this (e.g. confidence * ATR-projected move). If the
            caller cannot estimate it, pass 0 -> gate will veto (conservative).

    Returns (allow, reason).
    """
    # 4. Session block
    if _session_blocked_utc():
        return False, "session_block_utc"

    # 3. Spread gate
    sp = spread_bps(ctx)
    if sp > SPREAD_GATE_BPS:
        return False, f"spread:{sp:.1f}bps>{SPREAD_GATE_BPS:.0f}"

    # 1. Fee-aware EV gate: required move = K * (fee + slip). If the spread is
    #    known, add it (paid twice). Otherwise fee+slip only.
    cost_bps = FEE_BPS_ROUNDTRIP + SLIP_BPS + sp * 2.0
    required_bps = EV_GATE_K * cost_bps
    if expected_move_bps < required_bps:
        log.info("survival_gate VETO %s conf=%.2f: exp_move=%.1fbps < required=%.1fbps",
                  pair, confidence, expected_move_bps, required_bps)
        return False, f"ev_gate:{expected_move_bps:.1f}<{required_bps:.1f}bps"

    # 2. Rate / spacing throttle
    ok, reason = _TRACKER.allowed(pair)
    if not ok:
        log.info("survival_gate VETO %s conf=%.2f: %s", pair, confidence, reason)
        return False, reason

    log.info("survival_gate PASS %s conf=%.2f: exp_move=%.1fbps >= required=%.1fbps",
             pair, confidence, expected_move_bps, required_bps)
    return True, "ok"


def record_open(pair: str) -> None:
    """Call this ONLY after a wave actually opens, to update counters."""
    _TRACKER.record_open(pair)


def record_close(net_pnl: float) -> None:
    """Feed realized net PnL of a closed wave so the throttle can adapt.

    The adaptive cap RISES when expectancy is positive (more samples,
    compound) and FALLS when negative (cut bleed). Floor stays > 0 so the
    bot never fully stalls.
    """
    _TRACKER.record_close(net_pnl)


def current_cap() -> int:
    """Expose the live adaptive cap (for diagnostics / logging)."""
    return _TRACKER._cap
