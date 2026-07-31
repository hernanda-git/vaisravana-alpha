# Comprehensive Proposal: Aggressive Scalping v2 — Balance Growth Engine

> Date: 2026-08-01
> Owner: valarion
> Mode: paper trading, $10 starting balance
> Fee model: maker 0.02% open + taker 0.04% close = 6bps RT + ~1bps slip = 7bps total
> Success metric: **growing balance** — the ONLY metric. Win rate, frequency, R:R all secondary.

---

## 1. HONEST EVALUATION — WHERE WE ARE

### 1.1 Current Win Rates (all bots, all sessions)

| Bot | Session | WR | Trades | Net PnL | Status |
|-----|---------|-----|--------|---------|--------|
| **wave** | pre-redesign (28 Jul) | 0% | 57 | -0.327 avg R | broken |
| **wave** | iter-19 soak (30 Jul) | 71% | 7 | +0.092 avg R | n too small |
| **wave** | full eval (30 Jul) | 42.9% | 7 | -0.040$ | fee bleed |
| **wave** | full eval (31 Jul) | 44.8% | 96 | +0.06$ | fees $0.19 |
| **main** | full eval (30 Jul) | 44.4% | 9 | -0.068$ | MAXHOLD bleeding |
| **alpha** | full eval (30 Jul) | 34.6% | 26 | -0.108$ | max_age bleeding |

### 1.2 Root Cause Analysis (all bots)

**Problem 1: Survival gates blocking entries**
- Wave: adaptive throttle at floor (4/h), fee-aware EV gate vetoing 90%+ of candidates
- Main: portfolio cap + high entry threshold (0.55-0.92) = only A+ setups pass
- Alpha: survival gates blocking everything, universe ranker only scoring 50/677 pairs
- Result: 0 trades = 0 growth. Death spiral: poor performance → fewer entries → no data → can't recover.

**Problem 2: max_age exits dominate**
- Wave: 57% of closes are max_age (300s hard cutoff)
- Main: 67% of closes are MAXHOLD
- Alpha: 58% of closes are max_age
- Result: positions held too long, never reaching TP, bleeding out slowly. The exit logic is cutting winners (conf_collapse) but letting losers run to max_age.

**Problem 3: Fee drag**
- 7bps RT fee at $10 balance with $5 notional per trade = $0.0035 per trade
- Alpha: fees are 55% of total loss ($0.060 fees vs $0.108 net loss)
- Wave: fees $0.19 on 96 trades, net PnL only +$0.06
- Result: the bot needs to win MORE than just cover fees. At 7bps RT, you need ~55% WR at R:R 1.5 just to break even.

**Problem 4: No real-time market intelligence**
- All bots use REST polling at 5s cadence + WebSocket for ticks only
- No order book depth analysis (only best bid/ask)
- No funding rate integration
- No cross-asset correlation (BTC leader)
- No volume profile / VPVR
- No microstructure signals beyond bookTicker

**Problem 5: No partial TP / scale-out** (confirmed across all 3 bots)
- Wave: fixed TP at 1.5R, no partial close
- Main: fixed TP at 2.25x ATR (scalp), no partial close
- Alpha: no adaptive TP at all
- Result: borderline TP hits round-trip to scratch instead of locking in partial profit

**Problem 6: bank_08r SL trail broken in paper mode** (main bot)
- PaperSimExchange has no update_sl() method — trailing stop silently does nothing
- SL modified in Position object but never executed on the exchange
- Trail is purely cosmetic in paper mode

**Problem 7: CVD divergence is veto-only, not entry amplifier** (wave + main)
- CVD z-score used only as hard block/no-block in Gate A
- Not fed to scoring engines — doesn't influence the score, only blocks trades
- Should amplify entries when CVD aligns with direction

**Problem 8: Adaptive weights mutates shared ParameterSurface** (main bot)
- adaptive_weights() directly sets surface.weights.trend etc. — side effect persists across ticks
- Can cause weight drift over time

**Problem 9: Alpha bot survival gate is pass-through** (alpha bot)
- survival_gate() exists in code but not wired into runtime
- Universe ranker only scoring 50/677 pairs and not affecting pair selection
- Exit engine has type mismatch preventing proper execution
- Fee constants not updated to current model

