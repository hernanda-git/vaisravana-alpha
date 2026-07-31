-- Binance Futures Real-Time Data Pipeline
-- SQLite schema (compatible with migration to TimescaleDB hypertables later)

-- Core metrics for every futures pair at each snapshot
CREATE TABLE IF NOT EXISTS pair_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,  -- ISO-8601 timestamp (UTC)
    pair        TEXT    NOT NULL,  -- e.g. "BTCUSDT"
    price       REAL,              -- last price
    price_change_24h REAL,         -- 24h price change percent
    quote_volume    REAL,          -- 24h quote asset volume (USDT)
    bid_qty     REAL,              -- best bid quantity
    ask_qty     REAL,              -- best ask quantity
    bid_price   REAL,              -- best bid price
    ask_price   REAL,              -- best ask price
    mark_price  REAL,              -- mark price
    volume_delta    REAL,          -- bid_qty - ask_qty (snapshot order-book imbalance)
    cvd_trend   REAL,              -- cumulative volume delta trend (12-period EMA of volume_delta)
    strength_score  REAL,          -- composite strength score [0-100]
    btc_relative    REAL           -- price change relative to BTC (pair_pct - btc_pct)
);

-- High-level universe summary per snapshot
CREATE TABLE IF NOT EXISTS universe_snapshot (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  TEXT    NOT NULL,  -- ISO-8601 timestamp (UTC)
    pair_count          INTEGER NOT NULL,  -- total active pairs
    strongest_pairs_json TEXT,             -- JSON array of top-5 strongest pairs
    weakest_pairs_json  TEXT               -- JSON array of bottom-5 weakest pairs
);

-- BTC benchmark row per snapshot
CREATE TABLE IF NOT EXISTS btc_benchmark (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,  -- ISO-8601 timestamp (UTC)
    price           REAL,              -- BTC last price
    price_change_24h REAL,             -- BTC 24h change percent
    quote_volume    REAL               -- BTC 24h quote volume
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_pair_metrics_ts      ON pair_metrics(ts);
CREATE INDEX IF NOT EXISTS idx_pair_metrics_pair_ts  ON pair_metrics(pair, ts);
CREATE INDEX IF NOT EXISTS idx_universe_snapshot_ts  ON universe_snapshot(ts);
CREATE INDEX IF NOT EXISTS idx_btc_benchmark_ts      ON btc_benchmark(ts);
