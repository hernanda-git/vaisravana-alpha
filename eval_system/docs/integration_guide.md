# Evaluation System Integration Guide

## Quick Start

### 1. Copy evaluation system to each bot repo

```bash
# Wave bot
cp -r /root/vaisravana-workspace/evaluation-system /opt/bots/vaisravana-wave/eval_system/

# Main bot
cp -r /root/vaisravana-workspace/evaluation-system ~/vaisravana-workspace/vaisravana/eval_system/

# Alpha bot
cp -r /root/vaisravana-workspace/evaluation-system /root/vaisravana-alpha/eval_system/
```

### 2. Import and use in each bot

```python
from eval_system.core.engine.eval_engine import EvaluationEngine

# Initialize
engine = EvaluationEngine(
    bot_name="wave",
    balance=10.0,
    fee_open=0.0002,
    fee_close=0.0004,
    window_size=20,
)

# After each trade closes, call evaluate_trade
result = engine.evaluate_trade({
    "trade_id": "wave_001",
    "pair": "1000BONKUSDT",
    "side": "SELL",
    "entry": 0.00001234,
    "sl": 0.00001210,
    "tp": 0.00001260,
    "exit_price": 0.00001260,
    "r_achieved": 1.52,
    "fee_open": 0.00002468,
    "fee_close": 0.00005040,
    "gross_pnl": 0.00003210,
    "exit_reason": "tp_hit",
})

# After each evaluation window (20 trades), run full pipeline
if len(engine.trades) >= 20:
    pipeline_result = engine.run_pipeline()
    report = engine.reports[-1]
    print(report.alert_text)
```

### 3. Telegram integration

```python
# Send alert to Telegram
async def send_eval_alert(alert_text: str):
    await telegram_bot.send_message(
        chat_id=CHAT_ID,
        text=alert_text,
        parse_mode="Markdown",
    )
```

## Architecture

```
Bot Hot Loop (tick)
    |
    +---> Trade closes
    |       |
    |       +---> engine.evaluate_trade(trade_data)  [Layer 1]
    |
    +---> Every 20 trades
            |
            +---> engine.run_pipeline()
                    |
                    +---> Layer 1: Per-trade scores
                    +---> Layer 2: Aggregate metrics
                    +---> Layer 3: Baseline comparison
                    +---> Layer 4: Decision gate
                    +---> Layer 5: Report + alert
```

## Key Benefits

1. **No single point of failure** — 5 layers, each independently validated
2. **Autonomous** — runs in loop, no human intervention
3. **Fast** — <100ms per evaluation window
4. **Persistent** — all results stored for walk-forward analysis
5. **Self-correcting** — auto-rollback if strategy degrades
6. **Additive** — layers build on each other, never remove information