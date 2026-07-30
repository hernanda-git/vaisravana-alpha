"""Broker boundary -- the structural guarantee that paper cannot become live.

The most important safety property in this codebase: a (pair, tf, side) can
never reach a real exchange without both a real adapter being injected AND
that key sitting in a human-approved set. In paper mode no live adapter can
even be constructed, so the guarantee is enforced by types and control flow
rather than by a comment saying "there is no live order path".

Contents:
  * `Exchange`      -- the protocol every adapter satisfies
  * `PaperBroker`   -- deterministic simulated fills, no network
  * `LiveBroker`    -- wraps a real adapter, refuses unapproved symbols
  * `ModeGuard`     -- picks the broker and gates entries
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

TRADING_MODES = ("paper", "live")


class ModeBoundaryError(RuntimeError):
    """Raised when code attempts a live action the mode or approvals forbid."""


# -- order types --------------------------------------------------------------


@dataclass
class OrderDraft:
    """An intent to trade, before any adapter has seen it."""

    symbol: str
    side: str                       # BUY | SELL
    price: float
    qty: float
    order_type: str = "LIMIT"
    reduce_only: bool = False
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])


@dataclass
class OrderResult:
    """What the venue (real or simulated) reported back."""

    status: str                     # FILLED | NEW | CANCELED | REJECTED | FAILED
    order_id: str = ""
    filled_qty: float = 0.0
    avg_price: float = 0.0
    error_code: int | None = None
    error_msg: str = ""


class Exchange(Protocol):
    """Everything the position lifecycle needs from a venue."""

    def place_order(self, draft: OrderDraft) -> OrderResult: ...
    def cancel_order(self, symbol: str, order_id: str) -> OrderResult: ...
    def order_status(self, symbol: str, order_id: str) -> OrderResult: ...
    def place_conditional_stop(self, draft: OrderDraft, stop_price: float) -> OrderResult: ...
    def mark_price(self, symbol: str) -> float: ...


# -- error classification -----------------------------------------------------

AUTH, RATE_LIMIT, SERVER, NETWORK, ORDER_REJECT = (
    "AUTH", "RATE_LIMIT", "SERVER", "NETWORK", "ORDER_REJECT",
)


def classify_error(code: int | None, network: bool = False) -> tuple[str, bool]:
    """Return (category, retryable).

    Auth failures are never retried -- hammering a rejected key gets the IP
    banned. A network error is retryable and must be treated as PENDING, not
    FAILED: the order may well have landed.
    """
    if network:
        return NETWORK, True
    if code in (401, 403):
        return AUTH, False
    if code == 429:
        return RATE_LIMIT, True
    if code is not None and 500 <= code < 600:
        return SERVER, True
    return ORDER_REJECT, False


# -- paper broker -------------------------------------------------------------


class PaperBroker:
    """Simulated venue. No network, fully deterministic.

    Fills happen instantly at the requested price. That is optimistic versus
    reality, which is why realistic costs are modelled in the wallet as an
    explicit maker-open / taker-close fee split rather than being hidden in
    a fuzzy fill assumption.
    """

    def __init__(self, clock=time.time) -> None:
        self._prices: dict[str, float] = {}
        self._clock = clock

    def set_price(self, symbol: str, price: float) -> None:
        """Push the latest observed price so `mark_price` stays live."""
        self._prices[symbol] = price

    def mark_price(self, symbol: str) -> float:
        return self._prices.get(symbol, 0.0)

    def place_order(self, draft: OrderDraft) -> OrderResult:
        return OrderResult(
            status="FILLED",
            order_id=str(uuid.uuid4())[:12],
            filled_qty=draft.qty,
            avg_price=draft.price,
        )

    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        return OrderResult(status="CANCELED", order_id=order_id)

    def order_status(self, symbol: str, order_id: str) -> OrderResult:
        return OrderResult(status="FILLED", order_id=order_id)

    def place_conditional_stop(self, draft: OrderDraft, stop_price: float) -> OrderResult:
        return OrderResult(status="NEW", order_id=str(uuid.uuid4())[:12])


# -- live broker --------------------------------------------------------------


class LiveBroker:
    """Wraps a real adapter and refuses any symbol outside the approved set.

    Defence in depth. `ModeGuard.assert_entry_allowed` is the primary gate at
    entry time; this exists so a stray code path cannot reach the wire on an
    unapproved symbol even if that gate is bypassed.
    """

    def __init__(self, real: Exchange, approved: set) -> None:
        self._real = real
        self._approved = approved

    def _check(self, symbol: str) -> None:
        if symbol not in {pair for (pair, _tf, _side) in self._approved}:
            raise ModeBoundaryError(f"live order on {symbol} is not human-approved")

    def place_order(self, draft: OrderDraft) -> OrderResult:
        self._check(draft.symbol)
        return self._real.place_order(draft)

    def cancel_order(self, symbol: str, order_id: str) -> OrderResult:
        self._check(symbol)
        return self._real.cancel_order(symbol, order_id)

    def order_status(self, symbol: str, order_id: str) -> OrderResult:
        self._check(symbol)
        return self._real.order_status(symbol, order_id)

    def place_conditional_stop(self, draft: OrderDraft, stop_price: float) -> OrderResult:
        self._check(draft.symbol)
        return self._real.place_conditional_stop(draft, stop_price)

    def mark_price(self, symbol: str) -> float:
        return self._real.mark_price(symbol)


# -- mode guard ---------------------------------------------------------------


@dataclass
class ModeGuard:
    """Enforces the human-gated live boundary at construction time.

    paper: builds a `PaperBroker` and refuses to accept a live adapter at all.
    live:  requires a real adapter plus a non-empty approval set, and raises
           on any entry whose (pair, tf, side) was never approved.
    """

    mode: str = "paper"
    approved: set = field(default_factory=set)   # {(pair, tf, side), ...}

    def __post_init__(self) -> None:
        if self.mode not in TRADING_MODES:
            raise ModeBoundaryError(f"unknown trading mode {self.mode!r}")

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"

    def assert_entry_allowed(self, pair: str, tf: str, side: str) -> None:
        if self.mode == "paper":
            return
        if (pair, tf, side) not in self.approved:
            raise ModeBoundaryError(
                f"live entry {pair}/{tf}/{side} is not in the approved set"
            )

    def broker_for(self, live_adapter: Exchange | None = None) -> Exchange:
        """Return the broker the engine should drive."""
        if self.mode == "paper":
            if live_adapter is not None:
                raise ModeBoundaryError("a live adapter was passed while mode=paper")
            return PaperBroker()
        if live_adapter is None:
            raise ModeBoundaryError("live mode requires a real exchange adapter")
        if not self.approved:
            raise ModeBoundaryError("live mode requires a non-empty approval set")
        return LiveBroker(live_adapter, self.approved)
