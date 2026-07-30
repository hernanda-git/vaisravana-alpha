"""Market data -- live feeds and the REST safety net.

Two independent paths deliver the same `Tick` and kline dicts to the engine:
a multiplexed WebSocket stream (primary, sub-second) and a REST poll loop
(fallback, ~5s). They run concurrently and are deliberately redundant, since
a silently dead feed is indistinguishable from a flat market until you check.

Pitfall preserved in code: the futures STREAM host is `fstream.binance.com`.
`fapi.binance.com` is the REST host and returns HTTP 403 to a WS handshake --
a misdiagnosis that once cost a day of "we must be IP banned".
"""

from vaisravana_alpha.marketdata.feed import (
    FeedMux,
    parse_agg_trade,
    parse_book_ticker,
    parse_kline,
    parse_mark_price,
    parse_ws_message,
    refetch_klines,
)
from vaisravana_alpha.marketdata.rest import RestPoller

__all__ = [
    "FeedMux",
    "RestPoller",
    "refetch_klines",
    "parse_ws_message",
    "parse_agg_trade",
    "parse_book_ticker",
    "parse_mark_price",
    "parse_kline",
]
