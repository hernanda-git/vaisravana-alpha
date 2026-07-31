# Layer 4: Decision Gate

Makes go/no-go decision on strategy changes based on layers 1-3 output.

## Decision Logic

```
IF aggregate_score >= 0.60 AND baseline_score >= 0.40:
    decision = persist_changes
    action = "keep current strategy, continue trading"

IF aggregate_score >= 0.60 AND baseline_score < 0.40:
    decision = persist_changes_with_caution
    action = "keep strategy but monitor baseline gap"

IF aggregate_score 0.40-0.59 AND baseline_score >= 0.40:
    decision = iterate
    action = "tweak parameters, test for another 20 trades"

IF aggregate_score 0.40-0.59 AND baseline_score < 0.40:
    decision = rollback
    action = "revert to last known good state"

IF aggregate_score < 0.40:
    decision = rollback_immediate
    action = "immediately revert, pause trading for 1 hour"

IF balance_drawdown >= 20%:
    decision = pause_trading
    action = "pause all trading, re-evaluate strategy"

IF balance_drawdown >= 50%:
    decision = stop_trading
    action = "stop trading, full redesign needed"
```

## Input

- Layer 1: per-trade scores (last 20 trades)
- Layer 2: aggregate score, trend
- Layer 3: baseline comparison
- Current balance and drawdown

## Output

```json
{
  "decision_id": "wave_decision_005",
  "bot": "wave",
  "aggregate_score": 0.62,
  "baseline_score": 0.55,
  "balance_drawdown": 0.05,
  "decision": "persist_changes",
  "action": "keep current strategy, continue trading",
  "confidence": 0.78,
  "recommendation": "continue with current parameters",
  "rollback_to": null,
  "timestamp": "2026-08-01T01:00:00Z"
}
```