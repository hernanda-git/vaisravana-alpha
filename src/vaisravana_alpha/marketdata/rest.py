"""REST polling -- the feed that keeps working when the WebSocket does not.

Runs permanently alongside the WS mux rather than only on failure. A dead
socket is indistinguishable from a quiet market until you check, and the
legacy engine spent a full day trading on a feed everyone believed was live.
Redundancy is cheaper than that ambiguity.

The poller emits exactly the same `Tick` objects and kline dicts as the WS
path, so the engine's handlers cannot tell which source served them.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
import urllib.request

from vaisravana_alpha.core.models import Tick
from vaisravana_alpha.marketdata.feed import refetch_klines

log = logging.getLogger(__name__)

# Poll the 1h context less often than price -- it changes far more slowly and
# each pair costs a request.
CONTEXT_REFRESH_CYCLES = 12
HEARTBEAT_CYCLES = 6
REQUEST_TIMEOUT_S = 8
EXECUTOR_TIMEOUT_S = 10


class RestPoller:
    """Polls bookTicker and klines for every pair on a fixed interval."""

    def __init__(
        self,
        pairs: list[str],
        on_tick,
        on_kline,
        rest_base: str = "https://fapi.binance.com",
        interval_s: float = 5.0,
    ) -> None:
        self._pairs = list(pairs)
        self._on_tick = on_tick
        self._on_kline = on_kline
        self._rest_base = rest_base.rstrip("/")
        self._interval_s = interval_s
        self._cycles = 0
        self._ticks_emitted = 0
        self._running = False

    @property
    def cycles(self) -> int:
        return self._cycles

    @property
    def ticks_emitted(self) -> int:
        return self._ticks_emitted

    async def _fetch_json(self, url: str) -> dict | list | None:
        """Fetch JSON off the event loop so a slow request cannot stall ticks."""
        loop = asyncio.get_running_loop()
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    functools.partial(urllib.request.urlopen, request,
                                      timeout=REQUEST_TIMEOUT_S),
                ),
                timeout=EXECUTOR_TIMEOUT_S,
            )
            return json.loads(raw.read().decode())
        except asyncio.TimeoutError:
            log.debug("rest timeout: %s", url)
            return None
        except Exception as exc:
            log.debug("rest failed %s: %s", url, exc)
            return None

    async def _poll_price(self, pair: str) -> None:
        """Emit one tick from the book ticker.

        bookTicker carries no last-traded price, so the mid is derived from
        bid and ask. Reading a missing `lastPrice` field yields zero, and a
        zero price makes every downstream guard skip the tick silently.
        """
        data = await self._fetch_json(
            f"{self._rest_base}/fapi/v1/ticker/bookTicker?symbol={pair}"
        )
        if not isinstance(data, dict):
            return
        bid = float(data.get("bidPrice", 0) or 0)
        ask = float(data.get("askPrice", 0) or 0)
        if not (bid and ask):
            return
        price = (bid + ask) / 2.0
        self._ticks_emitted += 1
        await self._on_tick(Tick(
            pair=pair,
            price=price,
            qty=0.0,
            side="",
            bid=bid,
            ask=ask,
            mark=price,
            ts=time.time(),
            source="rest",
            bid_qty=float(data.get("bidQty", 0) or 0),
            ask_qty=float(data.get("askQty", 0) or 0),
        ))

    async def _poll_klines(self, pair: str, timeframe: str, limit: int) -> None:
        """Fetch and dispatch candles for one pair and timeframe."""
        loop = asyncio.get_running_loop()
        try:
            klines = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    functools.partial(refetch_klines, pair, timeframe, limit=limit),
                ),
                timeout=EXECUTOR_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            log.debug("kline timeout %s %s", pair, timeframe)
            return
        except Exception as exc:
            log.debug("kline fetch failed %s %s: %s", pair, timeframe, exc)
            return

        for kline in klines:
            kline["tf"] = timeframe
            kline["s"] = pair
            await self._on_kline(timeframe, kline)

    async def run(self) -> None:
        """Poll until cancelled. Never raises out of the loop.

        A crash here would take out the fallback feed, which is exactly the
        thing that must not fail, so every cycle is wrapped.
        """
        self._running = True
        log.info(
            "rest poller starting: %d pairs every %.1fs",
            len(self._pairs), self._interval_s,
        )
        while self._running:
            self._cycles += 1
            try:
                for pair in self._pairs:
                    await self._poll_price(pair)
                    await self._poll_klines(pair, "15m", limit=20)
                    if self._cycles % CONTEXT_REFRESH_CYCLES == 0:
                        await self._poll_klines(pair, "1h", limit=20)

                if self._cycles % HEARTBEAT_CYCLES == 0:
                    log.info(
                        "rest heartbeat: cycle=%d pairs=%d ticks=%d",
                        self._cycles, len(self._pairs), self._ticks_emitted,
                    )
            except asyncio.CancelledError:
                log.info("rest poller cancelled")
                break
            except Exception as exc:
                log.error("rest poll cycle failed: %s", exc)

            try:
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        self._running = False
