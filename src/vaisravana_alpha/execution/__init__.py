"""Execution -- position lifecycle, sizing, accounting, and the venue boundary.

`manager` owns a wave from open to close. `wallet` is the paper accounting
ledger (balance, margin, the maker-open/taker-close fee split). `broker` is
the only module that can talk to a venue, and `risk` holds the circuit
breakers.

Invariant worth preserving: side effects (charging a fee, sending an order)
happen only after every admission check has passed, and are idempotent per
live position. Violating that is how the legacy engine once billed 360 fees
for 27 real trades.
"""

from vaisravana_alpha.execution.broker import (
    Exchange,
    LiveBroker,
    ModeBoundaryError,
    ModeGuard,
    OrderDraft,
    OrderResult,
    PaperBroker,
)
from vaisravana_alpha.execution.manager import WaveManager
from vaisravana_alpha.execution.risk import KillSwitch, PairExcluder
from vaisravana_alpha.execution.wallet import PaperWallet, get_wallet, reset_wallet

__all__ = [
    "Exchange",
    "LiveBroker",
    "ModeBoundaryError",
    "ModeGuard",
    "OrderDraft",
    "OrderResult",
    "PaperBroker",
    "WaveManager",
    "KillSwitch",
    "PairExcluder",
    "PaperWallet",
    "get_wallet",
    "reset_wallet",
]
