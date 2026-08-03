"""Capital guards -- kill switch and pair exclusion.

These are circuit breakers, not strategy. They only ever reduce activity, so
a bug here can cost opportunity but never capital. The mode/live boundary
used to be duplicated in this module; it now lives solely in `broker.py`
so there is exactly one place that decides whether an order can reach a
real venue.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


class KillSwitch:
    """Trips on daily drawdown or an explicit call, blocking all new entries.

    Deliberately one-way until reset: an automated system that can un-trip
    its own circuit breaker does not have a circuit breaker. Reset happens on
    the midnight roll or an owner command.
    """

    def __init__(self, daily_loss_limit_pct: float = 2.0) -> None:
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self._tripped = False
        self._trip_reason = ""
        self._trip_ts = 0.0
        self._daily_loss = 0.0
        self._day = ""

    @property
    def is_tripped(self) -> bool:
        return self._tripped

    @property
    def trip_reason(self) -> str:
        return self._trip_reason

    @property
    def daily_loss(self) -> float:
        return self._daily_loss

    def trip(self, reason: str = "manual") -> None:
        self._tripped = True
        self._trip_reason = reason
        self._trip_ts = time.time()
        log.warning("kill switch tripped: %s", reason)

    def reset(self) -> None:
        """Clear the trip. Called on the midnight roll or an owner reset."""
        self._tripped = False
        self._trip_reason = ""
        self._trip_ts = 0.0

    def record_loss(self, usd: float) -> None:
        """Accumulate a realized loss, rolling the tally at UTC midnight."""
        today = time.strftime("%Y-%m-%d")
        if self._day != today:
            self._daily_loss = 0.0
            self._day = today
        self._daily_loss += abs(usd)

    def check_daily_dd(self, equity: float) -> bool:
        """Trip if the day's realized loss breaches the limit. Returns True on trip."""
        if equity <= 0:
            return False
        pct = (self._daily_loss / equity) * 100.0
        if pct >= self.daily_loss_limit_pct and not self._tripped:
            self.trip(f"daily drawdown {pct:.2f}% >= {self.daily_loss_limit_pct}%")
            return True
        return False


class PairExcluder:
    """Skip list for pairs that have proven unprofitable.

    Held in memory by default. The engine consults it before scanning, so an
    excluded pair costs nothing but the dictionary lookup.
    """

    def __init__(self, initial: set[str] | None = None) -> None:
        self._excluded: set[str] = set(initial or ())

    def exclude(self, pair: str) -> None:
        self._excluded.add(pair)

    def include(self, pair: str) -> None:
        self._excluded.discard(pair)

    def is_excluded(self, pair: str) -> bool:
        return pair in self._excluded

    def get_excluded(self) -> list[str]:
        return sorted(self._excluded)
