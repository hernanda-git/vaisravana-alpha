"""
Wave Bot Adapter v2
Adapter to integrate the meta-evaluation system with the vaisravana-wave bot.

This adapter provides the integration points between the wave bot
and the evaluation system.
"""

from eval_system.core.engine.eval_engine import EvaluationEngine


def create_wave_evaluator(balance: float = 10.0) -> EvaluationEngine:
    """Create an evaluation engine for the wave bot."""
    return EvaluationEngine(
        bot_name="wave",
        balance=balance,
        fee_open=0.0002,
        fee_close=0.0004,
        window_size=20,
        drawdown_pause=0.20,
        drawdown_stop=0.50,
    )


def map_wave_trade(wave_trade: dict) -> dict:
    """Map wave bot trade data to evaluation engine format."""
    return {
        "trade_id": wave_trade.get("id", ""),
        "pair": wave_trade.get("pair", ""),
        "side": wave_trade.get("side", ""),
        "entry": wave_trade.get("entry_price", 0.0),
        "sl": wave_trade.get("sl_price", 0.0),
        "tp": wave_trade.get("tp_price", 0.0),
        "exit_price": wave_trade.get("exit_price", 0.0),
        "r_achieved": wave_trade.get("r_achieved", 0.0),
        "fee_open": wave_trade.get("fee_open", 0.0),
        "fee_close": wave_trade.get("fee_close", 0.0),
        "gross_pnl": wave_trade.get("gross_pnl", 0.0),
        "exit_reason": wave_trade.get("exit_reason", "unknown"),
    }


def integrate_with_wave_bot(engine: EvaluationEngine, wave_trade: dict) -> dict:
    """
    Integrate evaluation into wave bot trade lifecycle.

    Call this after each wave trade closes.
    """
    mapped_trade = map_wave_trade(wave_trade)
    l1 = engine.evaluate_trade(mapped_trade)

    result = {
        "stage": "layer1",
        "trade_score": l1.trade_score,
        "verdict": l1.verdict.value,
        "is_positive_ev": l1.is_positive_ev,
    }

    # Check if we should run full pipeline
    if len(engine.trades) >= engine.window_size:
        pipeline_result = engine.run_pipeline()
        result["pipeline"] = pipeline_result

    return result