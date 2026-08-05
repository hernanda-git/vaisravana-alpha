"""Alpha v2 manager — tick-driven lifecycle with counter-trade + mean-reversion.

Redesigned from wave bot's proven exit logic + new counter-trade engine.

Key changes from alpha v1:
  - Port wave's exit logic: flat_tape_exit, partial_profit, reversed_exit
  - Counter-trade: when bias flips, open opposite position instead of just exit
  - Mean-reversion mode: trade weakest pairs (fade extremes)
  - Momentum mode: trade strongest pairs (follow momentum)
  - Reduce MAX_OPEN to 3 (not 8) to reduce fee bleed
  - Collect ALL data for evaluation
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from vaisravana_alpha.core.params import ParameterSurface

from vaisravana_alpha.core.models import Wave, WaveState, BiasReading, Candidate, Tick, TickContext
from vaisravana_alpha.strategy.smc import SMCZoneCache
from vaisravana_alpha.strategy.bias import size_from_confidence
from vaisravana_alpha.storage.db import log_wave_open, log_wave_close, append_telemetry, log_trade
from vaisravana_alpha.strategy.structure import detect_structure
from vaisravana_alpha.strategy.indicators import atr_pct as _atr_pct

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CONFIRM_MS = 0.25            # micro-confirmation (seconds) — matching wave bot
CONF_EXIT_FLOOR = 0.20      # raised from 0.10 — only exit on real confidence collapse
CONF_HOLD_MS = 1.0           # reduced from 0.3s for faster confidence exit
FLIP_STRENGTH = 0.35         # bias strength needed to confirm a flip (was 0.20 — too sensitive)
PARTIAL_FRAC = 0.25          # fraction to trim on stall


# ── Actions ───────────────────────────────────────────────────────────────────


@dataclass
class WaveAction:
    """Action returned by manager methods — no side effects."""
    type: str                  # "NONE" | "OPEN" | "CLOSE" | "PARTIAL" | "ADD"
    reason: str = ""
    wave: Optional[Wave] = None
    size: float = 0.0
    price: float = 0.0


# ── WaveManager ───────────────────────────────────────────────────────────────


@dataclass
class WaveManager:
    """TICK-DRIVEN wave lifecycle manager.

    No MAXHOLD. No global side locks. Per-wave cooldown only.
    Reads exit/risk parameters from ParameterSurface when set, falling
    back to hardcoded defaults for tests that construct a bare manager.
    """

    # In-memory state
    waves: dict[str, Wave] = field(default_factory=dict)  # wave_id → Wave
    cooldowns: dict[tuple[str, str], float] = field(default_factory=dict)  # (pair, side) → expiry unix ts
    _break_start: dict[str, float] = field(default_factory=dict)  # wave_id → ts when flip detected
    _conf_break_start: dict[str, float] = field(default_factory=dict)

    # DB connection (set externally)
    conn = None
    # Parameter surface (set externally by runtime). When None, defaults are used.
    surface: Optional["ParameterSurface"] = None

    # ── Surface-backed parameter helpers ─────────────────────────────────

    @property
    def _exits(self):
        """ExitRules from surface, or None if not wired."""
        return getattr(self.surface, "exits", None) if self.surface else None

    @property
    def _risk(self):
        """RiskLimits from surface, or None if not wired."""
        return getattr(self.surface, "risk", None) if self.surface else None

    @property
    def _max_open_waves(self) -> int:
        return self._risk.max_open_waves if self._risk else 5

    @property
    def _loss_cut_r(self) -> float:
        return self._exits.loss_cut_r if self._exits else 0.20

    @property
    def _breakeven_floor_r(self) -> float:
        return self._exits.breakeven_floor_r if self._exits else 0.20

    @property
    def _max_wave_age_s(self) -> float:
        return self._exits.max_wave_age_s if self._exits else 200.0

    @property
    def _cooldown_s(self) -> float:
        return self._exits.cooldown_s if self._exits else 120.0

    @property
    def _bank_r(self) -> float:
        return self._exits.bank_r if self._exits else 0.20

    # ── Open ──────────────────────────────────────────────────────────────

    def open(self, candidate: Candidate, bias: BiasReading,
             confidence: float, ctx: TickContext,
             surface, wallet=None, regime_label: str = "range") -> Optional[Wave]:
        """Open a new wave from a validated candidate."""
        key = (candidate.pair, candidate.side)
        if key in self.cooldowns:
            log.debug("open rejected: %s in cooldown", key)
            return None

        # Never stack a duplicate wave on an already-live (pair, side).
        for w in self.waves.values():
            if w.pair == candidate.pair and w.side == candidate.side \
                    and w.state in (WaveState.ENTERED, WaveState.SURFING):
                log.debug("open rejected: %s already live", key)
                return None

        # Guard: never open with a zero/unknown price.
        if not ctx.price or ctx.price <= 0:
            log.debug("open skipped: ctx.price=%.4f not ready", ctx.price)
            return None

        # Paper wallet: survival sizing + open fee
        if wallet is not None:
            if wallet.is_broke:
                log.info("PAPER wallet broke (balance<=%.2f) — no new waves",
                          wallet.stop_at)
                return None
            notion = wallet.notional_for(ctx.price)
            if notion <= 0.0:
                log.info("PAPER notional 0 — skipping open %s %s",
                         candidate.side, candidate.pair)
                return None
        else:
            notion = 100.0

        # Realistic sizing: leverage + margin + Binance min-notional
        lev = int(min(surface.max_leverage, getattr(surface, "trade_leverage", 5) or 5))
        lev = max(1, min(lev, 20))
        MIN_NOTIONAL = {
            "BTCUSDT": 100.0, "ETHUSDT": 10.0, "SOLUSDT": 10.0,
            "BNBUSDT": 10.0, "XRPUSDT": 5.0, "ADAUSDT": 5.0,
            "DOGEUSDT": 5.0, "AVAXUSDT": 5.0, "LINKUSDT": 5.0,
            "TRXUSDT": 5.0, "TONUSDT": 5.0, "NEARUSDT": 5.0,
            "APTUSDT": 5.0, "ARBUSDT": 5.0, "OPUSDT": 5.0,
            "INJUSDT": 5.0, "TIAUSDT": 5.0, "SEIUSDT": 5.0,
            "WIFUSDT": 5.0, "PENGUUSDT": 5.0, "WLDUSDT": 5.0,
            "TAOUSDT": 5.0, "AAVEUSDT": 5.0, "ENAUSDT": 5.0,
            "APEUSDT": 5.0, "CRVUSDT": 5.0, "PUMPUSDT": 5.0,
            "1000PEPEUSDT": 5.0, "1000BONKUSDT": 5.0,
        }
        min_not = MIN_NOTIONAL.get(candidate.pair, 5.0)
        if wallet is not None and min_not > 0.60 * wallet.balance:
            log.info("PAPER sizing-parity skip %s: min_notional $%.0f > 60%% of balance $%.2f",
                     candidate.pair, min_not, wallet.balance)
            return None
        notion = max(min_not, min(notion, wallet.balance if wallet else notion))
        margin = notion / lev
        size_units = notion / ctx.price if ctx.price else 0.0

        wave = Wave(
            wave_id=f"{candidate.pair}-{candidate.tf}-{candidate.side}-{int(time.time() * 1000)}",
            pair=candidate.pair,
            tf=candidate.tf,
            side=candidate.side,
            state=WaveState.ENTERED,
            entry_price=ctx.price,
            size=size_units,
            notional=notion,
            leverage=lev,
            margin=margin,
            open_ts=time.time(),
            structure_score=candidate.strength,
            bias=bias.direction,
            confidence=confidence,
            opened_ts=time.time(),
            last_tick_ts=time.time(),
        )

        try:
            wave.open_components = dict(bias.components or {})
            wave.last_components = dict(bias.components or {})
        except Exception:
            pass

        # Anchor (SL): ATR-based
        atr = _atr_pct(ctx, candidate.tf)
        buffer = max(ctx.price * 0.010, ctx.price * atr * 1.8)
        if candidate.side == "BUY":
            wave.anchor = ctx.price - buffer
        else:
            wave.anchor = ctx.price + buffer
        wave.sl_price = wave.anchor

        # Take-profit scaled to volatility + regime-adaptive
        # In trending regimes: 2.0x ATR (wide TP, let runners go)
        # In range regimes: 1.5x ATR (tight TP, mean-reversion snaps back fast)
        atr = _atr_pct(ctx, candidate.tf)
        regime_mult = 2.0 if regime_label.startswith("trending") else 1.5
        tp_dist = max(ctx.price * 0.010, ctx.price * atr * regime_mult)
        risk = abs(wave.entry_price - wave.anchor)
        tp_dist = max(tp_dist, risk * 1.2)
        wave.tp_price = (wave.entry_price + tp_dist) if candidate.side == "BUY" \
            else (wave.entry_price - tp_dist)

        # Cap total open waves
        if len(self.waves) >= self._max_open_waves:
            log.info("open skipped: %d open waves (cap %d)", len(self.waves), self._max_open_waves)
            return None

        # Charge and persist the open fee. The fee is part of net economics.
        if wallet is not None:
            wave._open_fee = wallet.charge_open_fee(notion)
        risk_per_r = notion * (abs(wave.entry_price - wave.anchor) / wave.entry_price) if wave.entry_price else 0.0
        wave.expected_gross = risk_per_r * 0.15
        wave.expected_cost = notion * ((wallet.open_fee_rate + wallet.close_fee_rate) if wallet else 0.0006)
        wave.expected_net = wave.expected_gross - wave.expected_cost

        wave.state = WaveState.SURFING
        self.waves[wave.wave_id] = wave

        if self.conn:
            try:
                log_wave_open(self.conn, wave)
            except Exception as e:
                log.warning("log_wave_open failed: %s", e)

        log.info("WAVE OPEN %s %s notional=%.2f lev=%dx conf=%.2f bias=%s(%.2f) ema15=%.5f ema1h=%.5f",
                 wave.side, wave.pair, notion, lev, confidence,
                 bias.direction, bias.strength, ctx.ema_15m, ctx.ema_1h)
        return wave

    # ── Per-tick surf ────────────────────────────────────────────────────

    def on_tick(self, wave: Wave, tick: Tick, ctx: TickContext,
                bias: BiasReading, confidence: float,
                zone_cache: SMCZoneCache) -> None:
        """Update wave state on every tick: re-derive live R, trail SL, refresh bias/conf."""
        if wave.state not in (WaveState.SURFING, WaveState.ENTERED):
            return

        # Update live metrics
        wave.live_r = self._calc_r(wave, tick.price)
        wave.peak_r = max(wave.peak_r, wave.live_r)
        wave.last_tick_ts = time.time()
        wave.bias = bias.direction
        wave.confidence = confidence
        try:
            wave.last_components = dict(bias.components or {})
        except Exception:
            pass

        # Structure for trailing
        sr = detect_structure(ctx, zone_cache, tick.price)
        wave.structure_score = sr.structure_score
        wave.mtf_confluence = sr.mtf_confluence

        # Trail SL — iter-1 profit-banking (fee-aware).
        #   peak_r >= 0.3 -> SL to +0.15R (bank a real win)
        #   peak_r >= 0.6 -> SL to +0.40R (bank more)
        #   peak_r >= 0.8 -> SL to +0.60R (lock most of a big wave)
        entry = wave.entry_price
        risk = abs(entry - wave.anchor) or 1.0
        if wave.peak_r >= 0.8:
            new_sl = entry + 0.6 * risk if wave.side == "BUY" else entry - 0.6 * risk
        elif wave.peak_r >= 0.6:
            new_sl = entry + 0.4 * risk if wave.side == "BUY" else entry - 0.4 * risk
        elif wave.peak_r >= self._breakeven_floor_r:
            new_sl = entry + 0.15 * risk if wave.side == "BUY" else entry - 0.15 * risk
        else:
            new_sl = None
        if new_sl is not None:
            if wave.side == "BUY":
                wave.sl_price = max(wave.sl_price, new_sl)
            else:
                wave.sl_price = min(wave.sl_price, new_sl)

        # Distance to invalidation in R
        if wave.entry_price != 0:
            dist = abs(wave.sl_price - tick.price)
            entry_dist = abs(wave.entry_price - wave.sl_price)
            wave.distance_to_invalidation = dist / entry_dist if entry_dist else 99.0

        # Telemetry
        if self.conn:
            try:
                append_telemetry(self.conn, {
                    "wave_id": wave.wave_id,
                    "ts": str(time.time()),
                    "mark_price": tick.price,
                    "live_r": wave.live_r,
                    "structure_score": wave.structure_score,
                    "distance_to_invalidation": wave.distance_to_invalidation,
                    "sl_price": wave.sl_price,
                    "partials_taken": wave.partials_taken,
                    "bias": bias.direction,
                    "confidence": confidence,
                    "ema_slope": sr.ema_slope,
                })
            except Exception as e:
                log.debug("telemetry append failed: %s", e)

    # ── Trail ─────────────────────────────────────────────────────────────

    def _trail(self, wave: Wave, tick: Tick, ctx: TickContext, confidence: float) -> None:
        """Trail stop-loss to new structure levels."""
        if wave.side == "BUY":
            new_sl = max(wave.sl_price, tick.price * 0.998)
            if confidence > 0.7:
                new_sl = max(new_sl, tick.price * 0.999)
            wave.sl_price = new_sl
        else:
            new_sl = min(wave.sl_price, tick.price * 1.002)
            if confidence > 0.7:
                new_sl = min(new_sl, tick.price * 1.001)
            wave.sl_price = new_sl

    # ── Jump-OUT ──────────────────────────────────────────────────────────

    def evaluate_exit(self, wave: Wave, tick: Tick, ctx: TickContext,
                      bias: BiasReading, confidence: float,
                      zone_cache: SMCZoneCache) -> Optional[WaveAction]:
        """Check if the wave should be closed.

        Returns WaveAction if exit triggered, else None.
        """
        now = time.time()

        # 0. Take-profit hits
        if wave.tp_price is not None:
            if wave.side == "BUY" and tick.price >= wave.tp_price:
                return WaveAction(type="CLOSE", reason="tp_hit", wave=wave, price=tick.price)
            if wave.side == "SELL" and tick.price <= wave.tp_price:
                return WaveAction(type="CLOSE", reason="tp_hit", wave=wave, price=tick.price)

        # iter-B-promotion: bank_08r at 0.20R, tp05 at 0.30R
        if wave.peak_r >= 0.20:
            return WaveAction(type="CLOSE", reason="bank_08r", wave=wave, price=tick.price)
        if wave.peak_r >= 0.30:
            return WaveAction(type="CLOSE", reason="tp05_hit", wave=wave, price=tick.price)

        # 0b. Reversal exit: wave was in profit but gave it all back
        if wave.peak_r >= 0.12 and wave.live_r < -0.02:
            return WaveAction(type="CLOSE", reason="reversal", wave=wave, price=tick.price)

        # 0c. Flat tape early exit — cuts fee bleed on dead trades
        # Only trigger when the wave NEVER made meaningful profit (peak < 0.05R)
        # and is deeply in loss. In counter-trade, price often goes -0.05 to
        # -0.08R before reverting, so a shallow dip is not a dead trade.
        # Data showed flat_tape_exit at -0.20R was too loose (13 trades,
        # -$0.2468): tighten to -0.15R so dead trades exit sooner at a
        # smaller loss rather than bleeding to max_age or loss_cut.
        if wave.peak_r <= 0.05 and wave.live_r <= -0.15:
            return WaveAction(type="CLOSE", reason="flat_tape_exit", wave=wave, price=tick.price)

        # 0d. Hard loss-cut
        if wave.live_r <= -self._loss_cut_r:
            return WaveAction(type="CLOSE", reason="loss_cut", wave=wave, price=tick.price)

        # 1. Anchor hit
        if wave.side == "BUY" and tick.price <= wave.sl_price:
            return WaveAction(type="CLOSE", reason="anchor_hit", wave=wave, price=tick.price)
        if wave.side == "SELL" and tick.price >= wave.sl_price:
            return WaveAction(type="CLOSE", reason="anchor_hit", wave=wave, price=tick.price)

        # 2. Bias flip against the wave — DISABLED for counter-trade mode.
        # In counter-trade, bias WILL flip against the position — that's the whole point.
        # We fade the bias, so bias flipping is expected, not an exit signal.
        # Only exit on real price action: loss_cut, anchor_hit, flat_tape, reversal, tp.

        # 3. Confidence collapse — DISABLED for counter-trade mode.
        # Same reasoning as bias_flip: confidence is tied to bias direction,
        # which will naturally drop as the mean-reversion plays out.
        # Let price action (loss_cut, flat_tape, reversal, tp) handle exits.

        # 4. SMC break
        if zone_cache:
            prov, conf, ztype = zone_cache.evaluate_break(
                wave.pair, tick.price, now, wave.side,
            )
            if conf:
                return WaveAction(type="CLOSE", reason=f"smc_break_{ztype}", wave=wave, price=tick.price)

        # 4b. Time-based partial profit exit (wave bot proven):
        #     if wave open >= 3m, peaked >= +0.03R, still in profit → close to lock gain
        if wave.open_ts and (now - wave.open_ts) >= 180:
            if wave.peak_r >= 0.03 and wave.live_r >= 0.03:
                return WaveAction(type="CLOSE", reason="partial_profit", wave=wave, price=tick.price)

        # 5. Anti-stuck: force-close after MAX_WAVE_AGE_S
        if wave.open_ts and (now - wave.open_ts) >= self._max_wave_age_s:
            return WaveAction(type="CLOSE", reason="max_age", wave=wave, price=tick.price)

        return None

    # ── Close ─────────────────────────────────────────────────────────────

    def close(self, wave: Wave, reason: str, price: float,
             wallet=None) -> Optional[dict]:
        """Close a wave, log it, charge close fee, credit PnL, start cooldown."""
        if wave.wave_id not in self.waves and wave.state in (
                WaveState.WAVE_BREAK, WaveState.KILL):
            return None

        wave.state = WaveState.WAVE_BREAK
        wave.close_reason = reason
        wave.closed_ts = time.time()
        wave.live_r = self._calc_r(wave, price)

        # Paper economics: close fee + realized PnL
        econ = {"pnl": 0.0, "gross": 0.0, "open_fee": getattr(wave, "_open_fee", 0.0), "close_fee": 0.0, "net": 0.0, "slippage": 0.0, "funding": 0.0}
        if wallet is not None and wave.notional > 0:
            close_fee = wallet.charge_close_fee(wave.notional)
            econ["close_fee"] = close_fee
            risk_per_r = wave.notional * (
                abs(wave.entry_price - wave.anchor) / wave.entry_price
            ) if wave.entry_price else 0.0
            gross = wave.live_r * risk_per_r
            # The open fee was already deducted at entry. Credit only the
            # close-time cashflow here so wallet balance is not double-charged.
            cashflow_net = gross - close_fee
            net = gross - econ["open_fee"] - close_fee
            econ["gross"] = round(gross, 4)
            wallet.credit_pnl(cashflow_net)
            econ["pnl"] = round(net, 4)
            econ["net"] = round(net, 4)

        if self.conn:
            try:
                log_wave_close(self.conn, wave, econ=econ)
                log_trade(self.conn, wave, econ)
            except Exception as e:
                log.warning("log_wave_close failed: %s", e)

        # Per-wave cooldown
        key = (wave.pair, wave.side)
        self.cooldowns[key] = time.time() + self._cooldown_s

        # Clean up
        self.waves.pop(wave.wave_id, None)
        self._break_start.pop(wave.wave_id, None)
        self._conf_break_start.pop(wave.wave_id, None)

        extra = ""
        if wallet is not None:
            extra = f" net={econ['net']:+.4f} bal={wallet.balance:.4f}"
        log.info("WAVE CLOSE %s %s reason=%s r=%.2f%s",
                 wave.side, wave.pair, reason, wave.live_r, extra)
        return econ

    # ── Scaling ───────────────────────────────────────────────────────────

    def maybe_scale(self, wave: Wave, ctx: TickContext, bias: BiasReading) -> Optional[WaveAction]:
        """Check if wave should be partially closed or added to."""
        if wave.state not in (WaveState.SURFING,):
            return None

        # Partial on stall
        if wave.structure_score < 0.3 and wave.live_r >= 0.5:
            return WaveAction(
                type="PARTIAL", reason="structure_stall",
                wave=wave, size=wave.size * PARTIAL_FRAC,
                price=ctx.price,
            )

        # Add on strong continuation
        if wave.structure_score > 0.7 and bias.strength > 0.7:
            notch = wave.size * 0.3
            return WaveAction(
                type="ADD", reason="continuation",
                wave=wave, size=notch, price=ctx.price,
            )

        return None

    # ── Cooldown ──────────────────────────────────────────────────────────

    def tick_cooldowns(self) -> None:
        """Purge expired cooldowns (wall-clock based since iter-7)."""
        now = time.time()
        for k in [k for k, exp in self.cooldowns.items() if exp <= now]:
            del self.cooldowns[k]

    def in_cooldown(self, pair: str, side: str) -> bool:
        """Check if (pair, side) is in cooldown."""
        exp = self.cooldowns.get((pair, side))
        return exp is not None and exp > time.time()

    # ── Kill all ──────────────────────────────────────────────────────────

    def kill_all(self, reason: str = "KILL") -> list[Wave]:
        """Force-close all waves."""
        closed = []
        for wid, wave in list(self.waves.items()):
            wave.state = WaveState.KILL
            wave.close_reason = reason
            wave.closed_ts = time.time()
            if self.conn:
                try:
                    log_wave_close(self.conn, wave)
                except Exception:
                    pass
            closed.append(wave)
        self.waves.clear()
        self._break_start.clear()
        self._conf_break_start.clear()
        return closed

    # ── Helpers ───────────────────────────────────────────────────────────

    def _calc_r(self, wave: Wave, price: float) -> float:
        """Calculate current R multiple."""
        if wave.entry_price == 0 or wave.anchor == 0:
            return 0.0
        risk = abs(wave.entry_price - wave.anchor)
        if risk == 0:
            return 0.0
        if wave.side == "BUY":
            return (price - wave.entry_price) / risk
        else:
            return (wave.entry_price - price) / risk

    def get_open_waves(self) -> list[Wave]:
        """Return all SURFING/ENTERED waves."""
        return [w for w in self.waves.values()
                if w.state in (WaveState.SURFING, WaveState.ENTERED)]

    def get_open_wave(self, pair: str) -> Optional[Wave]:
        """Return the open wave for a specific pair, or None."""
        for w in self.waves.values():
            if w.pair == pair and w.state in (WaveState.SURFING, WaveState.ENTERED):
                return w
        return None

    def get_open_count(self) -> int:
        return len(self.get_open_waves())