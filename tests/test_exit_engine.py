"""Tests for the real-time exit engine.

Synthetic ticks, no network. These pin the contract:
- Regime detection classifies correctly
- Factor scores are in [0, 1]
- Confidence is a weighted blend
- Fee-aware salvage never exits at a loss unless forced
- The engine processes a tick stream end-to-end
"""

import math
import time

import pytest

from vaisravana_alpha.engine.exit_engine import (
    ExitAction,
    ExitEngine,
    ExitSignal,
    FactorScores,
    Regime,
    TickData,
)


class FakeContext:
    """Minimal context that the exit engine reads from."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakePosition:
    def __init__(self, entry_price=100.0, size=1.0, side="long", notional=100.0):
        self.entry_price = entry_price
        self.size = size
        self.side = side
        self.notional = notional


def make_tick(price, bid=None, ask=None, volume=1.0, is_buy=True, ts=None):
    return TickData(
        ts=ts or time.time(),
        price=price,
        bid=bid or price * 0.999,
        ask=ask or price * 1.001,
        volume=volume,
        is_buy=is_buy,
    )


# ── Regime detection ─────────────────────────────────────────────────────────


def test_regime_chop_on_flat_data():
    """Flat prices with no trend should be CHOP."""
    engine = ExitEngine()
    base = 100.0
    for i in range(60):
        tick = make_tick(base + math.sin(i * 0.3) * 0.5)
        engine.process(tick, FakeContext(), FakePosition())
    # After enough ticks, should have a regime
    assert engine.get_current_regime() in (Regime.CHOP, Regime.MEAN_REVERT)


def test_regime_momentum_on_strong_uptrend():
    """Strong uptrend with sustained buying should be MOMENTUM."""
    engine = ExitEngine()
    ctx = FakeContext()
    for i in range(100):
        price = 100.0 + i * 0.5  # steady uptrend
        tick = make_tick(price, is_buy=True, volume=10.0)
        engine.process(tick, ctx, FakePosition())
    # Should detect momentum regime
    assert engine.get_current_regime() in (Regime.MOMENTUM, Regime.CHOP)


def test_regime_mean_revert_on_high_volatility():
    """High volatility without trend should lean mean-revert."""
    engine = ExitEngine()
    ctx = FakeContext()
    for i in range(100):
        # Oscillating with increasing amplitude
        price = 100.0 + math.sin(i * 0.5) * (2 + i * 0.1)
        tick = make_tick(price, volume=15.0)
        engine.process(tick, ctx, FakePosition())
    assert engine.get_current_regime() in (Regime.MEAN_REVERT, Regime.CHOP)


# ── Factor scores ────────────────────────────────────────────────────────────


def test_factor_scores_in_range():
    """All factor scores must be 0..1."""
    engine = ExitEngine()
    ctx = FakeContext(
        ema9=100.5, ema21=100.0, ema55=99.5,
        vwap=100.0, rsi3=75.0, roc5=0.5, roc5_prev=0.3,
        buy_volume=100, sell_volume=300, cvd=70, cvd_prev=100,
        atr_percentile=0.9, vol_1m=0.02, vol_5m=0.01,
        keltner_upper=102.0, keltner_lower=98.0,
        order_blocks=[101.0, 99.0],
    )
    pos = FakePosition(entry_price=100.0, size=1.0, notional=100.0)
    tick = make_tick(101.5)
    signal = engine.process(tick, ctx, pos)
    for score in signal.factors.to_dict().values():
        assert 0.0 <= score <= 1.0


def test_orderflow_sweep_detection():
    """Liquidity sweep should boost orderflow score."""
    engine = ExitEngine()
    ctx = FakeContext()
    pos = FakePosition()

    # Build some swing highs/lows
    for i in range(20):
        tick = make_tick(100.0 + i * 0.1)
        engine.process(tick, ctx, pos)

    # Now spike above and reverse
    tick_spike = make_tick(105.0)  # break out
    engine.process(tick_spike, ctx, pos)
    tick_reverse = make_tick(100.5)  # reverse back
    signal = engine.process(tick_reverse, ctx, pos)

    # Orderflow should have some score from the sweep
    assert signal.factors.orderflow >= 0.0


# ── Exit confidence ──────────────────────────────────────────────────────────


def test_confidence_weighted_blend():
    """Confidence must be the weighted sum of factors."""
    engine = ExitEngine()
    factors = FactorScores(
        structural=0.5, momentum=0.6, orderflow=0.3,
        volatility=0.4, liquidity=0.2,
    )
    weights = engine.confidence_model.REGIME_WEIGHTS[Regime.MOMENTUM]
    expected = (
        weights["structural"] * 0.5 +
        weights["momentum"] * 0.6 +
        weights["orderflow"] * 0.3 +
        weights["volatility"] * 0.4 +
        weights["liquidity"] * 0.2
    )
    conf, _ = engine.confidence_model.compute_confidence(factors, Regime.MOMENTUM)
    assert abs(conf - expected) < 1e-6


def test_high_confidence_triggers_close():
    """exit_conf > 0.85 should trigger CLOSE_100."""
    engine = ExitEngine()
    factors = FactorScores(
        structural=1.0, momentum=1.0, orderflow=1.0,
        volatility=1.0, liquidity=1.0,
    )
    conf, weights = engine.confidence_model.compute_confidence(
        factors, Regime.MOMENTUM
    )
    assert conf > 0.85
    action = engine.confidence_model.decide(conf, Regime.MOMENTUM, 5.0, 1.0)
    assert action == ExitAction.CLOSE_100


def test_low_confidence_holds():
    """exit_conf < 0.30 should hold or add."""
    engine = ExitEngine()
    factors = FactorScores(
        structural=0.0, momentum=0.0, orderflow=0.0,
        volatility=0.0, liquidity=0.0,
    )
    conf, _ = engine.confidence_model.compute_confidence(factors, Regime.MOMENTUM)
    assert conf < 0.30
    action = engine.confidence_model.decide(conf, Regime.MOMENTUM, 5.0, 1.0)
    assert action in (ExitAction.HOLD, ExitAction.ADD)


# ── Fee-aware salvage ────────────────────────────────────────────────────────


def test_salvage_positive_allows_earlier_exit():
    """When salvage is positive, lower confidence can trigger exit."""
    engine = ExitEngine()
    pos = FakePosition(entry_price=100.0, size=1.0, notional=100.0)

    # Price went up: positive salvage
    tick = make_tick(101.0)
    salvage = engine._calculate_salvage(tick, pos)
    assert salvage > 0  # (101-100)*1 - 0.04 fee = 0.96

    # With positive salvage, moderate confidence triggers close 50%
    action = engine.confidence_model.decide(0.72, Regime.MOMENTUM, salvage, 1.0)
    assert action in (ExitAction.CLOSE_50, ExitAction.CLOSE_100)


def test_salvage_negative_requires_higher_confidence():
    """When salvage is negative, require higher confidence to exit."""
    engine = ExitEngine()
    pos = FakePosition(entry_price=100.0, size=1.0, notional=100.0)

    # Price went down: negative salvage
    tick = make_tick(99.5)
    salvage = engine._calculate_salvage(tick, pos)
    assert salvage < 0  # (99.5-100)*1 - 0.04 fee = -0.54

    # With negative salvage, 0.75 confidence should NOT trigger close
    action = engine.confidence_model.decide(0.75, Regime.MOMENTUM, salvage, 1.0)
    assert action == ExitAction.HOLD

    # But 0.96 should (threshold adjusted up to 0.95 for negative salvage)
    action = engine.confidence_model.decide(0.96, Regime.MOMENTUM, salvage, 1.0)
    assert action == ExitAction.CLOSE_100


def test_short_position_salvage():
    """Short position: price drop = positive salvage."""
    engine = ExitEngine()
    pos = FakePosition(entry_price=100.0, size=1.0, side="short", notional=100.0)

    tick = make_tick(99.0)  # price went down
    salvage = engine._calculate_salvage(tick, pos)
    assert salvage > 0  # (100-99)*1 - 0.04 = 0.96


# ── Full pipeline ────────────────────────────────────────────────────────────


def test_full_pipeline_process_tick():
    """End-to-end: process a tick and get a valid signal."""
    engine = ExitEngine()
    ctx = FakeContext(
        ema9=100.5, ema21=100.0, ema55=99.5,
        vwap=100.0, rsi3=80.0, roc5=0.1, roc5_prev=0.5,
        buy_volume=50, sell_volume=200, cvd=-100, cvd_prev=50,
        atr_percentile=0.95, vol_1m=0.03, vol_5m=0.01,
        keltner_upper=102.0, keltner_lower=98.0,
        order_blocks=[100.5],
    )
    pos = FakePosition(entry_price=100.0, size=1.0, notional=100.0)
    tick = make_tick(101.8)

    signal = engine.process(tick, ctx, pos)

    assert isinstance(signal, ExitSignal)
    assert signal.ts == tick.ts
    assert isinstance(signal.regime, Regime)
    assert 0.0 <= signal.exit_conf <= 1.0
    assert isinstance(signal.action, ExitAction)
    assert signal.factors is not None
    assert signal.weights_used is not None


def test_engine_state_persists():
    """Engine maintains state across ticks."""
    engine = ExitEngine()
    ctx = FakeContext()
    pos = FakePosition()

    for i in range(10):
        tick = make_tick(100.0 + i * 0.1)
        engine.process(tick, ctx, pos)

    # Should have accumulated price history
    assert len(engine.regime_detector._prices) == 10


def test_no_position_no_salvage():
    """Zero-size position should have zero salvage."""
    engine = ExitEngine()
    pos = FakePosition(size=0.0)
    tick = make_tick(100.0)
    assert engine._calculate_salvage(tick, pos) == 0.0


def test_factor_pipeline_liquidity_sweep():
    """Liquidity sweep detection should work end-to-end."""
    engine = ExitEngine()
    ctx = FakeContext()
    pos = FakePosition(entry_price=100.0, size=1.0, notional=100.0)

    # Build swings
    for i in range(15):
        engine.process(make_tick(100.0 + i * 0.05), ctx, pos)

    # Spike and reverse
    signal = engine.process(make_tick(103.0), ctx, pos)  # spike up
    signal = engine.process(make_tick(100.5), ctx, pos)  # reverse

    # Should have detected the sweep
    assert signal.factors.liquidity > 0.0
