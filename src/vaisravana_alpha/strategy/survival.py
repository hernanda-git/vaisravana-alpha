"""Aggressive scalping — always allow entries.

No survival gates, no fee-aware EV gate, no trade-frequency throttle,
no spread gate, no session block. The bot must always be able to trade.
Balance growth is the only metric.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class AggressiveScaler:
    """Always allows entries — no gates, no throttling.

    Replaces the full survival-gate suite with a single pass-through.
    """

    def __init__(self):
        self._open_count = 0

    def allowed(self, pair: str) -> tuple[bool, str]:
        self._open_count += 1
        return True, "ok"


_SCALER = AggressiveScaler()


def survival_gate(pair: str, ctx, confidence: float, expected_move_bps: float) -> tuple[bool, str]:
    """Aggressive scaler — always allows entries."""
    return _SCALER.allowed(pair)


def record_open(pair: str) -> None:
    """No-op under aggressive mode."""
    pass


def record_close(net_pnl: float) -> None:
    """No-op under aggressive mode."""
    pass


def current_cap() -> int:
    """Expose the open count (for diagnostics / logging)."""
    return _SCALER._open_count