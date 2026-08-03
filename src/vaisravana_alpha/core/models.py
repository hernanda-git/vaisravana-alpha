"""Wave engine data models — the atomic trading entity and supporting types."""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Optional


# ── Enums ────────────────────────────────────────────────────────────────────


class WaveState(str, enum.Enum):
    """Wave lifecycle — tick-driven, no MAXHOLD."""
    SCANNING = "SCANNING"
    ENTERED = "ENTERED"
    SURFING = "SURFING"
    TRIM = "TRIM"
    ADD = "ADD"
    WAVE_BREAK = "WAVE_BREAK"
    KILL = "KILL"
    COOLDOWN = "COOLDOWN"


class SMCZoneType(str, enum.Enum):
    ORDER_BLOCK = "order_block"
    FVG = "fvg"
    LIQUIDITY_POOL = "liquidity_pool"
    BOS = "bos"
    CHOCH = "choch"


# ── Live tick ────────────────────────────────────────────────────────────────


@dataclass
class Tick:
    """A unified live tick emitted by FeedMux on every WS message.

    The feed layer coalesces aggTrade, bookTicker, and markPrice into one
    Tick object so the hot path sees every price-changing event immediately.
    """
    pair: str
    price: float                    # latest trade price (aggTrade) or mark
    qty: float                      # aggTrade quantity (0 for non-trade ticks)
    side: str                       # "BUY" | "SELL" | "" (mark/book ticks)
    bid: float                      # best bid (0 if not available)
    ask: float                      # best ask (0 if not available)
    mark: float                     # mark price (latest if available, else price)
    ts: float                       # event timestamp (seconds, monotonic-like)
    source: str = "aggTrade"        # which WS stream produced this tick
    bid_qty: float = 0.0            # best-bid size (iter-D: real book pressure)
    ask_qty: float = 0.0            # best-ask size (iter-D: real book pressure)


# ── Wave entity ──────────────────────────────────────────────────────────────


@dataclass
class Wave:
    """The atomic trading entity — opened on structure, dies on structure break.

    Everything is re-derived on every tick: bias, confidence, structure,
    trailing SL, live R. No per-candle wait, no MAXHOLD.
    """
    wave_id: str                    # f"{pair}-{tf}-{side}-{ts}"
    pair: str
    tf: str                         # decision timeframe
    side: str                       # BUY / SELL
    state: WaveState = WaveState.SCANNING

    entry_price: float = 0.0
    anchor: float = 0.0             # structure invalidation level (NOT ATR)
    sl_price: float = 0.0           # == anchor at open
    tp_price: Optional[float] = None
    size: float = 0.0
    notional: float = 0.0           # USD notional at open (paper wallet sizing)
    margin: float = 0.0             # margin used = notional / leverage
    leverage: int = 1
    open_ts: float = 0.0            # epoch seconds at open (for MAX_WAVE_AGE timeout)

    structure_score: float = 0.0    # 0..1, re-derived every tick
    mtf_confluence: bool = False

    live_r: float = 0.0
    distance_to_invalidation: float = 0.0  # in R units
    opened_ts: float = 0.0
    closed_ts: float = 0.0
    last_tick_ts: float = 0.0
    peak_r: float = 0.0
    partials_taken: int = 0

    bias: str = "neutral"           # BiasReading.direction at entry
    confidence: float = 0.0         # 0..1 at entry

    close_reason: Optional[str] = None  # WAVE_BREAK | KILL | PARTIAL_CHAIN
    _open_fee: float = 0.0          # fee charged at open (for close-card round-trip tally)
    open_components: dict = field(default_factory=dict)  # bias component breakdown at entry
    last_components: dict = field(default_factory=dict)  # freshest bias component breakdown
    strategy_mode: str = "single_leg_counter"
    trade_group_id: str = ""
    leg_id: str = ""
    expected_gross: float = 0.0
    expected_cost: float = 0.0
    expected_net: float = 0.0
    entry_rank: int = 0
    cvd_divergence_present: bool = False


# ── Bias / Confidence ────────────────────────────────────────────────────────


@dataclass
class BiasReading:
    """Per-tick conviction from MTF EMA cross + flow + book + risk + breadth."""
    direction: str           # bullish | bearish | neutral
    strength: float          # 0..1
    components: dict         # traceable breakdown
    ts: float


@dataclass
class ConfidenceReading:
    """Live 0..1 conviction from bias strength + structure + confluence + vol."""
    value: float
    components: dict


# ── SMC zones ────────────────────────────────────────────────────────────────


@dataclass
class SMCZone:
    """A cached SMC zone — static once computed, read on every tick."""
    id: str
    pair: str
    tf: str
    zone_type: SMCZoneType
    lo: float
    hi: float
    bias: str               # bullish | bearish
    matured: bool = False
    ts: float = 0.0


# ── Candidates ───────────────────────────────────────────────────────────────


@dataclass
class Candidate:
    """A validated wave candidate emitted by the scanner."""
    pair: str
    tf: str
    side: str
    strength: float
    ts: float
    reason: str = ""
    regime_label: str = "range"


# ── Tick context ─────────────────────────────────────────────────────────────


@dataclass
class TickContext:
    """Per-pair live context updated on every tick — the engine's working memory.

    Holds latest price, EMA state, book imbalance, kline snapshots,
    and HTF structure signals needed by BiasEngine, scanner, and manager.
    """
    pair: str
    price: float = 0.0
    mark: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: float = 0.0            # best-bid size (iter-D)
    ask_qty: float = 0.0            # best-ask size (iter-D)
    book_imbalance: float = 0.0

    # EMA tick-recursive state (spine)
    ema_15m: float = 0.0
    ema_1h: float = 0.0
    ema_slope: float = 0.0          # normalised cross-slope

    # kline forming / closed snapshots
    forming_candle: dict = field(default_factory=dict)
    klines: dict = field(default_factory=dict)  # {tf: [candle,...]}

    # flow (rolling aggTrade window)
    flow_delta: float = 0.0         # signed buy-sell volume
    flow_volume: float = 0.0

    # structure
    structure_score: float = 0.0
    mtf_confluence: bool = False

    # regime
    risk_regime: float = 0.0        # -1..+1
    alt_breadth: float = 0.0        # -1..+1

    # timestamps
    last_tick_ts: float = 0.0
    signal_age_s: float = 0.0

    @property
    def mid_price(self) -> float:
        return (self.bid + self.ask) / 2 if self.bid and self.ask else self.price
