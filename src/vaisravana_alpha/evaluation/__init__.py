"""Evaluation -- offline scoring of recorded runs.

A research tool, not part of the trading path. It imports nothing from the
engine and reads only the `wave_log` table, so a verdict is reproducible
long after the run ended and scoring can never perturb what it scores.

The guards here exist because the obvious way to evaluate a strategy is
wrong. `deflated_sharpe` discounts a Sharpe ratio by how many variants were
tried, and `cscv` estimates the probability that an in-sample winner is
overfit. A raw Sharpe over twenty trades means nothing on its own.
"""

from vaisravana_alpha.evaluation.archive import Archive
from vaisravana_alpha.evaluation.cscv import (
    cscv_stability,
    probability_of_backtest_overfitting,
)
from vaisravana_alpha.evaluation.deflated_sharpe import deflated_sharpe
from vaisravana_alpha.evaluation.metrics import build_equity, load_trades, trade_sharpe

__all__ = [
    "load_trades",
    "build_equity",
    "trade_sharpe",
    "deflated_sharpe",
    "cscv_stability",
    "probability_of_backtest_overfitting",
    "Archive",
]
