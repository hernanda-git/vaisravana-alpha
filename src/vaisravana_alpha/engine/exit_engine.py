"""Real-time exit engine for single-pair altcoin trading.

Architecture:
    Tick (100ms) -> ContextStore -> FactorPipeline -> RegimeDetector ->
    ExitConfidenceModel -> FeeAwareDecision -> Action

Designed for one pair (e.g., 1000BONKUSDT) to maximize learning signal.
The regime detector classifies market state, which selects the factor
weights and thresholds. This is the core of the "always growing balance"
principle: exit when holding no longer serves the balance.

Key design decisions from research:
- Volatility-adaptive exits (ATR-based trailing, EMA slope detection)
- Regime switching (momentum vs mean-revert vs chop)
- Order-flow proxy from tick data (CVD, imbalance, liquidity sweep)
- Fee-aware: never pay to exit unless drawdown exceeds salvage threshold
- Deflated Sharpe evaluation post-trade for weight adaptation
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger(__name__)


class Regime(Enum):
    """Market regime classification.

    MOMENTUM: strong directional trend, follow with trailing stop
    MEAN_REVERT: choppy, mean-reverting, exit at extremes
    CHOP: no clear direction, hold and wait
    """

    MOMENTUM = "momentum"
    MEAN_REVERT = "mean_revert"
    CHOP = "chop"


class ExitAction(Enum):
    """What the exit engine recommends."""

    HOLD = "hold"
    ADD = "add"
    CLOSE_50 = "close_50"
    CLOSE_100 = "close_100"
    FLIP = "flip"


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class TickData:
    """One market data tick."""

    ts: float
    price: float
    bid: float
    ask: float
    qty: float
    side: str = ""  # "BUY" | "SELL" | "" for mark/book ticks
    is_buy: bool = False  # legacy alias for backward-compat with tests


@dataclass
class FactorScores:
    """Normalized 0..1 scores for each factor category.

    1.0 = strong exit signal, 0.0 = no exit pressure.
    """

    structural: float = 0.0
    momentum: float = 0.0
    orderflow: float = 0.0
    volatility: float = 0.0
    liquidity: float = 0.0

    def to_dict(self) -> dict:
        return {
            "structural": round(self.structural, 4),
            "momentum": round(self.momentum, 4),
            "orderflow": round(self.orderflow, 4),
            "volatility": round(self.volatility, 4),
            "liquidity": round(self.liquidity, 4),
        }


@dataclass
class ExitSignal:
    """Complete exit decision with all metadata."""

    ts: float
    regime: Regime
    factors: FactorScores
    exit_conf: float
    action: ExitAction
    salvage: float  # PnL if exited now, after fees
    reason: str
    weights_used: dict  # which weight set was active
    pair: str = ""  # pair this signal applies to (filled by runtime)


# ── Regime Detector ──────────────────────────────────────────────────────────


class RegimeDetector:
    """Classifies market state to select appropriate exit behavior.

    Based on research showing crypto shifts between momentum and
    mean-reverting regimes, with different optimal exits for each.

    - MOMENTUM: strong EMA slope, high ATR percentile, sustained CVD
    - MEAN_REVERT: RSI extreme, price far from VWAP, high vol percentile
    - CHOP: weak trend, low vol, balanced volume
    """

    def __init__(self, ema_fast: int = 9, ema_slow: int = 55):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self._prices: list[float] = []
        self._vols: list[float] = []
        self._cvd = 0.0
        self._cvd_history: list[float] = []

    def update(self, tick: TickData, context) -> Regime:
        """Update internal state and return current regime."""
        self._prices.append(tick.price)
        if len(self._prices) > 200:
            self._prices.pop(0)
        # Volume regime
        self._vols.append(tick.qty if tick.qty else tick.volume if hasattr(tick, 'volume') else 0.0)
        if len(self._vols) > 200:
            self._vols.pop(0)

        # CVD: cumulative volume delta
        if tick.side == "BUY":
            self._cvd += tick.qty
        elif tick.side == "SELL":
            self._cvd -= tick.qty
        self._cvd_history.append(self._cvd)
        if len(self._cvd_history) > 50:
            self._cvd_history.pop(0)

        if len(self._prices) < self.ema_slow:
            return Regime.CHOP

        ema_fast = self._ema(self._prices, self.ema_fast)
        ema_slow = self._ema(self._prices, self.ema_slow)
        ema_slope = (ema_fast - ema_slow) / ema_slow if ema_slow > 0 else 0

        # Volatility percentile: current ATR vs 20-period
        atr_current = self._atr(14)
        atr_hist = self._atr_history(20)
        vol_pct = (
            sum(1 for a in atr_hist if a < atr_current) / len(atr_hist)
            if atr_hist else 0.5
        )

        # Trend strength
        trend_strength = abs(ema_slope) * 100  # percentage

        # CVD momentum
        cvd_momentum = self._cvd_momentum()

        # Classification logic:
        # MOMENTUM: strong trend + sustained CVD + normal-high vol
        if trend_strength > 0.3 and cvd_momentum > 0.1 and vol_pct > 0.4:
            return Regime.MOMENTUM

        # MEAN_REVERT: weak trend + high vol percentile + CVD divergence
        if trend_strength < 0.15 and vol_pct > 0.7:
            return Regime.MEAN_REVERT

        # CHOP: everything else
        return Regime.CHOP

    def _ema(self, prices: list[float], period: int) -> float:
        if len(prices) < period:
            return sum(prices) / len(prices) if prices else 0
        k = 2.0 / (period + 1)
        ema = prices[-period]
        for p in prices[-period + 1:]:
            ema = p * k + ema * (1 - k)
        return ema

    def _atr(self, period: int) -> float:
        """Approximate ATR from price series (no high/low, use price diffs)."""
        if len(self._prices) < period + 1:
            return 0.0
        diffs = [
            abs(self._prices[i] - self._prices[i - 1])
            for i in range(-period, 0)
        ]
        return sum(diffs) / len(diffs) if diffs else 0

    def _atr_history(self, n: int) -> list[float]:
        """ATR values over the last n periods for percentile calc."""
        result = []
        for i in range(1, min(n + 1, len(self._prices))):
            start = max(0, len(self._prices) - 14 - i)
            end = len(self._prices) - i
            if end > start:
                diffs = [
                    abs(self._prices[j] - self._prices[j - 1])
                    for j in range(start + 1, end)
                ]
                result.append(sum(diffs) / len(diffs) if diffs else 0)
        return result

    def _cvd_momentum(self) -> float:
        """CVD slope over last 10 ticks, normalized."""
        if len(self._cvd_history) < 10:
            return 0.0
        recent = self._cvd_history[-10:]
        slope = (recent[-1] - recent[0]) / (abs(recent[0]) + 1e-10)
        return slope


# ── Factor Pipeline ──────────────────────────────────────────────────────────


class FactorPipeline:
    """Computes normalized factor scores from market context.

    Each factor returns 0..1 where 1 = strong exit signal.
    Factors are regime-aware: the same raw signal may mean different
    things in momentum vs mean-revert regimes.
    """

    def __init__(self):
        self._swing_highs: list[tuple[float, float]] = []  # (price, ts)
        self._swing_lows: list[tuple[float, float]] = []
        self._volume_profile: dict[float, float] = {}  # price -> volume
        self._last_liquidity_sweep: Optional[float] = None

    def compute_factors(
        self,
        tick: TickData,
        context,
        regime: Regime,
    ) -> FactorScores:
        """Compute all factor scores for the current tick."""
        return FactorScores(
            structural=self._structural(tick, context, regime),
            momentum=self._momentum(tick, context, regime),
            orderflow=self._orderflow(tick, context, regime),
            volatility=self._volatility(tick, context, regime),
            liquidity=self._liquidity(tick, context, regime),
        )

    # ── Structural (1s-10s) ────────────────────────────────────────────────

    def _structural(self, tick: TickData, context, regime: Regime) -> float:
        """EMA slope, VWAP deviation, structure break rejection."""
        scores = []

        # EMA slope reversal: if fast EMA was above slow and now below
        ema9 = getattr(context, "ema9", tick.price)
        ema21 = getattr(context, "ema21", tick.price)
        ema55 = getattr(context, "ema55", tick.price)

        if ema9 < ema21 < ema55:
            # Downtrend accelerating
            scores.append(min(1.0, abs(ema9 - ema55) / ema55 * 100))
        elif ema9 > ema21 > ema55:
            # Uptrend, but check for rejection
            vwap = getattr(context, "vwap", tick.price)
            if tick.price > vwap:
                dev = (tick.price - vwap) / vwap
                if dev > 0.02:  # 2% above VWAP = extended
                    scores.append(min(1.0, dev * 50))

        # Structure break rejection: update swings
        self._update_swings(tick)
        if len(self._swing_highs) >= 2 and len(self._swing_lows) >= 2:
            recent_high = self._swing_highs[-1][0]
            recent_low = self._swing_lows[-1][0]
            # If price broke above high then came back below
            if tick.price < recent_high and tick.price > recent_low:
                rejection = (recent_high - tick.price) / recent_high
                scores.append(min(1.0, rejection * 20))

        return min(1.0, sum(scores) / max(len(scores), 1)) if scores else 0.0

    def _update_swings(self, tick: TickData) -> None:
        """Track swing highs and lows for structure analysis."""
        now = tick.ts
        # Simple swing detection: local extrema
        if len(self._swing_highs) == 0 or tick.price > self._swing_highs[-1][0]:
            self._swing_highs.append((tick.price, now))
        elif tick.price < self._swing_highs[-1][0] * 0.995:
            self._swing_highs.append((tick.price, now))
        if len(self._swing_highs) > 10:
            self._swing_highs.pop(0)

        if len(self._swing_lows) == 0 or tick.price < self._swing_lows[-1][0]:
            self._swing_lows.append((tick.price, now))
        elif tick.price > self._swing_lows[-1][0] * 1.005:
            self._swing_lows.append((tick.price, now))
        if len(self._swing_lows) > 10:
            self._swing_lows.pop(0)

    # ── Momentum (10s-60s) ────────────────────────────────────────────────

    def _momentum(self, tick: TickData, context, regime: Regime) -> float:
        """RSI(3) divergence, ROC(5) acceleration, volume profile shift."""
        scores = []

        # RSI(3) divergence: price makes new high but RSI doesn't
        rsi3 = getattr(context, "rsi3", 50.0)
        if rsi3 > 70:
            scores.append(min(1.0, (rsi3 - 70) / 30))
        elif rsi3 < 30:
            scores.append(min(1.0, (30 - rsi3) / 30))

        # ROC(5) acceleration: if ROC was positive and now negative
        roc5 = getattr(context, "roc5", 0.0)
        roc5_prev = getattr(context, "roc5_prev", 0.0)
        if roc5_prev > 0 and roc5 < 0:
            # Momentum flipped
            scores.append(0.8)
        elif roc5 < -0.5:
            scores.append(min(1.0, abs(roc5) / 2))

        # Volume profile shift: is volume concentrating at current level?
        self._update_volume_profile(tick)
        if len(self._volume_profile) > 5:
            total_vol = sum(self._volume_profile.values())
            current_vol = self._volume_profile.get(
                round(tick.price, 8), 0
            )
            if total_vol > 0:
                vol_share = current_vol / total_vol
                if vol_share > 0.3:  # 30% of volume at current level
                    scores.append(vol_share)

        return min(1.0, sum(scores) / max(len(scores), 1)) if scores else 0.0

    def _update_volume_profile(self, tick: TickData) -> None:
        """Accumulate volume by price level."""
        price_key = round(tick.price, 8)
        self._volume_profile[price_key] = (
            self._volume_profile.get(price_key, 0) + tick.qty
        )
        if len(self._volume_profile) > 50:
            # Remove oldest price level
            oldest = min(self._volume_profile.keys())
            del self._volume_profile[oldest]

    # ── Order Flow (1s-10s) ───────────────────────────────────────────────

    def _orderflow(self, tick: TickData, context, regime: Regime) -> float:
        """Bid/ask spread, imbalance, delta, liquidity sweep."""
        scores = []

        # Spread widening
        spread = tick.ask - tick.bid
        spread_pct = spread / tick.price if tick.price > 0 else 0
        if spread_pct > 0.001:  # 0.1% spread = widening
            scores.append(min(1.0, spread_pct * 1000))

        # Volume imbalance: buy vs sell ratio
        buy_vol = getattr(context, "buy_volume", 0)
        sell_vol = getattr(context, "sell_volume", 0)
        total = buy_vol + sell_vol
        if total > 0:
            imbalance = (sell_vol - buy_vol) / total
            if imbalance > 0.3:  # 30% more sells
                scores.append(imbalance)
            elif imbalance < -0.3:  # 30% more buys (for short positions)
                scores.append(abs(imbalance) * 0.5)  # less exit pressure for shorts

        # CVD divergence: price up but CVD (flow_delta) declining — institutional distribution
        cvd = getattr(context, "flow_delta", 0)
        cvd_prev = getattr(context, "flow_delta_prev", 0)
        if cvd_prev > 0 and cvd < cvd_prev * 0.5:
            scores.append(0.7)  # CVD collapsing — smart money exiting

        # Liquidity sweep: price spiked beyond recent range then reversed
        if self._detect_liquidity_sweep(tick):
            scores.append(0.9)

        return min(1.0, sum(scores) / max(len(scores), 1)) if scores else 0.0

    def _detect_liquidity_sweep(self, tick: TickData) -> bool:
        """Detect if price spiked beyond swing then reversed.

        A liquidity sweep = price breaks swing high/low (liquidating
        retail stops) then reverses back, indicating institutional
        distribution.
        """
        if len(self._swing_highs) < 2 or len(self._swing_lows) < 2:
            return False

        recent_high = self._swing_highs[-1][0]
        recent_low = self._swing_lows[-1][0]
        range_size = recent_high - recent_low

        # Check if price went beyond swing then came back
        if tick.price > recent_high + range_size * 0.5:
            # Spike above recent high
            if self._last_liquidity_sweep is None or \
               tick.ts - self._last_liquidity_sweep > 60:
                self._last_liquidity_sweep = tick.ts
                return True
        elif tick.price < recent_low - range_size * 0.5:
            # Spike below recent low
            if self._last_liquidity_sweep is None or \
               tick.ts - self._last_liquidity_sweep > 60:
                self._last_liquidity_sweep = tick.ts
                return True

        return False

    # ── Volatility (1s-10s) ───────────────────────────────────────────────

    def _volatility(self, tick: TickData, context, regime: Regime) -> float:
        """ATR percentile, realized vol shift, Keltner position."""
        scores = []

        # ATR percentile: high vol = exit more readily
        atr_pct = getattr(context, "atr_percentile", 0.5)
        if atr_pct > 0.8:
            scores.append((atr_pct - 0.8) * 5)  # 0-1 scale

        # Realized vol shift: 1min vol vs 5min vol
        vol_1m = getattr(context, "vol_1m", 0)
        vol_5m = getattr(context, "vol_5m", 0)
        if vol_5m > 0:
            vol_ratio = vol_1m / vol_5m
            if vol_ratio < 0.5:  # Vol collapsing = trend ending
                scores.append(1.0 - vol_ratio)

        # Keltner position: near outer channel
        keltner_upper = getattr(context, "keltner_upper", tick.price * 1.05)
        keltner_lower = getattr(context, "keltner_lower", tick.price * 0.95)
        keltner_width = keltner_upper - keltner_lower

        if keltner_width > 0:
            pos_in_channel = (tick.price - keltner_lower) / keltner_width
            if pos_in_channel > 0.9:  # Near upper = extended
                scores.append((pos_in_channel - 0.9) * 10)
            elif pos_in_channel < 0.1:  # Near lower = extended
                scores.append((0.1 - pos_in_channel) * 10)

        return min(1.0, sum(scores) / max(len(scores), 1)) if scores else 0.0

    # ── Liquidity (1s-10s) ────────────────────────────────────────────────

    def _liquidity(self, tick: TickData, context, regime: Regime) -> float:
        """Order block proximity, liquidity sweep, depth proxy."""
        scores = []

        # Order block proximity: distance to nearest identified zone
        order_blocks = getattr(context, "order_blocks", [])
        if order_blocks:
            min_dist = min(
                abs(tick.price - ob) / tick.price
                for ob in order_blocks
                if tick.price > 0
            )
            if min_dist < 0.02:  # Within 2% of order block
                scores.append(1.0 - min_dist * 50)

        # Liquidity sweep already detected in orderflow
        if self._last_liquidity_sweep is not None and \
           tick.ts - self._last_liquidity_sweep < 30:
            scores.append(0.8)

        # Depth proxy: spread + volume at level
        spread = tick.ask - tick.bid
        if spread / tick.price > 0.0005:  # 0.05% spread = low depth
            scores.append(0.5)

        return min(1.0, sum(scores) / max(len(scores), 1)) if scores else 0.0


# ── Exit Confidence Model ────────────────────────────────────────────────────


class ExitConfidenceModel:
    """Computes exit confidence from factor scores.

    Weights are regime-dependent:
    - MOMENTUM: structural + momentum dominant (trailing stop style)
    - MEAN_REVERT: structural + volatility dominant (exit at extremes)
    - CHOP: orderflow + liquidity dominant (wait for clarity)
    """

    REGIME_WEIGHTS = {
        Regime.MOMENTUM: {
            "structural": 0.35,
            "momentum": 0.30,
            "orderflow": 0.15,
            "volatility": 0.10,
            "liquidity": 0.10,
        },
        Regime.MEAN_REVERT: {
            "structural": 0.25,
            "momentum": 0.20,
            "orderflow": 0.15,
            "volatility": 0.25,
            "liquidity": 0.15,
        },
        Regime.CHOP: {
            "structural": 0.15,
            "momentum": 0.15,
            "orderflow": 0.30,
            "volatility": 0.20,
            "liquidity": 0.20,
        },
    }

    REGIME_THRESHOLDS = {
        Regime.MOMENTUM: {"close_100": 0.85, "close_50": 0.70, "add": 0.30},
        Regime.MEAN_REVERT: {"close_100": 0.75, "close_50": 0.60, "add": 0.25},
        Regime.CHOP: {"close_100": 0.90, "close_50": 0.75, "add": 0.20},
    }

    def __init__(self):
        self._weight_history: list[dict] = []
        self._accuracy_history: list[float] = []

    def compute_confidence(
        self,
        factors: FactorScores,
        regime: Regime,
    ) -> tuple[float, dict]:
        """Compute exit confidence and return (confidence, weights_used)."""
        weights = self.REGIME_WEIGHTS[regime]
        confidence = (
            weights["structural"] * factors.structural +
            weights["momentum"] * factors.momentum +
            weights["orderflow"] * factors.orderflow +
            weights["volatility"] * factors.volatility +
            weights["liquidity"] * factors.liquidity
        )
        return min(1.0, confidence), weights

    def decide(
        self,
        confidence: float,
        regime: Regime,
        salvage: float,
        position_size: float,
    ) -> ExitAction:
        """Decide action based on confidence and fee-aware salvage.

        Key principle: aggressive loss cutting. If salvage is negative,
        LOWER thresholds to exit faster — don't let losses compound.
        If salvage is positive, can be more selective.
        """
        thresholds = self.REGIME_THRESHOLDS[regime]

        # Adjust thresholds based on salvage — aggressive loss cutting
        if salvage < 0:
            # Exiting loses money: LOWER thresholds to exit FASTER
            # This prevents the death spiral of holding losing trades
            thresholds = {
                k: max(0.3, v - 0.15) for k, v in thresholds.items()
            }
        elif salvage > 0:
            # Exiting gains money: can be more selective
            thresholds = {
                k: min(0.95, v + 0.05) for k, v in thresholds.items()
            }

        if confidence >= thresholds["close_100"]:
            return ExitAction.CLOSE_100
        elif confidence >= thresholds["close_50"]:
            return ExitAction.CLOSE_50
        elif confidence < thresholds["add"]:
            if position_size > 0:
                return ExitAction.ADD
            else:
                return ExitAction.HOLD
        else:
            return ExitAction.HOLD

    def update_weights(self, regime: Regime, accuracy: float) -> None:
        """Record accuracy for learning. Called post-trade."""
        self._accuracy_history.append(accuracy)
        self._weight_history.append(self.REGIME_WEIGHTS[regime])

        # Simple adaptation: if accuracy is low, increase weight on
        # the factor that was most wrong. This is a placeholder for
        # a proper reinforcement learning update.
        if len(self._accuracy_history) > 10:
            recent_acc = sum(self._accuracy_history[-10:]) / 10
            if recent_acc < 0.5:
                log.warning(
                    "Exit confidence model accuracy low (%.2f) for %s "
                    "regime; weights may need manual review",
                    recent_acc, regime.value,
                )


# ── Main Engine ─�────────────────────────────────────────────────────────────


class ExitEngine:
    """Orchestrates the real-time exit pipeline.

    Usage:
        engine = ExitEngine()
        for tick in tick_stream:
            signal = engine.process(tick, context, position)
            if signal.action != ExitAction.HOLD:
                execute_exit(signal)
    """

    def __init__(self):
        self.regime_detector = RegimeDetector()
        self.factor_pipeline = FactorPipeline()
        self.confidence_model = ExitConfidenceModel()
        self._current_regime: Regime = Regime.CHOP
        self._last_signal: Optional[ExitSignal] = None

    def process(
        self,
        tick: TickData,
        context,
        position,
    ) -> ExitSignal:
        """Process one tick and return an exit signal.

        Args:
            tick: market data
            context: market context (EMA, RSI, VWAP, etc.)
            position: current position (entry_price, size, side, etc.)
        """
        # 1. Detect regime
        regime = self.regime_detector.update(tick, context)
        self._current_regime = regime

        # 2. Compute factor scores
        factors = self.factor_pipeline.compute_factors(tick, context, regime)

        # 3. Compute exit confidence
        confidence, weights = self.confidence_model.compute_confidence(
            factors, regime
        )

        # 4. Fee-aware salvage calculation
        salvage = self._calculate_salvage(tick, position)

        # 5. Decide action
        action = self.confidence_model.decide(
            confidence, regime, salvage,
            getattr(position, "size", 0),
        )

        # 6. Build signal
        signal = ExitSignal(
            ts=tick.ts,
            regime=regime,
            factors=factors,
            exit_conf=confidence,
            action=action,
            salvage=salvage,
            reason=self._explain(action, regime, factors),
            weights_used=weights,
            pair=getattr(tick, "pair", ""),
        )
        self._last_signal = signal

        return signal

    def _calculate_salvage(self, tick: TickData, position) -> float:
        """Calculate PnL if exited now, after fees.

        salvage = (exit_price - entry_price) * size - close_fee
        For shorts: salvage = (entry_price - exit_price) * size - close_fee
        """
        entry_price = getattr(position, "entry_price", 0)
        size = getattr(position, "size", 0)
        side = getattr(position, "side", "long")
        notional = getattr(position, "notional", 0)

        if size == 0 or entry_price == 0:
            return 0.0

        # Close fee: 4bps taker
        close_fee = notional * 0.0004

        if side == "long":
            gross = (tick.price - entry_price) * size
        else:  # short
            gross = (entry_price - tick.price) * size

        return gross - close_fee

    def _explain(
        self,
        action: ExitAction,
        regime: Regime,
        factors: FactorScores,
    ) -> str:
        """Generate human-readable explanation for the signal."""
        if action == ExitAction.HOLD:
            return f"Hold ({regime.value}): conf={factors.to_dict()}"
        elif action == ExitAction.ADD:
            return f"Add ({regime.value}): low exit pressure"
        elif action == ExitAction.CLOSE_50:
            return f"Close 50% ({regime.value}): moderate exit signal"
        elif action == ExitAction.CLOSE_100:
            return f"Close 100% ({regime.value}): strong exit signal"
        elif action == ExitAction.FLIP:
            return f"Flip bias ({regime.value}): extreme exit signal"
        return f"Unknown action ({regime.value})"

    def get_current_regime(self) -> Regime:
        return self._current_regime

    def get_last_signal(self) -> Optional[ExitSignal]:
        return self._last_signal
