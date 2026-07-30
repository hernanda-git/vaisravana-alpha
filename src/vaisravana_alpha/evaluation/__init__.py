"""Evaluation -- offline scoring of recorded runs.

A research tool, not part of the trading path. `layers` is the multi-layer
verdict stack; `metrics`, `cscv`, `deflated_sharpe`, and `archive` are its
supporting pieces; `cli` exposes it as `alpha-eval`. The improvement loop
imports the same functions, so there is one evaluation contract for both
the bot and a human.
"""