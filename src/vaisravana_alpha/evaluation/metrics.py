"""Trade metrics from a wave_log DB — the evaluator's source of truth.

Reads the recorded `wave_log` table (the engine writes here on every close).
We NEVER re-run the engine here; we evaluate what ACTUALLY happened. This is
what makes the evaluator honest: it scores reality, not a simulated path.
"""
from __future__ import annotations
import sqlite3
import math
from collections import defaultdict


def load_trades(db_path: str) -> list[dict]:
    """Return closed trades from wave_log, ordered by close time."""
    rows = []
    try:
        c = sqlite3.connect(db_path)
        rows = c.execute(
            "SELECT pair, side, close_reason, peak_r, final_r, pnl_usd, "
            "fees_usd, opened_ts, closed_ts FROM wave_log "
            "WHERE close_reason IS NOT NULL ORDER BY closed_ts"
        ).fetchall()
        c.close()
    except Exception as e:
        raise RuntimeError(f"cannot read {db_path}: {e}")
    out = []
    for pair, side, reason, peak, fr, pnl, fee, ots, cts in rows:
        out.append({
            "pair": pair, "side": side, "reason": reason,
            "peak_r": peak or 0.0, "final_r": fr or 0.0,
            "pnl": pnl or 0.0, "fee": fee or 0.0,
            "opened_ts": ots, "closed_ts": cts,
        })
    return out


def build_equity(trades: list[dict]) -> list[float]:
    """Cumulative net PnL curve (already net of fees)."""
    eq = []
    run = 0.0
    for t in trades:
        run += t["pnl"]
        eq.append(run)
    return eq


def trade_sharpe(trades: list[dict], annualize: bool = False) -> float:
    """Sharpe on the per-trade net PnL series.

    Per-trade Sharpe = mean(pnl) / std(pnl) * sqrt(n). If annualize=True we
    scale by sqrt(252 * trades_per_day) — but for live-soak comparison we keep
    it trade-normalized (sqrt(n)) so different soak lengths are comparable.
    Returns 0.0 when std is 0 (degenerate).
    """
    if not trades:
        return 0.0
    xs = [t["pnl"] for t in trades]
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0.0:
        return 0.0
    sr = mean / sd * math.sqrt(n)
    return sr


def summary(trades: list[dict]) -> dict:
    """Compact risk-adjusted summary block."""
    n = len(trades)
    if n == 0:
        return {"n": 0}
    pnls = [t["pnl"] for t in trades]
    fins = [t["final_r"] for t in trades]
    net = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    deep = sum(1 for fr in fins if fr < -0.5)
    worst = min(fins)
    reasons = defaultdict(int)
    for t in trades:
        reasons[t["reason"]] += 1
    return {
        "n": n, "wr": wins / n, "net": net, "net_per_trade": net / n,
        "avg_final_r": sum(fins) / n, "worst_r": worst, "deep": deep,
        "sharpe": trade_sharpe(trades), "reasons": dict(reasons),
    }


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
