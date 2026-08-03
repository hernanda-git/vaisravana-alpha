# Vaiśravaṇa Alpha redesign plan

## Objective
Preserve aggressive multi-pair trading while making net profitability, not win rate or R-multiple, the primary objective. All decisions must include open fees, close fees, slippage assumptions, and execution risk.

## High-level plan
1. Preserve the current Alpha baseline and persistent trade history.
2. Correct reporting so Telegram and SQLite agree on closed trades, gross PnL, fees, and net PnL. The fee-aware persistence migration is now implemented.
3. Keep aggressive universe-ranked single-leg counter-trading.
4. Add fee-aware expected-edge telemetry without creating a blanket no-trade gate. The schema and single-leg estimates are now implemented.
5. Add paired relative-value research as a separate strategy mode. The paper-only signal engine is now implemented; coordinated execution remains gated by synchronized data and leg tracking.
6. Evaluate single-leg, paired, and future basis modes independently.
7. Deploy only paper mode until each mode demonstrates positive net expectancy over a meaningful sample.

## Detailed execution phases

### Phase 0: baseline
- Record Git branch, commit, remote, container health, database counts, balance, fees, and current strategy constants.
- Do not overwrite historical SQLite volumes.

### Phase 1: documentation and accounting
- Document architecture, strategy, data model, operations, and agent workflow.
- Make Telegram performance persistent across restarts.
- Reconcile open fees, close fees, gross PnL, and net PnL.

### Phase 2: telemetry
Add strategy metadata to each trade: mode, trade group, leg, expected gross, expected fees, expected net, realized gross, realized fees, realized net, slippage, regime, rank, CVD divergence, and universe snapshot.

### Phase 3: aggressive single-leg improvement
- Keep universe rotation and multi-pair behavior.
- Use CVD divergence as an entry feature, not only an exit or veto.
- Use regime-adaptive target distance.
- Size by expected net edge while preserving aggressive participation.
- Track BUY and SELL separately.

### Phase 4: paired relative value
- Select liquid, correlated pairs.
- Buy the relatively weak leg and sell the relatively strong leg.
- Track both legs under one trade group.
- Close on spread normalization, invalidation, timeout, or risk failure.
- Include both legs' fees and slippage.

### Phase 5: evaluation and deployment
- Require accounting invariants to pass.
- Compare modes by net expectancy, fee-to-gross ratio, drawdown, and stability.
- Keep paper mode until at least 100 trades per mode are available.
- Verify Docker health, feed liveness, Telegram commands, and database continuity after every deployment.

## Acceptance criteria
- Positive net expectancy after all fees for the evaluated mode.
- Gross PnL minus fees equals reported net PnL.
- No unexplained missing or duplicate trade rows.
- Telegram values match the persistent database.
- Aggressive entries remain active; improvements must target edge quality and trade economics, not indiscriminate blocking.

## Current baseline snapshot
Captured before documentation commit: branch `main`, remote `origin` at `https://github.com/hernanda-git/vaisravana-alpha.git`, Alpha container healthy, 138 closed rows, aggregate wave_log PnL approximately `+$0.0160`, recorded close fees approximately `$0.2760`. The wallet and database fee definitions must be reconciled before using this figure as a profitability verdict.

## Explicit non-goals
- No claim that counter-trading alone is arbitrage.
- No live-money deployment based on a small sample.
- No deleting historical data to improve reports.
- No broad cooldown or blocking gate as a substitute for edge.

## Rollback
Every code change must be reversible through Git. Docker rollback means rebuilding the previous verified commit while preserving the named Alpha data volume.

## Commit convention
Use small conventional commits such as `docs:`, `feat:`, `fix:`, and `test:`. Never stage `.env`, credentials, database files, caches, or generated artifacts.

## Push convention
Verify branch and remote, inspect staged files, run tests and build, then push. Report the resulting commit SHA and remote branch.