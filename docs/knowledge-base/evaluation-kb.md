# Multi-Layer Evaluation Knowledge Base

## Core Principle: Always Growing Balance

The single success metric is **growing balance**. Not activity, not win rate,
not Sharpe. Every evaluation layer must answer: "does this help the balance
grow, or does it risk shrinking it?"

A trade that is profitable on paper but loses money after fees shrinks the
balance. A trade that survives but draws down 40% risks ruin. An edge that
is statistically noise will, over time, transfer money to variance.

The evaluation stack enforces all of these.

---

## Layer Architecture

```
L0  INTEGRITY     → Is the data trustworthy?
L1  EXECUTION     → Did the machine behave correctly?
L2  STATISTICAL   → Is this distinguishable from luck?
L3  ECONOMIC      → Does it make money after ALL costs?
L4  ROBUSTNESS    → Would it survive out of sample?
L5  RISK          → Can it ruin the account?
```

**Evaluation order is critical.** L0 runs first and short-circuits everything
on failure. A profit number from broken accounting is worse than no number —
it is a confident lie. Each layer can veto promotion independently.

### Promotion criteria
- L0, L1, L2, L3 must all PASS
- No layer may FAIL (WARN is survivable)
- INSUFFICIENT is never a pass — "we don't know yet" must never be read as "yes"

---

## L0: Integrity — "Can we trust the numbers?"

### What it checks
1. **Fee consistency**: total fees must be within [0.3x, 3x] of `notional × 0.0006`
   - 2bps maker open + 4bps taker close = 6bps total
   - If actual fees are 10x expected, the accounting is broken
2. **Accounting identity**: `net == gross - (open_fee + close_fee)`
   - This is recomputed once at write time and stored; readers never recompute
   - If this drifts, two code paths disagree and one is wrong
3. **Timestamp ordering**: `close_ts >= open_ts`
   - A trade that closes before it opens is a clock bug
4. **No duplicate IDs**: each trade_id must be unique
   - Duplicates mean the writer ran twice for one event
5. **No zero-notional trades**: phantom fills distort every average

### Failure consequence
If L0 fails, **no downstream layer runs**. The run is rejected outright.
You cannot promote based on lies.

---

## L1: Execution — "Did the machine actually do what we think?"

### What it checks
1. **Feed liveness**: ticks > 0, or the bot was dead
2. **Warmup-aware rejection analysis**: warmup rejections are expected;
   post-warmup, a single gate owning >95% of rejections with zero opens
   is misconfiguration, not selectivity
3. **Position lifecycle**: opened positions must eventually close
   - opens > 0 but closes == 0 after 2+ hours = exit path broken
4. **Crash detection**: run status "crashed" fails immediately

### Key insight
A bot can look healthy (active logs, steady tick stream) while a single
gate silently rejects 100% of candidates. This layer exists because that
exact failure cost the predecessor a full trading day.

---

## L2: Statistical — "Is this signal or noise?"

### What it checks
**Deflated Sharpe Ratio (DSR)**: corrects for multiple testing.

When an agent searches a parameter space, the best observed Sharpe is
inflated even when no variant has an edge. DSR discounts for the number of
trials:

```
DSR = Φ( (Sharpe - E[max]) / SE )
```

Where:
- `E[max]` = expected maximum Sharpe under the null (no edge)
- `SE` = standard error of the Sharpe, skew/kurtosis-adjusted
- `Φ` = normal CDF

### Thresholds
- **≥ 0.95**: PASS — statistically significant
- **≥ 0.80**: WARN — suggestive, gather more data
- **< 0.80**: FAIL — indistinguishable from luck

### Minimum sample
- 20 trades minimum for statistical evaluation
- Below that: INSUFFICIENT (never a pass)

---

## L3: Economic — "Does it make money after ALL costs?"

### What it checks
1. **Net profitability**: `sum(net_usd) > 0`
2. **Positive expectancy**: `mean(net_per_trade) > 0`
3. **Fee burden**: `fees / gross < 0.5` (fees must not consume half the edge)
4. **Accounting consistency**: gross is positive but net is negative →
   the edge is smaller than its own cost (explicit flag)

### Key insight
This is where "always growing balance" lives. A strategy with positive
gross PnL but negative net after fees shrinks the balance. The fee model
is:

```
open_fee  = notional × 0.0002  (2bps maker)
close_fee = notional × 0.0004  (4bps taker)
net       = gross - open_fee - close_fee
```

If `net > 0` after both fees, the balance grows. If not, it shrinks.

### What it reports
- net_usd, gross_usd, fees_usd
- win_rate, avg_win, avg_loss, profit_factor
- expectancy ($/trade)
- return_pct vs start_balance

---

## L4: Robustness — "Would this survive in the real world?"

### What it checks
1. **Out-of-sample decay**: split trades into first half and second half
   - First half profitable, second half not → overfit
2. **Single-trade dependency**: one trade contributing >50% of profit →
   not a strategy, a lucky position
3. **Single-pair dependency**: one pair contributing >70% of profit →
   not robust across instruments

### Minimum sample
- 40 trades minimum for robustness evaluation

---

## L5: Risk — "Can this destroy the account?"

### What it checks
1. **Ruin**: balance reached zero at any point → FAIL
2. **Drawdown**: max drawdown > 50% → FAIL (unrecoverable)
   - > 30% → WARN (uncomfortable)
3. **Single-trade catastrophe**: worst trade > 25% of starting balance → FAIL
4. **Loss streak**: longest consecutive loss streak (informational)

### Key insight
A profitable run with 55% drawdown is not "profitable" — it is a coin flip
that happened to land green, and the next flip could zero the account.
This layer has veto power regardless of L3's verdict.

---

## Fee-Aware Close Logic

### Pre-trade
```
expected_move_bps = confidence × atr_pct × 10000
required_bps      = open_fee + close_fee + safety_margin
survival_gate passes iff expected_move_bps >= required_bps
```

The bot does not enter a trade unless the expected move exceeds the total
fee cost plus a buffer. This is the first line of defense for balance growth.

### Post-trade
Every closed trade records:
- `gross_usd` = raw PnL before fees
- `open_fee`   = notional × 0.0002
- `close_fee`  = notional × 0.0004
- `net_usd`    = gross - fees (stored, never recomputed)

The economic layer checks: is `net_usd > 0`? Only then does the balance grow.

### Real-time exit (dynamic)
For the real-time exit engine, the same fee model applies:
- A dynamic exit signal must clear `close_fee + opportunity_cost`
- If the expected remaining move < close_fee, hold (don't pay to exit)
- If drawdown exceeds 2x expected gain, exit early (salvage)

---

## Decision Framework

```
promote = L0.PASS ∧ L1.PASS ∧ L2.PASS ∧ L3.PASS ∧ ¬any.FAIL

L0.FAIL → reject (data untrustworthy)
L1.FAIL → reject (machine broken)
L2.FAIL → reject (noise, not signal)
L3.FAIL → reject (loses money after fees)
L4.WARN → escalate (needs more data)
L5.FAIL → reject (ruin risk)
```

Every decision is recorded in the `decisions` table with:
- The evidence (metrics from each layer)
- The rationale (human-readable)
- The actor (agent or human)
- A lineage link to the iteration that produced it

---

## Knowledge Base Maintenance

This document is living. Each entry should be updated when:
- A new failure mode is discovered in production
- A threshold proves too tight or too loose
- A layer produces a false positive or false negative

The evaluation code itself (`layers.py`) is the source of truth for
thresholds and formulas. This document explains the *why*.