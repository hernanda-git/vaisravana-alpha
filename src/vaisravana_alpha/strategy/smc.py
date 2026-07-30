"""SMC zone cache — close-computed / tick-evaluated for real-time structure.

On every closed HTF kline (15m/1h/5m), detect and cache:
  - OrderBlocks, FVGs, LiquidityPools
  - Swing points (HH/HL/LH/LL) → BOS/CHoCH

Per-tick cost is just point-in-zone + live breach check against matured swings.
SMC is never recomputed per-tick — only on close.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from vaisravana_alpha.core.models import SMCZone, SMCZoneType

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

PIVOT_MATURE_TICKS = 3   # number of ticks price must stay clear of swing to mature
CONFIRM_MS = 0.25        # micro-confirmation window (seconds)
SWING_LOOKBACK = 5       # bars to left/right for swing detection


# ── Swing/pivot helpers ───────────────────────────────────────────────────────


def _find_pivot_highs(klines: list[dict], lookback: int = SWING_LOOKBACK) -> list[dict]:
    """Find swing highs in a kline series.

    A pivot high = bar whose high is >= all bars in [i-lookback, i+lookback].
    Returns list of {ts, price, idx}.
    """
    pivots = []
    for i in range(lookback, len(klines) - lookback):
        bar = klines[i]
        if all(bar["high"] >= klines[j]["high"]
               for j in range(i - lookback, i + lookback + 1)
               if j != i):
            pivots.append({
                "ts": bar.get("closed_ts", bar.get("ts", 0)),
                "price": bar["high"],
                "idx": i,
            })
    return pivots


def _find_pivot_lows(klines: list[dict], lookback: int = SWING_LOOKBACK) -> list[dict]:
    """Find swing lows.

    A pivot low = bar whose low is <= all bars in [i-lookback, i+lookback].
    """
    pivots = []
    for i in range(lookback, len(klines) - lookback):
        bar = klines[i]
        if all(bar["low"] <= klines[j]["low"]
               for j in range(i - lookback, i + lookback + 1)
               if j != i):
            pivots.append({
                "ts": bar.get("closed_ts", bar.get("ts", 0)),
                "price": bar["low"],
                "idx": i,
            })
    return pivots


def _detect_order_blocks(klines: list[dict]) -> list[dict]:
    """Detect demand/supply order blocks from consecutive bar analysis.

    A demand OB = bearish bar (close<open) whose low is immediately
    reclaimed by the next bullish bar (close>open, high above bear's low).
    A supply OB = bullish bar (close>open) immediately covered by bear.

    Returns list of {type, lo, hi, bias}.
    """
    obs = []
    for i in range(1, len(klines) - 1):
        prev, curr, nxt = klines[i - 1], klines[i], klines[i + 1]

        # Demand OB: bearish bar → next bar bullish and closes above bear's low
        if prev["close"] < prev["open"] and nxt["close"] > nxt["open"]:
            # The OB zone is the bearish bar's body
            ob_lo = min(prev["open"], prev["close"])
            ob_hi = max(prev["open"], prev["close"])
            # Check that next bar reclaimed the low
            if nxt["low"] <= ob_lo and nxt["close"] > ob_lo:
                obs.append({
                    "type": SMCZoneType.ORDER_BLOCK,
                    "lo": ob_lo,
                    "hi": ob_hi,
                    "bias": "bullish",
                })

        # Supply OB: bullish bar → next bar bearish and closes below bull's high
        if prev["close"] > prev["open"] and nxt["close"] < nxt["open"]:
            ob_lo = min(prev["open"], prev["close"])
            ob_hi = max(prev["open"], prev["close"])
            if nxt["high"] >= ob_hi and nxt["close"] < ob_hi:
                obs.append({
                    "type": SMCZoneType.ORDER_BLOCK,
                    "lo": ob_lo,
                    "hi": ob_hi,
                    "bias": "bearish",
                })

    return obs


def _detect_fvgs(klines: list[dict]) -> list[dict]:
    """Detect Fair Value Gaps (FVG).

    An FVG exists when consecutive bars have non-overlapping wicks:
      Bullish FVG: bar i's high < bar i+2's low (gap up)
      Bearish FVG: bar i's low > bar i+2's high (gap down)
    """
    fvgs = []
    for i in range(len(klines) - 2):
        b1, b2, b3 = klines[i], klines[i + 1], klines[i + 2]
        # Bullish FVG: gap up
        if b1["high"] < b3["low"]:
            fvgs.append({
                "type": SMCZoneType.FVG,
                "lo": b1["high"],
                "hi": b3["low"],
                "bias": "bullish",
            })
        # Bearish FVG: gap down
        if b1["low"] > b3["high"]:
            fvgs.append({
                "type": SMCZoneType.FVG,
                "lo": b3["high"],
                "hi": b1["low"],
                "bias": "bearish",
            })
    return fvgs


def _detect_liquidity_pools(swing_highs: list[dict],
                            swing_lows: list[dict]) -> list[dict]:
    """Detect liquidity pools at swing highs/lows.

    A liquidity pool is a concentration of stops beyond the most recent
    swing high (for longs) or swing low (for shorts).
    """
    pools = []
    # Liquidity above recent swing high (longs' stops)
    for sh in swing_highs[-3:]:  # last 3 swing highs
        pools.append({
            "type": SMCZoneType.LIQUIDITY_POOL,
            "lo": sh["price"] - 0.01,  # tiny buffer
            "hi": sh["price"] + 0.01,
            "bias": "bearish",  # price reaching here = liquidity grab before drop
        })
    # Liquidity below recent swing low (shorts' stops)
    for sl in swing_lows[-3:]:
        pools.append({
            "type": SMCZoneType.LIQUIDITY_POOL,
            "lo": sl["price"] - 0.01,
            "hi": sl["price"] + 0.01,
            "bias": "bullish",  # sweep here = grab before rally
        })
    return pools


def _classify_swing(highs: list[dict], lows: list[dict]) -> list[dict]:
    """Classify swing structure: HH, HL, LH, LL → BOS/CHoCH.

    Returns list of {type, price, bias, swing_type, idx} for each swing.
    """
    result = []

    # Merge highs and lows chronologically by idx
    swing_points = []
    for h in highs:
        swing_points.append({"idx": h["idx"], "price": h["price"],
                             "kind": "high", "ts": h["ts"]})
    for l in lows:
        swing_points.append({"idx": l["idx"], "price": l["price"],
                             "kind": "low", "ts": l["ts"]})
    swing_points.sort(key=lambda x: x["idx"])

    # Classify consecutive swings
    for i in range(1, len(swing_points)):
        prev = swing_points[i - 1]
        curr = swing_points[i]

        # Same kind → compare direction
        if curr["kind"] == "high" and prev["kind"] == "high":
            if curr["price"] > prev["price"]:
                # HH (higher high) = BOS in uptrend
                result.append({
                    "type": SMCZoneType.BOS,
                    "price": curr["price"],
                    "bias": "bullish",
                    "swing_type": "HH",
                    "ts": curr["ts"],
                })
            elif curr["price"] < prev["price"]:
                # LH (lower high) = CHoCH (change of character)
                result.append({
                    "type": SMCZoneType.CHOCH,
                    "price": curr["price"],
                    "bias": "bearish",  # lower high in uptrend → bearish CHoCH
                    "swing_type": "LH",
                    "ts": curr["ts"],
                })

        elif curr["kind"] == "low" and prev["kind"] == "low":
            if curr["price"] > prev["price"]:
                # HL (higher low) = BOS in uptrend
                result.append({
                    "type": SMCZoneType.BOS,
                    "price": curr["price"],
                    "bias": "bullish",
                    "swing_type": "HL",
                    "ts": curr["ts"],
                })
            elif curr["price"] < prev["price"]:
                # LL (lower low) = CHoCH in downtrend
                result.append({
                    "type": SMCZoneType.CHOCH,
                    "price": curr["price"],
                    "bias": "bearish",
                    "swing_type": "LL",
                    "ts": curr["ts"],
                })

    return result


# ── SMCZoneCache ──────────────────────────────────────────────────────────────


class MaturityTracker:
    """Tracks provisional→matured progression of a zone.

    Each zone starts as `provisional` (matured=False).
    Every tick where price stays on the correct side of the zone,
    we increment a counter. After PIVOT_MATURE_TICKS ticks, matured=True.
    """

    def __init__(self, zone_id: str, ref_price: float, bias: str):
        self.zone_id = zone_id
        self.ref_price = ref_price
        self.bias = bias
        self.ticks_clear = 0
        self.matured = False

    def tick(self, price: float) -> bool:
        """Increment maturity counter if price is on the correct side.

        For bullish zones (OB/demand): price must stay above the zone.
        For bearish zones (supply): price must stay below.
        For BOS/CHoCH: price must stay on the extension side.

        Returns True when newly matured this tick.
        """
        if self.matured:
            return False

        if self.bias == "bullish":
            clear = price > self.ref_price
        elif self.bias == "bearish":
            clear = price < self.ref_price
        else:
            clear = price != self.ref_price

        if clear:
            self.ticks_clear += 1
            if self.ticks_clear >= PIVOT_MATURE_TICKS:
                self.matured = True
                return True
        else:
            # Price re-entered zone → reset counter (whipsaw protection)
            self.ticks_clear = 0

        return False


class BreakTracker:
    """Two-tier BOS/CHoCH confirmation tracker.

    When price breaches a matured swing, a provisional break is registered.
    If the breach persists for CONFIRM_MS seconds → confirmed.
    If price reverts within CONFIRM_MS → whipsaw, no confirm.
    """

    def __init__(self):
        self._provisional: dict[str, dict] = {}  # zone_id → {since, price}

    def evaluate(self, zone_id: str, price: float, zone_price: float,
                 bias: str, ts: float) -> tuple[bool, bool]:
        """Evaluate break status.

        Returns (provisional, confirmed).
        provisional: price is currently on the break side.
        confirmed: provisional has persisted for >= CONFIRM_MS.
        """
        # Determine if currently in break territory
        if bias == "bullish":
            in_break = price > zone_price  # bullish BOS
        else:
            in_break = price < zone_price  # bearish CHoCH

        if in_break:
            if zone_id in self._provisional:
                since = self._provisional[zone_id]["since"]
                duration = ts - since
                if duration >= CONFIRM_MS:
                    del self._provisional[zone_id]
                    return (True, True)  # confirmed
                return (True, False)  # still provisional
            else:
                self._provisional[zone_id] = {"since": ts, "price": price}
                return (True, False)  # just became provisional
        else:
            # No longer in break territory → clear provisional
            self._provisional.pop(zone_id, None)
            return (False, False)


class SMCZoneCache:
    """Caches SMC zones computed on closed HTF klines.

    Hot path reads: point_in_zone (sub-ms), evaluate_break.
    Cold path: refresh() only on closed HTF klines (~1 per minute per TF).
    """

    def __init__(self):
        self._zones: dict[str, list[SMCZone]] = {}  # {pair: [Zone]}
        self._maturity: dict[str, MaturityTracker] = {}
        self._break_tracker = BreakTracker()
        self._last_refresh: dict[str, float] = {}  # {"pair:tf": ts}

    def refresh(self, pair: str, tf: str, closed_klines: list[dict]) -> int:
        """Refresh zones from closed HTF klines.

        Computes: OB, FVG, liquidity pools, swing structure (BOS/CHoCH).
        Returns number of zones cached.

        This is the ONLY place SMC is computed — never runs per tick.
        """
        if not closed_klines or len(closed_klines) < SWING_LOOKBACK * 2 + 1:
            return 0

        zones: list[SMCZone] = []
        ts = time.time()

        # 1. Order blocks
        obs = _detect_order_blocks(closed_klines)
        for i, ob in enumerate(obs):
            zones.append(SMCZone(
                id=f"{pair}-{tf}-ob-{i}",
                pair=pair, tf=tf,
                zone_type=ob["type"],
                lo=ob["lo"], hi=ob["hi"],
                bias=ob["bias"],
                matured=False,
                ts=ts,
            ))

        # 2. FVGs
        fvg_list = _detect_fvgs(closed_klines)
        for i, fvg in enumerate(fvg_list):
            zones.append(SMCZone(
                id=f"{pair}-{tf}-fvg-{i}",
                pair=pair, tf=tf,
                zone_type=fvg["type"],
                lo=fvg["lo"], hi=fvg["hi"],
                bias=fvg["bias"],
                matured=False,
                ts=ts,
            ))

        # 3. Swing points → BOS/CHoCH
        highs = _find_pivot_highs(closed_klines)
        lows = _find_pivot_lows(closed_klines)
        swings = _classify_swing(highs, lows)

        for i, sw in enumerate(swings):
            zone_type = sw["type"]
            # BOS/CHoCH zones: a thin band around the swing price
            zone_id = f"{pair}-{tf}-{zone_type.value}-{i}"
            zones.append(SMCZone(
                id=zone_id,
                pair=pair, tf=tf,
                zone_type=zone_type,
                lo=sw["price"] - 0.01,
                hi=sw["price"] + 0.01,
                bias=sw["bias"],
                matured=False,
                ts=sw.get("ts", ts),
            ))

        # 4. Liquidity pools
        pools = _detect_liquidity_pools(highs, lows)
        for i, p in enumerate(pools):
            zones.append(SMCZone(
                id=f"{pair}-{tf}-liq-{i}",
                pair=pair, tf=tf,
                zone_type=p["type"],
                lo=p["lo"], hi=p["hi"],
                bias=p["bias"],
                matured=False,
                ts=ts,
            ))

        # Store & initialise maturity trackers
        self._zones[pair] = zones
        self._last_refresh[f"{pair}:{tf}"] = ts

        for z in zones:
            if z.zone_type in (SMCZoneType.BOS, SMCZoneType.CHOCH):
                self._maturity[z.id] = MaturityTracker(
                    zone_id=z.id,
                    ref_price=(z.lo + z.hi) / 2,
                    bias=z.bias,
                )

        log.debug("SMCZoneCache refreshed %s %s: %d zones", pair, tf, len(zones))
        return len(zones)

    def point_in_zone(self, pair: str, price: float) -> Optional[SMCZone]:
        """Check if price is inside any cached zone for the pair.

        Sub-ms per-tick. Does NOT trigger SMC re-computation.
        """
        zones = self._zones.get(pair, [])
        for z in zones:
            if z.lo <= price <= z.hi:
                return z
        return None

    def get_zones(self, pair: str) -> list[SMCZone]:
        """Return all cached zones for a pair."""
        return self._zones.get(pair, [])

    def get_matured_bos_choch(self, pair: str, bias: str) -> list[SMCZone]:
        """Return matured BOS/CHoCH zones with matching bias."""
        result = []
        for z in self._zones.get(pair, []):
            if z.zone_type in (SMCZoneType.BOS, SMCZoneType.CHOCH):
                if z.bias == bias:
                    # Check maturity
                    mt = self._maturity.get(z.id)
                    if mt and mt.matured:
                        result.append(z)
        return result

    def tick_maturity(self, pair: str, price: float) -> list[str]:
        """Advance maturity trackers for a pair on each tick.

        Returns list of zone IDs that newly matured this tick.
        """
        newly = []
        for z in self._zones.get(pair, []):
            mt = self._maturity.get(z.id)
            if mt and mt.tick(price):
                newly.append(z.id)
                # Update the zone's matured flag
                z.matured = True
        return newly

    def evaluate_break(self, pair: str, price: float, ts: float,
                       wave_side: str) -> tuple[bool, bool, Optional[str]]:
        """Two-tier BOS/CHoCH evaluation against an active wave.

        Args:
            pair: trading pair
            price: current tick price
            ts: current timestamp
            wave_side: "BUY" or "SELL"

        Returns:
            (provisional, confirmed, zone_type)
            provisional: breach detected but not yet confirmed
            confirmed: breach persisted for CONFIRM_MS
            zone_type: "bos" or "choch" or None
        """
        # Determine which zones can break the wave
        # BUY wave → bearish CHoCH breaks it (higher-high structure fails)
        # SELL wave → bullish BOS breaks it (lower-low structure fails)
        if wave_side == "BUY":
            check_bias = "bearish"
            check_types = {SMCZoneType.CHOCH}
        else:
            check_bias = "bullish"
            check_types = {SMCZoneType.BOS}

        for z in self._zones.get(pair, []):
            if z.zone_type not in check_types:
                continue
            if z.bias != check_bias:
                continue
            mt = self._maturity.get(z.id)
            if not mt or not mt.matured:
                continue  # only matured swings can break

            zone_price = (z.lo + z.hi) / 2
            prov, conf = self._break_tracker.evaluate(
                z.id, price, zone_price, check_bias, ts,
            )
            if prov or conf:
                return (prov, conf, z.zone_type.value)

        return (False, False, None)
