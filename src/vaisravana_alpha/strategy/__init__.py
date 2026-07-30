"""Strategy -- signal generation. Reads market context, emits candidates.

Nothing in this layer opens, closes, or sizes a position; it only decides
whether an opportunity exists. That separation is what lets the gates be
tuned by an autonomous loop without any risk of touching order flow.

Pipeline: bias -> structure/SMC -> gate -> scanner -> Candidate.
`survival` sits alongside as admission control: fee-aware EV, spread,
throttle, and session filters that can only ever reject.
"""

from vaisravana_alpha.strategy.bias import read_bias, read_confidence, size_from_confidence
from vaisravana_alpha.strategy.gate import wave_quality_pass
from vaisravana_alpha.strategy.scanner import scan
from vaisravana_alpha.strategy.smc import SMCZoneCache
from vaisravana_alpha.strategy.structure import detect_structure, ema_update
from vaisravana_alpha.strategy.survival import (
    current_cap,
    record_close,
    record_open,
    survival_gate,
)

__all__ = [
    "read_bias",
    "read_confidence",
    "size_from_confidence",
    "wave_quality_pass",
    "scan",
    "SMCZoneCache",
    "detect_structure",
    "ema_update",
    "survival_gate",
    "record_open",
    "record_close",
    "current_cap",
]
