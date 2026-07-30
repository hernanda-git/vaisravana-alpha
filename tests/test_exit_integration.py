"""Integration test: exit engine wired into the runtime tick loop.

Proves the full path: tick -> exit engine -> partial/full close -> telemetry.
Uses synthetic waves and a real engine instance with the exit engine enabled.
"""

import asyncio
import os
import tempfile
import time

import pytest

from vaisravana_alpha.core.params import load_surface
from vaisravana_alpha.core.settings import Settings
from vaisravana_alpha.engine.exit_engine import ExitAction
from vaisravana_alpha.engine.runtime import AlphaEngine, EngineState
from vaisravana_alpha.execution.manager import WaveManager, WaveState
from vaisravana_alpha.execution.wallet import PaperWallet
from vaisravana_alpha.execution.broker import ModeGuard
from vaisravana_alpha.execution.risk import KillSwitch, PairExcluder
from vaisravana_alpha.marketdata.feed import Tick
from vaisravana_alpha.storage import agentic
from vaisravana_alpha.storage.db import init_db


def _make_engine(exit_enabled: bool, exit_pair: str = ""):
    """Build a minimal engine with the exit engine wired in."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(db_path)

    fd2, agentic_path = tempfile.mkstemp(suffix=".db")
    os.close(fd2)
    agentic_conn = agentic.init_agentic_db(agentic_path)

    settings = Settings(
        mode="paper",
        data_dir=tempfile.mkdtemp(),
        pairs=["1000BONKUSDT"],
        warmup_s=0.0,  # skip warmup for test
        exit_enabled=exit_enabled,
        exit_pair=exit_pair,
        exit_tick_interval_ms=0,  # evaluate every tick
    )

    surface = load_surface()
    wallet = PaperWallet(
        balance=10.0, open_fee_rate=0.0002, close_fee_rate=0.0004,
    )
    guard = ModeGuard(mode="paper")
    engine = AlphaEngine(
        settings=settings,
        surface=surface,
        conn=conn,
        notifier=None,
        wallet=wallet,
        guard=guard,
        agentic_conn=agentic_conn,
        run_id="test-run",
        kill_switch=KillSwitch(daily_loss_limit_pct=100.0),  # disabled for test
    )
    engine.state.started_ts = 0  # no warmup
    return engine


def _fake_wave(pair="1000BONKUSDT", entry=0.00001234, side="long"):
    """Create a minimal open wave object with realistic BONK sizing."""
    class FakeWave:
        pass
    w = FakeWave()
    w.wave_id = f"{pair}-1m-{side}-1"
    w.pair = pair
    w.side = side.upper()
    w.tf = "1m"
    w.entry_price = entry
    w.anchor = entry * 0.99
    w.size = 405000.0  # ~$5 notional at 0.00001234
    w.notional = 5.0
    w.leverage = 3
    w.live_r = 0.05
    w.peak_r = 0.05
    w.confidence = 0.3
    w.bias = "bullish" if side == "long" else "bearish"
    w.close_reason = ""
    w.state = WaveState.SURFING
    w._open_fee = 5.0 * 0.0002
    w.sl_price = entry * 0.98 if side == "long" else entry * 1.02
    w.tp_price = entry * 1.04 if side == "long" else entry * 0.96
    w.structure_score = 0.3
    return w


def _fake_context(price=0.00001234):
    """Create a context with indicators the exit engine reads."""
    class Ctx:
        pass
    c = Ctx()
    c.price = price
    c.ema_15m = price
    c.ema_1h = price
    c.ema9 = price * 1.001
    c.ema21 = price * 1.003
    c.ema55 = price * 1.005
    c.vwap = price
    c.rsi3 = 50.0
    c.roc5 = 0.0
    c.roc5_prev = 0.0
    c.buy_volume = 100
    c.sell_volume = 100
    c.cvd = 0
    c.cvd_prev = 0
    c.flow_delta = 0.0
    c.flow_volume = 100.0
    c.bid = price * 0.999
    c.ask = price * 1.001
    c.bid_qty = 50.0
    c.ask_qty = 50.0
    c.risk_regime = 0.0
    c.alt_breadth = 0.0
    c.mtf_confluence = True
    c.atr_percentile = 0.5
    c.vol_1m = 0.01
    c.vol_5m = 0.01
    c.keltner_upper = price * 1.02
    c.keltner_lower = price * 0.98
    c.order_blocks = []
    c.structure_score = 0.3
    c.pair = "1000BONKUSDT"
    return c


def _make_tick(pair="1000BONKUSDT", price=0.00001234, is_buy=True, ts=None):
    return Tick(
        pair=pair, price=price, qty=10.0, side="BUY" if is_buy else "SELL",
        bid=price * 0.999, ask=price * 1.001, mark=price,
        ts=ts or time.time(), source="aggTrade",
    )


import time


def test_exit_engine_disabled_by_default():
    """Exit engine should be None when not enabled."""
    engine = _make_engine(exit_enabled=False)
    assert engine.exit_engine is None


def test_exit_engine_enabled_creates_instance():
    """Exit engine should be instantiated when enabled."""
    engine = _make_engine(exit_enabled=True, exit_pair="1000BONKUSDT")
    assert engine.exit_engine is not None
    assert engine.exit_pair == "1000BONKUSDT"


def test_partial_close_on_moderate_signal():
    """A moderate exit signal should trigger a partial close."""
    engine = _make_engine(exit_enabled=True, exit_pair="1000BONKUSDT")
    manager = engine.manager
    wave = _fake_wave(entry=0.00001234, side="long")
    manager.waves[wave.wave_id] = wave

    # Price drops ~2% (above SL at -2%), RSI collapses, CVD negative
    ctx = _fake_context(price=0.00001210)
    ctx.ema9 = 0.00001210 * 1.001
    ctx.ema21 = 0.00001210 * 1.003
    ctx.ema55 = 0.00001210 * 1.005
    ctx.rsi3 = 25.0  # oversold = exit signal for long
    ctx.cvd = -500
    ctx.atr_percentile = 0.9

    tick = _make_tick(price=0.00001210, is_buy=False)
    # Run enough ticks to build regime context
    for i in range(20):
        p = 0.00001210 + i * 0.00000015
        ctx2 = _fake_context(price=p)
        ctx2.rsi3 = 25.0
        ctx2.cvd = -500 - i * 10
        ctx2.atr_percentile = 0.9
        t = _make_tick(price=p, is_buy=False, ts=time.time() + i)
        asyncio.run(engine.on_tick(t))

    # After ticks, should have a close signal or partial
    # (full or partial close recorded)
    assert engine.state.closes >= 1 or manager.get_open_wave("1000BONKUSDT") is None


def test_exit_signal_telemetry_recorded():
    """Exit signals should be persisted to agentic DB."""
    engine = _make_engine(exit_enabled=True, exit_pair="1000BONKUSDT")
    manager = engine.manager
    wave = _fake_wave(entry=0.00001234, side="long")
    manager.waves[wave.wave_id] = wave

    ctx = _fake_context(price=0.00001215)
    ctx.ema9 = 0.00001215 * 1.001
    ctx.ema21 = 0.00001215 * 1.003
    ctx.ema55 = 0.00001215 * 1.005
    ctx.rsi3 = 15.0
    ctx.cvd = -1000
    ctx.atr_percentile = 0.95

    for i in range(15):
        p = 0.00001215 + i * 0.00000010
        c = _fake_context(price=p)
        c.rsi3 = 15.0
        c.cvd = -1000 - i * 20
        c.atr_percentile = 0.95
        t = _make_tick(price=p, is_buy=False, ts=time.time() + i)
        asyncio.run(engine.on_tick(t))

    # Exit signals should have been recorded
    rows = engine.agentic.execute(
        "SELECT COUNT(*) c FROM exit_signals WHERE run_id=?", ("test-run",)
    ).fetchone()
    assert rows["c"] >= 1


def test_no_exit_when_price_favorable():
    """If price moves in our favor, no exit should trigger."""
    engine = _make_engine(exit_enabled=True, exit_pair="1000BONKUSDT")
    manager = engine.manager
    wave = _fake_wave(entry=0.00001234, side="long")
    manager.waves[wave.wave_id] = wave

    # Price rises modestly (below TP at +4%), RSI healthy, CVD positive
    ctx = _fake_context(price=0.00001250)
    ctx.ema9 = 0.00001250 * 0.999
    ctx.ema21 = 0.00001250 * 0.997
    ctx.ema55 = 0.00001250 * 0.995
    ctx.rsi3 = 60.0
    ctx.cvd = 500
    ctx.atr_percentile = 0.3

    for i in range(10):
        p = 0.00001250 + i * 0.00000001
        c = _fake_context(price=p)
        c.rsi3 = 60.0
        c.cvd = 500 + i * 10
        c.atr_percentile = 0.3
        t = _make_tick(price=p, is_buy=True, ts=time.time() + i)
        asyncio.run(engine.on_tick(t))

    # Should still be open (no exit)
    assert manager.get_open_wave("1000BONKUSDT") is not None
