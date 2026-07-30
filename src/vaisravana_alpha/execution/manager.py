"""Wave manager — tick-driven lifecycle: surf, trail, jump-OUT, partial/add, cooldown."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from vaisravana_alpha.core.models import Wave, WaveState, BiasReading, Candidate, Tick, TickContext
from vaisravana_alpha.strategy.smc import SMCZoneCache
from vaisravana_alpha.strategy.bias import size_from_confidence
from vaisravana_alpha.storage.db import log_wave_open, log_wave_close, append_telemetry
from vaisravana_alpha.strategy.structure import detect_structure
from vaisravana_alpha.strategy.indicators import atr_pct as _atr_pct

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CONFIRM_MS = 0.25            # micro-confirmation (seconds)
CONF_EXIT_FLOOR = 0.10      # allow low-conf positions to survive (entry floor is 0.12);
                          # only exit on real confidence collapse, not minor dips
CONF_HOLD_MS = 5.0           # require conf below floor for 5s before exit (debounce)
COOLDOWN_S = float(os.getenv("VAISRAVANA_COOLDOWN_S", "600.0"))  # wall-clock seconds before same (pair, side) can re-enter.
# iter-7: cooldown was tick-based (600 ticks) but tick_cooldowns() ran once per
# tick per PAIR, so with ~20 pairs it decayed ~20x too fast (INJ re-opened 4x in
# 40s in run11). Wall-clock expiry makes the cooldown deterministic: 10 min.
MAX_OPEN_WAVES = int(os.getenv("VAISRAVANA_MAX_OPEN_WAVES", "8"))  # hard cap on concurrent waves (fee-bleed guard)
BREAKEVEN_FLOOR_R = 0.3      # once peak_r >= this, SL moves to breakeven (tight enough to actually lock 0)
LOSS_CUT_R = 0.35            # iter-8: hard loss-cut (was 0.5). Two run12 losers bled to
                              # -0.57/-0.60R and sat at max_age (reversal 0b never armed
                              # because peak never reached 0.2R). Cutting early caps
                              # per-wave tail risk instead of riding to the full 1.0R SL.
                              # Pure loss protection: only fires when live_r <= -LOSS_CUT_R,
                              # so it can NEVER touch a winner and cannot raise frequency.
                              # iter-16: tightened 0.5 -> 0.35 on telemetry counterfactual
                              # over runs24-28 (44 closes): only 1 wave ever traded below
                              # -0.3R and it did NOT recover (run25 loss_cut -0.506R);
                              # cutting at -0.35 would have saved +0.12R with 0 winners
                              # clipped. Caps worst-case tail ~30% tighter at ~zero
                              # in-sample cost (capital-preservation directive).
FLIP_STRENGTH = 0.30         # bias strength needed to confirm a flip
PARTIAL_FRAC = 0.35          # fraction to trim on stall
MAX_WAVE_AGE_S = int(os.getenv("VAISRAVANA_MAX_WAVE_AGE_S", "900"))  # force-close a wave after 15m if nothing else exits it (anti-stuck; prod floor)


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
    """

    # In-memory state
    waves: dict[str, Wave] = field(default_factory=dict)  # wave_id → Wave
    cooldowns: dict[tuple[str, str], float] = field(default_factory=dict)  # (pair, side) → expiry unix ts
    _break_start: dict[str, float] = field(default_factory=dict)  # wave_id → ts when flip detected
    _conf_break_start: dict[str, float] = field(default_factory=dict)

    # DB connection (set externally)
    conn = None

    # ── Open ──────────────────────────────────────────────────────────────

    def open(self, candidate: Candidate, bias: BiasReading,
             confidence: float, ctx: TickContext,
             surface, wallet=None) -> Optional[Wave]:
        """Open a new wave from a validated candidate.

        Sizes realistically (leverage + margin + Binance min-notional),
        sets SL + TP, charges the open taker fee, and stops if broke.
        """
        key = (candidate.pair, candidate.side)
        if key in self.cooldowns:
            log.debug("open rejected: %s in cooldown", key)
            return None

        # iter-9: never stack a duplicate wave on an already-live (pair, side).
        # on_tick calls open() every tick; without this, a live wave's same
        # pair+side re-opens every tick, each charging a phantom open fee
        # (run13 leaked 378 fee-events from 27 real opens -> 360 fake trades).
        for w in self.waves.values():
            if w.pair == candidate.pair and w.side == candidate.side \
                    and w.state in (WaveState.ENTERED, WaveState.SURFING):
                log.debug("open rejected: %s already live", key)
                return None

        # Guard: never open with a zero/unknown price (ctx not seeded yet).
        if not ctx.price or ctx.price <= 0:
            log.debug("open skipped: ctx.price=%.4f not ready", ctx.price)
            return None

        # ── Paper wallet: survival sizing + open fee ────────────────
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

        # ── Realistic sizing: leverage + margin + Binance min-notional ──
        lev = int(min(surface.max_leverage, getattr(surface, "trade_leverage", 3) or 3))
        lev = max(1, min(lev, 20))
        # Binance USDT-M min notional per pair (USD). Pairs not listed
        # default to 5 (covers most alts); majors are higher.
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
        # Clamp notional into [min_notional, balance] so tiny $10 acct
        # still meets exchange minimums without over-leveraging.
        notion = max(min_not, min(notion, wallet.balance if wallet else notion))
        margin = notion / lev                       # isolated margin used
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

        # Anchor (SL): ATR-based, at least 1.0% so choppy tape oscillation
        # does not stop you out before the wave forms. Wider than a fixed
        # 1% SL so normal noise does not clip every SELL in a sideways/up tape.
        atr = _atr_pct(ctx, candidate.tf)
        buffer = max(ctx.price * 0.010, ctx.price * atr * 1.8)
        if candidate.side == "BUY":
            wave.anchor = ctx.price - buffer
        else:
            wave.anchor = ctx.price + buffer
        wave.sl_price = wave.anchor

        # Take-profit scaled to volatility (reachable target, not fixed 1.5R
        # that the tape can never travel). Aim for ~2x ATR so winners actually
        # get hit in normal conditions; floor at 1.0% so calm pairs still pay.
        atr = _atr_pct(ctx, candidate.tf)
        tp_dist = max(ctx.price * 0.010, ctx.price * atr * 2.0)
        risk = abs(wave.entry_price - wave.anchor)
        # Keep R consistent: TP distance should be >= risk so a win pays >1R.
        tp_dist = max(tp_dist, risk * 1.2)
        wave.tp_price = (wave.entry_price + tp_dist) if candidate.side == "BUY" \
            else (wave.entry_price - tp_dist)

        # Cap total open waves so we don't over-trade (and burn the
        # paper balance on fees). Skip new entries past the cap.
        if len(self.waves) > MAX_OPEN_WAVES:
            log.info("open skipped: %d open waves (cap %d)", len(self.waves), MAX_OPEN_WAVES)
            return None

        # iter-9: charge the open taker fee ONLY after the wave is accepted
        # (past the live-dup guard above AND the cap above). Charging before
        # the cap caused cap-rejected waves to pay a phantom fee (360 fake
        # trades in run13). Now a fee is paid exactly once per real opening.
        if wallet is not None:
            wallet.charge_open_fee(notion)

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

        # Structure for trailing
        sr = detect_structure(ctx, zone_cache, tick.price)
        wave.structure_score = sr.structure_score
        wave.mtf_confluence = sr.mtf_confluence

        # Trail SL — iter-1 profit-banking (fee-aware). Fee breakeven is only
        # ~0.06R (6bps round-trip / ~1% SL distance), so banking at >=0.15R is
        # strongly net-positive. Lock REALIZED profit progressively so a wave
        # that peaks and retraces banks a win instead of round-tripping to a
        # scratch/loss (the run16 bleed pattern: avg peak 0.33R, final 0.04R).
        #   peak_r >= 0.3 -> SL to +0.15R (bank a real win, not just breakeven)
        #   peak_r >= 0.6 -> SL to +0.40R (bank more)
        #   peak_r >= 0.8 -> SL to +0.60R (lock most of a big wave)
        entry = wave.entry_price
        risk = abs(entry - wave.anchor) or 1.0
        if wave.peak_r >= 0.8:
            new_sl = entry + 0.6 * risk if wave.side == "BUY" else entry - 0.6 * risk
        elif wave.peak_r >= 0.6:
            new_sl = entry + 0.4 * risk if wave.side == "BUY" else entry - 0.4 * risk
        elif wave.peak_r >= BREAKEVEN_FLOOR_R:
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
            # Tighter trail when confidence is high
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
        Confirms all exits with CONFIRM_MS micro-persistence.
        """
        now = time.time()

        # 0. Take-profit hits (lets the account actually grow)
        #     tp_hit at +1.5R (full exit, ride target)
        #     tp05_hit at +0.5R (bank partial profit, don't give it all back)
        if wave.tp_price is not None:
            if wave.side == "BUY" and tick.price >= wave.tp_price:
                return WaveAction(type="CLOSE", reason="tp_hit", wave=wave, price=tick.price)
            if wave.side == "SELL" and tick.price <= wave.tp_price:
                return WaveAction(type="CLOSE", reason="tp_hit", wave=wave, price=tick.price)
        # iter-1 profit-bank, RETUNED iter-B-promotion (run21): the realized peak
        # band measured across 459 historical closes is ~0.10-0.16R (avgPeakR +0.12).
        # The original 0.8R/0.9R arms sat ABOVE that band, so bank_08r/tp05 never
        # fired and winners round-tripped to anchor_hit/max_age (-0.19R final).
        # Lower the arms into the empiric band so the exit can actually catch the
        # peaks that exist. 0.15R and 0.22R are still far above the ~0.06R fee
        # breakeven, so every bank close stays net-positive.
        if wave.peak_r >= 0.15:
            return WaveAction(type="CLOSE", reason="bank_08r", wave=wave, price=tick.price)
        if wave.peak_r >= 0.22:
            # bank a partial at +0.22R near the top of the realized band,
            # so the wave is given room to reach the full 1.5R TP first
            return WaveAction(type="CLOSE", reason="tp05_hit", wave=wave, price=tick.price)

        # 0b. Reversal exit: the wave was in profit (peak >= 0.12R, the realized
        # band) but has now given it all back to a near-scratch -0.04R. Close to
        # lock the scratch instead of riding to full SL or decaying into a negative
        # max_age close. Retuned from 0.2R peak / live_r<0 so it arms on the
        # common small-winners that bleed back, not just rare large winners.
        if wave.peak_r >= 0.12 and wave.live_r < -0.04:
            return WaveAction(type="CLOSE", reason="reversal", wave=wave, price=tick.price)

        # 0c. Hard loss-cut: if the wave is down >= 0.5R, close immediately.
        # Pure loss protection — only fires when live_r <= -0.5, so it can NEVER
        # close a winner. Catches losers that never peaked >=0.2R (so reversal 0b
        # never armed) but bled to half-loss; without this they ride to max_age
        # near the full 1.0R SL. iter-8: run12 had two such losers at -0.57/-0.60R.
        if wave.live_r <= -LOSS_CUT_R:
            return WaveAction(type="CLOSE", reason="loss_cut", wave=wave, price=tick.price)

        # 1. Anchor hit (price crossed SL)
        if wave.side == "BUY" and tick.price <= wave.sl_price:
            return WaveAction(type="CLOSE", reason="anchor_hit", wave=wave, price=tick.price)
        if wave.side == "SELL" and tick.price >= wave.sl_price:
            return WaveAction(type="CLOSE", reason="anchor_hit", wave=wave, price=tick.price)

        # 2. Bias flip against the wave
        flip = False
        if wave.side == "BUY" and bias.direction == "bearish" and bias.strength >= FLIP_STRENGTH:
            flip = True
        if wave.side == "SELL" and bias.direction == "bullish" and bias.strength >= FLIP_STRENGTH:
            flip = True

        if flip:
            wid = wave.wave_id
            if wid in self._break_start:
                if now - self._break_start[wid] >= CONFIRM_MS:
                    del self._break_start[wid]
                    return WaveAction(type="CLOSE", reason="bias_flip", wave=wave, price=tick.price)
            else:
                self._break_start[wid] = now
        else:
            self._break_start.pop(wave.wave_id, None)

        # 3. Confidence collapse
        if confidence < CONF_EXIT_FLOOR:
            wid = wave.wave_id
            if wid in self._conf_break_start:
                if now - self._conf_break_start[wid] >= CONF_HOLD_MS:
                    del self._conf_break_start[wid]
                    return WaveAction(type="CLOSE", reason="conf_collapse", wave=wave, price=tick.price)
            else:
                self._conf_break_start[wid] = now
        else:
            self._conf_break_start.pop(wave.wave_id, None)

        # 4. SMC break (matured CHoCH/BOS against the wave)
        if zone_cache:
            prov, conf, ztype = zone_cache.evaluate_break(
                wave.pair, tick.price, now, wave.side,
            )
            if conf:
                return WaveAction(type="CLOSE", reason=f"smc_break_{ztype}", wave=wave, price=tick.price)

        # 5. Anti-stuck: force-close after MAX_WAVE_AGE_S if nothing else exited.
        # Without this a sideways market leaves the wave open forever (balance frozen).
        if wave.open_ts and (now - wave.open_ts) >= MAX_WAVE_AGE_S:
            return WaveAction(type="CLOSE", reason="max_age", wave=wave, price=tick.price)

        return None

    # ── Close ─────────────────────────────────────────────────────────────

    def close(self, wave: Wave, reason: str, price: float,
             wallet=None, fraction: float = 1.0) -> Optional[dict]:
        """Close a wave (or a fraction of it), log, charge fee, credit PnL.

        `fraction` < 1.0 performs a partial close: the wave stays open with
        reduced size, and a proportional share of PnL and fee is booked. This
        is what the real-time exit engine uses when its confidence is moderate
        (close 50%, trail the rest) rather than maximal.

        Returns a dict of realized economics, or None if already gone.
        """
        if wave.wave_id not in self.waves and wave.state in (
                WaveState.WAVE_BREAK, WaveState.KILL, WaveState.CLOSE):
            return None

        fraction = max(0.0, min(1.0, fraction))
        full_close = fraction >= 0.999

        # For a partial close, reduce size but keep the wave alive.
        if not full_close and wave.size > 0:
            closed_size = wave.size * fraction
            wave.size -= closed_size
            wave.notional = wave.notional * (1.0 - fraction)
            # open_fee is sunk cost; only charge close fee on the closed part.
            close_notional = wave.notional + closed_size * (
                wave.entry_price if wave.entry_price else 0
            ) * 0  # placeholder; see below
            close_notional = closed_size * (wave.entry_price or 0)
        else:
            close_notional = wave.notional

        wave.state = WaveState.WAVE_BREAK if full_close else wave.state
        wave.close_reason = reason if full_close else f"{reason} (partial {fraction:.0%})"
        if full_close:
            wave.closed_ts = time.time()
        wave.live_r = self._calc_r(wave, price)

        # ── Paper economics ──
        econ = {"pnl": 0.0, "close_fee": 0.0, "net": 0.0}
        if wallet is not None and close_notional > 0:
            close_fee = wallet.charge_close_fee(close_notional)
            econ["close_fee"] = close_fee
            risk_per_r = close_notional * (
                abs(wave.entry_price - wave.anchor) / wave.entry_price
            ) if wave.entry_price else 0.0
            gross = wave.live_r * risk_per_r
            net = gross - close_fee
            wallet.credit_pnl(net)
            econ["pnl"] = round(net, 4)
            econ["net"] = round(net, 4)

        if full_close and self.conn:
            try:
                log_wave_close(self.conn, wave, econ=econ)
            except Exception as e:
                log.warning("log_wave_close failed: %s", e)

        # Feed realized net PnL to the adaptive throttle
        try:
            from vaisravana_alpha.strategy.survival import record_close
            record_close(econ["net"])
        except Exception:
            pass

        if full_close:
            key = (wave.pair, wave.side)
            self.cooldowns[key] = time.time() + COOLDOWN_S
            self.waves.pop(wave.wave_id, None)
            self._break_start.pop(wave.wave_id, None)
            self._conf_break_start.pop(wave.wave_id, None)
            extra = ""
            if wallet is not None:
                extra = f" net={econ['net']:+.4f} bal={wallet.balance:.4f}"
            log.info("WAVE CLOSE %s %s reason=%s r=%.2f%s",
                     wave.pair, wave.side, wave.close_reason,
                     wave.live_r, extra)
        else:
            log.info("WAVE PARTIAL %s %s fraction=%.0f%% live_r=%.2f",
                     wave.pair, wave.side, fraction * 100, wave.live_r)

        return econ

    # ── Scaling ───────────────────────────────────────────────────────────

    def maybe_scale(self, wave: Wave, ctx: TickContext, bias: BiasReading) -> Optional[WaveAction]:
        """Check if wave should be partially closed or added to.

        Returns PARTIAL on structure stall, ADD on continuation.
        """
        if wave.state not in (WaveState.SURFING,):
            return None

        # Partial on stall: low structure score despite decent R
        if wave.structure_score < 0.3 and wave.live_r >= 0.5:
            return WaveAction(
                type="PARTIAL", reason="structure_stall",
                wave=wave, size=wave.size * PARTIAL_FRAC,
                price=ctx.price,
            )

        # Add on strong continuation: structure improving + bias agrees
        if wave.structure_score > 0.7 and bias.strength > 0.7:
            notch = wave.size * 0.3  # max 30% add
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

    def get_open_wave(self, pair: str) -> Wave | None:
        """Return the open wave for a single pair, or None."""
        for w in self.waves.values():
            if w.pair == pair and w.state in (
                WaveState.SURFING, WaveState.ENTERED
            ):
                return w
        return None

    def get_open_count(self) -> int:
        return len(self.get_open_waves())
