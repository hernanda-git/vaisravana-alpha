"""
Evaluation Utilities
Math helpers, stats computation, and visualization for the evaluation system.
"""

import math
from typing import List


def sharpe_ratio(returns: List[float], risk_free: float = 0.0) -> float:
    """Compute Sharpe ratio from a list of returns."""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    std_r = math.sqrt(variance)
    if std_r == 0:
        return 0.0
    return (mean_r - risk_free) / std_r


def max_drawdown(cumulative_returns: List[float]) -> float:
    """Compute maximum drawdown from cumulative returns."""
    if not cumulative_returns:
        return 0.0
    peak = cumulative_returns[0]
    max_dd = 0.0
    for ret in cumulative_returns:
        if ret > peak:
            peak = ret
        dd = peak - ret
        if dd > max_dd:
            max_dd = dd
    return max_dd


def win_rate(trades: List[dict], verdict_key: str = "verdict", win_value: str = "win") -> float:
    """Compute win rate from trade list."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.get(verdict_key) == win_value)
    return wins / len(trades)


def average_r(trades: List[dict], r_key: str = "r_achieved") -> float:
    """Compute average R from trade list."""
    if not trades:
        return 0.0
    return sum(t.get(r_key, 0.0) for t in trades) / len(trades)


def net_pnl(trades: List[dict], pnl_key: str = "net_pnl") -> float:
    """Compute cumulative net PnL from trade list."""
    if not trades:
        return 0.0
    return sum(t.get(pnl_key, 0.0) for t in trades)


def fee_drag(trades: List[dict], fee_key: str = "total_fees", pnl_key: str = "gross_pnl") -> float:
    """Compute fee drag as ratio of fees to gross PnL."""
    total_fees = sum(t.get(fee_key, 0.0) for t in trades)
    gross_pnl = sum(t.get(pnl_key, 0.0) for t in trades)
    if gross_pnl == 0:
        return 0.0
    return total_fees / abs(gross_pnl)


def information_ratio(current_returns: List[float], benchmark_returns: List[float]) -> float:
    """Compute information ratio (active return / tracking error)."""
    if len(current_returns) < 2 or len(benchmark_returns) < 2:
        return 0.0
    if len(current_returns) != len(benchmark_returns):
        return 0.0
    active_returns = [c - b for c, b in zip(current_returns, benchmark_returns)]
    mean_active = sum(active_returns) / len(active_returns)
    std_active = (sum((r - mean_active) ** 2 for r in active_returns) / len(active_returns)) ** 0.5
    if std_active == 0:
        return 0.0
    return mean_active / std_active


def normalize_score(value: float, target: float, max_val: float = 1.0) -> float:
    """Normalize a score to 0-1 range based on target."""
    return min(max(value / target, 0.0), max_val)


def confidence_interval(values: List[float], confidence: float = 0.95) -> tuple:
    """Compute simple confidence interval (mean ± margin)."""
    if len(values) < 2:
        return (0.0, 0.0, 0.0)
    n = len(values)
    mean = sum(values) / n
    std = (sum((v - mean) ** 2 for v in values) / n) ** 0.5
    # Z-score for 95% confidence
    z = 1.96 if confidence == 0.95 else 2.576 if confidence == 0.99 else 1.645
    margin = z * std / (n ** 0.5)
    return (mean - margin, mean + margin, margin)