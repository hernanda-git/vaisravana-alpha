# Multi-Layer Engine Evaluation — Wave Bot & Alpha Bot
**Date:** 2026-07-31  
**Evaluator:** Automated Code Audit (source-level, no trade data — both DBs are fresh)  
**Scope:** `/opt/bots/vaisravana-wave/src/wave/` (38 files) + `/root/vaisravana-alpha/src/vaisravana_alpha/` (41 files)  

---

## Executive Summary

Both engines share the same architecture DNA: tick-driven wave engine with bias/scanner/gate/manager pipeline. The Alpha Bot is a ~2-week-newer fork that adds **universe ranking** (677 pairs), a **real-time exit engine**, and a **cleaner dependency graph**. The Wave Bot is the older codebase with deeper iteration history and more empirical tuning.

**Critical findings:**
1. **Neither engine reloads open waves from DB on restart** — container restart = orphaned positions = silent capital loss  
2. Both engines have a **bearish bias default** baked into their EMA-based scoring (0.40 weight on `ema_15m vs ema_1h` — if EMAs are unseeded, score collapses to 0 → neutral → no trades, NOT bearish)  
3. Alpha Bot's survivor gate is a **no-op pass-through** (aggressive scalping mode) — no fee-aware EV gate, no throttle, no spread gate  
4. Wave Bot's cooldown length of 600s (10 min) is **longer than alpha's 120s (2 min)** — Wave is more conservative  
5. The Wave Bot's conf_collapse exit has an **adverse-excursion gate** (must be underwater ≥ 0.20R); Alpha has **no such gate**  

---

## 1. Entry Logic

### 1.1 Bias Engine

| Component | Wave Bot (wave/bias.py) | Alpha Bot (strategy/bias.py) | Delta |
|-----------|------------------------|------------------------------|-------|
| **mtf_ema weight** | 0.40 | 0.30 | Alpha reduced; universe now dominates |
| **flow_delta weight** | 0.25 | 0.15 | Alpha cuts flow weight by 40% |
| **book_pressure** | 0.20 | 0.10 | Both have real top-of-book size imbalance (iter-D) |
| **risk_regime weight** | 0.10 | 0.05 | Both derive from BTC ema_15m vs ema_1h cross |
| **breadth weight** | 0.05 | 0.05 | Identical cross-pair trend agreement |
| **universe weight** | — | **0.35** | NEW: Alpha's dominant bias component |
| **BIAS_THRESH** | 0.06 | 0.03 | Alpha has lower threshold (more sensitive) |
| **BIAS_SATURATE** | 0.60 | 0.25 | Alpha saturates strength faster |
| **MIN_BIAS_STRENGTH** | 0.40 | 0.25 | Alpha allows weaker signals through |
| **Neutral fallback** | direction="neutral" | micro-fallback to flow+book (direction always bullish/bearish) | Alpha NEVER goes neutral — always picks a side |

