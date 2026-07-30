"""The paper/live boundary.

The single most important safety property in the codebase: no order can
reach a real venue unless a real adapter was injected AND the symbol sits in
a human-approved set. These tests assert that the guarantee is structural
rather than a matter of discipline.
"""

from __future__ import annotations

import pytest

from vaisravana_alpha.execution.broker import (
    LiveBroker,
    ModeBoundaryError,
    ModeGuard,
    OrderDraft,
    PaperBroker,
)


class SpyExchange:
    """Records anything that reaches it. Nothing should, in paper mode."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def place_order(self, draft):
        self.calls.append(("place", draft.symbol))
        return None

    def cancel_order(self, symbol, order_id):
        self.calls.append(("cancel", symbol))
        return None

    def order_status(self, symbol, order_id):
        self.calls.append(("status", symbol))
        return None

    def place_conditional_stop(self, draft, stop_price):
        self.calls.append(("stop", draft.symbol))
        return None

    def mark_price(self, symbol):
        return 100.0


def draft(symbol: str = "BTCUSDT") -> OrderDraft:
    return OrderDraft(symbol=symbol, side="BUY", price=100.0, qty=1.0)


class TestModeGuard:
    def test_default_is_paper(self):
        assert ModeGuard().is_paper

    def test_unknown_mode_is_rejected_at_construction(self):
        with pytest.raises(ModeBoundaryError):
            ModeGuard(mode="backtest")

    def test_paper_yields_a_simulated_broker(self):
        assert isinstance(ModeGuard(mode="paper").broker_for(), PaperBroker)

    def test_paper_refuses_a_live_adapter(self):
        # The structural guarantee. Even handed a real exchange, paper mode
        # will not construct a path to it.
        with pytest.raises(ModeBoundaryError, match="mode=paper"):
            ModeGuard(mode="paper").broker_for(SpyExchange())

    def test_live_without_an_adapter_is_refused(self):
        with pytest.raises(ModeBoundaryError, match="requires a real exchange"):
            ModeGuard(mode="live", approved={("BTCUSDT", "1m", "BUY")}).broker_for(None)

    def test_live_without_approvals_is_refused(self):
        # A live adapter alone is not consent.
        with pytest.raises(ModeBoundaryError, match="non-empty approval"):
            ModeGuard(mode="live").broker_for(SpyExchange())

    def test_live_with_adapter_and_approvals_is_allowed(self):
        guard = ModeGuard(mode="live", approved={("BTCUSDT", "1m", "BUY")})
        assert isinstance(guard.broker_for(SpyExchange()), LiveBroker)


class TestEntryApproval:
    def test_paper_allows_any_entry(self):
        ModeGuard(mode="paper").assert_entry_allowed("DOGEUSDT", "1m", "BUY")

    def test_live_allows_an_approved_entry(self):
        guard = ModeGuard(mode="live", approved={("BTCUSDT", "1m", "BUY")})
        guard.assert_entry_allowed("BTCUSDT", "1m", "BUY")

    def test_live_blocks_an_unapproved_symbol(self):
        guard = ModeGuard(mode="live", approved={("BTCUSDT", "1m", "BUY")})
        with pytest.raises(ModeBoundaryError, match="not in the approved set"):
            guard.assert_entry_allowed("ETHUSDT", "1m", "BUY")

    def test_approval_is_per_side(self):
        # Approving a long is not approving a short on the same pair.
        guard = ModeGuard(mode="live", approved={("BTCUSDT", "1m", "BUY")})
        with pytest.raises(ModeBoundaryError):
            guard.assert_entry_allowed("BTCUSDT", "1m", "SELL")


class TestPaperBroker:
    def test_orders_fill_at_the_requested_price(self):
        result = PaperBroker().place_order(draft())
        assert result.status == "FILLED"
        assert result.avg_price == pytest.approx(100.0)
        assert result.filled_qty == pytest.approx(1.0)

    def test_mark_price_reflects_the_last_pushed_price(self):
        broker = PaperBroker()
        assert broker.mark_price("BTCUSDT") == 0.0
        broker.set_price("BTCUSDT", 12345.6)
        assert broker.mark_price("BTCUSDT") == pytest.approx(12345.6)


class TestLiveBrokerDefenceInDepth:
    def test_approved_symbol_reaches_the_adapter(self):
        spy = SpyExchange()
        LiveBroker(spy, {("BTCUSDT", "1m", "BUY")}).place_order(draft("BTCUSDT"))
        assert spy.calls == [("place", "BTCUSDT")]

    def test_unapproved_symbol_never_reaches_the_adapter(self):
        # Second line of defence: even if the entry gate were bypassed, the
        # order still cannot reach the wire.
        spy = SpyExchange()
        broker = LiveBroker(spy, {("BTCUSDT", "1m", "BUY")})
        with pytest.raises(ModeBoundaryError):
            broker.place_order(draft("SHIBUSDT"))
        assert spy.calls == []