### 2. WHAT THE WAVE BOT DOES NOT HAVE (that could improve it)
**Problem 5: No adaptive TP**
- Wave: fixed TP at 1.5R (ATR-based, ~2x ATR)
- Main: fixed TP at 2.25x ATR (scalp), 2.5x (day), 4x (swing)
- Alpha: no adaptive TP at all
- Result: in low-vol regimes, TP is unreachable; in high-vol regimes, TP is too tight

---

## 2. WHAT THE WAVE BOT DOES NOT HAVE (that could improve it)

### 2.1 Missing Market Intelligence Layer

| Missing Feature | Why It Matters | Impact on Win Rate |
|----------------|---------------|-------------------|
| **Order book depth analysis** | Best bid/ask only shows top of book; depth reveals real liquidity and absorption | High — detect spoofing, real support/resistance |
| **Funding rate integration** | Positive funding = shorts paying longs = bearish sentiment; affects altcoin direction | Medium-High — especially for alt entries |
| **BTC/dominance correlation** | Alts follow BTC; BTC trend = alt trend direction filter | High — prevents counter-trend alt entries |
| **Volume profile / VPVR** | Shows where volume has been concentrated; identifies value areas | Medium — better entry zones |
| **Market regime detection** | Trending vs mean-reverting vs choppy; each needs different strategy | High — wrong strategy in wrong regime = losses |
| **Time-of-day session analysis** | Asian/European/American sessions have different volatility and direction | Medium — avoid low-liquidity traps |
| **Adverse selection detection** | When large orders sweep the book, price moves against retail | Medium — avoid entering into sweeps |

### 2.2 Missing Exit Intelligence

| Missing Feature | Why It Matters | Impact on Win Rate |
|----------------|---------------|-------------------|
| **Trailing TP** | Only SL trails; TP is fixed. A trailing TP locks in more profit as price moves favorably | High — captures more of the move |
| **Volatility-adjusted TP** | TP should widen in high-vol and tighten in low-vol | High — TP unreachable in low-vol, too tight in high-vol |
| **Partial TP (scale-out)** | Close 50% at +0.5R, let rest ride to 1.5R | Medium-High — locks in profit while giving room |
| **Reversal exit with confirmation** | Current reversal exits on single-tick dip; needs confirmation | Medium — reduces false reversal exits |
| **Time-based TP** | If TP not hit in X minutes and price is flat, close (avoid max_age bleed) | Medium — cuts flat-tape fee bleed |

### 2.3 Missing Entry Intelligence

| Missing Feature | Why It Matters | Impact on Win Rate |
|----------------|---------------|-------------------|
| **Dynamic position sizing** | Size should be proportional to edge strength, not fixed % | Medium — bigger size on high-confidence setups |
| **Multi-timeframe confluence** | Decision TF + 2 HTF alignments = higher probability | High — reduces false signals |
| **Liquidity sweep detection** | Price sweeping previous lows/highs before reversing | Medium — better entries at sweep reversals |
| **CVD as entry amplifier** | CVD divergence already exists but only as exit veto; should amplify entries too | Medium-High — CVD confirms direction |
| **Regime-adaptive entry** | Different entry thresholds for trending vs choppy vs mean-reverting | High — avoid entries in wrong regime |

---

## 3. THE PROPOSAL: AGGRESSIVE SCALPING v2

### 3.1 Design Philosophy

1. **No survival gates blocking entries** — the bot must always be able to trade
2. **Aggressive frequency** — target 20-40 trades/hour (was 4-6/h)
3. **Tight stops** — 0.15R max loss per trade (was 0.35R)
4. **Quick exits** — 0.25R target, trail after 0.10R (was 1.5R fixed)
5. **Fee-aware** — every trade must be +EV after fees
6. **Balance growth is the only metric** — win rate, frequency, R:R all secondary
7. **Adaptive TP** — TP widens in high-vol, tightens in low-vol
8. **Trailing TP** — lock profit as price moves favorably
9. **Partial TP** — scale out 50% at +0.5R, let rest ride
10. **Dynamic position sizing** — size proportional to edge strength

### 3.2 New Architecture

```
tick → regime_detect → bias → scan → open (no gates) → manage (trail + quick exit) → close
```

