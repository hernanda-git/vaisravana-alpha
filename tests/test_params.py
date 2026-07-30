"""Parameter surface validation.

The surface is the one thing an autonomous tuning loop may rewrite, so its
validators are load-bearing safety code rather than input hygiene. Each test
here pins an invariant that a bad autotune write must not be able to breach.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from vaisravana_alpha.core.params import (
    BiasWeights,
    EntryGates,
    ParameterSurface,
    RiskLimits,
    default_surface,
    load_surface,
    save_surface,
)


class TestBiasWeights:
    def test_defaults_are_valid_and_normalised(self):
        assert sum(BiasWeights().as_dict().values()) == pytest.approx(1.0)

    def test_weights_that_do_not_sum_to_one_are_rejected(self):
        # Every value below is individually in bounds, so this exercises the
        # sum validator rather than the per-field ranges. Silently
        # renormalising would hide the mistake and change every component's
        # effective influence at once.
        with pytest.raises(ValidationError, match="sum to 1.0"):
            BiasWeights(
                mtf_ema=0.40, flow_delta=0.25, book_pressure=0.20,
                breadth=0.10, risk_regime=0.20,     # sums to 1.15
            )

    def test_weights_summing_below_one_are_also_rejected(self):
        with pytest.raises(ValidationError, match="sum to 1.0"):
            BiasWeights(
                mtf_ema=0.20, flow_delta=0.20, book_pressure=0.20,
                breadth=0.10, risk_regime=0.05,     # sums to 0.75
            )

    def test_component_bounds_are_enforced(self):
        with pytest.raises(ValidationError):
            BiasWeights(mtf_ema=0.99)


class TestRiskLimits:
    def test_default_rr_meets_the_owner_floor(self):
        assert RiskLimits().rr == pytest.approx(2.0)

    def test_rr_below_two_to_one_is_rejected(self):
        # An owner mandate: one win must recover at least two losses.
        with pytest.raises(ValidationError, match="below the 2:1"):
            RiskLimits(tp_atr_mult=1.5, sl_atr_mult=1.0)

    def test_wider_tp_is_accepted(self):
        assert RiskLimits(tp_atr_mult=3.0, sl_atr_mult=1.0).rr == pytest.approx(3.0)


class TestEntryGates:
    def test_throttle_band_must_be_ordered(self):
        with pytest.raises(ValidationError, match="floor <= start <= ceil"):
            EntryGates(trades_per_hour_floor=10, trades_per_hour_start=5)

    def test_throttle_floor_stays_above_zero(self):
        # A floor of zero lets the adaptive throttle halt trading entirely,
        # and a bot that never trades cannot compound.
        assert EntryGates().trades_per_hour_floor >= 1
        with pytest.raises(ValidationError):
            EntryGates(trades_per_hour_floor=0)

    def test_ev_gate_requires_a_margin_over_cost(self):
        assert EntryGates().ev_gate_k >= 1.0
        with pytest.raises(ValidationError):
            EntryGates(ev_gate_k=0.5)


class TestParameterSurface:
    def test_default_surface_constructs(self):
        surface = default_surface()
        assert surface.revision == 0
        assert surface.max_leverage == surface.risk.max_leverage

    def test_roundtrip_through_disk(self, tmp_path):
        original = ParameterSurface(revision=7, note="iter-7 promotion")
        path = save_surface(original, str(tmp_path / "surface.json"))
        assert load_surface(path).revision == 7

    def test_missing_file_yields_defaults(self, tmp_path):
        # First boot has no promoted surface; that is normal, not an error.
        assert load_surface(str(tmp_path / "absent.json")).revision == 0

    def test_corrupt_file_falls_back_instead_of_crashing(self, tmp_path):
        path = tmp_path / "surface.json"
        path.write_text("{not json")
        assert load_surface(str(path)).revision == 0

    def test_out_of_bounds_file_is_refused_in_favour_of_defaults(self, tmp_path):
        # The critical case: an autotune loop writes an unsafe R:R. Running
        # known-good defaults beats trading a surface nobody validated.
        path = tmp_path / "surface.json"
        path.write_text(json.dumps({
            "revision": 99,
            "risk": {"tp_atr_mult": 1.0, "sl_atr_mult": 1.0},
        }))
        loaded = load_surface(str(path))
        assert loaded.revision == 0
        assert loaded.risk.rr >= 2.0

    def test_atomic_save_leaves_no_temp_file(self, tmp_path):
        save_surface(default_surface(), str(tmp_path / "surface.json"))
        assert not list(tmp_path.glob("*.tmp"))
