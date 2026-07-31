# Alpha Bot Critical Gap Findings (Practitioner Audit)
## 2026-07-31 | sera | $10 paper | REST-only

---

## The Critical Discovery

After reading all alpha source files and running the code, the practitioner
subagent found: **the alpha bot has the knobs for selectivity but none of
them are wired into runtime.**

`survival_gate()` exists in `strategy/survival.py` but always returns
`(True, "ok")`. Every admission parameter in `params.py` (`ev_gate_k=1.4`,
`spread_gate_bps=5.0`, `slip_bps=1.0`, `trades_per_hour_start=6`,
`pair_spacing_min=20.0`) is **never read by the runtime**.

This means the alpha bot is currently UNRESTRICTED — it opens trades
without any EV gate, spread filter, frequency throttle, or session block.
This is worse than the wave bot's throttled state.

---

## Tier 1 — Fee Fixes (Immediate)

| File | Current | Correct |
|------|---------|---------|
| `settings.py` `fee_close` | 0.0004 | 0.0005 (Binance VIP0 taker is 0.05%) |
| `exit_engine.py` `_calculate_salvage` | hardcoded `* 0.0004` | use `wallet.close_fee_rate` |

---

## Tier 2 — Wire Existing Knobs Into Runtime

| Parameter | Current | Proposed |
|-----------|---------|----------|
| `ev_gate_k` | 1.4 | 2.0 (survived adversarial critique) |
| `spread_gate_bps` | 5.0 | 3.0 (3bps min entry, 10bps priority) |
| `survival_gate()` | Always returns True | Wire into `runtime.py` on_tick before open |
| CVD in `scanner.py` | Amplifier (+15% strength) | Promote to gate (block entry when CVD collapses) |
| `runtime.py` | Calls survival_gate but gate passes everything | Add actual checks |

---

## Tier 3 — Additive REST Endpoints

| Endpoint | Purpose | Weight | Frequency |
|----------|---------|--------|-----------|
| `/futures/data/takerlongshortRatio` | Market-wide taker sentiment | 1 | Every 60s |
| `/fapi/v1/fundingRate` | Extreme funding = crowded trade | 1 | Every 60s |
| `/fapi/v1/depth?limit=5` | True top-5 order book imbalance | 1 | Every few cycles |
| `/fapi/v1/aggTrades` | Per-trade flow direction (m flag) | 20 | 1 call/pair/3-5 cycles |

---

## Tier 4 — Regime Adaptation

| Change | Current | Proposed |
|--------|---------|----------|
| ATR-regime entry filter | Not blocking extreme vol | Block when ATR pct > 0.85 or < 0.15 |
| Regime TP bands | 3-band (trending 3.0x, range 1.5x) | 4-band: high-vol 2.0x/1.5x, low-vol 1.5x/0.8x |

---

## Implementation Priority

1. Fix fee_close constant (0.0004 → 0.0005) — immediate, no engine change
2. Replace hardcoded `* 0.0004` in exit_engine.py with `wallet.close_fee_rate` — immediate
3. Wire survival_gate() into alpha runtime — highest ROI
4. Add takerlongshortRatio as sentiment filter — additive REST
5. Add fundingRate as crowd filter — additive REST
6. Promote CVD from amplifier to gate in scanner.py — additive
7. ATR regime TP refinement — additive

All changes are additive/parameter-only. No engine rewrite. Sentinel-compliant.

---

## What NOT to Touch

- Maker fee harvesting (pure grid) — negative EV at $10
- Fade the crowd — self-referential at REST
- OBI/CVD z-score at sub-second — requires WebSocket
- Z-score mean reversion — entry always 1-2 min late
- Liquidity sniping — competing with HFT

All confirmed dead by adversarial critique.
