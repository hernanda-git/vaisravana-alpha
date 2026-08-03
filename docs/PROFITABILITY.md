# Profitability and accounting specification

## Required identity
For every completed trade:

`net_pnl = gross_pnl - open_fee - close_fee - slippage - funding`

If a value is unavailable, record it as unknown and do not silently treat it as zero in evaluation.

## Required metrics
- closed trades;
- win rate;
- gross PnL;
- open fees;
- close fees;
- total fees;
- slippage;
- funding;
- net PnL;
- mean and median R;
- average win and average loss;
- expectancy per trade;
- fee-to-gross ratio;
- maximum drawdown;
- profit factor;
- holding-time distribution;
- results by side, pair, regime, strategy mode, and exit reason.

## Reporting rules
Telegram must use persistent SQLite history, not an in-memory list. `/alpha_performance` must report the same rows used by offline evaluation. `/alpha_trades` must show recent persistent trades. Open and close fees must be distinguishable.

## Current baseline warning
The existing database contains 138 closed wave_log rows at the captured baseline. Historical accounting has evidence of fee-definition drift: wave_log records close fees while wallet totals include open and close fees. This must be reconciled before treating aggregate totals as authoritative.

## Acceptance thresholds
A mode is provisionally promising only when:
- net expectancy is positive after fees and slippage;
- accounting identity passes for every row;
- performance is not explained by one outlier;
- results are stable across multiple time intervals;
- at least 100 completed trades exist for that mode.

## Failure patterns to monitor
- positive mean R but negative net PnL;
- fees greater than gross PnL;
- repeated same-pair churn;
- one side profitable and the other structurally negative;
- loss-cut outliers;
- database rows missing open fees;
- Telegram values differing from SQLite queries.

## Evaluation SQL examples
```sql
SELECT COUNT(*), SUM(pnl_usd), SUM(fees_usd)
FROM wave_log
WHERE state NOT IN ('ENTERED', 'SURFING');

SELECT close_reason, COUNT(*), SUM(pnl_usd), AVG(final_r)
FROM wave_log
WHERE state NOT IN ('ENTERED', 'SURFING')
GROUP BY close_reason;

SELECT side, COUNT(*), SUM(pnl_usd), AVG(final_r)
FROM wave_log
WHERE state NOT IN ('ENTERED', 'SURFING')
GROUP BY side;
```

## Decision rule
Do not optimize for win rate alone. Optimize for expected net dollars per trade while retaining aggressive participation and transparent downside.