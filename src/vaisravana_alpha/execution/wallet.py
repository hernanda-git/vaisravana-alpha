"""Paper wallet -- fake balance, explicit fees, survival sizing.

The account starts at $10 and is allowed to run to zero. That is the point:
a paper run that gets topped up teaches nothing, while an honest run to ruin
tells you exactly how long the edge survived its own costs.

Fee model, split rather than symmetric, matching the actual plan of a
limit-style entry and a market exit:

    open  : maker 0.02% (0.0002) of notional
    close : taker 0.04% (0.0004) of notional

Sizing is a fraction of the LIVE balance, not the starting balance, so the
account shrinks its own risk during a drawdown and compounds on a winning
streak without any separate logic.

State persists to disk, since a restart that silently resets the balance to
$10 would erase the very result the experiment is measuring.

Construct with `PaperWallet.from_settings(settings)`; the module-level
`get_wallet()` singleton exists only for legacy call sites.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

DEFAULT_START_BALANCE = 10.0
DEFAULT_OPEN_FEE_RATE = 0.0002      # maker
DEFAULT_CLOSE_FEE_RATE = 0.0004     # taker
DEFAULT_RISK_PCT = 0.20
DEFAULT_MAX_TARGET = 100.0          # 10x the starting balance
DEFAULT_STOP_AT = 0.0


def _default_wallet_path() -> str:
    return os.path.join(os.getenv("ALPHA_DATA", os.getenv("VAISRAVANA_DATA", "/data")),
                        "paper_wallet.json")


@dataclass
class PaperWallet:
    """Thread-safe paper balance with fees and survival sizing."""

    balance: float = DEFAULT_START_BALANCE
    open_fee_rate: float = DEFAULT_OPEN_FEE_RATE
    close_fee_rate: float = DEFAULT_CLOSE_FEE_RATE
    risk_pct: float = DEFAULT_RISK_PCT
    max_target: float = DEFAULT_MAX_TARGET
    stop_at: float = DEFAULT_STOP_AT
    path: str = field(default_factory=_default_wallet_path)
    trades: int = 0
    fees_paid: float = 0.0
    realized_pnl: float = 0.0
    net_realized_pnl: float = 0.0
    peak_balance: float = DEFAULT_START_BALANCE
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_settings(cls, settings) -> "PaperWallet":
        """Build a wallet from a `Settings` snapshot and load persisted state."""
        return cls(
            balance=settings.paper_balance,
            open_fee_rate=settings.fee_open,
            close_fee_rate=settings.fee_close,
            risk_pct=settings.risk_pct,
            stop_at=settings.paper_stop_at,
            path=settings.wallet_path,
            peak_balance=settings.paper_balance,
        ).load()

    # -- persistence -------------------------------------------------------

    def load(self) -> "PaperWallet":
        """Restore saved state. A missing file is a normal first run."""
        try:
            with open(self.path) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            log.info("paper wallet fresh start: balance=%.2f", self.balance)
            self._save()
            return self
        except Exception as exc:
            log.warning("wallet load failed (%s) -- using configured defaults", exc)
            return self

        self.balance = float(data.get("balance", self.balance))
        self.trades = int(data.get("trades", 0))
        self.fees_paid = float(data.get("fees_paid", 0.0))
        self.realized_pnl = float(data.get("realized_pnl", 0.0))
        self.net_realized_pnl = float(data.get("net_realized_pnl", self.realized_pnl - self.fees_paid))
        self.peak_balance = float(data.get("peak_balance", self.balance))
        log.info(
            "paper wallet loaded: balance=%.4f trades=%d fees=%.4f",
            self.balance, self.trades, self.fees_paid,
        )
        return self

    def _save(self) -> None:
        """Persist atomically. Called inside the lock by every mutation.

        Written via a temp file and rename so a crash mid-write cannot leave
        a truncated JSON that reads as a zero balance on the next boot.
        """
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({
                    "balance": self.balance,
                    "trades": self.trades,
                    "fees_paid": self.fees_paid,
                    "realized_pnl": self.realized_pnl,
                    "net_realized_pnl": self.net_realized_pnl,
                    "peak_balance": self.peak_balance,
                }, fh)
            os.replace(tmp, self.path)
        except Exception as exc:
            log.debug("wallet save failed: %s", exc)

    # -- queries -----------------------------------------------------------

    @property
    def is_broke(self) -> bool:
        return self.balance <= self.stop_at

    def notional_for(self, price: float = 0.0) -> float:
        """USD notional to open, as a fraction of the live balance.

        Capped at the balance itself. Paper leverage that exceeds the account
        produces impressive equity curves and teaches nothing transferable.
        """
        with self._lock:
            balance = self.balance
        effective = min(balance, self.max_target)
        return max(0.0, min(effective * self.risk_pct, balance))

    # -- mutations ---------------------------------------------------------

    def charge_open_fee(self, notional: float) -> float:
        """Deduct the maker fee for an entry. Returns the fee charged."""
        fee = notional * self.open_fee_rate
        with self._lock:
            self.balance -= fee
            self.fees_paid += fee
            self.trades += 1
            self.peak_balance = max(self.peak_balance, self.balance)
            self._save()
        log.info("fee(open) -%.4f balance=%.4f", fee, self.balance)
        return fee

    def charge_close_fee(self, notional: float) -> float:
        """Deduct the taker fee for an exit. Returns the fee charged."""
        fee = notional * self.close_fee_rate
        with self._lock:
            self.balance -= fee
            self.fees_paid += fee
            self.peak_balance = max(self.peak_balance, self.balance)
            self._save()
        log.info("fee(close) -%.4f balance=%.4f", fee, self.balance)
        return fee

    def credit_pnl(self, pnl_usd: float) -> None:
        """Apply gross realized price PnL; fees are charged separately."""
        with self._lock:
            self.balance += pnl_usd
            self.realized_pnl += pnl_usd
            self.peak_balance = max(self.peak_balance, self.balance)
            self._save()
        log.info("pnl %+.4f balance=%.4f", pnl_usd, self.balance)

    def record_net_pnl(self, net_pnl: float) -> None:
        """Record final net result without changing balance again."""
        with self._lock:
            self.net_realized_pnl += net_pnl
            self._save()

    # -- reporting ---------------------------------------------------------

    def snapshot(self, open_waves: list | None = None) -> dict:
        """Account state for the notification footer.

        Unrealized PnL is derived from each wave's R multiple times its
        dollar risk, which keeps one definition of R across the engine, the
        cards, and the evaluator.
        """
        used = 0.0
        unrealized = 0.0
        for wave in open_waves or ():
            used += getattr(wave, "margin", 0.0) or 0.0
            notional = getattr(wave, "notional", 0.0) or 0.0
            entry = getattr(wave, "entry_price", 0.0) or 0.0
            anchor = getattr(wave, "anchor", 0.0) or 0.0
            r_mult = getattr(wave, "live_r", 0.0) or 0.0
            if entry:
                risk_per_r = notional * (abs(entry - anchor) / entry)
                unrealized += r_mult * risk_per_r

        with self._lock:
            balance, peak = self.balance, self.peak_balance
            fees, realized, trades = self.fees_paid, self.realized_pnl, self.trades

        return {
            "balance": round(balance, 4),
            "used": round(used, 4),
            "unrealized": round(unrealized, 4),
            "free": round(balance - used, 4),
            "fees_paid": round(fees, 4),
            "realized": round(realized, 4),
            "trades": trades,
            "peak": round(peak, 4),
            "max_target": self.max_target,
            "broke": self.is_broke,
        }


# Legacy singleton accessors. New code should use `PaperWallet.from_settings`.
_wallet: PaperWallet | None = None


def get_wallet() -> PaperWallet:
    global _wallet
    if _wallet is None:
        _wallet = PaperWallet().load()
    return _wallet


def reset_wallet(path: str | None = None) -> PaperWallet:
    """Wipe persisted state back to the configured start balance."""
    global _wallet
    target = path or _default_wallet_path()
    try:
        os.remove(target)
    except FileNotFoundError:
        pass
    _wallet = PaperWallet(path=target).load()
    return _wallet
