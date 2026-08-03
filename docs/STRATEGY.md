# Alpha strategy and arbitrage research

## Current strategy
Alpha ranks Binance symbols by RSI/VWAP/CVD/volume/BTC-relative features. The counter-trade scanner maps bearish bias to BUY and bullish bias to SELL. The manager uses wave-style exits including bank, partial profit, flat-tape, loss cut, SMC break, and max-age handling.

This is aggressive single-leg mean reversion. It is not arbitrage because one unhedged position remains exposed to market direction.

## Why Polymarket-style arbitrage is different
Polymarket opportunities generally arise from payoff constraints, such as complementary outcomes whose combined price is below a guaranteed settlement value, equivalent markets with inconsistent prices, or cross-market execution differences. The edge is structural and hedged, not simply a reversed directional forecast.

Alpha currently lacks:
- a guaranteed payoff relationship;
- a second hedge leg;
- cross-venue price locking;
- explicit spread or basis accounting;
- atomic or coordinated execution of multiple legs.

Therefore, flipping BUY to SELL does not create arbitrage. It changes the directional hypothesis.

## Aggressive single-leg improvements
1. Preserve universe rotation and multi-pair participation.
2. Treat CVD divergence as a primary entry amplifier. Record whether it was present.
3. Estimate expected gross move from ATR, rank, divergence, and regime.
4. Estimate round-trip fees and slippage before entry and record them even if no trade filter is applied.
5. Use regime-adaptive TP and asymmetric risk sizing.
6. Analyze BUY and SELL independently because their edge can differ materially.
7. Keep trade frequency high, but favor trades whose expected gross edge is materially larger than expected costs.

## Paired relative-value mode
A pair trade uses two related liquid symbols:
- rank a universe by short-horizon residual return;
- identify a stable correlation or beta relationship;
- buy the negative residual leg and sell the positive residual leg;
- close when the spread returns toward its mean or the relationship invalidates.

Required data:
- synchronized prices and timestamps;
- hedge ratio or beta;
- rolling correlation;
- spread z-score;
- both leg fees and slippage;
- borrow/funding and execution status.

Risks:
- correlation breakdown;
- one leg fills while the other fails;
- both assets move together against the hedge;
- fees exceed convergence profit;
- volatile symbols create false residuals.

## Basis mode research
A future basis mode can compare spot and futures or multiple liquid venues. It should only be implemented after collecting synchronized bid/ask data, funding, transfer constraints, latency, and fill assumptions. Paper simulation must model both legs before deployment.

## Exit and risk principles
- Fees are part of the trade outcome, not an afterthought.
- A high win rate can still lose money if losses and costs are larger.
- Bank and partial-profit exits need gross and net attribution.
- Loss-cut size must cap single-trade damage.
- Max-age is a cost-control mechanism, not evidence of edge.
- Strategy modes must never share performance statistics.

## Evaluation matrix
Evaluate by mode, side, pair, regime, CVD divergence, rank bucket, exit reason, holding time, gross PnL, fees, slippage, and net PnL. Do not promote a mode from fewer than 100 completed observations without clearly labeling the result as preliminary.