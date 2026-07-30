"""Paper wallet accounting.

Money arithmetic gets its own suite because an error here is invisible: the
bot keeps running and the equity curve is simply wrong. The fee split in
particular is asymmetric, and a symmetric assumption understates cost by
33% on every round trip.
"""

from __future__ import annotations

import pytest

from vaisravana_alpha.core.settings import Settings
from vaisravana_alpha.execution.wallet import PaperWallet


@pytest.fixture
def fresh(tmp_path) -> PaperWallet:
    return PaperWallet.from_settings(Settings(
        data_dir=str(tmp_path), paper_balance=10.0,
        fee_open=0.0002, fee_close=0.0004, risk_pct=0.20,
    ))


class TestFees:
    def test_open_charges_the_maker_rate(self, fresh):
        assert fresh.charge_open_fee(100.0) == pytest.approx(0.02)
        assert fresh.balance == pytest.approx(9.98)

    def test_close_charges_the_taker_rate(self, fresh):
        assert fresh.charge_close_fee(100.0) == pytest.approx(0.04)

    def test_close_costs_double_the_open(self, fresh):
        # Maker-open plus taker-close is the actual plan. Treating the two as
        # equal would understate the true round-trip cost.
        assert fresh.close_fee_rate == pytest.approx(2 * fresh.open_fee_rate)

    def test_round_trip_cost_is_six_basis_points(self, fresh):
        fresh.charge_open_fee(100.0)
        fresh.charge_close_fee(100.0)
        assert fresh.fees_paid == pytest.approx(0.06)
        assert fresh.balance == pytest.approx(9.94)

    def test_fees_accumulate_across_trades(self, fresh):
        for _ in range(5):
            fresh.charge_open_fee(10.0)
            fresh.charge_close_fee(10.0)
        assert fresh.fees_paid == pytest.approx(0.03)
        assert fresh.trades == 5


class TestSizing:
    def test_notional_is_a_fraction_of_live_balance(self, fresh):
        assert fresh.notional_for(100.0) == pytest.approx(2.0)

    def test_sizing_shrinks_with_the_account(self, fresh):
        # Survival behaviour: a drawdown automatically reduces risk without
        # any separate de-risking rule.
        fresh.credit_pnl(-5.0)
        assert fresh.notional_for(100.0) == pytest.approx(1.0)

    def test_sizing_grows_with_the_account(self, fresh):
        fresh.credit_pnl(10.0)
        assert fresh.notional_for(100.0) == pytest.approx(4.0)

    def test_notional_never_exceeds_the_balance(self, fresh):
        fresh.risk_pct = 5.0
        assert fresh.notional_for(100.0) <= fresh.balance

    def test_broke_account_sizes_to_zero(self, fresh):
        fresh.credit_pnl(-10.0)
        assert fresh.notional_for(100.0) == 0.0


class TestRuin:
    def test_not_broke_at_start(self, fresh):
        assert not fresh.is_broke

    def test_broke_at_exactly_zero(self, fresh):
        # The run is designed to be allowed to reach zero, and the boundary
        # must count as ruin rather than one last free trade.
        fresh.credit_pnl(-10.0)
        assert fresh.balance == pytest.approx(0.0)
        assert fresh.is_broke

    def test_broke_below_zero(self, fresh):
        fresh.credit_pnl(-11.0)
        assert fresh.is_broke


class TestPersistence:
    def test_balance_survives_a_restart(self, tmp_path):
        settings = Settings(data_dir=str(tmp_path), paper_balance=10.0)
        first = PaperWallet.from_settings(settings)
        first.credit_pnl(-3.5)

        # A restart that silently reset to $10 would erase the experiment.
        reloaded = PaperWallet.from_settings(settings)
        assert reloaded.balance == pytest.approx(6.5)
        assert reloaded.realized_pnl == pytest.approx(-3.5)

    def test_peak_tracks_the_high_water_mark(self, fresh):
        fresh.credit_pnl(5.0)
        fresh.credit_pnl(-8.0)
        assert fresh.peak_balance == pytest.approx(15.0)
        assert fresh.balance == pytest.approx(7.0)

    def test_corrupt_state_falls_back_to_configured_defaults(self, tmp_path):
        settings = Settings(data_dir=str(tmp_path), paper_balance=10.0)
        (tmp_path / "paper_wallet.json").write_text("{truncated")
        assert PaperWallet.from_settings(settings).balance == pytest.approx(10.0)


class TestSnapshot:
    def test_snapshot_of_a_flat_account(self, fresh):
        snap = fresh.snapshot()
        assert snap["balance"] == pytest.approx(10.0)
        assert snap["used"] == 0.0
        assert snap["unrealized"] == 0.0
        assert snap["free"] == pytest.approx(10.0)

    def test_snapshot_accounts_for_open_exposure(self, fresh):
        class FakeWave:
            margin, notional = 2.0, 6.0
            entry_price, anchor, live_r = 100.0, 99.0, 1.5

        snap = fresh.snapshot([FakeWave()])
        assert snap["used"] == pytest.approx(2.0)
        # risk per R = 6.0 * (1/100) = 0.06; 1.5R unrealized = 0.09
        assert snap["unrealized"] == pytest.approx(0.09)
        assert snap["free"] == pytest.approx(8.0)
