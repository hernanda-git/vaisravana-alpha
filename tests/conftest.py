"""Shared fixtures and lightweight fakes.

Deliberately no network, no sleeps, and no real Telegram. Every test here
should run in milliseconds so the suite stays worth running on every edit.
"""

from __future__ import annotations

import sqlite3

import pytest

from vaisravana_alpha.core.models import BiasReading, Tick, TickContext
from vaisravana_alpha.core.settings import Settings
from vaisravana_alpha.execution.wallet import PaperWallet
from vaisravana_alpha.storage.db import init_wave_db


class FakeNotifier:
    """Captures cards instead of sending them."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.enabled = True

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        self.sent.append(text)
        return True

    def register_commands(self, commands) -> bool:
        return True

    def close(self) -> None:
        pass


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory database with the real schema applied."""
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    init_wave_db(connection)
    return connection


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings pointed at a temp dir so no test touches /data."""
    return Settings(
        mode="paper",
        data_dir=str(tmp_path),
        pairs=["BTCUSDT", "ETHUSDT"],
        paper_balance=10.0,
        warmup_s=0.0,
        telegram_token="",
    )


@pytest.fixture
def wallet(settings) -> PaperWallet:
    return PaperWallet.from_settings(settings)


def make_klines(n: int = 40, start: float = 100.0, step: float = 1.0) -> list[dict]:
    """A deterministic ascending candle series."""
    out = []
    for i in range(n):
        close = start + i * step
        out.append({
            "ts": 1_700_000_000 + i * 900,
            "open": close - step * 0.5,
            "high": close + step * 0.5,
            "low": close - step * 0.8,
            "close": close,
            "volume": 1000.0,
            "taker_buy_volume": 600.0,
            "is_final": True,
            "s": "BTCUSDT",
            "tf": "15m",
        })
    return out


@pytest.fixture
def ctx() -> TickContext:
    """A context seeded with enough history for the indicators."""
    context = TickContext(pair="BTCUSDT")
    context.klines = {"1m": [], "5m": [], "15m": make_klines(), "1h": make_klines(20)}
    context.price = 139.0
    context.bid = 138.9
    context.ask = 139.1
    return context


@pytest.fixture
def tick() -> Tick:
    return Tick(
        pair="BTCUSDT", price=139.0, qty=1.0, side="BUY",
        bid=138.9, ask=139.1, mark=139.0, ts=1_700_036_000.0,
        source="test", bid_qty=500.0, ask_qty=400.0,
    )


@pytest.fixture
def bias() -> BiasReading:
    return BiasReading(
        direction="bullish", strength=0.8, components={}, ts=1_700_036_000.0,
    )
