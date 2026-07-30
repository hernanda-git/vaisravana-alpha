"""Storage -- SQLite schema and writers.

One database holds everything: open/closed waves (`wave_log`), per-tick
samples (`wave_telemetry`), and cached SMC zones. The evaluation layer reads
these tables as its ground truth and never re-simulates, which is what makes
a verdict about a run reproducible after the fact.
"""

from vaisravana_alpha.storage.db import (
    append_telemetry,
    clear_zones,
    get_open_waves,
    get_recent_closed,
    init_db,
    log_wave_close,
    log_wave_open,
    table_exists,
    upsert_zone,
    wave_exists,
)

__all__ = [
    "init_db",
    "table_exists",
    "log_wave_open",
    "log_wave_close",
    "wave_exists",
    "append_telemetry",
    "upsert_zone",
    "clear_zones",
    "get_open_waves",
    "get_recent_closed",
]
