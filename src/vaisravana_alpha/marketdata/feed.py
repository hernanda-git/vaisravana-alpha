"""Feed layer — WebSocket multiplexer (FeedMux) for real-time tick streams.

Parses Binance Futures WS messages (aggTrade, bookTicker, markPrice, kline)
and emits unified Tick objects to the engine hot path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable, Optional

from vaisravana_alpha.core.models import Tick

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

RECONNECT_BASE_S = 2.0
RECONNECT_MAX_S = 60.0
MAX_AGE_S = 30.0  # staleness threshold
CONFIRM_MS = 0.25  # micro-confirmation window for exits (250ms)

# ── REST backfill (on WS disconnect / re-subscribe) ──────────────────────────

# Default Binance klines URL; override via VAISRAVANA_KLINES_URL for geo-blocked regions.
KLINES_URL = "https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval={t}&limit={n}"
BACKFILL_LIMIT = 100  # number of klines to refetch on reconnect


def refetch_klines(symbol: str, tf: str, limit: int = BACKFILL_LIMIT,
                   url_override: str = "") -> list[dict]:
    """Fetch recent klines from Binance REST API.

    Returns list of dicts with keys: ts, o, h, l, c, v, is_final (always True).
    Uses the process-wide HTTPS_PROXY/HTTP_PROXY if configured (no direct
    urllib proxy support — use VAISRAVANA_KLINES_URL to point at a mirror in
    geo-blocked regions).
    """
    import json
    import urllib.request
    base = url_override or KLINES_URL
    url = base.format(s=symbol, t=tf, n=limit)
    try:
        raw = json.loads(urllib.request.urlopen(url, timeout=15).read().decode())
    except Exception as e:
        log.warning("refetch_klines %s %s failed: %s", symbol, tf, e)
        return []
    klines = []
    for r in raw:
        k = {
            "ts": r[0],
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
            "is_final": True,
        }
        # iter-E: Binance kline index 9 = taker-buy base volume. Lets the
        # engine derive signed flow_delta = buys - sells for read_bias()'s
        # 25%-weight flow component (dead/0 until now).
        if len(r) > 9:
            k["taker_buy_volume"] = float(r[9])
        klines.append(k)
    return klines

# ── Message parsing (pure, fully testable) ────────────────────────────────────


def parse_agg_trade(raw: dict, pair: str) -> Optional[Tick]:
    """Parse a Binance aggTrade event into a live Tick."""
    if raw.get("e") != "aggTrade":
        return None
    return Tick(
        pair=pair,
        price=float(raw["p"]),
        qty=float(raw["q"]),
        side="BUY" if raw.get("m") is False else "SELL",
        bid=0.0,
        ask=0.0,
        mark=float(raw["p"]),
        ts=raw.get("T", int(time.time() * 1000)) / 1000.0,
        source="aggTrade",
    )


def parse_book_ticker(raw: dict, pair: str) -> Optional[Tick]:
    """Parse a @bookTicker event into a live Tick (bid/ask pressure)."""
    if raw.get("e") != "bookTicker":
        return None
    return Tick(
        pair=pair,
        price=float(raw.get("a", 0)),  # best ask as reference price
        qty=0.0,
        side="",
        bid=float(raw.get("b", 0)),
        ask=float(raw.get("a", 0)),
        mark=0.0,
        ts=raw.get("T", int(time.time() * 1000)) / 1000.0,
        source="bookTicker",
        bid_qty=float(raw.get("B", 0)),  # iter-D: best-bid size
        ask_qty=float(raw.get("A", 0)),  # iter-D: best-ask size
    )


def parse_mark_price(raw: dict, pair: str) -> Optional[Tick]:
    """Parse a @markPrice event into a live Tick."""
    if raw.get("e") != "markPriceUpdate":
        return None
    return Tick(
        pair=pair,
        price=float(raw.get("p", 0)),
        qty=0.0,
        side="",
        bid=0.0,
        ask=0.0,
        mark=float(raw.get("p", 0)),
        ts=raw.get("E", int(time.time() * 1000)) / 1000.0,
        source="markPrice",
    )


def parse_kline(raw: dict, pair: str) -> Optional[dict]:
    """Parse a kline event; returns None if still forming, dict if closed + final.

    Returns:
        None — kline is still forming (ignore for context updates)
        dict with keys {tf, open, close, high, low, volume, closed_ts} — final kline
    """
    k = raw.get("k")
    if not k:
        return None
    is_final = k.get("x", False)
    # Return the kline data for both forming and final — BiasEngine uses forming
    # candles for real-time context; SMCZoneCache uses only final.
    result = {
        "tf": k.get("i", ""),
        "s": raw.get("s", ""),  # ITER-18: symbol so engine on_kline() routes it
        "open": float(k.get("o", 0)),
        "close": float(k.get("c", 0)),
        "high": float(k.get("h", 0)),
        "low": float(k.get("l", 0)),
        "volume": float(k.get("v", 0)),
        "closed_ts": k.get("T", 0),
        "is_final": is_final,
    }
    # ITER-18: Binance kline WS event carries taker-buy base volume in k["V"].
    # Surface it so flow_delta can be derived from the LIVE WS path (not only
    # the REST poll, which fails from sera's geo-blocked container).
    if k.get("V") is not None:
        result["taker_buy_volume"] = float(k["V"])
    return result


def parse_ws_message(raw_str: str, pair: str) -> Optional[Tick | dict]:
    """Route a raw WS message to the correct parser.

    Returns:
        Tick — for aggTrade/bookTicker/markPrice (fast path)
        dict — for kline events (context path)
        None — unrecognised / heartbeat
    """
    try:
        raw = json.loads(raw_str)
    except json.JSONDecodeError:
        return None

    # Heartbeat / ping handling
    if raw.get("e") is None and raw.get("E") is None:
        # Likely a ping or empty keepalive
        return None

    # Try each parser in order of expected frequency
    # aggTrade is our hottest path
    tick = parse_agg_trade(raw, pair)
    if tick:
        return tick

    tick = parse_book_ticker(raw, pair)
    if tick:
        return tick

    tick = parse_mark_price(raw, pair)
    if tick:
        return tick

    if raw.get("e") == "kline":
        return parse_kline(raw, pair)

    return None


# ── FeedMux multiplexer ───────────────────────────────────────────────────────


class FeedMux:
    """Manages one multiplexed Binance Futures WebSocket connection.

    Emits a unified Tick to the on_tick callback for every aggTrade,
    bookTicker, and markPrice event. Kline events are dispatched to
    on_kline for structural context.
    """

    def __init__(
        self,
        on_tick: Callable[[Tick], Awaitable[None]],
        on_kline: Optional[Callable[[str, dict], Awaitable[None]]] = None,
        ws_url: str = "wss://fapi.binance.com/ws",
        proxy_url: Optional[str] = None,
    ):
        self._on_tick = on_tick
        self._on_kline = on_kline
        self._ws_url = ws_url
        self._proxy_url = proxy_url
        self._ws: Optional = None
        self._running = False
        self._got_msg = False
        self._last_tick_ts: float = 0.0
        self._last_book_ts: float = 0.0
        self._last_mark_ts: float = 0.0

    @property
    def last_tick_ts(self) -> float:
        return self._last_tick_ts

    def is_frozen(self, max_age_s: float = MAX_AGE_S) -> bool:
        """Return True if no tick has arrived within max_age_s."""
        if self._last_tick_ts == 0:
            return False  # never started
        return (time.time() - self._last_tick_ts) > max_age_s

    def _build_subscribe_msg(self, streams: list[str]) -> str:
        """Build the JSON subscribe message for multiple streams."""
        params = [f"{s.lower()}@aggTrade" for s in streams]
        params += [f"{s.lower()}@bookTicker" for s in streams]
        params += [f"{s.lower()}@markPrice" for s in streams]
        params += [f"{s.lower()}@kline_1m" for s in streams]
        params += [f"{s.lower()}@kline_5m" for s in streams]
        params += [f"{s.lower()}@kline_15m" for s in streams]
        params += [f"{s.lower()}@kline_1h" for s in streams]
        return json.dumps({
            "method": "SUBSCRIBE",
            "params": params,
            "id": 1,
        })

    async def connect(
        self,
        streams: list[str],
        on_ready: Optional[Callable[[], None]] = None,
    ) -> None:
        """Connect to Binance Futures WS and subscribe to streams.

        On each message, dispatches Tick to on_tick and klines to on_kline.
        Auto-reconnects on disconnect with exponential backoff. Calls on_ready
        (if provided) the moment the initial subscribe succeeds, so the caller
        can mark the live feed as healthy without waiting for connect() to
        return (it never does — it loops on the read socket forever).
        """
        self._running = True
        retry = RECONNECT_BASE_S

        while self._running:
            try:
                if self._proxy_url:
                    log.info("FeedMux connecting via proxy %s", self._proxy_url)
                    await asyncio.sleep(30)
                    continue

                from websockets.client import connect as ws_connect
                async with ws_connect(
                    self._ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    await ws.send(self._build_subscribe_msg(streams))
                    log.info("FeedMux subscribed to %d streams", len(streams))
                    if on_ready:
                        try:
                            on_ready()
                        except Exception:
                            pass
                    # NOTE: retry is reset in _read_loop only after a REAL
                    # message arrives. If the upstream (e.g. Binance 403 via
                    # relay) closes us right after subscribe, backoff keeps
                    # growing instead of storming reconnects every 2s.
                    self._got_msg = False
                    await self._read_loop(ws)
                    if self._got_msg:
                        retry = RECONNECT_BASE_S
                    else:
                        # clean close (code 1000) with zero messages: upstream
                        # rejected us politely. Back off exactly like an error,
                        # otherwise the while-loop reconnects instantly and
                        # storms the relay with hundreds of connects/minute.
                        log.warning(
                            "FeedMux closed with no data, backing off %.1fs", retry)
                        await asyncio.sleep(retry)
                        retry = min(retry * 1.5, RECONNECT_MAX_S)

            except asyncio.CancelledError:
                log.info("FeedMux cancelled")
                self._running = False
                break
            except Exception as e:
                log.warning("FeedMux disconnected (%s), reconnecting in %.1fs", e, retry)
                await asyncio.sleep(retry)
                retry = min(retry * 1.5, RECONNECT_MAX_S)

    async def _read_loop(self, ws) -> None:
        """Read messages from WS until disconnect."""
        async for raw_msg in ws:
            if not self._running:
                break
            self._got_msg = True
            await self._dispatch(raw_msg)

    async def _dispatch(self, raw_msg: str) -> None:
        """Parse and route a single WS message."""
        result = parse_ws_message(raw_msg, "")
        if result is None:
            return

        if isinstance(result, Tick):
            self._last_tick_ts = time.time()
            await self._on_tick(result)
        elif isinstance(result, dict) and self._on_kline:
            # kline: dict with tf, is_final, etc.
            tf = result.get("tf", "")
            await self._on_kline(tf, result)

    async def stop(self) -> None:
        """Gracefully stop the feed."""
        self._running = False
        if self._ws:
            await self._ws.close()
