"""
Main Bot Adapter v2
Adapter to integrate the meta-evaluation system with the vaisravana main bot.
"""

from eval_system.core.engine.eval_engine import EvaluationEngine


def create_main_evaluator(balance: float = 10.0) -> EvaluationEngine:
    """Create an evaluation engine for the main bot."""
    return EvaluationEngine(
        bot_name="main",
        balance=balance,
        fee_open=0.0002,
        fee_close=0.0004,
        window_size=20,
        drawdown_pause=0.20,
        drawdown_stop=0.50,
    )


def map_main_trade(main_trade: dict) -> dict:
    """Map main bot trade data to evaluation engine format."""
    return {
        "trade_id": main_trade.get("id", ""),
        "pair": main_trade.get("pair", ""),
        "side": main_trade.get("side", ""),
        "entry": main_trade.get("entry_price", 0.0),
        "sl": main_trade.get("sl_price", 0.0),
        "tp": main_trade.get("tp_price", 0.0),
        "exit_price": main_trade.get("exit_price", 0.0),
        "r_achieved": main_trade.get("r_achieved", 0.0),
        "fee_open": main_trade.get("fee_open", 0.0),
        "fee_close": main_trade.get("fee_close", 0.0),
        "gross_pnl": main_trade.get("gross_pnl", 0.0),
        "exit_reason": main_trade.get("exit_reason", "unknown"),
    }


def integrate_with_main_bot(engine: EvaluationEngine, main_trade: dict) -> dict:
    """Integrate evaluation into main bot trade lifecycle."""
    mapped_trade = map_main_trade(main_trade)
    l1 = engine.evaluate_trade(mapped_trade)

    result = {
        "stage": "layer1",
        "trade_score": l1.trade_score,
        "verdict": l1.verdict.value,
        "is_positive_ev": l1.is_positive_ev,
    }

    if len(engine.trades) >= engine.window_size:
        pipeline_result = engine.run_pipeline()
        result["pipeline"] = pipeline_result

    return result