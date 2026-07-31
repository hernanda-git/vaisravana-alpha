"""
Evaluation Configuration
All parameters for the 5-layer evaluation system.
"""

# Evaluation window size (trades per window)
WINDOW_SIZE = 20

# Drawdown thresholds
DRAWDOWN_PAUSE = 0.20  # pause at 20% drawdown
DRAWDOWN_STOP = 0.50   # stop at 50% drawdown

# Layer 1: Per-Trade
TRADE_SCORE_WEIGHTS = {
    "r_achieved": 0.40,
    "fee_efficiency": 0.30,
    "ev_status": 0.30,
}
TARGET_R = 0.50  # target R for score normalization

# Layer 2: Aggregate
AGGREGATE_SCORE_WEIGHTS = {
    "wr_score": 0.35,
    "avg_r_score": 0.25,
    "fee_efficiency": 0.20,
    "sharpe_score": 0.20,
}
TARGET_WR = 0.60    # 60% win rate target
TARGET_AVG_R = 0.50  # 0.50R average target
TARGET_SHARPE = 2.0  # 2.0 Sharpe target

# Thresholds
GREEN_THRESHOLD = 0.60
YELLOW_THRESHOLD = 0.40

# Layer 3: Baseline
BASELINE_WEIGHTS = {
    "beat_random": 0.40,
    "beat_buyhold": 0.30,
    "beat_previous": 0.30,
}
RANDOM_WR = 0.50
RANDOM_AVG_R = 0.0

# Layer 4: Decision Gate
DECISION_THRESHOLDS = {
    "persist_min_aggregate": 0.60,
    "persist_min_baseline": 0.40,
    "iterate_min_aggregate": 0.40,
    "rollback_max_aggregate": 0.40,
    "pause_drawdown": 0.20,
    "stop_drawdown": 0.50,
}

# Fee model
FEE_MODEL = {
    "open_maker": 0.0002,   # 0.02%
    "close_taker": 0.0004,  # 0.04%
    "total_rt": 0.0006,     # 6bps RT (maker + taker)
    "estimated_slip": 0.0001,  # ~1bps slippage
    "total_cost": 0.0007,   # 7bps total
}