# Layer 2: Aggregate Evaluation

Evaluates a window of N trades (default 20) to assess strategy health.

## Metrics

- win_rate: percentage of winning trades
- avg_r: average R achieved per trade
- net_pnl: cumulative net PnL after fees
- fee_drag: total fees as percentage of gross PnL
- sharpe_ratio: risk-adjusted return (20 trades)
- max_drawdown: largest peak-to-trough decline
- trend: improving / stable / declining (based on WR slope)
- aggregate_score: composite (0.0-1.0)

## Aggregate Score Formula

aggregate_score = wr_score * 0.35 + avg_r_score * 0.25 + fee_efficiency * 0.20 + sharpe_score * 0.20

Where:
- wr_score = win_rate / 0.60 (normalized to 60% target)
- avg_r_score = min(avg_r / 0.50, 1.0) (normalized to 0.50R target)
- fee_efficiency = 1.0 if net_pnl > 0, else net_pnl / abs(net_pnl) * 0.5
- sharpe_score = min(sharpe / 2.0, 1.0) (normalized to 2.0 Sharpe target)

## Decision Thresholds

- aggregate_score >= 0.60: strategy is healthy (green)
- aggregate_score 0.40-0.59: strategy needs attention (yellow)
- aggregate_score < 0.40: strategy is degraded (red)

## Output

```json
{
  "window_id": "wave_window_005",
  "bot": "wave",
  "trades_evaluated": 20,
  "win_rate": 0.55,
  "avg_r": 0.32,
  "net_pnl": 0.045,
  "fee_drag": 0.15,
  "sharpe_ratio": 1.2,
  "max_drawdown": 0.08,
  "trend": "improving",
  "aggregate_score": 0.62,
  "verdict": "green",
  "timestamp": "2026-08-01T01:00:00Z"
}
```