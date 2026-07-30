# Vaiśravaṇa Alpha - Architecture & Operations

Clean-structure redesign of `vaisravana-wave` (same strategy, rebuilt as a
layered package). Private repo `hernanda-git/vaisravana-alpha`, branch `main`.
Local working copy on sera: `/root/vaisravana-alpha`.

Last updated: 2026-07-30 (exit engine merged into the single alpha container;
the separate `vaisravana-alpha-exit` container was retired).

---

## 1. The two-bot question (alpha vs alpha-exit)

There was never a separate *engine*. `vaisravana-alpha` and `vaisravana-alpha-exit`
shared the exact same Docker image (`bots-vaisravana-alpha:latest`). The only
difference was the environment:

| | alpha (multi-pair) | alpha-exit (retired) |
|---|---|---|
| pairs | 15 (full universe) | 1000BONKUSDT only |
| EXIT_ENGINE | false (legacy) | true |
| EXIT_PAIR | (empty) | 1000BONKUSDT |
| data volume | ./vaisravana-alpha/data | ./vaisravana-alpha/exit-data |
| purpose | broad portfolio trader | single-pair exit-engine stress test |

The exit engine was already fully wired into `engine/runtime.py` (since
2026-07-30). The second container was a *temporary experiment* to validate the
real-time exit logic on one high-frequency altcoin before scaling.

**Merge (2026-07-30):** the exit engine is now ENABLED in the main alpha for
ALL pairs. The `vaisravana-alpha-exit` service was removed from
`/opt/bots/docker-compose.yml`. One container, one wallet, one codebase, the
regime-adaptive exit logic runs across the whole universe.

Why merge rather than keep separate:
- the exit engine already guarded itself (single `exit_pair` filter, own cadence)
- a separate container doubled the maintenance surface and the WS/rest load
- val's standing preference is "separate repos per bot", which this satisfies
  at the repo level (alpha is its own repo); two containers of the same image
  was an experiment, not the architecture

---

## 2. Layered package layout

Strict downward imports - never sideways. Each layer is independently testable.

```
src/vaisravana_alpha/
  core/        models, params (pydantic surface), settings, symbols - imports nothing local
  strategy/    bias, structure, smc, gate, scanner, survival, indicators
  execution/   manager, wallet, broker (paper/live boundary), risk
  marketdata/  feed (WS mux), rest (poller)
  engine/      runtime (orchestrator - ONLY layer that wires), context, exit_engine
  notify/      telegram (transport), cards (pure formatting)
  storage/     db (trading sqlite), agentic (runs/iterations/trades/rejections/evals/decisions)
  evaluation/  metrics, deflated_sharpe, cscv, archive, layers (6-layer verdict), cli (alpha-eval)
  tests/       94 tests, no network, no sleeps (test_exit_engine.py + test_exit_integration.py)
  docs/adr/      0001 layered architecture, 0002 paper/live boundary, 0003 agentic DB, 0004 multi-layer eval
  docs/knowledge-base/  evaluation-kb.md, realtime-exit-kb.md
```

Entrypoint: `python -m vaisravana_alpha` (130-line `__main__.py` that only
builds the object graph). Console scripts: `vaisravana-alpha`, `alpha-eval`.

---

## 3. Key design facts

- **Settings**: one typed registry in `core/settings.py`. Reads `ALPHA_*` first,
  falls back to legacy `VAISRAVANA_*` - an existing wave .env keeps working.
  No module may call os.getenv for anything declared there.
- **Paper/live boundary is structural** (ADR 0002): `ModeGuard(mode="paper")
  .broker_for(live_adapter)` RAISES; live mode requires adapter + non-empty
  human-approved (pair, tf, side) set. `MODE=live` refuses to boot - intentional.
- **ParameterSurface** (core/params.py) is pydantic-validated: bias weights sum
  to 1.0, R:R >= 2:1 owner floor, throttle floor >= 1, ev_gate_k >= 1. Invalid
  surface -> falls back to defaults, never trades it.
- **Wallet**: `PaperWallet.from_settings()`; $10 start, runs to $0; maker 0.02%
  open + taker 0.04% close; sizing = risk_pct of LIVE balance; atomic
  temp-file+rename persistence.
- `/stop` writes `alpha_stop.flag` under ALPHA_DATA; engine refuses to trade at
  boot while it exists; `/resume` clears it. SIGTERM also writes it.

---

## 4. Real-time exit engine (merged 2026-07-30)

`src/vaisravana_alpha/engine/exit_engine.py` - dynamic, fee-aware, regime-adaptive
exit logic. Runs EVERY tick for every open wave now (all pairs).

Pipeline (per tick, 100-500ms):
```
Tick → RegimeDetector → FactorPipeline → ExitConfidenceModel → FeeAwareDecision → Action
```

- **RegimeDetector**: MOMENTUM / MEAN_REVERT / CHOP from EMA slope, ATR
  percentile, CVD (cumulative volume delta of buy vs sell volume).
- **FactorPipeline**: 5 normalized 0..1 factor categories:
  - structural (1s-10s): EMA slope reversal, VWAP deviation, structure break reject
  - momentum (10s-60s): RSI(3) divergence, ROC(5) accel, volume profile shift
  - orderflow (1s-10s): spread, imbalance, CVD divergence, liquidity sweep
  - volatility (1s-10s): ATR percentile, vol shift, Keltner position
  - liquidity (1s-10s): order block proximity, sweep, depth proxy
