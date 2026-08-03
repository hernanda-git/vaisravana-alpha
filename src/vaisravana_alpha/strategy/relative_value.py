"""Paper-only paired relative-value signal engine.

This module deliberately does not submit orders. It computes a hedge ratio,
spread z-score, and a two-leg convergence signal so the strategy can be
validated with complete fee accounting before coordinated execution is enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import mean, pstdev


@dataclass(frozen=True)
class PairObservation:
    pair_a: str
    pair_b: str
    price_a: float
    price_b: float
    history_a: tuple[float, ...]
    history_b: tuple[float, ...]
    fee_rate_round_trip: float
    min_z: float = 1.5


@dataclass(frozen=True)
class PairSignal:
    pair_a: str
    pair_b: str
    hedge_ratio: float
    spread: float
    zscore: float
    direction: str
    expected_gross: float
    expected_cost: float
    expected_net: float


def _returns(values: tuple[float, ...]) -> list[float]:
    return [math.log(b / a) for a, b in zip(values, values[1:]) if a > 0 and b > 0]


def build_signal(obs: PairObservation) -> PairSignal | None:
    """Return a convergence signal only when the spread exceeds costs."""
    if min(len(obs.history_a), len(obs.history_b)) < 20:
        return None
    if obs.price_a <= 0 or obs.price_b <= 0:
        return None
    ra, rb = _returns(obs.history_a), _returns(obs.history_b)
    n = min(len(ra), len(rb))
    if n < 10:
        return None
    ra, rb = ra[-n:], rb[-n:]
    var_b = sum((x - mean(rb)) ** 2 for x in rb)
    beta = sum((a - mean(ra)) * (b - mean(rb)) for a, b in zip(ra, rb)) / var_b if var_b else 1.0
    beta = max(0.1, min(5.0, beta))
    spread_history = [a - beta * b for a, b in zip(ra, rb)]
    sigma = pstdev(spread_history)
    if sigma <= 1e-9:
        return None
    current = spread_history[-1]
    z = (current - mean(spread_history)) / sigma
    if abs(z) < obs.min_z:
        return None
    direction = "BUY_A_SELL_B" if z < 0 else "SELL_A_BUY_B"
    expected_gross = abs(z) * sigma * 2.0
    expected_cost = obs.fee_rate_round_trip * (1.0 + beta)
    if expected_gross <= expected_cost:
        return None
    return PairSignal(obs.pair_a, obs.pair_b, beta, current, z, direction, expected_gross, expected_cost, expected_gross - expected_cost)


class RelativeValueEngine:
    """Maintains paper-only pair signals and never places orders."""
    def __init__(self, fee_rate_round_trip: float = 0.0012):
        self.fee_rate_round_trip = fee_rate_round_trip
        self.signals: list[PairSignal] = []

    def evaluate(self, observations: list[PairObservation]) -> list[PairSignal]:
        self.signals = []
        for obs in observations:
            signal = build_signal(obs)
            if signal:
                self.signals.append(signal)
        return list(self.signals)
