"""Shared indicator maths.

Small, pure functions used by more than one layer. They live here rather
than inside the manager because the engine's expected-value gate needs the
same ATR the manager sizes stops with, and two copies of an indicator drift
apart the moment one is tuned.

Every function is total: given degenerate input it returns a documented
fallback instead of raising, since an indicator that throws on a thin
candle window would take down the tick loop.
"""

from __future__ import annotations

# Used when the candle window is too short to measure. 1% is a deliberately
# ordinary crypto 15m range: it keeps stops sane during warmup without
# pretending to precision the data cannot support.
DEFAULT_ATR_PCT = 0.010


def atr_pct(ctx, tf: str = "15m", period: int = 14) -> float:
    """Average true range as a fraction of price.

    Expressed as a fraction rather than absolute currency so one threshold
    works across BTC at $100k and a meme perp at $0.00001.

    Accepts both the verbose kline keys ("high"/"low"/"close") and Binance's
    single-letter WS form ("h"/"l"/"c"), because the two feeds deliver
    different shapes and normalising at the reader is cheaper than forcing
    both producers into one schema.
    """
    klines = ctx.klines.get(tf, [])
    if len(klines) < period + 1:
        return DEFAULT_ATR_PCT

    true_ranges = []
    for i in range(1, min(period, len(klines))):
        high = float(klines[i].get("high", klines[i].get("h", 0)) or 0)
        low = float(klines[i].get("low", klines[i].get("l", 0)) or 0)
        prev_close = float(
            klines[i - 1].get("close", klines[i - 1].get("c", 0)) or 0
        )
        if high and low:
            true_ranges.append(
                max(high - low, abs(high - prev_close), abs(low - prev_close))
            )

    if not true_ranges:
        return DEFAULT_ATR_PCT

    atr = sum(true_ranges) / len(true_ranges)
    last = klines[-1]
    price = float(last.get("close", last.get("c", ctx.price)) or 0) or ctx.price
    return atr / price if price else DEFAULT_ATR_PCT
