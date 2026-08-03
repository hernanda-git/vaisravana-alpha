# Agentic evaluation protocol

Agents evaluating Alpha must follow this order:

1. Inspect current code, Git SHA, container health, and database schema.
2. Query persistent trades before reading logs.
3. Reconcile gross PnL, open fees, close fees, slippage, funding, and net PnL.
4. Segment results by strategy mode, side, pair, regime, rank, divergence, and exit reason.
5. Compare expected edge with realized edge.
6. Identify data or accounting defects before changing strategy parameters.
7. Propose the smallest reversible experiment.
8. Deploy only paper mode and verify health plus Telegram.
9. Record the observation window and sample size.
10. Never call a small-sample result conclusive.

Agents must not infer arbitrage from reversed direction alone. An arbitrage claim requires a defined hedge or payoff constraint and complete cost accounting.

## Required experiment record

- Git SHA
- configuration values
- start and end timestamps
- strategy mode
- number of trades
- gross, fees, slippage, funding, and net
- win rate, expectancy, drawdown
- database integrity result
- deployment and Telegram verification result
- rollback SHA

## Decision language

Use `confirmed`, `preliminary`, `inconclusive`, or `blocked by accounting/data`. Do not describe a strategy as profitable solely because its mean R is positive.

## Data collection priority

Preserve the raw decision context needed to reconstruct why every trade was opened or closed. Future models need snapshots, not only final PnL.