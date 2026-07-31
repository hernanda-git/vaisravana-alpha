# Layer 1: Per-Trade Evaluation

Evaluates each individual trade immediately after close.

## Metrics

- entry_quality: how good was the entry signal (0.0-1.0)
- exit_reason: tp_hit, sl_hit, max_age, reversal, partial_tp, conf_collapse
- r_achieved: actual R achieved (positive = winner, negative = loser)
- fee_impact: total fees paid on this trade
- ev_status: +EV or -EV (net PnL after fees)
- trade_score: composite score (0.0-1.0)
- verdict: win / loss / scratch

## Trade Score Formula

trade_score = (r_achieved / target_r) * 0.4 + fee_efficiency * 0.3 + entry_quality * 0.3

Where:
- fee_efficiency = 1.0 if net_pnl > 0 after fees, 0.0 otherwise
- entry_quality from signal confidence at entry time

## Output

```json
{
  "trade_id": "wave_001",
  "bot": "wave",
  "pair": "1000BONKUSDT",
  "side": "SELL",
  "entry": 0.00001234,
  "sl": 0.00001210,
  "tp": 0.00001260,
  "exit": 0.00001260,
  "r_achieved": 1.52,
  "fee_open": 0.00002468,
  "fee_close": 0.00005040,
  "fee_total": 0.00007508,
  "gross_pnl": 0.00003210,
  "net_pnl": 0.00002459,
  "ev_status": "+EV",
  "trade_score": 0.78,
  "verdict": "win",
  "exit_reason": "tp_hit",
  "timestamp": "2026-08-01T00:38:19Z"
}
```