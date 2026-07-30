"""Engine smoke test -- does it actually boot and trade?

Import tests prove modules load. This proves the wiring works: a synthetic
tick stream is driven through the real engine with a real wallet and a real
database, and the assertions check that state actually moved.

No network, no sleeps. The feeds are bypassed by calling `on_tick` and
`on_kline` directly, which is exactly what a live feed would do.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from vaisravana_alpha.core.models import Tick
from vaisravana_alpha.core.params import default_surface
from vaisravana_alpha.core.settings import Settings
from vaisravana_alpha.engine.runtime import AlphaEngine
from vaisravana_alpha.execution.broker import ModeGuard
from vaisravana_alpha.execution.wallet import PaperWallet
from vaisravana_alpha.storage.db import init_wave_db

from conftest import FakeNotifier, make_klines


def build_engine(tmp_path, warmup: float = 0.0) -> AlphaEngine:
    settings = Settings(
        mode="paper",
        data_dir=str(tmp_path),
        pairs=["BTCUSDT", "ETHUSDT"],
        paper_balance=10.0,
        warmup_s=warmup,
        telegram_token="",
    )
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_wave_db(conn)
    return AlphaEngine(
        settings=settings,
        surface=default_surface(),
        conn=conn,
        notifier=FakeNotifier(),
        wallet=PaperWallet.from_settings(settings),
        guard=ModeGuard(mode="paper"),
    )


def tick_at(price: float, pair: str = "BTCUSDT", ts: float = 1_700_040_000.0) -> Tick:
    return Tick(
        pair=pair, price=price, qty=1.0, side="BUY",
        bid=price - 0.05, ask=price + 0.05, mark=price, ts=ts,
        source="test", bid_qty=800.0, ask_qty=400.0,
    )


async def feed_candles(engine, pair: str = "BTCUSDT") -> None:
    """Seed enough history that the indicators are meaningful."""
    for tf in ("15m", "1h"):
        for kline in make_klines(40):
            kline["s"] = pair
            kline["tf"] = tf
            await engine.on_kline(tf, kline)


class TestBoot:
    def test_engine_constructs_with_real_dependencies(self, tmp_path):
        engine = build_engine(tmp_path)
        assert engine.wallet.balance == pytest.approx(10.0)
        assert engine.state.ticks == 0
        assert len(engine.context.pairs) == 2

    def test_stop_flag_round_trip(self, tmp_path):
        # The restart policy would resurrect a stopped bot, so the stop must
        # persist to disk rather than living only in memory.
        engine = build_engine(tmp_path)
        assert not engine.stop_flag_present
        engine.request_stop("test halt")
        assert engine.stop_flag_present
        assert engine.state.stop_requested
        engine.clear_stop()
        assert not engine.stop_flag_present


class TestTickLoop:
    async def test_ticks_are_counted_and_context_updates(self, tmp_path):
        engine = build_engine(tmp_path)
        await feed_candles(engine)
        for i in range(10):
            await engine.on_tick(tick_at(140.0 + i))

        assert engine.state.ticks == 10
        ctx = engine.context.get("BTCUSDT")
        assert ctx.price == pytest.approx(149.0)
        assert ctx.bid_qty == pytest.approx(800.0)

    async def test_book_imbalance_is_derived_from_tick_sizes(self, tmp_path):
        engine = build_engine(tmp_path)
        await engine.on_tick(tick_at(140.0))
        # (800 - 400) / 1200
        assert engine.context.get("BTCUSDT").book_imbalance == pytest.approx(1 / 3)

    async def test_warmup_suppresses_entries(self, tmp_path):
        # Indicators start at zero, so trading the first ticks would act on
        # an arithmetically meaningless signal.
        engine = build_engine(tmp_path, warmup=3600.0)
        await feed_candles(engine)
        for i in range(20):
            await engine.on_tick(tick_at(140.0 + i))
        assert engine.state.opens == 0

    async def test_stop_request_halts_the_loop(self, tmp_path):
        engine = build_engine(tmp_path)
        engine.request_stop("owner /stop")
        with pytest.raises(asyncio.CancelledError):
            await engine.on_tick(tick_at(140.0))

    async def test_kill_switch_blocks_entries(self, tmp_path):
        engine = build_engine(tmp_path)
        await feed_candles(engine)
        engine.kill.trip("test")
        for i in range(20):
            await engine.on_tick(tick_at(140.0 + i))
        assert engine.state.opens == 0

    async def test_excluded_pair_is_skipped(self, tmp_path):
        engine = build_engine(tmp_path)
        await feed_candles(engine)
        engine.excluder.exclude("BTCUSDT")
        for i in range(20):
            await engine.on_tick(tick_at(140.0 + i))
        assert engine.state.opens == 0


class TestKlineHandling:
    async def test_candles_are_stored_and_bounded(self, tmp_path):
        engine = build_engine(tmp_path)
        for kline in make_klines(300):
            kline["s"] = "BTCUSDT"
            await engine.on_kline("15m", kline)
        # An unbounded window is a slow leak in a process meant to run for weeks.
        assert len(engine.context.get("BTCUSDT").klines["15m"]) <= 200

    async def test_final_candles_seed_the_indicators(self, tmp_path):
        engine = build_engine(tmp_path)
        await feed_candles(engine)
        ctx = engine.context.get("BTCUSDT")
        assert ctx.ema_15m > 0
        assert ctx.ema_1h > 0

    async def test_kline_without_a_symbol_is_dropped(self, tmp_path):
        # Unattributable data must not corrupt another pair's context.
        engine = build_engine(tmp_path)
        await engine.on_kline("15m", {"close": 100.0, "is_final": True})
        assert engine.context.get("BTCUSDT").klines["15m"] == []

    async def test_repeated_rest_fetches_do_not_drift_indicators(self, tmp_path):
        # REST re-delivers the same closed candles every poll. Folding them
        # in incrementally would drift the EMA; recomputing is idempotent.
        engine = build_engine(tmp_path)
        await feed_candles(engine)
        first = engine.context.get("BTCUSDT").ema_15m
        await feed_candles(engine)
        assert engine.context.get("BTCUSDT").ema_15m == pytest.approx(first)


class TestRuin:
    async def test_engine_halts_when_the_account_is_spent(self, tmp_path):
        # The run is designed to be allowed to reach zero, and must stop
        # there rather than trading a negative balance.
        engine = build_engine(tmp_path)
        engine.wallet.credit_pnl(-10.0)
        await engine.on_tick(tick_at(140.0))
        assert engine.state.stop_requested
        assert "exhausted" in engine.state.halt_reason


class TestCommands:
    def test_status_reports_live_state(self, tmp_path):
        engine = build_engine(tmp_path)
        engine.state.ticks = 42
        engine.handle_command("/status")
        assert "42" in engine.notifier.sent[-1]

    def test_positions_renders_when_flat(self, tmp_path):
        engine = build_engine(tmp_path)
        engine.handle_command("/positions")
        assert "Open positions: 0" in engine.notifier.sent[-1]

    def test_stop_and_resume_toggle_the_flag(self, tmp_path):
        engine = build_engine(tmp_path)
        engine.handle_command("/stop")
        assert engine.stop_flag_present
        engine.handle_command("/resume")
        assert not engine.stop_flag_present

    def test_suffixed_command_is_accepted(self, tmp_path):
        engine = build_engine(tmp_path)
        engine.handle_command("/status@alpha_bot")
        assert engine.notifier.sent

    def test_notification_failure_never_breaks_the_loop(self, tmp_path):
        # A Telegram outage is an inconvenience; one that stops trading or
        # strands a position is a real loss.
        engine = build_engine(tmp_path)

        def boom(text, parse_mode="HTML"):
            raise RuntimeError("telegram is down")

        engine.notifier.send = boom
        engine.handle_command("/status")    # must not raise
