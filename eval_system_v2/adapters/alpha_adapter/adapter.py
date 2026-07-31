"""
Alpha Bot Adapter v2
Adapter to integrate the meta-evaluation system with the vaisravana-alpha bot.
"""

from eval_system.core.engine.eval_engine import EvaluationEngine


def create_alpha_evaluator(balance: float = 10.0) -> EvaluationEngine:
    """Create an evaluation engine for the alpha bot."""
    return EvaluationEngine(
        bot_name="alpha",
        balance=balance,
        fee_open=0.0002,
        fee_close=0.0004,
        window_size=20,
        drawdown_pause=0.20,
        drawdown_stop=0.50,
    )


def map_alpha_trade(alpha_trade: dict) -> dict:
    """Map alpha bot trade data to evaluation engine format."""
    return {
        "trade_id": alpha_trade.get("id", ""),
        "pair": alpha_trade.get("pair", ""),
        "side": alpha_trade.get("side", ""),
        "entry": alpha_trade.get("entry_price", 0.0),
        "sl": alpha_trade.get("sl_price", 0.0),
        "tp": alpha_trade.get("tp_price", 0.0),
        "exit_price": alpha_trade.get("exit_price", 0.0),
        "r_achieved": alpha_trade.get("r_achieved", 0.0),
        "fee_open": alpha_trade.get("fee_open", 0.0),
        "fee_close": alpha_trade.get("fee_close", 0.0),
        "gross_pnl": alpha_trade.get("gross_pnl", 0.0),
        "exit_reason": alpha_trade.get("exit_reason", "unknown"),
    }


def integrate_with_alpha_bot(engine: EvaluationEngine, alpha_trade: dict) -> dict:
    """Integrate evaluation into alpha bot trade lifecycle."""
    mapped_trade = map_alpha_trade(alpha_trade)
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