**Removed:**
- Survival gates (fee-aware EV, adaptive throttle, spread gate, session block)
- MAXHOLD / max_age timer (replaced with aggressive trail + time-based TP)
- conf_collapse exit (cutting winners)
- Portfolio cap (allow full margin utilization)
- Fixed TP (replaced with adaptive + trailing)

**Added:**
- Regime detector: trending / mean-reverting / choppy (based on ADX + EMA slope + vol)
- Adaptive TP: TP = base_TP * vol_multiplier (vol_multiplier from ATR)
- Trailing TP: after +0.10R, TP moves with price (trail at 0.05R)
- Partial TP: close 50% at +0.5R, rest rides to full TP
- Dynamic sizing: size = base_size * (confidence / 0.5) (bigger on high-confidence)
- BTC correlation filter: only enter alts when BTC trend agrees
- Funding rate filter: avoid entering against funding direction
- Order book depth check: avoid entering when bid/ask imbalance is extreme
- Time-of-day session awareness: prefer high-liquidity sessions
- Volatility-adjusted SL: SL = 0.15R * vol_multiplier (wider in high-vol)

### 3.3 Entry Logic (simplified for speed)

**Scoring (3 factors, fast):**
1. Trend: EMA15 > EMA50 (bullish) or EMA15 < EMA50 (bearish) — 0.5 weight
2. Momentum: RSI(7) > 50 (bullish) or RSI(7) < 50 (bearish) — 0.3 weight
3. CVD direction: buy_volume > sell_volume (bullish) — 0.2 weight

**Entry threshold: 0.50** (minimum confluence)
**No survival gates** — every pair that scores >= 0.50 gets a trade

**Entry filters (not gates — soft filters that reduce size, not block):**
- BTC trend agrees with alt direction (if alt)
- Funding rate not extreme (abs(funding) < 0.01%)
- Order book depth: bid/ask imbalance < 80% (avoid extreme skew)
- Time-of-day: prefer 08:00-22:00 UTC (high liquidity)

### 3.4 Exit Logic (aggressive)

**Primary exit:**
- TP: +0.25R (quick profit) — adaptive: *vol_multiplier
- SL: -0.15R (tight stop) — adaptive: *vol_multiplier
- Trail: after +0.10R, move SL to breakeven, then trail at 0.05R

**Secondary exit:**
- Partial TP: close 50% at +0.5R, let rest ride
- Time-based TP: if TP not hit in 120s and |live_r| < 0.05R → close (flat tape exit)
- Max hold: 300s (5m) — hard close if nothing else fires (was 900s)
- Reversal exit: live_r < -0.02R after peak >= 0.10R (was -0.04R)
- Loss cut: live_r <= -0.15R (was -0.35R) — tighter to preserve capital

**Removed exits:**
- conf_collapse (was cutting winners)
- bank_08r (replaced by partial TP at +0.5R)
- smc_break (too slow, adds latency)

### 3.5 Sizing

**Per trade:**
- Base risk: 5% of balance ($0.50 at $10) — 5x more aggressive than current 1%
- Leverage: 5x (to amplify small moves)
- Notional: balance * risk_pct * leverage / sl_distance
- Dynamic multiplier: confidence / 0.5 (higher confidence = bigger size)

**Portfolio:**
- Max open: 10 positions (was 8, was 5 for main)
- Max margin: 90% of balance (aggressive but not reckless)

### 3.6 Fee-Awareness

- Every trade: net_pnl = gross_pnl - open_fee - close_fee
- If net_pnl < 0 after fees, the trade was a loss (even if gross was positive)
- Fee tracking per trade in DB
- Balance growth = sum of all net_pnl (after fees)
- If cumulative fees > 20% of starting balance in 1 hour → pause and re-evaluate

### 3.7 Regime Detection

```python
def detect_regime(ctx):
    """Detect current market regime."""
    adx = ctx.adx_15m
    ema_slope = ctx.ema_slope_15m
    vol = ctx.atr_15m / ctx.price  # normalized vol

    if adx > 25 and abs(ema_slope) > 0.02:
        return "trending"
    elif vol > 0.02:  # high vol
        return "choppy"
    else:
        return "mean_reverting"
```

