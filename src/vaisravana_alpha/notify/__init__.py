"""Notify -- the Telegram surface.

`telegram` handles transport (sending, polling, failure modes). `cards`
handles wording and is pure: data in, string out. Keeping them apart means
a card can be unit-tested with no network and reworded with no risk to
delivery.
"""

from vaisravana_alpha.notify.cards import (
    halt_card,
    performance_card,
    positions_card,
    startup_card,
    status_card,
    wave_close_card,
    wave_open_card,
)
from vaisravana_alpha.notify.telegram import (
    CommandListener,
    TelegramNotifier,
    html_escape,
)

__all__ = [
    "TelegramNotifier",
    "CommandListener",
    "html_escape",
    "startup_card",
    "status_card",
    "positions_card",
    "performance_card",
    "wave_open_card",
    "wave_close_card",
    "halt_card",
]
