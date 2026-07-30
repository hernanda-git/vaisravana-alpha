"""Vaiśravaṇa Alpha — tick-driven crypto-futures paper trading engine.

Layered package. Import direction is strictly downward, never sideways:

    engine/      orchestration + async runtime (the only layer that wires)
      |
      +-- strategy/    signal generation (bias, structure, SMC, gates, scanner)
      +-- execution/   position lifecycle, sizing, wallet, broker boundary
      +-- marketdata/  feeds (WS mux + REST poll)
      +-- notify/      Telegram surface
      +-- storage/     SQLite schema + writers
            |
            +-- core/  models, params, settings (depends on nothing)

`evaluation/` is a sibling research tool: it reads recorded trade DBs and
never imports the engine, so scoring can never perturb what it scores.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
