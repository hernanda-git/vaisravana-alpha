# Vaiśravaṇa Alpha — Knowledge Bases & Methods (consolidated)

> Authored 2026-07-30 for the `vaisravana-alpha` repo. The alpha bot is the
> real-time exit engine, **merged into the main + wave engines for all 15 pairs**
> (EXIT_ENGINE=true). The standalone single-pair `vaisravana-alpha-exit` container
> was retired 2026-07-30. This repo is the source of truth for the *exit* doctrine.

---

## 0. What this repo is

Real-time, tick-driven exit engine (per 100–500 ms) for Binance USDT-M futures.
It monitors structural, momentum, order-flow, volatility, and liquidity-zone
signals and closes (full or partial) when the expected value of holding drops
below the expected value of exiting minus fees. Deployed on **sera** as a single
container `bots-vaisravana-alpha`, workspace `/root/vaisravana-alpha`. Exit logic
is now MERGED into alpha for all 15 pairs (EXIT_ENGINE=true). Stop-flag gotcha:
`rm /opt/bots/vaisravana-alpha/data/alpha_stop.flag` after a restart.

---

## 1. Knowledge bases

### 1.1 Multi-Layer Evaluation KB (`docs/knowledge-base/evaluation-kb.md`)

The canonical evaluation doctrine shared across all three repos. Single success
metric: **growing balance**.

Six layers, each can veto promotion independently; L0 short-circuits everything:

| Layer | Question | Key checks |
|-------|----------|-----------|
| L0 Integrity | Can we trust the numbers? | fee consistency [0.3×,3×] of `notional×0.0006`; `net == gross − (open_fee+close_fee)`; `close_ts >= open_ts`; no dup IDs; no zero-notional |
| L1 Execution | Did the machine behave? | feed liveness; warmup-aware rejection; opened→closed lifecycle; crash detection |
| L2 Statistical | Signal or noise? | **Deflated Sharpe Ratio** — ≥0.95 PASS, ≥0.80 WARN, <0.80 FAIL; min 20 trades |
| L3 Economic | Money after ALL costs? | `sum(net_usd) > 0`; `mean(net) > 0`; `fees/gross < 0.5` |
| L4 Robustness | Out of sample? | first-half vs second-half; single-trade >50% profit; single-pair >70% profit; min 40 trades |
| L5 Risk | Ruin possible? | balance hit 0 → FAIL; max DD >50% → FAIL; worst trade >25% start → FAIL |

Promotion: L0–L3 PASS and no layer FAIL. INSUFFICIENT is never a pass. Every
decision recorded in a `decisions` table with evidence, rationale, actor, and
lineage link.

### 1.2 Real-Time Exit KB (`docs/knowledge-base/realtime-exit-kb.md`)

The exit engine's operating doctrine — "exit when balance growth is at risk."

Five per-tick factor categories:
1. **Structural** (1–10 s): EMA slope cascade, VWAP deviation, structure-break
   rejection, volume spike on rejection.
2. **Momentum** (10–60 s): RSI(3) divergence, ROC(5) acceleration, volume
   profile shift, tick-volume imbalance.
3. **Order-flow proxy** (1–10 s): spread widening, imbalance ratio, price
   congestion, delta proxy (candle-body shrink).
4. **Volatility regime**: ATR percentile, realized-vol shift, Keltner position.
5. **Liquidity-zone awareness** (SMC): order-block proximity, liquidity sweep,
   depth proxy.

Exit-confidence model:
```
exit_conf = w1·f_struct + w2·f_mom + w3·f_flow + w4·f_vol + w5·f_liq
```
weights adapt by regime / asset / historical accuracy.
Thresholds: >0.85 close 100%; >0.70 close 50% + trail rest to breakeven+fee;
0.30–0.70 hold; <0.30 hold or add; <0.15 consider flipping bias.

**Fee-aware exit logic** (critical): every exit pays `close_fee = notional ×
0.0004`. Salvage rule: `expected_salvage = current_pnl − close_fee`; if `< 0`
hold unless drawdown > threshold (don't pay to lose). Trailing: once
`exit_conf > 0.70`, close 50% and trail the rest with a dynamic stop.

Single-pair focus rationale: altcoins (e.g. 1000BONKUSDT) give high volatility
(5–15% daily), mean-reversion, concentrated liquidity (strong order-flow
signals), and fast pattern convergence — rich signal environment for learning.

### 1.3 Learning layer (post-trade)

- Exit-timing accuracy: compare actual vs hypothetical exits at other conf levels.
- Confidence calibration: is `exit_conf=0.85` really 85% accurate?
- Factor importance: which factors predicted good exits (gradient-free weight adjust).
- Regime detection: classify trending / mean-revert / chop → select weight set.

Feedback: `trade_closed → evaluate exit quality → adjust weights → next trade`.
Every exit decision recorded with its `exit_conf`, driving factors, and outcome.

---

## 2. Methods

### 2.1 Real-time monitoring loop

```
every 100–500 ms:
  1. receive tick (price, volume, bid/ask)
  2. update ContextStore (EMA, VWAP, RSI, …)
  3. compute all 5 factor categories
  4. calculate exit_conf with current weights
  5. check fee-aware salvage
  6. if exit_conf > threshold: execute close (full/partial), record to DB
  7. if position closed: update learning layer with outcome, adjust weights
```

### 2.2 Integration with the multi-layer evaluation

The exit engine is the real-time manifestation of the evaluation principles:
L0 records exits with full factor breakdown; L1 checks exit-timing consistency;
L2 evaluates exit accuracy (DSR on exit timing); L3 uses net PnL after exit fees;
L4 checks exit robustness across regimes; L5 includes pre-exit drawdown.

### 2.3 Fee model (shared across all repos)

```
open_fee  = notional × 0.0002   (2 bps maker)
close_fee = notional × 0.0004   (4 bps taker)
net       = gross − open_fee − close_fee
```
Pre-trade survival gate: `expected_move_bps >= open_fee + close_fee + safety_margin`.
L0 integrity check: total fees within [0.3×, 3×] of `notional × 0.0006`.

### 2.4 Deployment

Local-to-**sera** only (no Fly.io). Container `bots-vaisravana-alpha`, single
service. Rebuild rule: `build --no-cache` + `up --force-recreate` picks up code.
Stop-flag must be removed after restart (`rm /opt/bots/vaisravana-alpha/data/alpha_stop.flag`).

---

## 3. Relationship to the other repos

- The **exit** doctrine here is consumed by both the main bot (`vaisravana`) and
  the wave engine (`vaisravana-wave`) — the alpha exit logic is merged into both
  for all 15 pairs (EXIT_ENGINE=true, 2026-07-30).
- The **evaluation** doctrine (`evaluation-kb.md`) is the parent of the wave
  `evaluator/` package's verdict logic.
- The **SMC** doctrine (`vaisravana/docs/knowledge/smc*.md`) is the microstructure
  input layer that the alpha liquidity-zone awareness category builds on.
