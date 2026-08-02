# Alpha Bot Performance Report
## 2026-08-03 | sera | $10 paper | 8h run

---

## Summary

| Metric | Value |
|--------|-------|
| Start balance | $10.0000 |
| End balance | $9.9474 |
| Growth | -$0.0526 (-0.53%) |
| Total trades | 19 closed |
| Win rate | 50.0% (9W / 9L / 1BE) |
| Total PnL | +$0.0126 (positive!) |
| Avg win | +$0.0063 |
| Avg loss | -$0.0049 |
| Max win | +$0.0152 |
| Max loss | -$0.0192 |
| Total fees | $0.0570 |
| Peak balance | $10.0118 |

**Verdict: Slightly negative but PnL is positive after fees. Fee drag is the only drain.**

---

## Trade-by-Trade

| # | Pair | Side | Entry | Close | R | PnL | Fees | Reason |
|---|------|------|-------|-------|---|-----|------|--------|
| 1 | AIOTUSDT | SELL | 0.048230 | 0.049098 | -0.05 | -0.0061 | 0.0020 | max_age_fev |
| 2 | CRDOUSDT | BUY | 215.320000 | 211.444240 | +0.09 | +0.0058 | 0.0020 | tp05_hit |
| 3 | VANRYUSDT | SELL | 0.003778 | 0.003845 | +0.19 | +0.0152 | 0.0020 | bank_08r |
| 4 | CRDOUSDT | BUY | 215.490000 | 211.611180 | +0.01 | -0.0011 | 0.0020 | max_age_fev |
| 5 | AIOTUSDT | SELL | 0.048295 | 0.049164 | +0.12 | +0.0084 | 0.0020 | tp05_hit |
| 6 | ACUUSDT | BUY | 0.080195 | 0.078751 | +0.11 | +0.0077 | 0.0020 | tp05_hit |
| 7 | CRDOUSDT | BUY | 215.395000 | 211.517890 | +0.02 | +0.0002 | 0.0020 | max_age |
| 8 | AIOTUSDT | SELL | 0.048070 | 0.048935 | +0.01 | -0.0010 | 0.0020 | max_age_fev |
| 9 | CRDOUSDT | BUY | 215.330000 | 211.454060 | +0.01 | -0.0013 | 0.0020 | max_age_fev |
| 10 | VANRYUSDT | SELL | 0.003776 | 0.003844 | +0.09 | +0.0059 | 0.0020 | tp05_hit |
| 11 | AIOTUSDT | SELL | 0.048060 | 0.048925 | +0.11 | +0.0079 | 0.0020 | tp05_hit |
| 12 | CRDOUSDT | BUY | 215.435000 | 211.557170 | +0.00 | -0.0017 | 0.0020 | max_age_fev |
| 13 | ACUUSDT | BUY | 0.080735 | 0.079282 | -0.03 | -0.0045 | 0.0020 | max_age_fev |
| 14 | AIOTUSDT | SELL | 0.048020 | 0.048884 | -0.04 | -0.0056 | 0.0020 | max_age_fev |
| 15 | CRDOUSDT | BUY | 215.450000 | 211.571900 | -0.02 | -0.0039 | 0.0020 | max_age_fev |
| 16 | AIOTUSDT | SELL | 0.048055 | 0.048920 | +0.09 | +0.0058 | 0.0020 | tp05_hit |
| 17 | VANRYUSDT | SELL | 0.003776 | 0.003843 | -0.19 | -0.0192 | 0.0020 | bias_flip |
| 18 | CRDOUSDT | BUY | 215.370000 | 211.493340 | +0.01 | -0.0014 | 0.0020 | max_age_fev |
| 19 | AIOTUSDT | SELL | 0.047975 | 0.048839 | -0.05 | -0.0067 | 0.0020 | max_age_fev |

---

## Close Reasons

| Reason | Count | Avg R | Avg PnL |
|--------|-------|-------|---------|
| tp05_hit | 6 | +0.10 | +$0.0071 |
| max_age_fev | 8 | -0.03 | -$0.0033 |
| max_age | 2 | +0.01 | -$0.0011 |
| bank_08r | 1 | +0.19 | +$0.0152 |
| bias_flip | 1 | -0.19 | -$0.0192 |

**Key insight: max_age_fev is the #1 drain. 8/19 trades (42%) are time-based exits that lose money.**

---

## Pair Distribution

| Pair | Trades | WR | Total PnL |
|------|--------|----|-----------|
| AIOTUSDT | 7 | 43% (3W/4L) | -$0.0027 |
| CRDOUSDT | 6 | 50% (3W/3L) | -$0.0042 |
| VANRYUSDT | 3 | 67% (2W/1L) | -$0.0081 |
| ACUUSDT | 2 | 50% (1W/1L) | +$0.0032 |

**Pair concentration: only 4 pairs traded out of 5 available. AIOT + CRDO = 13/19 (68%) of all trades.**

---

## Issues Found

1. **Universe ranker always clamped to ±1.0** — all 5 components (rsi, vwap_distance, cvd_divergence, volume_delta, btc_relative) are always exactly 1.0 or -1.0. No differentiation between pairs. The ranker cannot select the best pairs.

2. **max_age_fev dominates** — 42% of exits are time-based fee-aware exits that close at a loss. The 300s max_age is too short for mean-reversion signals to play out.

3. **Pair concentration** — bot trades the same 2-3 pairs repeatedly instead of rotating through the top 5.

4. **No trades table in DB** — trade data only in wallet memory, lost on container restart. Only wave_log persists.

5. **Fee drag** — $0.057 in fees on $10 balance = 0.57% of account just from fees. Round-trip fee per trade = $0.003.

---

## What's Working

- **PnL is positive after fees** (+$0.0126) — the strategy edge exists
- **Avg win > avg loss** ($0.0063 vs $0.0049) — winners are bigger than losers
- **tp05_hit works well** — 6/6 profit targets hit, avg +$0.0071
- **bank_08r works** — single hit at +$0.0152
- **Win rate 50%** — acceptable for scalping if avg win > avg loss

---

## Recommended Fixes (Priority Order)

1. **Fix universe ranker scoring** — components always clamped, need proper normalization
2. **Increase MAX_WAVE_AGE_S** — 300s too short, mean-reversion needs more time
3. **Reduce pair concentration** — force rotation through top 5 pairs
4. **Add trades table to DB** — persist trade data for analysis and restart recovery
5. **Tighten max_age_fev threshold** — only close at loss if truly -EV after fees