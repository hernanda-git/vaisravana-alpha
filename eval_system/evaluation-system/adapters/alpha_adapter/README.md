# Alpha Bot Adapter

Adapter to integrate the meta-evaluation system with the vaisravana-alpha bot.

## Integration Points

1. **After trade close**: call `evaluate_trade()` with trade data
2. **Every 20 trades**: run full 5-layer pipeline
3. **On decision**: persist changes or rollback
4. **On degradation**: auto-rollback to last good state

## Alpha Bot Specifics

- CVD divergence as entry trigger (not just veto)
- Regime-adaptive TP
- Current issues: survival gate pass-through, universe ranker broken (50/677), exit engine type mismatch, fee constants outdated
- Target: wire survival gate, fix ranker, fix exit engine, update fees, remove survival gates

## Adapter Code Skeleton

```python
from eval_system.core.engine.eval_engine import EvaluationEngine

alpha_engine = EvaluationEngine(
    bot_name="alpha",
    balance=10.0,
    fee_open=0.0002,
    fee_close=0.0004,
    window_size=20,
)

# Alpha bot specific trade data mapping
def map_alpha_trade(alpha_trade):
    return {
        "trade_id": alpha_trade.id,
        "pair": alpha_trade.pair,
        "side": alpha_trade.side,
        "entry": alpha_trade.entry_price,
        "sl": alpha_trade.sl_price,
        "tp": alpha_trade.tp_price,
        "exit_price": alpha_trade.exit_price,
        "r_achieved": alpha_trade.r_achieved,
        "fee_open": alpha_trade.fee_open,
        "fee_close": alpha_trade.fee_close,
        "gross_pnl": alpha_trade.gross_pnl,
        "exit_reason": alpha_trade.exit_reason,
    }
```