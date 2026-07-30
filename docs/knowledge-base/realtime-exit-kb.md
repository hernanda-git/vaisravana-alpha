# Real-Time Exit Engine Knowledge Base

## Core Principle: Exit When Balance Growth Is At Risk

The entry decision asks "is there an edge?" The exit decision asks
"does holding this position still serve the balance?"

A position that is currently profitable can become unprofitable. A position
that is currently losing can become more losing. The exit engine monitors
real-time signals and closes when the expected value of holding drops below
the expected value of exiting (minus fees).

---

## Signal Categories (per tick, 100-500ms)

### 1. Structural Factors (1s-10s horizon)
Short-term price action that signals trend integrity.

| Factor | Signal | Exit trigger |
|--------|--------|-------------|
| EMA slope cascade | 9/21/55 EMA slopes diverge | Trend weakening |
| VWAP deviation | Price far from VWAP | Mean reversion likely |
| Structure break rejection | Swing broken then reclaimed | Trapped traders |
| Volume spike on rejection | High volume, no follow-through | Distribution |

### 2. Momentum Factors (10s-60s horizon)
Medium-term momentum that signals continuation or exhaustion.

| Factor | Signal | Exit trigger |
|--------|--------|-------------|
| RSI(3) divergence | Price makes high, RSI doesn't | Momentum fading |
| ROC(5) acceleration | Return rate decelerating | Trend losing steam |
| Volume profile shift | Volume distribution changes | Institutions shifting |
| Tick volume imbalance | Buy/sell volume ratio shifts | Sentiment changing |

### 3. Order-Flow Proxy (1s-10s horizon)
Market microstructure signals from price/volume behavior.

| Factor | Signal | Exit trigger |
|--------|--------|-------------|
| Bid/ask spread widening | Spread expanding | Market makers fleeing |
| Imbalance ratio | (ask_vol - bid_vol) / total | Liquidity shifting |
| Price congestion | Stuck at level | Resistance/support |
| Delta proxy | Candle body shrinking | Momentum stalling |

### 4. Volatility Regime
Market volatility context that affects exit timing.

| Factor | Signal | Exit trigger |
|--------|--------|-------------|
| ATR percentile | High vs 20-period | Volatility exhaustion |
| Realized vol shift | 1min vol vs 5min vol | Regime change |
| Keltner position | Near outer channel | Extreme reached |

### 5. Liquidity Zone Awareness
SMC-based signals from identified liquidity zones.

| Factor | Signal | Exit trigger |
|--------|--------|-------------|
| Order block proximity | Near identified zone | Likely reversal |
| Liquidity sweep | Price spikes then reverses | Retail liquidated |
| Depth proxy | Spread + volume at level | Low depth = move coming |

---

## Exit Confidence Model

```
exit_conf = w1·f_structural + w2·f_momentum + w3·f_orderflow +
            w4·f_volatility + w5·f_liquidity
```

Where each `f()` returns a normalized 0..1 score (1 = strong exit signal),
and weights adapt based on:
- Current market regime (mean-revert vs momentum)
- Asset characteristics (altcoin vs BTC)
- Historical accuracy of each factor

### Exit thresholds
| exit_conf | Action |
|-----------|--------|
| > 0.85 | Close 100% (high confidence trend reversing) |
| > 0.70 | Close 50% (medium confidence, trail rest) |
| 0.30-0.70 | Hold (uncertain, wait for clarity) |
| < 0.30 | Hold or add (low exit pressure) |
| < 0.15 | Consider flipping bias (strong contrary signal) |

---

## Fee-Aware Exit Logic

### Critical constraint
Every exit pays `close_fee = notional × 0.0004`. The exit signal must
clear this cost:

```
expected_salvage = current_pnl - close_fee
if expected_salvage < 0:
    hold unless drawdown > threshold  # don't pay to lose
```

### Salvage calculation
```
current_value = entry_price + (current_pnl / size)
exit_value    = current_price - close_fee_per_unit
salvage       = exit_value - entry_price  (per unit)

if salvage > 0:
    exit_conf threshold can be lower (salvage positive)
if salvage < 0:
    exit_conf threshold must be higher (avoid paying to lose)
```

### Trailing logic
Once `exit_conf > 0.70`:
- Close 50% at current price
- Move remaining position to breakeven + fee
- Trail the rest with a dynamic stop based on exit_conf

---

## Single-Pair Focus: Why Altcoin First

### Altcoin characteristics (e.g., 1000BONKUSDT)
1. **High volatility**: 5-15% daily swings → rich signal environment
2. **Mean-reverting tendency**: pumps get sold, dumps get bought
3. **Liquidity concentration**: few whales → order flow signals stronger
4. **Noise**: lots of fake breakouts → structural factors more valuable
5. **Coordination risk**: coordinated pumps/dumps → volatility regime shifts

### Learning advantage
- Single pair = no cross-asset noise
- Every tick is relevant data
- Pattern recognition converges faster
- Factor weights can be highly tuned

---

## Real-Time Monitoring Loop

```
every 100-500ms:
    1. receive tick (price, volume, bid/ask)
    2. update ContextStore (EMA, VWAP, RSI, etc.)
    3. compute all 5 factor categories
    4. calculate exit_conf with current weights
    5. check fee-aware salvage
    6. if exit_conf > threshold:
        - execute close (full or partial)
        - record trade to agentic DB
    7. if position closed:
        - update learning layer with outcome
        - adjust weights based on accuracy
```

---

## Learning Layer (post-trade)

### What it learns
1. **Exit timing accuracy**: was exiting at this exit_conf level optimal?
   - Compare actual exit vs hypothetical exits at different conf levels
   - Reward: higher PnL or lower drawdown

2. **Confidence calibration**: does exit_conf=0.85 mean 85% accuracy?
   - If overconfident: raise thresholds
   - If underconfident: lower thresholds

3. **Factor importance**: which factors predicted good exits?
   - Track each factor's contribution to successful exits
   - Adjust weights via gradient-free optimization

4. **Regime detection**: when does the model perform well vs poorly?
   - Classify market state (trending, mean-revert, chop)
   - Use regime to select weight set

### Feedback loop
```
trade_closed → evaluate exit quality → adjust weights → next trade
```

The agentic DB records every exit decision with its exit_conf, the factors
that drove it, and the outcome. The learning layer queries this to improve.

---

## Integration with Multi-Layer Evaluation

The real-time exit engine feeds into the evaluation stack:

- **L0**: Exit decisions are recorded with full factor breakdown
- **L1**: Exit timing is checked for consistency (no random exits)
- **L2**: Exit accuracy is statistically evaluated (DSR on exit timing)
- **L3**: Net PnL after exit fees is the economic verdict
- **L4**: Exit robustness across market regimes
- **L5**: Max drawdown includes pre-exit drawdown (exit timing affects risk)

The exit engine is not separate from evaluation — it is the real-time
manifestation of the evaluation principles.