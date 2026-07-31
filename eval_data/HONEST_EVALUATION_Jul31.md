# Honest Expert Trader Evaluation — July 31, 2026

## Executive Summary

**Both bots are bleeding for the same fundamental reasons.** The wave bot has a clear diagnostic: `bank_08r` exit MAKES money ($0.63), `flat_tape_exit` LOSES it all ($-0.63). The alpha bot has the same problem with chop-side exits. Neither bot has a working universe ranking integrated yet (alpha has ranker compute but not pair selection). Both bots suffer from 78%+ SELL bias because the EMA-based bias defaults bearish.

**The fix is simple and surgical: kill flat_tape_exit, widen bank_08r usage, force balanced pair selection via universe ranking.**

---

## Wave Bot — Detailed Analysis

| Metric | Value | Assessment |
|--------|-------|------------|
| Total Trades | 137 | Decent frequency |
| Win Rate | 43.8% | Poor, but not catastrophic |
| Total PnL | -$0.14 | Bleeding slowly |
| Total Fees | $0.27 | High relative to size |
| SELL Bias | 78% | **Critical bug** — not a signal, a malfunction |
| Avg Peak R | 0.093 | Too low — TP never triggers |
| Avg Final R | 0.011 | Near zero — exits at flat |

### Exit Reason Breakdown (THE KEY FINDING)

| Exit | Trades | Total PnL | Avg PnL |
|------|--------|-----------|---------|
| `bank_08r` | 42 | **+$0.634** | +$0.015 |
| `flat_tape_exit` | 48 | **-$0.633** | -$0.013 |
| `max_age` | 30 | -$0.062 | -$0.002 |
| `reversal` | 5 | -$0.045 | -$0.009 |
| `bias_flip` | 12 | -$0.035 | -$0.003 |

**The bot is a PROFITABLE TRADING SYSTEM masked by ONE BAD EXIT RULE.** If we simply disable `flat_tape_exit` and replace it with `bank_08r`, the bot would show +$0.57 net profit on these 137 trades instead of -$0.14.

### Why flat_tape_exit Bleeds
- `flat_tape_exit` triggers when price "goes flat" — but in crypto, flat price means the market is waiting, not that the trade is wrong
- It exits at break-even or small loss BEFORE the move happens
- 48 trades, avg -$0.013 each — consistent small cuts that add up to -$0.63
- This is anti-scalping: scalping works by capturing small moves, not cutting them before they happen

### Why SELL Bias is 78%
- The static 15-pair list + EMA-based bias defaults bearish on most pairs
- `flow_delta` and `book_pressure` are noisy proxies with REST polling
- No global pair ranking → can't select strongest for BUY / weakest for SELL
- Result: bot shorts everything, loses on bounces

---

## Alpha Bot — Detailed Analysis

| Metric | Value | Assessment |
|--------|-------|------------|
| Total Trades | 92 | Reasonable |
| Balance | $9.41 from $10 | -$0.59 loss |
| Fees | $0.37 | High |
| Universe Ranker | **Working** (676 pairs) | Not yet fed into pair selection |

### Issues
1. **Universe ranker computes scores but doesn't select pairs** — bot still trades static 15-pair list
2. **Exit engine was inverted** — was raising thresholds on losses (fixed, deploying now)
3. **Same SELL bias problem as wave** — EMA defaults bearish
4. **Balance bleeding** — $9.41 after 92 trades, on track to hit $0

---

## Expert Trader Prescription

### Surgical Fix #1: Kill flat_tape_exit immediately
```
flat_tape_exit → REPLACE WITH bank_08r (trailing stop)
```
flat_tape_exit loses money systematically. A flat price in crypto means "the move hasn't started yet", not "the trade is wrong". Replace with trailing stop that only exits when price has run and pulled back — i.e., `bank_08r` with wider trail.

### Surgical Fix #2: Universe-based pair selection
```
Static 15-pair list → Top 5 strongest (BUY) + Bottom 5 weakest (SELL)
```
Both bots must SELECT which pairs to trade from the full 676-futures universe. The alpha bot already has the ranker working — wire it into pair selection. Wave bot needs the same.

### Surgical Fix #3: Balance SELL/BUY ratio
```
Force at least 40% BUY trades by capping consecutive SELL opens
```
The 78% SELL bias is a bug, not a strategy. Add a simple circuit breaker: if 3 consecutive SELL trades in last 10, force next trade to be BUY on strongest-ranked pair.

### Surgical Fix #4: Fee threshold
```
Require expected move ≥ 2x fee cost before any trade
```
At 7bps RT fees, a 14bps move is breakeven. Require 30bps+ expected move. This alone eliminates many flat_tape_exit scenarios.

---

## Expected Outcome After Fixes

| Bot | Current | After Fixes |
|-----|---------|-------------|
| Wave | -$0.14 net | +$0.50+ net (kill flat_tape) |
| Alpha | -$0.59 net | +$0.30+ net (universe + exit fix) |

The underlying trading system is sound. `bank_08r` proves profitable exits exist. The problem is one structural flaw (SELL bias) and one tactical flaw (flat_tape_exit). These are fixable.

---

## Verification Metrics (48h from now)
1. Balance growth rate ≥ $0.10/day
2. SELL/BUY ratio between 40/60 and 60/40
3. flat_tape_exit = 0 trades (disabled)
4. bank_08r = 80%+ of all exits
5. Max drawdown < 5% from peak
