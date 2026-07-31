"""
Walk-Forward Analysis for Evaluation System v2.

Provides walk-forward backtesting capabilities for evaluating
strategy performance across different market regimes.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone


def walk_forward_analysis(
    trades: List[Dict[str, Any]],
    window_size: int = 20,
    step_size: int = 5,
    min_trades: int = 10,
) -> Dict[str, Any]:
    """
    Perform walk-forward analysis on trade history.

    Splits trade history into overlapping windows and evaluates
    each window independently to assess strategy robustness.

    Args:
        trades: List of trade dictionaries
        window_size: Number of trades per evaluation window
        step_size: Number of trades to advance between windows
        min_trades: Minimum trades required for analysis

    Returns:
        Walk-forward analysis results
    """
    if len(trades) < min_trades:
        return {
            "status": "insufficient_data",
            "total_trades": len(trades),
            "min_required": min_trades,
        }

    windows = []
    start = 0
    while start + window_size <= len(trades):
        window_trades = trades[start : start + window_size]
        window_result = _analyze_window(window_trades, start)
        windows.append(window_result)
        start += step_size

    if not windows:
        return {"status": "no_windows_created", "total_trades": len(trades)}

    # Compute aggregate statistics
    wr_values = [w["win_rate"] for w in windows]
    avg_r_values = [w["avg_r"] for w in windows]
    pnl_values = [w["net_pnl"] for w in windows]

    overall_wr = sum(wr_values) / len(wr_values) if wr_values else 0.0
    overall_avg_r = sum(avg_r_values) / len(avg_r_values) if avg_r_values else 0.0
    overall_pnl = sum(pnl_values)

    # Stability metrics
    wr_std = _std(wr_values) if len(wr_values) > 1 else 0.0
    wr_cv = wr_std / overall_wr if overall_wr != 0 else 0.0

    # Regime detection
    regimes = _detect_regimes(windows)

    # Best and worst windows
    best_window = max(windows, key=lambda w: w["aggregate_score"]) if windows else None
    worst_window = min(windows, key=lambda w: w["aggregate_score"]) if windows else None

    return {
        "status": "complete",
        "total_trades": len(trades),
        "num_windows": len(windows),
        "window_size": window_size,
        "step_size": step_size,
        "overall_wr": round(overall_wr, 4),
        "overall_avg_r": round(overall_avg_r, 4),
        "overall_pnl": round(overall_pnl, 6),
        "wr_mean": round(overall_wr, 4),
        "wr_std": round(wr_std, 4),
        "wr_cv": round(wr_cv, 4),
        "best_window": best_window["window_id"] if best_window else None,
        "best_wr": best_window["win_rate"] if best_window else 0.0,
        "worst_window": worst_window["window_id"] if worst_window else None,
        "worst_wr": worst_window["win_rate"] if worst_window else 0.0,
        "regimes": regimes,
        "windows": windows,
    }


def _analyze_window(trades: List[Dict[str, Any]], start_idx: int) -> Dict[str, Any]:
    """Analyze a single window of trades."""
    wins = sum(1 for t in trades if t.get("verdict") == "win")
    losses = sum(1 for t in trades if t.get("verdict") == "loss")
    scratches = sum(1 for t in trades if t.get("verdict") == "scratch")
    total = len(trades)

    win_rate = wins / total if total > 0 else 0.0
    avg_r = sum(t.get("r_achieved", 0.0) for t in trades) / total if total > 0 else 0.0
    net_pnl = sum(t.get("net_pnl", 0.0) for t in trades)
    gross_pnl = sum(t.get("gross_pnl", 0.0) for t in trades)
    total_fees = sum(t.get("fee_open", 0.0) + t.get("fee_close", 0.0) for t in trades)

    # Sharpe ratio
    r_values = [t.get("r_achieved", 0.0) for t in trades]
    if len(r_values) > 1:
        mean_r = sum(r_values) / len(r_values)
        std_r = (sum((r - mean_r) ** 2 for r in r_values) / len(r_values)) ** 0.5
        sharpe = mean_r / std_r if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # Aggregate score
    wr_score = min(win_rate / 0.60, 1.0)
    avg_r_score = min(abs(avg_r) / 0.50, 1.0)
    fee_efficiency = 1.0 if net_pnl > 0 else 0.5
    sharpe_score = min(abs(sharpe) / 2.0, 1.0)
    aggregate_score = wr_score * 0.35 + avg_r_score * 0.25 + fee_efficiency * 0.20 + sharpe_score * 0.20

    return {
        "window_id": f"wf_window_{start_idx // 5 + 1}",
        "start_trade": start_idx,
        "end_trade": start_idx + total,
        "trades_evaluated": total,
        "win_rate": round(win_rate, 4),
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "avg_r": round(avg_r, 4),
        "net_pnl": round(net_pnl, 6),
        "gross_pnl": round(gross_pnl, 6),
        "total_fees": round(total_fees, 6),
        "sharpe_ratio": round(sharpe, 4),
        "aggregate_score": round(aggregate_score, 4),
    }


def _detect_regimes(windows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect different market regimes from window results."""
    if not windows:
        return []

    regimes = []
    current_regime = {"type": "unknown", "start": 0, "windows": []}

    for i, w in enumerate(windows):
        wr = w["win_rate"]
        score = w["aggregate_score"]

        if wr >= 0.60 and score >= 0.60:
            regime_type = "bullish_high_performance"
        elif wr >= 0.50 and score >= 0.50:
            regime_type = "neutral_profitable"
        elif wr < 0.40 or score < 0.40:
            regime_type = "bearish_low_performance"
        else:
            regime_type = "transitional"

        if regime_type != current_regime["type"]:
            if current_regime["windows"]:
                regimes.append({
                    "type": current_regime["type"],
                    "start_window": current_regime["start"],
                    "end_window": i,
                    "num_windows": len(current_regime["windows"]),
                    "avg_wr": round(
                        sum(w["win_rate"] for w in current_regime["windows"])
                        / len(current_regime["windows"]),
                        4,
                    ),
                    "avg_score": round(
                        sum(w["aggregate_score"] for w in current_regime["windows"])
                        / len(current_regime["windows"]),
                        4,
                    ),
                })
            current_regime = {"type": regime_type, "start": i, "windows": [w]}
        else:
            current_regime["windows"].append(w)

    # Add final regime
    if current_regime["windows"]:
        regimes.append({
            "type": current_regime["type"],
            "start_window": current_regime["start"],
            "end_window": len(windows),
            "num_windows": len(current_regime["windows"]),
            "avg_wr": round(
                sum(w["win_rate"] for w in current_regime["windows"])
                / len(current_regime["windows"]),
                4,
            ),
            "avg_score": round(
                sum(w["aggregate_score"] for w in current_regime["windows"])
                / len(current_regime["windows"]),
                4,
            ),
        })

    return regimes


def _std(values: List[float]) -> float:
    """Compute standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance ** 0.5