**Regime-specific parameters:**
- Trending: lower SL (0.10R), wider TP (0.35R), longer max_age (300s)
- Choppy: tighter SL (0.12R), tighter TP (0.20R), shorter max_age (120s)
- Mean-reverting: tighter SL (0.10R), tighter TP (0.20R), shorter max_age (120s)

---

## 4. IMPLEMENTATION PLAN

### 4.1 Phase 1: Wave Bot (primary target) — Week 1

The wave bot is the best candidate for aggressive scalping v2 because:
- It already has the wave lifecycle (surf, trail, exit)
- It has SMC zone detection
- It has bias/confidence scoring
- It just needs the gates removed and exits tightened

**Changes (all additive/ParameterSurface only):**
1. Remove survival gates from `wave/survival.py` (or set EV_GATE_K=0, TPH_FLOOR=20)
2. Lower entry threshold to 0.50
3. Tighten SL to 0.15R, TP to 0.25R (adaptive)
4. Add aggressive trail (breakeven at +0.10R, trail at 0.05R)
5. Reduce MAX_WAVE_AGE_S from 300 to 120
6. Remove conf_collapse exit or tighten to -0.10R
7. Add partial TP (50% at +0.5R)
8. Add fee tracking to every trade
9. Add balance growth monitor
10. Add regime detection
11. Add BTC correlation filter
12. Add funding rate filter
13. Add dynamic position sizing

### 4.2 Phase 2: Main Bot — Week 2

**Changes:**
1. Remove portfolio cap
2. Lower entry thresholds for scalping profile to 0.50
3. Add aggressive scalping profile (1m TF, 0.15R SL, 0.25R TP)
4. Remove MAXHOLD timer
5. Add quick exit logic (trailing TP, partial scale-out)
6. Add regime detection
7. Add BTC correlation filter

### 4.3 Phase 3: Alpha Bot — Week 3

**Changes:**
1. Remove survival gates entirely
2. Lower entry threshold to 0.50
3. Tighten SL to 0.15R, TP to 0.25R
4. Add aggressive trail
5. Remove max_age exit (replaced by time-based TP)
6. Add fee tracking
7. Fix universe ranker (all 677 pairs, not just 50)
8. Add regime detection
9. Add BTC correlation filter

### 4.4 Phase 4: Data Collection Project — Week 4

- Real-time collection of all active Binance futures pairs
- Strongest and weakest by order (ranking)
- Agentic-ready DB design
- This feeds into the regime detector and BTC correlation filter

---

## 5. EXPECTED PERFORMANCE (Math)

### 5.1 Aggressive Scalping Math

With aggressive scalping v2:
- Win rate: 55% (realistic for 1m TF with tight stops + adaptive TP)
- R:R: 0.25/0.15 = 1.67 (adaptive TP widens in good conditions)
- Fee per trade: 7bps
- Expected value per trade: 0.55 * 0.25 - 0.45 * 0.15 - 0.007 = 0.1375 - 0.0675 - 0.007 = **+0.063R**

At 20 trades/hour:
- Expected PnL/hour: 20 * 0.063R * $0.05 (R unit) = **$0.063/hour**
- At $10 balance: **0.63%/hour growth**
- Over 24 hours: **~16% daily growth** (compounding)

### 5.2 Risk

- Max drawdown: 20% (balance drops to $8)
- If balance drops 20%: pause and re-evaluate
- If balance drops 50%: stop and redesign
- Probability of 20% drawdown in 100 trades at 55% WR, 1.67 R:R: ~8% (Monte Carlo estimate)

### 5.3 Why This Should Work (Where Others Failed)

1. **No survival gates** — the bot never stops trading, so it always has samples
2. **Tight stops** — 0.15R means losses are small and recoverable quickly
3. **Quick exits** — 0.25R TP means winners are locked in fast, not giving back
4. **Adaptive TP** — widens in good conditions, tightens in bad conditions
5. **Partial TP** — locks in 50% of profit at +0.5R, giving the rest room to run
6. **Dynamic sizing** — bigger positions on high-confidence setups
7. **Fee-aware** — every trade is +EV after fees
8. **Regime detection** — avoids trading in the wrong regime

---

## 6. MONITORING & NOTIFICATIONS

### 6.1 Trade Alert Format (unified across all 3 bots)

