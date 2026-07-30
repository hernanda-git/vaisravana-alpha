"""Symbol registry, 1000x contract mapping, and the liquidity filter.

Pure logic, no network. Binance quotes meme perps in lots of 1000 units under
a `1000`-prefixed symbol, so a user typing PEPEUSDT must be resolved to
1000PEPEUSDT before any request goes out; getting this wrong produces silent
-4130 rejections. `SymbolRegistry` additionally drops pairs whose spread or
24h volume make them untradeable at our size.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


# 1000x leveraged meme-coin perps need special handling (doc 32 lesson):
#  - lot-size / notional floors differ; validate qty to avoid instant -4130 rejection
#  - LIMIT SL can be rejected (-4120) when conditional orders disabled on the contract
#  - do NOT rely on LIMIT-SL; use conditional STOP (reduceOnly) + mark-price backup
KNOWN_1000X = {
    "BONKUSDT", "PEPEUSDT", "SHIBUSDT", "FLOKIUSDT",
    "WIFUSDT", "DOGEUSDT", "1000BONKUSDT", "1000PEPEUSDT",
    "1000SHIBUSDT", "1000FLOKIUSDT", "1000WIFUSDT", "1000LUNCUSDT",
    "1000XECUSDT", "1000RATSUSDT", "1000SATSUSDT", "BOMEUSDT",
}

# Default monitored universe (v0.1.0). Leaders (BTC/ETH/SOL) + the 12 requested alts.
# 1000x meme perps use their 1000-prefixed exchange symbol (doc 30 §2 symbol resolution).
DEFAULT_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
    "1000PEPEUSDT", "1000BONKUSDT", "ENAUSDT", "WLDUSDT", "PENGUUSDT",
    "AAVEUSDT", "TAOUSDT", "INJUSDT", "APEUSDT", "PUMPUSDT", "WIFUSDT", "CRVUSDT",
]

# User-facing base -> exchange symbol resolution for the 1000x contracts (doc 30 §2:
# "BONKUSDT->1000BONKUSDT"). Plain perps (ENA/WLD/PENGU/PUMP/...) pass through unchanged.
_SYMBOL_ALIASES = {
    "PEPEUSDT": "1000PEPEUSDT",
    "BONKUSDT": "1000BONKUSDT",
    "SHIBUSDT": "1000SHIBUSDT",
    "FLOKIUSDT": "1000FLOKIUSDT",
    "LUNCUSDT": "1000LUNCUSDT",
    "RATSUSDT": "1000RATSUSDT",
    "SATSUSDT": "1000SATSUSDT",
    "XECUSDT": "1000XECUSDT",
}


def resolve_symbol(user_symbol: str) -> str:
    """Map a user-supplied pair to its Binance USDⓈ-M exchange symbol.

    Normalizes case, appends USDT if the quote is omitted, and applies the 1000x mapping
    (e.g. PEPE / PEPEUSDT -> 1000PEPEUSDT). Already-prefixed 1000x symbols pass through.
    """
    s = user_symbol.strip().upper()
    if not s:
        return s
    if not s.endswith("USDT"):
        s = s + "USDT"
    return _SYMBOL_ALIASES.get(s, s)


@dataclass
class SymbolInfo:
    symbol: str
    base: str = ""          # derived from symbol if empty
    quote: str = "USDT"
    price_precision: int = 2
    qty_precision: int = 0
    tick_size: float = 0.01     # price granularity (exchangeInfo PRICE_FILTER)
    step_size: float = 1.0      # qty granularity (exchangeInfo LOT_SIZE); 1.0 = integer lots
    min_notional: float = 5.0
    min_qty: float = 0.0
    is_1000x: bool = False
    # live-updated liquidity metrics (doc 30 §2)
    avg_spread_bps: float = 0.0
    vol_24h_usd: float = 0.0
    liquidity_ok: bool = True

    def __post_init__(self) -> None:
        if not self.base:
            self.base = self.symbol.replace("USDT", "")

    @property
    def contract_multiplier(self) -> float:
        # Binance 1000x meme perps trade in "lots" of 1000 units (price quoted per 1 unit).
        return 1000.0 if self.is_1000x else 1.0


class SymbolRegistry:
    """Holds the tradable universe + liquidity filter (doc 30 §2, doc 20).

    Universe = all Binance USDT perpetuals, then a liquidity filter drops pairs with
    avg spread > `max_spread_bps` or 24h vol < `min_vol_usd` (doc 30 §2, [OPEN] thresholds
    tracked in doc 31).
    """

    def __init__(
        self,
        max_spread_bps: float = 20.0,   # [OPEN] doc 31: tune from live data
        min_vol_usd: float = 5_000_000.0,  # [OPEN] doc 31
        known_1000x: set[str] | None = None,
    ) -> None:
        self.max_spread_bps = max_spread_bps
        self.min_vol_usd = min_vol_usd
        self._known_1000x = known_1000x or set(KNOWN_1000X)
        self._symbols: dict[str, SymbolInfo] = {}

    def upsert(self, info: SymbolInfo) -> None:
        info.is_1000x = info.symbol in self._known_1000x
        info.liquidity_ok = self._passes_liquidity(info)
        self._symbols[info.symbol] = info

    def bulk_load(self, infos: list[SymbolInfo]) -> None:
        for i in infos:
            self.upsert(i)

    def _passes_liquidity(self, info: SymbolInfo) -> bool:
        return info.avg_spread_bps <= self.max_spread_bps and info.vol_24h_usd >= self.min_vol_usd

    @property
    def all_symbols(self) -> list[str]:
        return list(self._symbols.keys())

    def tradable(self) -> list[str]:
        """Liquidity-filtered universe (doc 20: 'all pairs available' but protected)."""
        return [s for s, i in self._symbols.items() if i.liquidity_ok]

    def get(self, symbol: str) -> SymbolInfo | None:
        return self._symbols.get(symbol)

    def is_tradable(self, symbol: str) -> bool:
        info = self._symbols.get(symbol)
        return info is not None and info.liquidity_ok

    def validate_order_qty(self, symbol: str, qty: float, notional: float) -> tuple[bool, str]:
        """Reject orders that would hit Binance -4130/-4131 (lot/minNotional).
        For 1000x, notional uses contract_multiplier (doc 32)."""
        info = self._symbols.get(symbol)
        if info is None:
            return False, "UNKNOWN_SYMBOL"
        if qty <= 0:
            return False, "QTY_LE_ZERO"
        if notional < info.min_notional:
            return False, "BELOW_MIN_NOTIONAL"
        if info.min_qty and qty < info.min_qty:
            return False, "BELOW_MIN_QTY"
        return True, "OK"

    def stats(self) -> dict:
        total = len(self._symbols)
        ok = len(self.tradable())
        return {
            "total": total,
            "tradable": ok,
            "filtered_out": total - ok,
            "pct_tradable": round(100.0 * ok / total, 1) if total else 0.0,
        }
