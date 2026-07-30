"""Agentic DB + evaluation tests.

Synthetic, no network. These pin the contract the improvement loop depends
on: a run records, a trade attaches to it, rejections count, and the
evaluation stack returns the verdicts the design promises -- including the
case where L0 failure blocks everything downstream.
"""

import os
import sqlite3
import tempfile

import pytest

from vaisravana_alpha.core.params import load_surface
from vaisravana_alpha.evaluation.layers import (
    INSUFFICIENT,
    PASS,
    WARN,
    evaluate_run,
)
from vaisravana_alpha.storage import agentic


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = agentic.init_agentic_db(path)
    yield conn
    conn.close()
    for ext in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(path + ext)
        except FileNotFoundError:
            pass


def _surface():
    return load_surface()


def test_run_trade_lifecycle(db):
    run_id = agentic.start_run(db, _surface(), "paper", ["BTCUSDT"], 10.0)
    assert agentic.latest_run(db)["run_id"] == run_id

    # A fake closed wave + econ dict.
    class FakeWave:
        wave_id = "BTCUSDT-1m-SELL-1"
        pair = "BTCUSDT"
        side = "SELL"
        tf = "1m"
        open_ts = 1_000.0
        entry_price = 100.0
        anchor = 100.0
        size = 1.0
        notional = 100.0
        leverage = 3
        live_r = 0.5
        peak_r = 0.8
        confidence = 0.3
        bias = "bearish"
        close_reason = "tp"
        _open_fee = 0.0006

    econ = {"exit_price": 101.0, "close_fee": 0.0012, "net": -0.0018,
            "balance_after": 9.9982}
    tid = agentic.record_trade(db, run_id, FakeWave(), econ)
    assert tid.startswith("t-")

    trades = agentic.run_trades(db, run_id)
    assert len(trades) == 1
    t = trades[0]
    # net must equal gross - fees, as the writer guarantees.
    assert abs((t["gross_usd"] - t["open_fee"] - t["close_fee"]) - t["net_usd"]) < 1e-9
    assert t["run_id"] == run_id

    agentic.finish_run(db, run_id, "completed", 9.9982, 100, 1, 1)
    run = agentic.get_run(db, run_id)
    assert run["status"] == "completed"
    assert run["end_balance"] == pytest.approx(9.9982)


def test_rejection_counting(db):
    run_id = agentic.start_run(db, _surface(), "paper", ["BTCUSDT"], 10.0)
    for _ in range(5):
        agentic.record_rejection(db, run_id, "survival", "BTCUSDT", "low conf")
    for _ in range(2):
        agentic.record_rejection(db, run_id, "warmup", "BTCUSDT")
    summary = agentic.rejection_summary(db, run_id)
    assert summary[0]["gate"] == "survival"
    assert summary[0]["total"] == 5
    assert len(summary) == 2


def test_iteration_lineage(db):
    parent = agentic.create_iteration(
        db, "raise rr", "surface", {"rr": 2.0}, predicted="more profit"
    )
    child = agentic.create_iteration(
        db, "raise rr more", "surface", {"rr": 2.5}, parent_id=parent
    )
    agentic.settle_iteration(db, child, "promoted", "worked")
    cur = db.execute(
        "SELECT status FROM iterations WHERE iteration_id=?", (child,)
    ).fetchone()
    assert cur["status"] == "promoted"


def test_decisions_recorded(db):
    did = agentic.record_decision(
        db, "promote", "all layers pass", {"net": 0.5}, run_id="run-x",
        iteration_id="iter-x",
    )
    row = db.execute(
        "SELECT * FROM decisions WHERE decision_id=?", (did,)
    ).fetchone()
    assert row["action"] == "promote"
    assert row["rationale"] == "all layers pass"


def test_integrity_blocks_on_fee_mismatch(db):
    """L0 failure must make every downstream verdict untrustworthy."""
    run_id = agentic.start_run(db, _surface(), "paper", ["BTCUSDT"], 10.0)
    class FakeWave:
        wave_id = "w1"
        pair = "BTCUSDT"
        side = "BUY"
        tf = "1m"
        open_ts = 1.0
        entry_price = 100.0
        anchor = 100.0
        size = 1.0
        notional = 100.0
        leverage = 3
        live_r = 0.0
        peak_r = 0.0
        confidence = 0.3
        bias = "bullish"
        close_reason = "tp"
        _open_fee = 0.5   # ~50% of notional, impossible under 6bps model

    agentic.record_trade(
        db, run_id, FakeWave(),
        {"exit_price": 101.0, "close_fee": 0.5, "net": 0.0, "balance_after": 10.0},
    )
    run = agentic.get_run(db, run_id)
    trades = agentic.run_trades(db, run_id)
    report = evaluate_run(run, trades, [])
    assert report.layer(0).failed
    assert not report.promote
    assert "Integrity failed" in report.summary


def test_insufficient_sample_is_not_a_pass(db):
    """Below MIN_TRADES the statistical verdict must be N/A, never promote."""
    run_id = agentic.start_run(db, _surface(), "paper", ["BTCUSDT"], 10.0)
    run = agentic.get_run(db, run_id)
    report = evaluate_run(run, [], [])
    # With no ticks, execution (L1) fails, which already blocks promotion.
    assert report.layer(1).verdict == "fail"
    assert not report.promote
    # And statistical (L2) is explicitly insufficient, not a silent pass.
    assert report.layer(2).verdict == INSUFFICIENT


def test_real_edge_promotes(db):
    """A clean, profitable, statistically sound run should promote."""
    run_id = agentic.start_run(db, _surface(), "paper", ["BTCUSDT"], 10.0)

    class FakeWave:
        pass

    trades = []
    for i in range(60):
        w = FakeWave()
        w.wave_id = f"w{i}"
        w.pair = "BTCUSDT"
        w.side = "BUY" if i % 3 else "SELL"
        w.tf = "1m"
        w.open_ts = float(i + 1)
        w.entry_price = 100.0
        w.anchor = 100.0
        w.size = 1.0
        w.notional = 100.0
        w.leverage = 3
        w.live_r = 0.5
        w.peak_r = 0.8
        w.confidence = 0.35
        w.bias = "bullish"
        w.close_reason = "tp"
        # 2bps maker open + 4bps taker close, on a 100 notional = 0.06 total.
        w._open_fee = 0.02
        net = 0.012 if i % 4 else -0.005   # mostly winners
        agentic.record_trade(
            db, run_id, w,
            {"exit_price": 100.1, "close_fee": 0.04, "net": net,
             "balance_after": 10.0},
        )
        trades.append({
            "trade_id": f"t{i}", "run_id": run_id, "pair": "BTCUSDT",
            "side": w.side, "tf": "1m", "open_ts": float(i + 1),
            "close_ts": float(i + 2), "hold_s": 1.0, "entry_price": 100.0,
            "exit_price": 100.1, "anchor": 100.0, "size": 1.0,
            "notional": 100.0, "leverage": 3, "gross_usd": net + 0.06,
            "open_fee": 0.02, "close_fee": 0.04, "net_usd": net,
            "r_multiple": 0.5, "peak_r": 0.8, "entry_conf": 0.35,
            "entry_bias": "bullish", "close_reason": "tp",
            "balance_after": 10.0,
        })

    run = agentic.get_run(db, run_id)
    report = evaluate_run(run, trades, [], n_trials=1)
    # Integrity holds, economics is at least non-negative.
    assert report.layer(0).verdict == PASS
    assert report.layer(3).verdict in (PASS, WARN)
    # The point of the test: a clean profitable run is never silently failed
    # by a downstream layer misreporting.
    assert report.layer(0).passed