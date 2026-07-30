# Vaiśravaṇa Alpha

Tick-driven crypto futures paper trading engine with fee-aware admission
control and an offline evaluation harness.

A clean-structure redesign of `vaisravana-wave`. Same strategy logic,
rebuilt as a layered package so each part can be tested, tuned, and
replaced on its own.

## Why this exists

The predecessor worked but had grown into a shape that resisted change: a
~1200-line entrypoint that was also the engine, the notifier, the command
router, and the wallet; configuration read via `os.getenv` from a dozen
modules; and a flat `src/wave/` package with no import direction, so nothing
could be tested without importing everything.

Alpha keeps the strategy and rebuilds the scaffolding around it.

## Architecture

Imports flow strictly downward. A layer never imports its siblings.

```
engine/         orchestration and the async runtime (the only layer that wires)
  |
  +-- strategy/     signal generation: bias, structure, SMC, gates, scanner
  +-- execution/    position lifecycle, sizing, wallet, venue boundary
  +-- marketdata/   websocket mux and REST safety net
  +-- notify/       Telegram transport and message cards
  +-- storage/      SQLite schema and writers
        |
        +-- core/   models, parameter surface, settings (depends on nothing)

evaluation/     offline scoring. Imports no engine code by design.
```

Three properties follow from that shape:

- **Strategy cannot trade.** It emits candidates; only `execution` opens or
  closes anything. Gates can be tuned by an autonomous loop with no path to
  order flow.
- **Evaluation cannot perturb what it measures.** It reads recorded trades
  from SQLite and never re-simulates, so a verdict is reproducible after the
  run has ended.
- **Paper cannot silently become live.** See below.

## The paper/live boundary

The most important safety property here. An order reaches a real venue only
if a real adapter was injected *and* the symbol is in a human-approved set.

```python
ModeGuard(mode="paper").broker_for(real_exchange)   # raises ModeBoundaryError
ModeGuard(mode="live").broker_for(real_exchange)    # raises: no approvals
```

In paper mode a live broker cannot be constructed at all, so the guarantee
is enforced by control flow rather than by convention. `LiveBroker` then
re-checks every symbol as a second line of defence. `MODE=live` currently
refuses to boot, because no adapter is wired.

## The paper account

Starts at $10 and is allowed to run to zero. That is the experiment: a run
that gets topped up teaches nothing, while an honest run to ruin shows
exactly how long the edge survived its own costs.

Fees are asymmetric, matching a limit entry and a market exit:

| Event | Rate            | On $100 notional |
|-------|-----------------|------------------|
| open  | 0.02% (maker)   | $0.02            |
| close | 0.04% (taker)   | $0.04            |
| round trip | 6 bps      | $0.06            |

Position size is a fraction of the **live** balance, so the account
de-risks itself in a drawdown and compounds on a winning streak with no
separate rule. Balance persists across restarts.

## Parameter surface

Everything an autonomous tuning loop may change lives in
`core/params.py` and is validated by pydantic on load. Engine code,
execution logic, and the telemetry schema are structural and need a human.

Validators are safety code, not input hygiene. A surface that fails them is
rejected in favour of known-good defaults rather than being traded:

- bias weights must sum to exactly 1.0
- reward-to-risk must be at least 2:1 (owner mandate)
- the throttle floor must stay above zero, so tuning can never halt trading
- the EV gate must demand a margin over round-trip cost

## Quick start

```bash
cp .env.example .env      # fill in TELEGRAM_BOT_TOKEN and NOTIFY_CHAT_ID
docker compose up -d --build
docker compose logs -f
```

Running locally instead:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m vaisravana_alpha
```

The bot runs headless with no Telegram token; cards go to the log.

## Tests

```bash
pytest -q                                    # local
docker build -f docker/Dockerfile.test -t alpha-test . \
  && docker run --rm -v "$PWD:/app" alpha-test    # containerised
```

The suite covers the surface validators, wallet arithmetic, and the
paper/live boundary. It uses no network and no sleeps.

## Operations

| Command        | Effect                                      |
|----------------|---------------------------------------------|
| `/status`      | uptime, feed state, tick count, throttle    |
| `/positions`   | open waves plus the account footer          |
| `/performance` | realized results, win rate, median R        |
| `/stop`        | halt and persist the stop flag              |
| `/resume`      | clear the stop flag                         |

`/stop` writes a flag file to `ALPHA_DATA`. This matters because Docker's
restart policy would otherwise bring a stopped bot straight back up; the
engine refuses to trade at boot while the flag exists.

## Layout

```
src/vaisravana_alpha/
  core/        models, params, settings, symbols
  strategy/    bias, structure, smc, gate, scanner, survival, indicators
  execution/   manager, wallet, broker, risk
  marketdata/  feed (ws), rest (poller)
  engine/      runtime, context
  notify/      telegram, cards
  storage/     db
  evaluation/  metrics, deflated_sharpe, cscv, archive
tests/
docs/adr/      architecture decision records
```

## Status

Paper mode only. Live trading is deliberately unwired.