- **ExitConfidenceModel**: regime-dependent weights (MOMENTUM weights
  structural+momentum; MEAN_REVERT weights structural+volatility; CHOP weights
  orderflow+liquidity).
- **Fee-aware salvage**: `salvage = (exit_price - entry_price) * size - close_fee`.
  If salvage < 0, require higher confidence to exit.
- **Action**: HOLD / ADD / CLOSE_50 (trail rest) / CLOSE_100 / FLIP.

Wiring (engine/runtime.py):
- `__init__` builds one `ExitEngine` iff `settings.exit_enabled`
- `on_tick` calls `_evaluate_exit` after `_manage_open_waves`
- `_evaluate_exit` skips non-`exit_pair` (empty = all), feeds the engine every
  tick to keep RegimeDetector current, acts on cadence `exit_tick_interval_ms`
- `CLOSE_50` -> `manager.close(..., fraction=0.5)`; `CLOSE_100` -> fraction=1.0
- every signal recorded via `agentic.record_exit_signal` (audit trail)

The static TP/SL + kill switch still also close. The exit engine OVERRIDES the
close decision when its confidence crosses the regime threshold.

---

## 5. Agentic DB + multi-layer evaluation

Two databases, by design:
- `vaisravana-alpha.db` - trading path (hot, lean)
- `alpha-agentic.db` - agentic workflow (WAL + busy_timeout 30000). Separate so
  the loop's analytical queries never contend with the tick path. Tables: runs,
  iterations, trades, gate_rejections, evaluations, decisions, schema_meta,
  exit_signals.

`runs` = one row per engine start, full surface JSON + hash + git sha. Every
trade references `run_id` -> verdicts are always "this run vs that run".

Evaluator: `evaluation/layers.py`, six layers, L0 short-circuits:
  L0 integrity   fee-consistency, net==gross-fees, ts-order, dup-ids
  L1 execution   ticks>0, no gate owning ~all rejections, opens>0
  L2 statistical deflated Sharpe (MIN_TRADES=20)
  L3 economic    net>0, expectancy>0, fee_ratio reported
  L4 robustness   second-half expectancy, concentration
  L5 risk         max_drawdown veto (>50% = fail)

CLI: `alpha-eval report [DB] [--run RUN|--last N] [--trials N]` and
`alpha-eval promote [DB] [--dry-run]`.

---

## 6. Deployment & ops on sera

Compose service `vaisravana-alpha` (container `bots-vaisravana-alpha`) lives in
`/opt/bots/docker-compose.yml` (the `bots` project). It runs the locally built
image `bots-vaisravana-alpha:latest`.

```bash
# after editing /root/vaisravana-alpha
cd /root/vaisravana-alpha && docker build -t bots-vaisravana-alpha:latest .
cd /opt/bots && docker compose up -d --force-recreate vaisravana-alpha
```

WS via `BINANCE_WS_URL=ws://wsproxy:8888/ws` (sera's direct IP gets no stream).
Data at `./vaisravana-alpha/data` (owned by uid 10001 `alpha`).

Healthcheck reads `alpha-agentic.db` mtime (written every 60s by the run
heartbeat), not the trade DB - avoids false "unhealthy" when alive but between
trades.

Env for the merged alpha (in `/opt/bots/docker-compose.yml`):
```
BINANCE_WS_URL=ws://wsproxy:8888/ws
ALPHA_DATA=/data
ALPHA_MODE=paper
ALPHA_LOG_LEVEL=INFO
ALPHA_EXIT_ENGINE=true      # merged: real-time exit across all pairs
ALPHA_EXIT_PAIR=            # empty = all 15 pairs
ALPHA_EXIT_TICK_INTERVAL_MS=200
```

Telegram: alpha uses its OWN token (`TG_BOT_TOKEN`, distinct from the wave bot's
`TELEGRAM_BOT_TOKEN`). Chat ID `5894116684`.

---

## 7. Gotchas

- **Stop-flag persists in the volume**: `/stop` or SIGTERM writes
  `alpha_stop.flag`; the engine refuses to trade at boot while it exists.
  Symptom: container healthy but 0 opens forever. Fix:
  `rm -f /opt/bots/vaisravana-alpha/data/alpha_stop.flag && docker restart bots-vaisravana-alpha`.
- **Non-root container needs owned data dir**: image runs as uid 10001. Fresh
  bind mount owned by root -> `sqlite3.OperationalError: unable to open database
  file`. Fix: `chown -R 10001:10001 /opt/bots/vaisravana-alpha/data`.
- **Dockerfile ordering**: source COPYed before `pip install` (see skill).
- **Concurrent DB access**: agentic DB uses WAL + busy_timeout=30000 on both
  writer and CLI reader. Never connect without busy_timeout.
- **Tick model drift**: `Tick.qty` (not `volume`), `Tick.side` = "BUY"/"SELL"/""
  (not `is_buy`). Exit engine mirrors this.
- **exit_price=0.0 in trades** (fixed 2026-07-30): `manager.close()` now seeds
  `econ = {"pnl":0.0,"close_fee":0.0,"net":0.0,"exit_price":price}`. Cross-check
  the DB after a close-path change - the close card shows R, not raw exit price.

---

## 8. Hard rules

- Alpha is its own repo/container (satisfies "separate repo per bot").
- Deploy into /opt/bots as `bots-vaisravana-alpha`; WS via wsproxy.
- Same eval discipline as wave: measure after every change, one change per
  iteration, honest verdicts, commit negative results too.
- Capital preservation > activity. R:R >= 2 floor. $10 -> $0 honest run.