**Analysis:**
- **Wave Bot's bias is conservative**: higher thresholds, neutral fallback = fewer trades, fewer false positives.
- **Alpha Bot's bias is aggressive**: lower thresholds, micro-fallback direction = more trades, more noise exposure.
- The Wave Bot's 40% mtf_ema weight anchors bias to the 15m/1h EMA cross — a medium-term trend signal. If these EMAs are unseeded (fresh DB, first minute), `ema_1h == 0` → trend falls back to `ema_15m vs price` which collapses → bias ~0 → neutral → NO TRADES until EMAs populate.
- The Alpha Bot's 35% universe weight is the single largest component. If the UniverseRanker hasn't finished its first fetch (takes 2-5 seconds to fetch 677 pairs' tickers), universe_score = 0 → bias is reduced by 35% of its theoretical range. On a fresh start, the first 60 seconds of trading operate at 65% effective bias range.

**Verdict:** Wave Bot's bias is calibrated for multi-pair survivability (lower WR targets, higher hit rate). Alpha Bot's bias is designed for high-frequency single-pair scalping on BONK. Neither is broken, but Alpha's bias will produce **more trades on noise** and Wave's bias will produce **fewer trades, higher conviction**.

### 1.2 Scanner

Both scanners are nearly identical: check bias direction agreement → detect structure → pass quality gate → return Candidate.

**Alpha Bot additions (v0.1):**
- **CVD divergence entry amplifier** (`cvd_divergence_bonus = 0.15`): if flow_delta is collapsing while price moves in trade direction, candidate strength gets a 15% boost. This is a smart-money-detection edge that Wave Bot lacks.
- Regime label passed to candidate → enables regime-adaptive TP widths in the manager.

**Wave Bot additions (iter-22):**
- **Gate rejection logging**: if a candidate is rejected by the quality gate, the reason is logged (throttled to once per 60s per pair+side). Alpha Bot has NO equivalent — rejected candidates are silent.

**Verdict:** The scanner layers are both solid. Alpha's CVD divergence bonus is the superior design — it adds entry edge without adding false positives. Wave's rejection logging is critical diagnostics that Alpha should adopt.

### 1.3 Quality Gate (wave_quality_pass)

| Check | Wave Bot | Alpha Bot | Notes |
|-------|----------|-----------|-------|
| Bias direction | ✅ | ✅ | Same |
| Bias strength floor | 0.30 | 0.10 | Alpha is MUCH looser |
| Confidence floor | 0.12 | 0.10 | Alpha slightly looser |
| Structure score floor | 0.12 | 0.10 | Alpha slightly looser |
| ADX floor | 18 | 15 | Alpha allows more chop |
| SMC zone check | Only if zones seeded | Only if zones seeded | Same safety guard |
| SMC invalidation | Only if zones seeded | Only if zones seeded | Same |

**Verdict:** The Wave Bot's gate is ~2-3x stricter on bias strength (0.30 vs 0.10). Combined with the Wave Bot's higher BIAS_THRESH (0.06 vs 0.03), this means Wave Bot rejects the majority of marginal signals while Alpha Bot lets them through. On a $10 paper account paying 6bps round-trip fees, the Wave Bot's selectivity is rational — every false-positive trade costs ~12.6 bps minimum.

### 1.4 Survival Gates (Admission Control)

| Gate | Wave Bot | Alpha Bot |
|------|----------|-----------|
| Fee-aware EV gate | ✅ K=1.4, requires E[move] ≥ 1.4×(6bps fee + slip + spread) | ❌ **DISABLED** — AggressiveScaler passes everything |
| Trade-frequency throttle | ✅ Adaptive, 4-20 TPH based on realized expectancy | ❌ **DISABLED** |
| Pair spacing | ✅ 20 min minimum between same-pair opens | ❌ **DISABLED** |
| Spread gate | ✅ Rejects pairs with >5bps spread | ❌ **DISABLED** |
| Session block | ✅ Blocks UTC 0-5 (low liquidity) | ❌ **DISABLED** |

**This is the single largest design gap between the two bots.** Alpha Bot's `survival.py` is a 47-line pass-through:

```python
class AggressiveScaler:
    """Always allows entries — no gates, no throttling."""
    def allowed(self, pair: str) -> tuple[bool, str]:
        self._open_count += 1
        return True, "ok"
```

This means Alpha Bot will:
1. **Trade through every spread** — on thin alts like BONK, spread can exceed 5bps and eat the entire expected edge
2. **No per-hour cap** — can open hundreds of waves/hour on a single pair, each paying 6bps RT fees
3. **No pair spacing** — can re-enter the same pair+side immediately after close (only cooldown applies)
4. **Trade during low-liquidity UTC 0-5** — when adverse selection is highest

On a $10 account with 20% risk sizing ($2 notional/wave), a 6bps RT fee is $0.0012/wave — negligible per trade. But with no throttle, at 200+ trades/hour the fee bleed alone drains the account without any adverse PnL.

**Verdict:** Alpha Bot's survival gate is intentionally disabled for "aggressive scalping." This is a deliberate design choice, but it shifts all risk management onto the exit logic. If the exit engine doesn't cut losers fast enough, the unlimited entry rate will compound the bleed. Wave Bot's survival gates are mature and empirically tuned.

---

## 2. Exit Logic

### 2.1 Close Reasons — Priority Order

Both managers evaluate exits in the same priority order:

| Priority | Reason | Wave Bot | Alpha Bot | Profit/Loss Design |
|----------|--------|----------|-----------|---------------------|
| 0 | **tp_hit** | Full TP at ~2.0x ATR | Full TP at 1.5-3.0x ATR (regime-adaptive) | ✅ PROFITABLE |
| 0a | **bank_08r** | Close at peak_r ≥ 0.15 | Close at peak_r ≥ 0.50 (BANK_R) | ✅ PROFITABLE (strictly net-positive after 6bps fee) |
| 0b | **reversal** | Close at peak_r ≥ 0.12 AND live_r < -0.02 | Close at peak_r ≥ 0.12 AND live_r < -0.04 | 🟡 DEFENSIVE (banks scratch, prevents round-trips) |
| 0c | **flat_tape_exit** | ✅ ENABLED (peak_r ≤ 0.10 AND live_r ≤ -0.10) | ❌ ABSENT | 🟡 DEFENSIVE (cuts fee bleed on dead rides) |
| 0d | **loss_cut** | Close at live_r ≤ -0.35 | Close at live_r ≤ -0.50 | 🔴 LOSS (pure loss protection) |
| 1 | **anchor_hit** | Price crossed SL | Price crossed SL | 🔴 LOSS (structural invalidation) |
| 2 | **bias_flip** | Bias flipped against wave + confirm delay | Same (CONFIRM_MS = 150ms vs 250ms for Wave) | 🟡 DEFENSIVE |
| 3 | **conf_collapse** | conf < 0.16 AND live_r ≤ -0.20 (adverse gate) | conf < 0.10 (NO adverse gate) | 🔴 LOSS (Wave has smarter gate) |
| 4 | **smc_break** | Matured CHoCH/BOS against wave | Same | 🟡 DEFENSIVE |
| 5 | **max_age** | 300s (5 min) | 300s (5 min) | 🟡 MIXED (some profitable, some fee bleed) |

### 2.2 Key Differences in Exit Design

**Profit-banking (bank_08r):**
- Wave Bot: `peak_r ≥ 0.15` → close immediately. This is empirically tuned (run21: avg peak 0.12R on 459 historical closes).
- Alpha Bot: `peak_r ≥ 0.50` (default BANK_R=0.50). **This is 3.3x higher.** Alpha Bot will let winners run further but will also let more small winners round-trip.

**Flat-tape exit (Wave only):**
The Wave Bot has a unique early-exit gate: if a wave peaked shallow (≤0.10R) AND is drifting negative (≤-0.10R), close it early rather than waiting for max_age. This was added after telemetry showed max_age as the dominant loss bucket. Alpha Bot has no equivalent — a shallow-peaking wave will ride to max_age and pay full fees.

**Confidence collapse adverse gate (Wave only):**
Wave Bot requires BOTH low confidence (<0.16) AND adverse excursion (live_r ≤ -0.20) to fire conf_collapse. This prevents conf dips on flat/positive waves from cutting trades prematurely (the "conf_collapse as worst exit bucket" finding from runs 34/36/37). Alpha Bot fires conf_collapse at ANY live_r when confidence drops below 0.10 — this is more aggressive and may cut winners.

**Loss-cut:**
- Wave: -0.35R (tighter, capital preservation from iter-16 telemetry)
- Alpha: -0.50R (wider, gives trades more room)

### 2.3 Real-Time Exit Engine (Alpha Only)

Alpha Bot has a dedicated `ExitEngine` (722 lines) that runs in parallel with the structural exits. Architecture:
- **RegimeDetector**: classifies MOMENTUM / MEAN_REVERT / CHOP from EMAs + ATR percentile + CVD
- **FactorPipeline**: computes 5 normalized factor scores (structural, momentum, orderflow, volatility, liquidity)
- **ExitConfidenceModel**: regime-weighted factor blend → exit confidence 0..1
- **FeeAwareDecision**: adjusts thresholds based on salvage (PnL if exited now — negative salvage LOWERS exit threshold for faster loss cutting)

This is the most sophisticated component in either codebase. Key observations:
1. **It only operates on a single pair** (`settings.exit_pair`) — designed for 1000BONKUSDT
2. **It runs at a configurable cadence** (`exit_tick_interval_ms`) — feeds the engine every tick but acts on a schedule
3. **The regime detector has no tick data** — `TickData` is constructed but never fed from the engine's actual tick stream (the `process()` call in `_evaluate_exit` passes raw Tick/context, not TickData). **This is a potential silent failure — the regime detector and factor pipeline expect TickData with qty/side fields, but the runtime passes a raw `Tick` which may not have them.**
4. **Factor weights are hardcoded** — the "weight adaptation" in `update_weights()` only logs a warning when accuracy < 0.5; it never actually adjusts weights.

**Verdict:** The ExitEngine is architecturally impressive but has integration gaps. The TickData/Tick mismatch could cause the regime detector to get zero qty on every tick → CVD always 0 → always classified as CHOP → wrong weights applied.

---

## 3. Pair Selection

| Dimension | Wave Bot | Alpha Bot |
|-----------|----------|-----------|
| **Pair source** | `VAISRAVANA_PAIRS` env var (resolved via symbols.py) | `VAISRAVANA_PAIRS` env var + UniverseRanker |
| **Static list** | 15 pairs (BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, LINK, TRX, TON, NEAR, APT, ARB, OP) | Same 15 pairs for WS feed |
| **Dynamic ranking** | ❌ None | ✅ UniverseRanker fetches ALL 677 Binance futures pairs every 60s |
| **How ranking is used** | — | Injected into bias.py as the 0.35-weight "universe" component |
| **Pair filtering** | PairExcluder (manual, empty by default) | PairExcluder (same) |

**UniverseRanker Analysis:**
- Fetches exchangeInfo → filters to TRADING PERPETUAL USDT pairs (~677)
- Scores each pair on: 24h price change (0.30), volume delta from bookTicker (0.20), CVD trend proxy (0.25), BTC relative strength (0.25)
- **Critical bug: `_fetch_24hr_tickers` only fetches first 50 pairs** — the comma-separated batch is `pairs[:50]`. Lines 237-244:
  ```python
  symbols = ",".join(pairs[:50])  # batch limit
  resp = await client.get(f"{self.rest_base}/fapi/v1/ticker/24hr", params={"symbols": symbols})
  ```
  This means **only the first 50 pairs get scored**. The remaining 627 pairs get no ticker data → skipped → score of 0. The "strongest" and "weakest" rankings will be drawn from an arbitrary 50-pair subset (whatever alphabetical order Binance returns).
- **Volume delta from bookTicker is a single snapshot** — one bidQty/askQty reading at fetch time. This is noisy and gameable.

**Verdict:** Alpha Bot's universe ranking is a good idea undermined by a critical implementation bug. Only 50 of 677 pairs are ever scored. Wave Bot's static 15-pair list is simpler but actually works as intended.

---

## 4. SELL/BUY Bias — Is the EMA-Based Bias Still Defaulting Bearish?

**The claim:** "EMA-based bias defaults bearish."

**The code reality:**

Both engines compute bias as:
```python
if ctx.ema_1h:
    trend = _ema_cross_strength(ctx.ema_15m, ctx.ema_1h)  # fast vs slow
else:
    trend = _ema_cross_strength(ctx.ema_15m, ctx.price)

momentum = _ema_cross_strength(ctx.price, ctx.ema_15m)
mtf_ema = 0.6 * trend + 0.4 * momentum
```

- If `ema_1h` is 0 (fresh DB, never seeded): trend falls back to `ema_15m vs price`. Since `ema_15m` is seeded from the first tick price and then EMA-recursively updated, `ema_15m ≈ price` on the first few ticks → trend ≈ 0 → mtf_ema ≈ 0.
- The other components (flow_delta, book_pressure, risk_regime, breadth) also start at 0.
- **Result: bias score ≈ 0 → direction = "neutral" (Wave Bot) or micro-fallback direction (Alpha Bot)**.

**For Wave Bot:** neutral direction → scanner returns None for both BUY and SELL → NO TRADES until EMAs populate. This takes 1-2 hours of 1h kline data.

**For Alpha Bot:** micro-fallback (`0.6 * flow_delta + 0.4 * book_pressure`) with both at 0 → direction = "bearish" (since micro ≥ 0 → bullish, micro < 0 → bearish; 0 ≥ 0 → bullish). Wait — `micro = 0.6 * 0 + 0.4 * 0 = 0`, and `direction = "bullish" if micro >= 0 else "bearish"`. So on a cold start with all zeros, Alpha Bot defaults **BULLISH**, not bearish.

**Neither engine defaults bearish on a cold start.** They default to their respective fallback behaviors. However, **once EMAs populate**, the direction depends entirely on market conditions. The question "is the EMA-based bias still defaulting bearish" misunderstands the architecture — there is no hardcoded bearish default. The bias is always a weighted blend of 5-6 live components.

**What COULD cause a persistent bearish skew:**
1. If the BTC risk_regime is negative (BTC 15m < 1h EMA) → -0.05 to -0.10 bias contribution
2. If alt breadth is negative → another -0.025 to -0.05
3. On a downtrend day, these compound → overall score negative → bearish direction

But this is market-driven, not a code default.

**Verdict:** No hardcoded bearish bias. Both engines are market-responsive. Cold start = neutral/no-trades (Wave) or micro-fallback (Alpha). The real issue is **how long it takes to seed EMAs** — Wave Bot has a 90s warmup that blocks opens, but the 1h EMA takes ~2 hours of live data to converge from REST backfill.

---

## 5. Risk Management

### 5.1 Position Sizing

| Parameter | Wave Bot | Alpha Bot |
|-----------|----------|-----------|
| Base risk per trade | 20% of balance | 20% of balance (same env var) |
| Start balance | $10 | $10 |
| Per-wave notional | ~$2 (20% × $10) | ~$2 |
| Max balance target | $100 (10x) | $100 |
| Min notional clamp | Pair-specific (BTC $100, ETH $10, alt $5) | Same |
| **Sizing parity gate** | ✅ Skip if min_notional > 60% of balance | ❌ ABSENT |
| **Open fee rate** | 0.02% (maker) | 0.02% (maker) |
| **Close fee rate** | 0.04% (taker) | 0.04% (taker) |

**Wave Bot's sizing parity gate (iter-23)** is a critical safety feature: if a pair's exchange minimum notional exceeds 60% of the wallet balance, the pair is SKIPPED. Without this, a $10 account trying to trade BTCUSDT ($100 min notional) would:
1. Have the notional clamped UP from $2 to $100
2. Pay $0.02 open fee + $0.04 close fee = $0.06 RT fees
3. Any loss would be amplified 50x relative to normal sizing
4. One BTCUSDT wave dominates the entire account PnL

Alpha Bot **does not have this gate**. On a $10 account, the first BTCUSDT trade opens at $100 notional (10x the intended size). This is a **material risk management gap**.

### 5.2 Max Open Waves

Both cap at 8. Check is `len(self.waves) > MAX_OPEN_WAVES` — meaning it's actually capped at **9** open waves (the 9th triggers the skip). This is an off-by-one oversight in both codebases.

### 5.3 Kill Switch

Both have identical KillSwitch implementations: trip on manual command or daily DD threshold (50%). Both track daily loss with a rolling date-string reset. Neither engine has:
- Trailing daily DD (stops tightening as balance drops)
- Consecutive-loss counter
- Per-pair DD limits
- Maximum drawdown from peak equity

### 5.4 Wallet Persistence

Both paper wallets persist to JSON on disk. Both reload on restart. **This works correctly** — a container restart does not reset the balance.

---

## 6. Engine Reliability

### 6.1 Critical Bug: Open Waves Not Reloaded on Restart

**Both engines have this bug.** Here's the proof:

**Wave Bot** (`engine.py` lines 160-161):
```python
manager = WaveManager()
manager.conn = conn
```
- `WaveManager.__init__` creates an empty `self.waves: dict = field(default_factory=dict)`
- Nowhere in `run_wave_engine()` does it call `get_open_waves(conn)` to reload waves from DB
- The DB has `get_open_waves()` (db.py line 187) that queries `WHERE state IN ('ENTERED','SURFING')` — **this function exists but is never called**

**Alpha Bot** (`runtime.py` lines 119-120):
```python
self.manager = WaveManager()
self.manager.conn = conn
```
- Same pattern — fresh WaveManager, no reload from DB
- The DB has an equivalent schema and query pattern — **also never called on startup**

**Impact:** If the engine is running when the container restarts (crash, OOM, deployment), all in-memory waves are lost. The DB has the wave_log rows, but the manager starts with zero open waves. The engine resumes scanning and opens new waves, but:
1. The paper wallet balance is persisted correctly
2. Any unrealized PnL on the lost waves is **gone** — the wallet was already debited for the open fee, and the close fee + PnL credit never happens
3. If this happens mid-trade on multiple waves, it's a **silent capital loss** equal to `(num_open_waves × open_fee)`

**Severity:** HIGH. This is a direct capital loss on every restart while waves are open.

**Fix needed:** Call `get_open_waves(conn)` at startup, reconstruct Wave objects from DB rows, re-populate `manager.waves`.

### 6.2 Container Restart Survivability Summary

| Component | Survives Restart? | Notes |
|-----------|-------------------|-------|
| Paper wallet balance | ✅ Yes | Persisted to JSON, reloads correctly |
| DB schema | ✅ Yes | SQLite file persists |
| Trade history | ✅ Yes | All closed waves logged |
| **Open waves** | ❌ **No** | **Lost on restart — CRITICAL** |
| SMC zones | ❌ No | In-memory only, re-seeded from klines (acceptable) |
| EMA state | ❌ No | Re-seeded from REST backfill (acceptable, warmup protects) |
| Universe ranking | ❌ No | Fetched fresh every 60s (acceptable) |
| Cooldowns | ❌ No | In-memory only — acceptable, worst case is a re-entry that should have been blocked |
| Stop flag | ✅ Yes (Alpha) / ⚠️ Maybe (Wave) | Alpha persists to file and auto-clears stale flags; Wave checks `/data/.wave_stop` |

### 6.3 Feed Resilience

Both engines have dual-feed architecture:
- **Primary:** Binance WebSocket (aggTrade + bookTicker + klines)
- **Fallback:** REST polling every 5s (prices + 15m klines, 1h klines every 60s)

Both use REST fallback as a **permanent parallel task**, not a temporary backup. This means the REST poll runs even when WS is healthy, creating duplicate ticks and redundant network calls. The design rationale ("safety net") is valid but wasteful in normal operation.

**Alpha Bot's REST fallback** is factored into a separate `RestPoller` class — cleaner code. **Wave Bot's REST fallback** is an inline async function in engine.py — harder to test.

---

## 7. Code Quality

### 7.1 Architecture & Dependency Flow

**Alpha Bot** has a cleaner architecture:
```
engine/ → strategy/, execution/, marketdata/, notify/, storage/
                                          storage/ → core/
```
- `core/` depends on nothing
- `evaluation/` reads trade DBs and never imports the engine (orthogonal)
- Each layer is independently importable

**Wave Bot** has a flat module structure:
- All modules live in `wave/`
- `engine.py` imports from 12 other modules
- No formal layering — everything can import everything
- Harder to test in isolation

### 7.2 Dead Code

| Location | Dead Code | Why |
|----------|-----------|-----|
| Wave `telemetry.py` | Entire file (6 lines, empty body) | Telemetry is inlined in `manager.py` via `append_telemetry()`; this file is a placeholder |
| Wave `manager.py:_trail()` | Method defined but never called | Profitable trailing is done inline in `on_tick()`; `_trail()` exists but is dead |
| Wave `scanner.py:Candidate.reason` | Always set to `"pass"` | Gate rejection returns None before candidate creation |
| Alpha `strategy/survival.py:record_close()` | No-op (empty body) | AggressiveScaler doesn't track anything |
| Alpha `strategy/survival.py:record_open()` | No-op (empty body) | Same as above |
| Alpha `engine/exit_engine.py:update_weights()` | Logs warning but never changes weights | Advertised as "weight adaptation" but is actually just monitoring |
| Alpha `engine/exit_engine.py:ExitAction.FLIP` | Enum value never returned by any code path | Defined but unreachable |

### 7.3 Race Conditions

| Location | Risk | Notes |
|----------|------|-------|
| Wave `survival.py:_RateTracker` | Low | Uses `threading.Lock` but the engine is single-threaded async — lock is unnecessary overhead |
| Alpha `strategy/bias.py:_universe_score_cache` | Medium | Module-level dict updated by background asyncio task, read by bias function in the main loop. In CPython, dict reads are atomic but the refresh isn't — a partial write to `_universe_score_cache` during a read is possible |
| Both `engine.py:wave_state` | Low | Module-level dict written from async loop, read from Telegram command handlers. Single-threaded but accessed from different tasks |
| Both `paper_wallet.py` | Low | `threading.Lock` guards balance mutations, but all mutations happen in the async loop. Lock is defensive but unnecessary |

### 7.4 Silent Failures

| Location | What Fails Silently | Impact |
|----------|--------------------|--------|
| Wave `engine.py:on_tick` exceptions | `survival_gate` import is inside `on_tick` (line 199) — if the module is missing, the first tick silently skips the survival check | Wave continues without fee gating |
| Alpha `engine/runtime.py:_evaluate_exit` (line 319) | `self.exit_engine.process(tick, ctx, wave)` — if `process()` throws, it's caught and logged as debug, wave stays open | Exit engine failure = no real-time exits, position unmanaged by that path |
| Both `engine.py` REST poll | Individual pair fetch failures are caught per-pair and logged as error. No cumulative failure tracking. | One broken pair doesn't halt the engine (correct), but 15 broken pairs look like "no data" instead of "all pairs broken" |
| Alpha `universe_ranker.py` | Only first 50 pairs scored (line 237). 627 pairs silently skipped. | Universe component biased toward alphabetical-first 50 pairs |
| Both `db.py` | `log_wave_open/log_wave_close` exceptions are caught and logged as warning — wave opens/closes without DB record | Telemetry gap but trading continues |

### 7.5 Code Duplication

The two codebases share ~70% structural similarity:
- `models.py` — nearly identical
- `bias.py` — same helper functions, different weights
- `scanner.py`, `gate.py` — nearly identical
- `manager.py` — shared constants, similar open/close/exit logic
- `feed.py` — identical WS parsing
- `db.py` — identical schema

Neither codebase imports from the other. This is a maintenance liability — bug fixes in one must be manually ported to the other.

---

## 8. Summary Scores

| Layer | Wave Bot | Alpha Bot | Winner |
|-------|----------|-----------|--------|
| **Entry Logic** | Mature, empirically tuned, neutral-fallback safe | Aggressive, micro-fallback always picks a side, universe integration | Wave (reliability) |
| **Exit Logic** | 9 exit reasons, adverse-gated conf_collapse, flat_tape_exit | 8 exit reasons + real-time ExitEngine, wider profit-banking | Alpha (capability), Wave (reliability) |
| **Pair Selection** | Static 15 pairs, works correctly | Universe ranking 677 pairs, broken (only 50 scored) | Wave (correctness) |
| **Bias Default** | Neutral on cold start, no bearish default | Micro-fallback on cold start, no bearish default | Tie (neither is bearish by default) |
| **Risk Management** | Sizing parity gate, adaptive throttle, spread gate, session block | No survival gates, missing sizing parity gate, wider loss-cut | Wave (substantially) |
| **Engine Reliability** | Open waves lost on restart | Open waves lost on restart | Tie (both broken) |
| **Code Quality** | Flat structure, dead code, harder to test | Layered architecture, cleaner, still has dead code | Alpha |
| **Feed Resilience** | WS + REST parallel, inline REST code | WS + REST parallel, factored RestPoller class | Alpha (cleaner) |

### Overall Assessment

**Wave Bot** is a conservative, empirically-tuned trading engine designed for multi-pair survivability on a small account. Its survival gates, profit-banking thresholds, and sizing parity logic show deep iteration and learning from real trading data. The code is messier but the logic is more reliable.

**Alpha Bot** is an ambitious, aggressively-designed engine that aims for higher trade frequency on a single pair (BONK). Its universe ranking, real-time exit engine, and regime-adaptive TP widths are architecturally superior concepts, but the implementation has critical gaps: the universe ranker only scores 50 pairs, the survival gates are fully disabled, the exit engine has a TickData/Tick type mismatch, and the sizing parity gate is missing.

**Neither engine can survive a container restart with open waves.** This is a showstopper bug for any deployment that restarts containers (which is all of them). Both need `get_open_waves()` called at startup.

**Recommended action:** Fix the critical restart bug in both engines. Then either merge the best of both codebases (Alpha's architecture + Wave's survival gates + a fixed universe ranker) or keep them as separate products with full awareness of their risk profiles.
