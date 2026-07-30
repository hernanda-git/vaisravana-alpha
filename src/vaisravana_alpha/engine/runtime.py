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
from vaisravana_alpha.execution.manager import WaveManager
from vaisravana_alpha.execution.risk import KillSwitch, PairExcluder
from vaisravana_alpha.execution.wallet import PaperWallet
from vaisravana_alpha.marketdata.feed import FeedMux
from vaisravana_alpha.marketdata.rest import RestPoller
from vaisravana_alpha.notify import cards
from vaisravana_alpha.storage import db as storage
from vaisravana_alpha.strategy.bias import read_bias, read_confidence
from vaisravana_alpha.strategy.indicators import atr_pct
from vaisravana_alpha.strategy.scanner import scan
from vaisravana_alpha.strategy.smc import SMCZoneCache
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

        self.state = EngineState()
        self.context = ContextStore(settings.pairs, settings.context_tfs + ["1h"])
        self.zones = SMCZoneCache()
        self.excluder = PairExcluder()
        self.manager = WaveManager()
        self.manager.conn = conn

        self._feed: FeedMux | None = None
        self._poller: RestPoller | None = None

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
        self.manager.tick_cooldowns()
        self._refresh_shared_state()
        self._check_wallet()

    def _entries_allowed(self, pair: str, ctx, confidence: float) -> bool:
        """Admission control. Every branch can only ever reject."""
        if self.kill.is_tripped:
            return False

        # Warmup: indicators start at zero, so the first ticks would trade on
        # a signal that is arithmetically meaningless.
        elapsed = time.time() - self.state.started_ts
        if elapsed < self.settings.warmup_s:
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
            log.debug("gate rejected %s: %s", pair, reason)
        return allowed

    async def _try_open(self, pair: str, ctx, bias, confidence: float) -> None:
        """Scan both directions and open whatever passes."""
        for side in SIDES:
            if self.manager.in_cooldown(pair, side):
                continue
            candidate = scan(
                pair, side, self.settings.decision_tf,
                bias, confidence, ctx, self.zones, adx=20,
            )
            if candidate is None:
                continue

            wave = self.manager.open(
                candidate, bias, confidence, ctx, self.surface, self.wallet
            )
            if wave is None:
                continue

            record_open(pair)
            self.state.opens += 1
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
        self.state.closed_waves = list(getattr(self.manager, "closed_today", []))

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

        if command in ("/positions", "/wave"):
            self._notify(cards.positions_card(self.state.open_waves, self.wallet))
        elif command in ("/performance", "/surf"):
            self._notify(cards.performance_card(self.state.closed_waves, self.wallet))
        elif command == "/status":
            self._notify(cards.status_card(
                version=_version(),
                uptime_s=self.state.uptime_s,
                pairs=len(self.settings.pairs),
                open_n=len(self.state.open_waves),
                feed_ok=self.state.feed_ok,
                ticks=self.state.ticks,
                throttle_cap=current_cap(),
            ))
        elif command == "/stop":
            self.request_stop("owner /stop")
            self._notify(cards.halt_card("owner /stop", self.wallet.balance))
        elif command == "/resume":
            self.clear_stop()
            self._notify("▶️ <b>Stop flag cleared.</b> Restart to resume trading.")

    # -- main loop ---------------------------------------------------------

    async def run(self) -> None:
        """Start both feeds and run until stopped.

        Both run concurrently and permanently. The WS path is faster when
        healthy; the REST path guarantees the engine keeps seeing prices when
        it is not.
        """
        storage.init_wave_db(self.conn)

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

        ws_task = asyncio.create_task(_run_ws(), name="ws-feed")
        rest_task = asyncio.create_task(self._poller.run(), name="rest-feed")

        log.info(
            "engine running: %d pairs, warmup %.0fs, balance %.4f$",
            len(self.settings.pairs), self.settings.warmup_s, self.wallet.balance,
        )

        try:
            await asyncio.gather(ws_task, rest_task)
        except asyncio.CancelledError:
            log.info("engine halting: %s", self.state.halt_reason or "cancelled")
        finally:
            for task in (ws_task, rest_task):
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