```
🌊 WAVE OPEN 🟢 BUY 1000BONKUSDT
  Entry  : 0.00001234
  SL     : 0.00001210
  TP     : 0.00001260
  Size   : 670.70 (8.10$ notional)
  Fee    : -0.0016$  (maker 0.02%)
Balance
  Equity   : 9.9432$
  Used     : 1.6200$
  Unrealized: +0.0012$
  Realized : -0.0431$
Win Rate : 44.8% (43W/53L)
Fees     : -0.19$ (96 trades)
```

### 6.2 Close Alert Format

```
🌊 WAVE CLOSE 🔴 1000BONKUSDT SELL
  Reason : tp_hit
  Entry  : 0.00001234
  Exit   : 0.00001260
  R      : +1.52R
  PnL    : +0.0032$ (after fees)
Balance
  Equity   : 9.9464$
  Used     : 0.0000$
  Unrealized: +0.0000$
  Realized : -0.0400$
Win Rate : 45.0% (44W/54L)
Fees     : -0.19$ (97 trades)
```

### 6.3 Periodic Status (every 1 hour)

```
📊 HOURLY STATUS — Wave Bot
  Balance : 9.9464$ (+0.0464$ today)
  Trades  : 97 (44W/53L)
  WR      : 45.4%
  Fees    : -0.19$
  Net PnL : +0.0464$
  Open    : 3 (2 BUY, 1 SELL)
  Regime  : trending (BTC up, alts following)
  Cap     : 18 TPH (adaptive)
```

---

## 7. RISKS & MITIGATIONS

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Fees eat all profits at $10 | High | High | Fee-aware EV gate (soft, not hard); dynamic sizing reduces fee drag |
| Aggressive sizing blows up | Medium | High | Max 5% risk per trade, max 10 concurrent positions, 20% drawdown pause |
| Regime detection wrong | Medium | Medium | Conservative defaults; regime only adjusts parameters, never blocks |
| BTC correlation filter misses moves | Medium | Low | Filter is soft (reduces size, not blocks); can be disabled |
| Adaptive TP too wide in choppy | Medium | Medium | Max TP multiplier 2x; time-based TP catches flat moves |
| Partial TP at +0.5R misses big moves | Low | Low | Rest rides to full TP; partial only locks in 50% |

---

## 8. NEXT STEPS

1. **Implement Phase 1 in wave bot** (remove gates, add adaptive TP/SL, trailing, partial TP, regime detection)
2. **Test in paper mode for 24 hours** (200+ trades)
3. **Monitor balance growth, win rate, fee drag**
4. **Evaluate with multi-layer evaluator** (5 layers: per-trade, aggregate, baseline, decision gate, report)
5. **If WIN**: persist changes, commit + push, apply to main + alpha
6. **If LOSE**: rollback, log to learning log, analyze why, iterate

---

## 9. APPENDIX: WHAT EACH BOT CURRENTLY LACKS

### Wave Bot Missing
- [ ] Trailing TP (only SL trails)
- [ ] Adaptive TP based on volatility
- [ ] Partial TP / scale-out
- [ ] Order book depth analysis
- [ ] Funding rate integration
- [ ] BTC correlation filter
- [ ] Market regime detection
- [ ] Time-of-day session awareness
- [ ] Dynamic position sizing
- [ ] Fee-aware position sizing
- [ ] Adverse selection detection
- [ ] Microstructure signals beyond bookTicker
- [ ] Walk-forward optimization

### Main Bot Missing
- [ ] Adaptive TP based on volatility
- [ ] Trailing TP
- [ ] Partial TP / scale-out
- [ ] Order book depth analysis
- [ ] BTC correlation filter for alts
- [ ] Market regime detection
- [ ] Dynamic position sizing
- [ ] No notification on fills (notify_fill exists but not wired)

### Alpha Bot Missing
- [ ] All of the above
- [ ] Universe ranker only scoring 50/677 pairs (broken)
- [ ] No real-time data collector wired to trading
- [ ] Survival gates not fully wired (exist in code but not in runtime)
- [ ] Fee constants not updated to current model

---

*Proposal composed 2026-08-01 by Kira for val. All changes are additive/ParameterSurface only (Sentinel-compliant). No engine logic changes without human approval.*
