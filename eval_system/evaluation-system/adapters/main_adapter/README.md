# Main Bot Adapter

Adapter to integrate the meta-evaluation system with the vaisravana main bot.

## Integration Points

1. **After trade close**: call `evaluate_trade()` with trade data
2. **Every 20 trades**: run full 5-layer pipeline
3. **On decision**: persist changes or rollback
4. **On degradation**: auto-rollback to last good state

## Main Bot Specifics

- Multi-strategy: Scalping (1m), Day (15m), Swing (1h)
- Current issues: MAXHOLD bleeding, CVD veto-only, adaptive weights mutates shared ParameterSurface, bank_08r SL trail broken in paper mode
- Target: remove MAXHOLD, add quick exit, fix bank_08r, wire CVD as entry amplifier

## Adapter Code Skeleton

```python
from eval_system.core.engine.eval_engine import EvaluationEngine

main_engine = EvaluationEngine(
    bot_name="main",
    balance=10.0,
    fee_open=0.0002,
    fee_close=0.0004,
    window_size=20,
)

# Main bot specific trade data mapping
def map_main_trade(main_trade):
    return {
        "trade_id": main_trade.id,
        "pair": main_trade.pair,
        "side": main_trade.side,
        "entry": main_trade.entry_price,
        "sl": main_trade.sl_price,
        "tp": main_trade.tp_price,
        "exit_price": main_trade.exit_price,
        "r_achieved": main_trade.r_achieved,
        "fee_open": main_trade.fee_open,
        "fee_close": main_trade.fee_close,
        "gross_pnl": main_trade.gross_pnl,
        "exit_reason": main_trade.exit_reason,
    }
```