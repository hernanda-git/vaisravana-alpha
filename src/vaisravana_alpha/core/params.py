"""Parameter surface -- the only thing an autonomous agent may mutate.

This is the Sentinel boundary. Weights, thresholds, and risk limits live
here and may be tuned by the improvement loop. Engine code, execution logic,
and the telemetry schema are structural: changing them requires a human.

The legacy surface carried three concurrent StrategyProfiles (scalp/day/swing)
inherited from the main bot even though the wave engine only ever used one
timescale, plus a 9-factor weight vector where four factors were never
populated. Both are dropped here. What remains is what the engine actually
reads, so a tuning agent cannot waste iterations on dead knobs.
"""

from __future__ import annotations

import json
import logging
import os

from pydantic import BaseModel, Field, model_validator

log = logging.getLogger(__name__)


class BiasWeights(BaseModel):
    """Blend weights for the composite bias signal. Must sum to 1.0.

    Every component listed here is populated by the engine at runtime. If you
    add one, wire its assignment before adding weight, otherwise you silently
    dilute the live signal -- the failure that made 65% of the legacy blend
    dead weight.
    """

    mtf_ema: float = Field(default=0.40, ge=0.10, le=0.70)
    flow_delta: float = Field(default=0.25, ge=0.00, le=0.50)
    book_pressure: float = Field(default=0.20, ge=0.00, le=0.40)
    breadth: float = Field(default=0.10, ge=0.00, le=0.30)
    risk_regime: float = Field(default=0.05, ge=0.00, le=0.30)

    @model_validator(mode="after")
    def _sum_to_one(self) -> "BiasWeights":
        total = sum(self.model_dump().values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"bias weights must sum to 1.0, got {total:.6f}")
        return self

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class EntryGates(BaseModel):
    """Admission thresholds. Raising these trades frequency for selectivity.

    A gate change that drives opens to zero is a rejection, not a success:
    a bot that never trades cannot compound. Raise one floor at a time and
    watch the opens/hour counter.
    """

    bias_strength_floor: float = Field(default=0.30, ge=0.0, le=1.0)
    confidence_floor: float = Field(default=0.12, ge=0.0, le=1.0)
    structure_floor: float = Field(default=0.12, ge=0.0, le=1.0)
    adx_floor: float = Field(default=18.0, ge=0.0, le=50.0)

    # Fee-aware expected-value gate: require E[move] >= k x round-trip cost.
    ev_gate_k: float = Field(default=1.4, ge=1.0, le=5.0)
    spread_gate_bps: float = Field(default=5.0, ge=0.0, le=50.0)
    slip_bps: float = Field(default=1.0, ge=0.0, le=20.0)

    # Adaptive throttle bounds. The engine floats the cap inside this band
    # from realized expectancy; the floor stays above zero so it never halts.
    trades_per_hour_start: int = Field(default=6, ge=1, le=100)
    trades_per_hour_floor: int = Field(default=4, ge=1, le=100)
    trades_per_hour_ceil: int = Field(default=20, ge=1, le=200)
    pair_spacing_min: float = Field(default=20.0, ge=0.0, le=240.0)

    @model_validator(mode="after")
    def _throttle_band_ordered(self) -> "EntryGates":
        if not (self.trades_per_hour_floor
                <= self.trades_per_hour_start
                <= self.trades_per_hour_ceil):
            raise ValueError(
                f"throttle band must be floor <= start <= ceil, got "
                f"{self.trades_per_hour_floor} / {self.trades_per_hour_start} / "
                f"{self.trades_per_hour_ceil}"
            )
        return self


class ExitRules(BaseModel):
    """When a wave dies. Loss-protection rules can only ever cut a loser."""

    loss_cut_r: float = Field(default=0.35, ge=0.05, le=2.0)
    breakeven_arm_r: float = Field(default=0.30, ge=0.05, le=2.0)
    reversal_arm_r: float = Field(default=0.20, ge=0.05, le=2.0)
    confidence_exit_floor: float = Field(default=0.16, ge=0.0, le=1.0)
    cooldown_s: float = Field(default=600.0, ge=0.0, le=7200.0)


class RiskLimits(BaseModel):
    """Hard capital bounds. The R:R floor is an owner mandate, not a hint."""

    max_leverage: int = Field(default=3, ge=1, le=20)
    max_open_waves: int = Field(default=8, ge=1, le=50)
    risk_per_trade_pct: float = Field(default=0.20, ge=0.01, le=1.0)
    daily_loss_limit_pct: float = Field(default=0.5, ge=0.1, le=5.0)
    sl_atr_mult: float = Field(default=1.0, ge=0.5, le=3.0)
    tp_atr_mult: float = Field(default=2.0, ge=1.0, le=6.0)

    @property
    def rr(self) -> float:
        """Reward-to-risk ratio implied by the ATR multipliers."""
        return self.tp_atr_mult / self.sl_atr_mult if self.sl_atr_mult else 0.0

    @model_validator(mode="after")
    def _rr_floor(self) -> "RiskLimits":
        """Owner floor: one win must recover at least two losses (R:R >= 2)."""
        if self.rr < 2.0 - 1e-9:
            raise ValueError(
                f"R:R {self.rr:.3f} is below the 2:1 owner floor "
                f"(tp_atr_mult={self.tp_atr_mult}, sl_atr_mult={self.sl_atr_mult})"
            )
        return self


class ParameterSurface(BaseModel):
    """Everything tunable, in one validated object.

    Constructing an invalid surface raises immediately, so a bad autotune
    write cannot reach the trading loop -- the process refuses to boot rather
    than trading a surface nobody checked.
    """

    bias: BiasWeights = Field(default_factory=BiasWeights)
    gates: EntryGates = Field(default_factory=EntryGates)
    exits: ExitRules = Field(default_factory=ExitRules)
    risk: RiskLimits = Field(default_factory=RiskLimits)

    # Bookkeeping so a promoted surface is traceable to the run that produced it.
    revision: int = Field(default=0, ge=0)
    note: str = ""

    @property
    def max_leverage(self) -> int:
        """Compatibility shim for execution code that reads a flat field."""
        return self.risk.max_leverage

    def as_dict(self) -> dict:
        return self.model_dump()


def default_surface() -> ParameterSurface:
    """A fresh, validated surface with the current live defaults."""
    return ParameterSurface()


def load_surface(path: str | None = None) -> ParameterSurface:
    """Load a promoted surface from disk, falling back to defaults.

    A missing file is normal (first boot). A malformed or out-of-bounds file
    is logged loudly and ignored: running the known-good defaults beats
    running whatever a half-written JSON happens to contain.
    """
    if not path:
        path = os.path.join(os.getenv("ALPHA_DATA", "/data"), "surface.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return default_surface()
    except Exception as exc:
        log.warning("surface %s unreadable (%s) -- using defaults", path, exc)
        return default_surface()
    try:
        surface = ParameterSurface(**data)
    except Exception as exc:
        log.error("surface %s failed validation (%s) -- using defaults", path, exc)
        return default_surface()
    log.info("loaded surface rev=%d from %s", surface.revision, path)
    return surface


def save_surface(surface: ParameterSurface, path: str | None = None) -> str:
    """Persist a surface atomically so a crash mid-write cannot corrupt it."""
    if not path:
        path = os.path.join(os.getenv("ALPHA_DATA", "/data"), "surface.json")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(surface.as_dict(), fh, indent=2)
    os.replace(tmp, path)
    return path
