# Wave Bot Adapter

Adapter to integrate the meta-evaluation system with the vaisravana-wave bot.

## Integration Points

1. **After trade close**: call `evaluate_trade()` with trade data
2. **Every 20 trades**: run full 5-layer pipeline
3. **On decision**: persist changes or rollback
4. **On degradation**: auto-rollback to last good state

## Wave Bot Specifics

- Entry logic: structure-based (bias + confidence + gate)
- Exit logic: 6 exit checks (max_age, bank_08r, bias_flip, conf_collapse, tp_hit, sl_hit)
- Current issues: fee-aware EV gate blocking 90% candidates, max_age dominate, no partial TP
- Target: remove gates, add adaptive TP/SL, trailing, partial TP, regime detection

## Adapter Code Skeleton

```python
from eval_system.core.engine.eval_engine import EvaluationEngine

wave_engine = EvaluationEngine(
    bot_name="wave",
    balance=10.0,
    fee_open=0.0002,
    fee_close=0.0004,
    window_size=20,
)

# Wave bot specific trade data mapping
def map_wave_trade(wave_trade):
    return {
        "trade_id": wave_trade.id,
        "pair": wave_trade.pair,
        "side": wave_trade.side,
        "entry": wave_trade.entry_price,
        "sl": wave_trade.sl_price,
        "tp": wave_trade.tp_price,
        "exit_price": wave_trade.exit_price,
        "r_achieved": wave_trade.r_achieved,
        "fee_open": wave_trade.fee_open,
        "fee_close": wave_trade.fee_close,
        "gross_pnl": wave_trade.gross_pnl,
        "exit_reason": wave_trade.exit_reason,
    }
```