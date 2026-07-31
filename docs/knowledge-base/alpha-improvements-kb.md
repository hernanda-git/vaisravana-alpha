# alpha bot improvements — knowledge base
# written 2026-07-31, sera

---

## What was done (commit 8c3eda5)

### 1. CVD divergence as entry amplifier (scanner.py)

**method**: `flow_delta` (CVD) in the tick context is now used as a positive entry signal, not just an exit veto.

**how it works**:
- `flow_delta` = 2× buy_volume - total_volume (from Binance taker-buy volume in klines)
- `flow_delta_prev` = previous cycle's value
- When price moves in a direction but flow_delta collapses, institutional money is fading the move
- SELL entries get +15% strength bonus when price rises but CVD drops (smart money distributing)
- BUY entries get +15% strength bonus when price falls but CVD rises (smart money accumulating)

**why it matters**: most bots use CVD only as a veto (do not trade against the flow). using it as an AMPLIFIER for entries that align with smart money creates a real edge.

### 2. Regime-adaptive TP (manager.py)

**method**: TP width now adapts based on `regime_label` passed from the runtime to the scanner to the manager.

**regime mapping** (derived from ctx.risk_regime, -1..+1):
- `trending_bull` / `trending_bear` (|risk_regime| > 0.3): 3.0x ATR TP
  - trending markets need wider TP so winners can run
- `range` (|risk_regime| <= 0.3): 1.5x ATR TP
  - choppy markets need tighter TP to bank quick hits before noise reverses

**default TP** was 2.0x ATR (volatility-scaled).

### 3. TP/SL display fix (cards.py)

**problem**: `wave_open_card` showed `Entry: 0.0 | SL: 0.0 | TP: trailing` when fields were not yet populated on the wave object.

**fix**: fallback to `wave.entry_price` for missing TP/SL values so the card always shows numeric data.

**positions_card** now also shows TP for every open position.

### 4. CVD field name fix (exit_engine.py)

**problem**: `_orderflow()` used `getattr(context, "cvd", 0)` but the context field is `flow_delta`, not `cvd`. CVD divergence scoring was always returning 0.

**fix**: `cvd = getattr(context, "flow_delta", 0)` and `cvd_prev = getattr(context, "flow_delta_prev", 0)`.

### 5. Candidate dataclass extended (models.py)

Added `regime_label: str = "range"` to the `Candidate` dataclass so the regime information flows through the entire pipeline from scanner → manager → TP sizing.

---

## Fee model (unchanged, verified correct)

```
open_fee  = notional × 0.0002  (2bps maker — post-only LIMIT)
close_fee = notional × 0.0004  (4bps taker — stop-market exit)
net       = gross - open_fee - close_fee
```

Fees are shown on:
- open card: maker fee displayed as `-X.XXXX$ (maker)`
- close card: both open + close fees displayed separately, then net

---

## Balance footer (cards.py — unchanged)

Every trade card ends with:
```
Balance
  Equity     : X.XXXX$
  Used       : X.XXXX$
  Unrealized : +/-X.XXXX$
  Realized   : +/-X.XXXX$
```

The `wallet.snapshot()` call computes this on the fly for every notification.

---

## Architecture: alpha bot is standalone

The alpha bot (`/root/vaisravana-alpha`) is an independent repository with its own:
- docker-compose.yml
- Dockerfile
- source code (vaisravana_alpha package)
- tests
- knowledge bases

It does NOT depend on the wave bot codebase. The two communicate only via shared Binance data (wsproxy relay) and are separate containers:
- `bots-vaisravana` (main engine, 9 strategies)
- `bots-vaisravana-wave` (wave surf engine)
- `bots-vaisravana-alpha` (real-time exit engine, 15 pairs aggressive scalp)

---

## How to evaluate if this improves balance growth

1. Watch `@vaisravana_bot` Telegram channel for the new cards
2. compare next 50 trades: WR should improve by 5-10pp
3. compare net PnL after fees: should be positive instead of negative
4. check if trades are hitting TP more often (regime-adaptive TP helps)
5. check if CVD-amplified entries have better R:R than random entries

The success metric is always: growing balance, not activity.