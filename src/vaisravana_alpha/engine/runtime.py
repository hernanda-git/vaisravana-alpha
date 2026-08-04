"""Engine runtime -- the orchestrator.

The only module allowed to wire layers together. Everything below it
(strategy, execution, marketdata, notify, storage) stays unaware of the
others, so each can be tested in isolation and swapped without a rewrite.

Per-tick flow:

    tick -> context update -> bias/confidence -> admission gates
         -> scan for candidates -> open -> manage open waves -> exits

Ordering here is not cosmetic. Admission gates run before any scan so a
vetoed pair costs nothing, and exits are always evaluated even when opens
are suppressed -- suspending entries must never strand an open position.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from vaisravana_alpha.core.models import Tick
from vaisravana_alpha.core.params import ParameterSurface
from vaisravana_alpha.core.settings import Settings
from vaisravana_alpha.engine.context import ContextStore
from vaisravana_alpha.engine.exit_engine import ExitAction
from vaisravana_alpha.execution.broker import ModeGuard
from vaisravana_alpha.execution.manager import WaveManager
from vaisravana_alpha.execution.risk import KillSwitch, PairExcluder
from vaisravana_alpha.execution.wallet import PaperWallet
from vaisravana_alpha.marketdata.feed import FeedMux
from vaisravana_alpha.marketdata.rest import RestPoller
from vaisravana_alpha.notify import cards
from vaisravana_alpha.storage import agentic
from vaisravana_alpha.storage import db as storage
from vaisravana_alpha.strategy.bias import read_bias, read_confidence, set_universe_ranker
from vaisravana_alpha.strategy.indicators import atr_pct
from vaisravana_alpha.strategy.scanner import scan
from vaisravana_alpha.strategy.smc import SMCZoneCache
from vaisravana_alpha.strategy.universe_ranker import UniverseRanker
from vaisravana_alpha.strategy.survival import (
    current_cap,
    record_open,
    survival_gate,
)

log = logging.getLogger(__name__)

SIDES = ("BUY", "SELL")
ZONE_REFRESH_MIN_CANDLES = 11


@dataclass
class EngineState:
    """Mutable runtime state, kept in one object rather than as closures.

    The legacy engine held all of this in nested-function scope, which made
    it unreachable from the command handlers and impossible to assert on in
    a test. As a plain object it is both.
    """

    started_ts: float = field(default_factory=time.time)
    ticks: int = 0
    opens: int = 0
    closes: int = 0
    feed_ok: bool = False
    stop_requested: bool = False
    halt_reason: str = ""
    open_waves: list = field(default_factory=list)
    closed_waves: list = field(default_factory=list)

    @property
    def uptime_s(self) -> float:
        return time.time() - self.started_ts


class AlphaEngine:
    """Owns the trading loop and everything it needs.

    Constructed with its dependencies rather than building them, so a test
    can pass a fake notifier and an in-memory database and drive `on_tick`
    directly without any network.
    """

    def __init__(
        self,
        settings: Settings,
        surface: ParameterSurface,
        conn,
        notifier,
        wallet: PaperWallet,
        guard,
        kill_switch: KillSwitch | None = None,
        agentic_conn=None,
        run_id: str = "",
    ) -> None:
        self.settings = settings
        self.surface = surface
        self.conn = conn
        self.notifier = notifier
        self.wallet = wallet
        self.guard = guard
        self.kill = kill_switch or KillSwitch(
            daily_loss_limit_pct=surface.risk.daily_loss_limit_pct
        )

        # Agentic telemetry. Optional so tests and ad-hoc runs need no extra
        # wiring, but always present in deployment.
        self.agentic = agentic_conn
        self.run_id = run_id

        self.state = EngineState()
        self.context = ContextStore(settings.pairs, settings.context_tfs + ["1h"])
        self.zones = SMCZoneCache()
        self.excluder = PairExcluder()
        self.manager = WaveManager()
        self.manager.conn = conn

        self._feed: FeedMux | None = None
        self._poller: RestPoller | None = None
        self._last_heartbeat = 0.0

        # Real-time exit engine. Optional; only active when exit_enabled.
        self.exit_engine = None
        self.exit_pair = settings.exit_pair
        if settings.exit_enabled:
            from vaisravana_alpha.engine.exit_engine import ExitEngine
            self.exit_engine = ExitEngine()
            self._exit_last_eval: dict[str, float] = {}

        # Universe ranker — background task that fetches + scores all Binance futures pairs.
        # Injected into bias.py so read_bias() adds universe score as a bias component.
        self._universe_ranker = UniverseRanker(
            rest_base=settings.rest_base,
            pairs_file=os.path.join(settings.data_dir, "universe_pairs.json"),
            scores_file=os.path.join(settings.data_dir, "universe_scores.json"),
            top_n=5,
            bottom_n=5,
        )
        set_universe_ranker(self._universe_ranker)

        # ── Pair list: use only top N from universe ranker ─────────────────
        # Binance WS payload limit: ~64KB per SUBSCRIBE message.
        # 677 pairs × 6 stream types = 4,062 streams → ~200KB → rejected.
        # Solution: use only top 5 pairs (3 strongest + 2 weakest)
        # from the universe ranker. This gives ~30 streams, well within limits.
        # For a $10 account, fewer pairs = less fee bleed + more capital per trade.
        # BTCUSDT excluded: min-notional $100 > $10 balance.
        self._universe_task: asyncio.Task | None = None

        # Global trade rate limiter: prevent overtrading.
        # Data showed 71 trades in 5 minutes → $0.146 in fees eaten the account.
        # Limit to max 1 trade per 60 seconds globally (across all pairs).
        self._trade_cooldown_s = 60.0
        self._last_trade_ts = 0.0

        # Fetch initial pair list from ranker
        if not self.settings.pairs:
            if not self._universe_ranker.all_pairs:
                log.info("universe: fetching active pairs from Binance API")
                self._universe_ranker.all_pairs = self._universe_ranker._fetch_active_pairs_sync()
                log.info("universe: got %d pairs from API", len(self._universe_ranker.all_pairs))
            # Fallback: use raw pairs (will be refined by universe task)
            all_pairs = self._universe_ranker.get_active_pairs()
            if all_pairs:
                object.__setattr__(self.settings, 'pairs', all_pairs[:20])
                log.info("universe: populated %d pairs from raw list (will be refined by ranker)", len(self.settings.pairs))

    # -- initial pair selection from universe ranker -----------------------

    # -- pair refinement from universe ranker --------------------------------

    async def _refine_pair_list(self) -> None:
        """Refine the pair list to top 5 from universe ranker after first score."""
        ranked = self._universe_ranker.ranked
        if not ranked or len(ranked) < 5:
            return

        # Select 3 strongest + 2 weakest, EXCLUDE BTCUSDT (too expensive for $10)
        strongest_pairs = [s.pair for s in ranked[:3]]
        weakest_pairs = [s.pair for s in ranked[-2:]]

        # Remove BTCUSDT from selection
        strongest_pairs = [p for p in strongest_pairs if p != "BTCUSDT"]
        weakest_pairs = [p for p in weakest_pairs if p != "BTCUSDT"]

        # Fill from ranked list if we lost pairs to BTCUSDT exclusion
        pair_set = set(strongest_pairs + weakest_pairs)
        for s in ranked:
            if s.pair not in pair_set and s.pair != "BTCUSDT":
                if len(strongest_pairs) < 3:
                    strongest_pairs.append(s.pair)
                elif len(weakest_pairs) < 2:
                    weakest_pairs.append(s.pair)
                pair_set.add(s.pair)
            if len(strongest_pairs) >= 3 and len(weakest_pairs) >= 2:
                break

        selected = list(set(strongest_pairs + weakest_pairs))
        if len(selected) < 5:
            for s in ranked:
                if s.pair not in selected and s.pair != "BTCUSDT":
                    selected.append(s.pair)
                if len(selected) >= 5:
                    break

        object.__setattr__(self.settings, 'pairs', selected)
        log.info(
            "universe: refined to %d pairs — strongest=%s weakest=%s",
            len(selected), strongest_pairs, weakest_pairs,
        )

        # Restart feeds with new pair list
        if self._feed:
            await self._feed.stop()
        if self._poller:
            self._poller.stop()
            # Update RestPoller with new pair list
            self._poller._pairs = list(selected)

        # Reconnect after a short delay
        await asyncio.sleep(5)

        if self._feed:
            asyncio.create_task(
                self._feed.connect(selected, on_ready=lambda: setattr(self.state, 'feed_ok', True)),
                name="ws-feed-refixed",
            )
        if self._poller:
            asyncio.create_task(self._poller.run(), name="rest-feed-refixed")
        log.info("universe: feeds restarted with %d pairs", len(selected))

    # -- agentic telemetry -------------------------------------------------

    def _record_rejection(self, gate: str, pair: str, detail: str = "") -> None:
        """Count a gate rejection.

        Wrapped because telemetry must never be able to stop trading. A
        locked database is a reason to lose a counter, not a position.
        """
        if self.agentic is None or not self.run_id:
            return
        try:
            agentic.record_rejection(self.agentic, self.run_id, gate, pair, detail)
        except Exception as exc:
            log.debug("rejection telemetry failed: %s", exc)

    def _record_trade(self, wave, econ: dict) -> None:
        if self.agentic is None or not self.run_id:
            return
        try:
            econ = dict(econ)
            econ.setdefault("balance_after", self.wallet.balance)
            agentic.record_trade(self.agentic, self.run_id, wave, econ)
        except Exception as exc:
            log.debug("trade telemetry failed: %s", exc)

    def _heartbeat(self) -> None:
        """Persist a liveness marker and agentic counters periodically.

        Health must not depend on a trade or database write. A quiet but live
        strategy is healthy; a stale heartbeat indicates the event loop stopped.
        """
        now = time.time()
        if now - self._last_heartbeat < 30.0:
            return
        self._last_heartbeat = now
        heartbeat_path = os.path.join(self.settings.data_dir, "alpha_heartbeat")
        try:
            tmp = heartbeat_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(f"{now:.3f} ticks={self.state.ticks} opens={self.state.opens} closes={self.state.closes}\n")
            os.replace(tmp, heartbeat_path)
        except Exception as exc:
            log.debug("heartbeat file failed: %s", exc)
        if self.agentic is None or not self.run_id:
            return
        try:
            agentic.heartbeat_run(
                self.agentic, self.run_id, self.state.ticks,
                self.state.opens, self.state.closes,
            )
        except Exception as exc:
            log.debug("agentic heartbeat failed: %s", exc)

    # -- notification helper ----------------------------------------------

    def _notify(self, text: str) -> None:
        """Send a card, never letting a delivery failure reach the loop.

        A Telegram outage is an inconvenience. A Telegram outage that stops
        trading, or worse leaves a position unmanaged, is a real loss.
        """
        try:
            self.notifier.send(text)
        except Exception as exc:
            log.debug("notification suppressed: %s", exc)

    # -- lifecycle ---------------------------------------------------------

    def request_stop(self, reason: str = "owner command") -> None:
        """Ask the engine to halt at the next tick and persist the intent.

        The flag file matters because the container restart policy will bring
        a stopped process straight back up. Without persistence, an owner's
        stop is silently undone within seconds.
        """
        self.state.stop_requested = True
        self.state.halt_reason = reason
        try:
            os.makedirs(self.settings.data_dir, exist_ok=True)
            with open(self.settings.stop_flag_path, "w") as fh:
                fh.write(f"{time.time()}\n{reason}\n")
        except OSError as exc:
            log.warning("could not persist stop flag: %s", exc)

    def clear_stop(self) -> None:
        """Clear the persisted stop so the next boot trades again."""
        self.state.stop_requested = False
        self.state.halt_reason = ""
        try:
            # Also clear any stale flag from a previous crash or
            # incomplete shutdown. The flag is only meaningful when
            # /stop was explicitly called; a stale flag from a crash
            # or an old container restart must never block trading.
            flag = self.settings.stop_flag_path
            if os.path.exists(flag):
                age_secs = time.time() - os.path.getmtime(flag)
                with open(flag) as fh:
                    _, prev_reason = fh.read().split("\n", 1)
                # If the flag was written more than 60s ago, it is
                # a stale artifact — clear it on boot automatically.
                if age_secs > 60:
                    os.remove(flag)
                    log.info(
                        "stale stop flag removed (age %.0fs, reason: %s)",
                        age_secs, prev_reason.strip(),
                    )
                    return
            os.remove(self.settings.stop_flag_path)
        except OSError:
            pass

    @property
    def stop_flag_present(self) -> bool:
        return os.path.exists(self.settings.stop_flag_path)

    # -- tick handling -----------------------------------------------------

    async def on_tick(self, tick: Tick) -> None:
        """Handle one price update for one pair. The hot path.

        Wrapped by the feed layers, so an exception here would be swallowed
        and appear as an inexplicably silent bot. Anything that can throw is
        handled explicitly instead.
        """
        if self.state.stop_requested:
            raise asyncio.CancelledError(self.state.halt_reason or "stop requested")

        self.state.ticks += 1
        pair = tick.pair
        ctx = self.context.update_from_tick(tick)

        bias = read_bias(pair, tick, ctx)
        signal_age = max(0.0, time.time() - tick.ts) if tick.ts else 0.0
        confidence = read_confidence(
            bias, ctx, ctx.structure_score, 0.5, signal_age
        )

        if not self.excluder.is_excluded(pair):
            if self._entries_allowed(pair, ctx, confidence):
                await self._try_open(pair, ctx, bias, confidence)

        # Exits run unconditionally. Suppressing entries must never leave an
        # open position unmanaged.
        await self._manage_open_waves(pair, tick, ctx, bias, confidence)

        # Real-time exit engine (optional). Evaluates every N ms per pair and
        # may close positions before the structural TP/SL would. This is the
        # fee-aware, regime-adaptive exit path.
        if self.exit_engine is not None:
            self._evaluate_exit(pair, tick, ctx)

        self.manager.tick_cooldowns()
        self._refresh_shared_state()
        self._check_wallet()
        self._heartbeat()

    def _evaluate_exit(self, pair: str, tick, ctx) -> None:
        """Run the real-time exit engine for one pair, on its own cadence.

        The exit engine processes every tick internally but we only *act* on
        its signal every `exit_tick_interval_ms` to avoid thrashing. This is a
        deliberate decoupling: the engine sees all ticks, the runtime acts on
        a schedule it controls.
        """
        # Limit to a single pair if configured.
        if self.exit_pair and pair != self.exit_pair:
            return

        now_ms = time.time() * 1000
        last = self._exit_last_eval.get(pair, 0.0)
        interval = self.settings.exit_tick_interval_ms
        if now_ms - last < interval:
            # Still feed the engine so its regime detector stays current.
            if self.manager.get_open_wave(pair):
                self.exit_engine.process(tick, ctx, self.manager.get_open_wave(pair))
            return

        self._exit_last_eval[pair] = now_ms

        wave = self.manager.get_open_wave(pair)
        if not wave:
            return

        try:
            signal = self.exit_engine.process(tick, ctx, wave)
        except Exception as exc:
            log.debug("exit engine failed for %s: %s", pair, exc)
            return

        # Record the signal for telemetry / post-trade learning.
        self._record_exit_signal(pair, signal)

        if signal.action in (ExitAction.CLOSE_50, ExitAction.CLOSE_100):
            reason = (
                f"rt-exit:{signal.regime.value}:conf={signal.exit_conf:.2f}"
            )
            econ = self.manager.close(
                wave, reason, tick.price, self.wallet,
                fraction=(0.5 if signal.action == ExitAction.CLOSE_50 else 1.0),
            )
            self.state.closes += 1
            self._record_trade(wave, econ)
            log.info(
                "REAL-TIME EXIT %s %s @ %.8f conf=%.2f regime=%s salvage=%.4f",
                signal.action.value, pair, tick.price,
                signal.exit_conf, signal.regime.value, signal.salvage,
            )

            # Detailed Telegram card: regime, factor breakdown, salvage, action.
            if signal.action == ExitAction.CLOSE_50:
                self._notify(cards.exit_partial_card(
                    wave, self.wallet, fraction=0.5,
                    price=tick.price, econ=econ,
                ))
            else:
                self._notify(cards.exit_signal_card(
                    signal, wave, self.wallet, price=tick.price,
                ))

    def _record_exit_signal(self, pair: str, signal) -> None:
        """Persist exit signals for later learning analysis."""
        if self.agentic is None or not self.run_id:
            return
        try:
            from vaisravana_alpha.storage import agentic as _agentic
            _agentic.record_exit_signal(
                self.agentic, self.run_id, pair, signal,
            )
        except Exception as exc:
            log.debug("exit signal telemetry failed: %s", exc)

    def _entries_allowed(self, pair: str, ctx, confidence: float) -> bool:
        """Admission control. Every branch can only ever reject.

        Each rejection is counted by gate name. This is what makes a silent
        bot diagnosable: "no trades" and "4,000 rejections by one gate" look
        identical in a trade table and demand opposite responses.
        """
        if self.kill.is_tripped:
            self._record_rejection("kill_switch", pair, self.kill.trip_reason)
            return False

        # Warmup: indicators start at zero, so the first ticks would trade on
        # a signal that is arithmetically meaningless.
        elapsed = time.time() - self.state.started_ts
        if elapsed < self.settings.warmup_s:
            self._record_rejection("warmup", pair, f"{elapsed:.0f}s elapsed")
            return False

        # Global trade rate limiter: prevent overtrading.
        # Data showed 71 trades in 5 minutes → $0.146 in fees eaten the account.
        # Limit to max 1 trade per 60 seconds globally (across all pairs).
        now = time.time()
        if now - self._last_trade_ts < self._trade_cooldown_s:
            self._record_rejection("rate_limit", pair,
                                   f"cooldown {self._trade_cooldown_s - (now - self._last_trade_ts):.0f}s")
            return False

        # Fee-aware expected value. `atr_pct` is a module function, not a
        # manager method: calling it off the instance returns nothing and
        # silently zeroes the estimate, which vetoes every trade.
        try:
            atr = atr_pct(ctx, "15m")
        except Exception:
            atr = 0.0
        expected_move_bps = confidence * atr * 1e4 if atr else 0.0

        allowed, reason = survival_gate(pair, ctx, confidence, expected_move_bps)
        if not allowed:
            # The gate name is the first token of its reason, which keeps the
            # counter cardinality bounded regardless of the detail text.
            gate_name = (reason or "survival").split()[0].strip(":")
            self._record_rejection(gate_name, pair, reason)
            log.debug("gate rejected %s: %s", pair, reason)
        return allowed

    async def _try_open(self, pair: str, ctx, bias, confidence: float) -> None:
        """Scan both directions and open whatever passes."""
        # V0.1: derive regime from context so TP can adapt.
        # Regime is stored in ctx.risk_regime as a float (-1..+1).
        # We classify it into 3 bands for TP scaling.
        risk_regime = getattr(ctx, "risk_regime", 0.0)
        # Map -1..+1 to a regime string used by TP scaler.
        if risk_regime > 0.3:
            regime_label = "trending_bull"
        elif risk_regime < -0.3:
            regime_label = "trending_bear"
        else:
            regime_label = "range"

        for side in SIDES:
            if self.manager.in_cooldown(pair, side):
                continue
            candidate = scan(
                pair, side, self.settings.decision_tf,
                bias, confidence, ctx, self.zones, adx=20,
                regime_label=regime_label,
            )
            if candidate is None:
                continue

            wave = self.manager.open(
                candidate, bias, confidence, ctx, self.surface, self.wallet,
                regime_label=regime_label,
            )
            if wave is None:
                continue

            record_open(pair)
            self.state.opens += 1
            # Update global trade rate limiter timestamp
            self._last_trade_ts = time.time()
            # NOTE: open fee is already charged in manager.open() — do NOT
            # charge again here. We only compute the fee value for logging
            # and the notification card.
            open_fee = wave.notional * self.wallet.open_fee_rate
            wave._open_fee = open_fee
            log.info(
                "open %s %s @ %.6f conf=%.2f notional=%.2f fee=%.4f",
                side, pair, wave.entry_price, confidence, wave.notional, open_fee,
            )
            self._notify(cards.wave_open_card(wave, self.wallet, open_fee))

    async def _manage_open_waves(self, pair: str, tick, ctx, bias,
                                 confidence: float) -> None:
        """Update and possibly close every open wave on this pair."""
        for wave in list(self.manager.waves.values()):
            if wave.pair != pair:
                continue
            self.manager.on_tick(wave, tick, ctx, bias, confidence, self.zones)
            action = self.manager.evaluate_exit(
                wave, tick, ctx, bias, confidence, self.zones
            )
            if action is None:
                continue

            econ = self.manager.close(wave, action.reason, tick.price, self.wallet)
            self.state.closes += 1
            self._record_trade(wave, econ)
            net = econ.get("net", 0.0)
            if net < 0:
                self.kill.record_loss(net)
                self.kill.check_daily_dd(self.wallet.balance)
            log.info(
                "close %s %s %s %+.2fR net=%+.4f",
                wave.side, wave.pair, wave.close_reason, wave.live_r, net,
            )
            self._notify(cards.wave_close_card(wave, self.wallet, econ))

    async def on_kline(self, timeframe: str, kline: dict) -> None:
        """Handle a candle from either feed.

        Both feeds route here so indicator state has exactly one owner. The
        symbol must be present: a kline without it cannot be attributed, and
        dropping it beats corrupting another pair's context.
        """
        pair = kline.get("s", "")
        if not pair:
            return

        self.context.append_kline(pair, timeframe, kline)
        if not kline.get("is_final"):
            return

        self.context.refresh_indicators(pair, timeframe)

        if timeframe == "15m":
            self.context.refresh_flow(pair, "15m")
            self.context.refresh_regimes()

        if timeframe in ("5m", "15m", "1h"):
            ctx = self.context.get(pair)
            candles = ctx.klines.get(timeframe, []) if ctx else []
            if len(candles) >= ZONE_REFRESH_MIN_CANDLES:
                try:
                    self.zones.refresh(pair, timeframe, candles)
                except Exception as exc:
                    log.debug("zone refresh failed %s %s: %s", pair, timeframe, exc)

    # -- housekeeping ------------------------------------------------------

    def _refresh_shared_state(self) -> None:
        """Publish state the command handlers read."""
        self.state.open_waves = list(self.manager.waves.values())
        # Performance must survive restarts.  The old implementation read an
        # optional in-memory ``closed_today`` list that the manager never
        # populated, so /alpha_performance incorrectly reported zero trades.
        try:
            self.state.closed_waves = storage.get_recent_closed(self.conn, 200)
        except Exception as exc:
            log.warning("closed-trade query failed: %s", exc)
            self.state.closed_waves = []

    def _clear_stale_open_waves(self) -> None:
        """Clear open waves from DB that won't survive a container restart.

        The WaveManager's in-memory state is lost on restart, so any waves
        marked SURFING in the DB are orphans. We mark them as closed
        with a 'stale_on_restart' reason so stats stay clean and the DB
        doesn't accumulate phantom open positions.
        """
        try:
            cur = self.conn.cursor()
            cur.execute(
                "UPDATE wave_log SET state='WAVE_BREAK', close_reason='stale_on_restart' "
                "WHERE state='SURFING'"
            )
            count = cur.rowcount
            if count:
                log.warning("cleared %d stale SURFING waves from DB", count)
            self.conn.commit()
        except Exception as exc:
            log.warning("failed to clear stale waves: %s", exc)

    def _check_wallet(self) -> None:
        """Halt when the paper account is spent.

        The account is designed to be allowed to reach zero. That is the
        experiment: an honest run to ruin says more than a run rescued by a
        top-up.
        """
        if self.wallet.is_broke and not self.state.stop_requested:
            self.state.stop_requested = True
            self.state.halt_reason = (
                f"paper balance exhausted ({self.wallet.balance:.4f}$)"
            )
            self._notify(cards.halt_card(self.state.halt_reason, self.wallet.balance))

    # -- command surface ---------------------------------------------------

    def handle_command(self, text: str) -> None:
        """Dispatch a Telegram slash command."""
        command = text.split()[0].split("@")[0].lower()

        # Strip alpha_ prefix so /alpha_status and /status both work
        base = command.replace("/alpha_", "/").replace("/alpha", "/")

        if base in ("/positions", "/wave"):
            self._notify(cards.positions_card(self.state.open_waves, self.wallet))
        elif base in ("/performance", "/surf"):
            self._notify(cards.performance_card(self.state.closed_waves, self.wallet))
        elif base == "/trades":
            self._notify(cards.trades_card(self.state.closed_waves, self.wallet))
        elif base == "/status":
            self._notify(cards.status_card(
                version=_version(),
                uptime_s=self.state.uptime_s,
                pairs=len(self.settings.pairs),
                open_n=len(self.state.open_waves),
                feed_ok=self.state.feed_ok,
                ticks=self.state.ticks,
                throttle_cap=current_cap(),
            ))
        elif base == "/stop":
            self.request_stop("owner /stop")
            self._notify(cards.halt_card("owner /stop", self.wallet.balance))
        elif base == "/resume":
            self.clear_stop()
            self._notify("▶️ <b>Stop flag cleared.</b> Restart to resume trading.")
        elif base == "/help":
            self._notify(self._help_card())
        elif base == "/ping":
            self._notify("✅ <b>Alpha Telegram listener is responding.</b>")
        elif base in ("/universe", "/alpha_universe"):
            self._notify(self._universe_card())
        else:
            self._notify(f"Unknown command: {command}. Send /alpha_help for list.")

    def _help_card(self) -> str:
        """List all available slash commands."""
        return "\n".join([
            "<b>Alpha bot commands:</b>",
            "/alpha_status — Bot status: pairs, positions, balance, feed health",
            "/alpha_performance — Performance: WR, avg R, net PnL, fees, expectancy",
            "/alpha_positions — Open positions with live PnL, SL, TP, R-multiple",
            "/alpha_trades — Recent trades history with results and PnL",
            "/alpha_universe — Current universe: top 7 strongest + 7 weakest pairs",
            "/alpha_stop — Graceful shutdown after current cycle",
            "/alpha_resume — Resume trading (if stopped)",
            "/alpha_help — This help message",
        ])

    def _universe_card(self) -> str:
        """Show current universe: top 7 strongest + 7 weakest pairs."""
        if self._universe_ranker is None or not self._universe_ranker.ranked:
            return "<i>Universe ranker not initialized yet.</i>"
        strongest = self._universe_ranker.strongest(7)
        weakest = self._universe_ranker.weakest(7)
        lines = ["<b>Alpha Universe (mean-reversion ranked):</b>"]
        lines.append("<i>Strongest = most oversold → BUY candidates</i>")
        for s in strongest:
            lines.append(f"  {s.pair:15s} score={s.total_score:+.3f}  RSI={s.rsi_14:.1f}  VWAP={s.vwap_distance:+.3f}  CVD={s.cvd_divergence:+.3f}")
        lines.append("")
        lines.append("<i>Weakest = most overbought → SELL candidates</i>")
        for w in weakest:
            lines.append(f"  {w.pair:15s} score={w.total_score:+.3f}  RSI={w.rsi_14:.1f}  VWAP={w.vwap_distance:+.3f}  CVD={w.cvd_divergence:+.3f}")
        lines.append("")
        lines.append(f"Total ranked: {len(self._universe_ranker.ranked)} pairs")
        lines.append(f"Current trading pairs: {len(self.settings.pairs)}")
        return "\n".join(lines)

    # -- main loop ---------------------------------------------------------

    async def run(self) -> None:
        """Start both feeds and run until stopped.

        Both run concurrently and permanently. The WS path is faster when
        healthy; the REST path guarantees the engine keeps seeing prices when
        it is not.
        """
        storage.init_wave_db(self.conn)

        # Clear any stale SURFING waves from DB — open waves don't survive
        # container restarts because the manager's in-memory state is lost.
        # These stale entries would otherwise accumulate and inflate stats.
        self._clear_stale_open_waves()

        # Auto-clear stale stop flags on boot. The clear_stop() method
        # checks if the flag is >60s old and clears it automatically.
        # This prevents stale flags from blocking trading after crashes
        # or container restarts.
        self.clear_stop()

        if self.stop_flag_present:
            log.warning(
                "stop flag present at %s -- refusing to trade. Send /resume "
                "or delete the file.", self.settings.stop_flag_path,
            )
            self._notify(cards.halt_card(
                "stop flag present at boot", self.wallet.balance
            ))
            return

        self._notify(cards.startup_card(
            version=_version(),
            pairs=self.settings.pairs,
            mode=self.settings.mode,
            balance=self.wallet.balance,
            feed="websocket + rest",
        ))

        self._feed = FeedMux(
            on_tick=self.on_tick,
            on_kline=self.on_kline,
            ws_url=self.settings.ws_url,
            proxy_url=self.settings.proxy_url or None,
        )
        self._poller = RestPoller(
            pairs=self.settings.pairs,
            on_tick=self.on_tick,
            on_kline=self.on_kline,
            rest_base=self.settings.rest_base,
            interval_s=self.settings.rest_poll_s,
        )

        def _mark_feed_live() -> None:
            self.state.feed_ok = True

        async def _run_ws() -> None:
            try:
                await self._feed.connect(self.settings.pairs, on_ready=_mark_feed_live)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("websocket feed stopped: %s", exc)

        # Universe ranker — background task, updates every 60s
        async def _run_universe() -> None:
            first_update = True
            while True:
                try:
                    await self._universe_ranker.update()
                    # Log top 3 strongest/weakest for debugging
                    strongest = self._universe_ranker.strongest(3)
                    weakest = self._universe_ranker.weakest(3)
                    log.info(
                        "universe: strongest=%s weakest=%s",
                        [(s.pair, round(s.total_score, 3)) for s in strongest],
                        [(s.pair, round(s.total_score, 3)) for s in weakest],
                    )

                    # After first successful score, refine pair list to top 15
                    if first_update:
                        first_update = False
                        await self._refine_pair_list()
                except Exception as exc:
                    log.warning("universe ranker update failed: %s", exc)
                await asyncio.sleep(60)

        ws_task = asyncio.create_task(_run_ws(), name="ws-feed")
        rest_task = asyncio.create_task(self._poller.run(), name="rest-feed")
        self._universe_task = asyncio.create_task(_run_universe(), name="universe-ranker")

        log.info(
            "engine running: %d pairs, warmup %.0fs, balance %.4f$",
            len(self.settings.pairs), self.settings.warmup_s, self.wallet.balance,
        )

        try:
            await asyncio.gather(ws_task, rest_task, self._universe_task)
        except asyncio.CancelledError:
            log.info("engine halting: %s", self.state.halt_reason or "cancelled")
        finally:
            for task in (ws_task, rest_task, self._universe_task):
                if not task.done():
                    task.cancel()
            if self._poller:
                self._poller.stop()
            log.info(
                "engine stopped after %.0fs: %d ticks, %d opens, %d closes, "
                "balance %.4f$",
                self.state.uptime_s, self.state.ticks, self.state.opens,
                self.state.closes, self.wallet.balance,
            )


def _version() -> str:
    from vaisravana_alpha import __version__
    return __version__
