# Layer 5: Report Generator

Generates human-readable reports and Telegram alerts.

## Report Structure

### Trade Summary
- Total trades in window
- Wins / Losses / Scratch
- Win rate %
- Avg R
- Net PnL (after fees)
- Total fees

### Balance Growth
- Starting balance
- Current balance
- Balance change %
- Growth rate (per hour)

### Strategy Health
- Aggregate score
- Trend (improving/stable/declining)
- Baseline comparison
- Decision gate output

### Visual Elements
- Balance growth chart (ASCII or simple text)
- WR trend over last N windows
- Fee breakdown pie

## Telegram Alert Format

```
EVALUATION REPORT — Wave Bot
Window: 005 | Trades: 20
WR: 55% (11W/9L) | Avg R: +0.32
Net PnL: +$0.045 | Fees: -$0.015
Score: 0.62 (GREEN)
Decision: PERSIST CHANGES
Balance: $9.945 (+$0.045 today)
```

## Output Files

- `eval_report_<timestamp>.md` — full report in docs/
- `eval_alert_<timestamp>.json` — machine-readable alert for Telegram
- `eval_history.json` — cumulative evaluation history for walk-forward analysis