# Alpha Bot Additive Improvements (Surviving Adversarial Critique)
## bots-vaisravana-alpha | sera | 2026-07-31

---

## Critical Distinction

These improvements are for the **alpha bot** (`bots-vaisravana-alpha`),
NOT the wave bot. The wave bot already has `survival_gate()` fully wired.
The alpha bot has the knobs but they are not all wired into runtime.

---

## Tier 1 — Fee Fixes (Highest ROI)

| Change | Current | Correct | Why |
|--------|---------|---------|-----|
| `fee_close` in settings.py | 0.0004 | 0.0005 | Binance VIP0 taker is 0.05%, not 0.04% |
| Hardcoded `* 0.0004` in exit_engine.py | 0.0004 | use `wallet.close_fee_rate` | Fee model must stay truthful if tuned |

Savings: $0.0025/trade x 100 trades = $0.25 on $10 balance.

---

## Tier 2 — Wire Existing Knobs Into Alpha Runtime

| Parameter | Current State | Action |
|-----------|--------------|--------|
| `ev_gate_k` | 1.4 in params.py | Bump to 2.0 (survived adversarial critique) |
| `spread_gate_bps` | 5.0 in params.py | Tighten to 3.0 (3bps min entry, 10bps priority) |
| `survival_gate()` | Function exists but not fully wired | Wire into `runtime.py` on_tick before open |
| CVD in scanner.py | Amplifier (+15% strength to bad trades) | Promote to gate (block entry when CVD collapses) |

---

## Tier 3 — Additive REST Endpoints (No WebSocket Needed)

| Endpoint | Purpose | Weight | Frequency |
|----------|---------|--------|-----------|
| `/futures/data/takerlongshortRatio` | Market-wide taker sentiment | 1 | Every 60s |
| `/fapi/v1/fundingRate` | Extreme funding = crowded trade | 1 | Every 60s |
| `/fapi/v1/depth?limit=5` | True top-5 order book imbalance | 1 | Every few cycles |
| `/fapi/v1/aggTrades` | Per-trade flow direction (m flag) | 20 | 1 call/pair/3-5 cycles |

All free, all REST, all usable from sera.

---

## Tier 4 — Regime Adaptation

| Change | Current | Proposed |
|--------|---------|----------|
| ATR-regime entry filter | Not blocking extreme vol | Block when ATR pct > 0.85 or < 0.15 |
| Regime TP bands | 3-band (trending 3.0x, range 1.5x) | 4-band: high-vol 2.0x/1.5x, low-vol 1.5x/0.8x |

---

## What NOT to Touch (Confirmed Dead)

- Maker fee harvesting (pure grid)
- Fade the crowd (self-referential at REST)
- OBI/CVD z-score at sub-second
- Z-score mean reversion
- Liquidity sniping

All require WebSocket streaming or $1000+ capital.

---

## Implementation Priority

1. Fix fee_close constant (0.0004 → 0.0005) — immediate
2. Wire survival_gate into alpha runtime — highest ROI
3. Add takerlongshortRatio as sentiment filter — additive
4. Add fundingRate as crowd filter — additive
5. Promote CVD from amplifier to gate in scanner.py — additive
6. ATR regime TP refinement — additive
