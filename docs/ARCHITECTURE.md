# Alpha architecture

## Runtime flow

`market data -> context -> indicators -> universe ranking -> bias/confidence -> counter-trade scan -> gate -> manager -> exits -> wallet -> SQLite -> Telegram`

## Components

- `marketdata`: Binance WebSocket and REST fallback.
- `engine/context.py`: candle, tick, indicator, flow, and regime state.
- `strategy/universe_ranker.py`: ranks active symbols by relative strength and weakness.
- `strategy/bias.py`: derives directional bias and confidence.
- `strategy/scanner.py`: creates BUY or SELL candidates. Counter-trade maps bearish bias to BUY and bullish bias to SELL.
- `strategy/gate.py`: validates bias, confidence, structure, ADX, and available SMC zones.
- `execution/manager.py`: owns wave lifecycle, sizing, fees, targets, stops, partial profit, and close persistence.
- `execution/wallet.py`: paper balance, margin, fees, realized and unrealized PnL.
- `execution/risk.py`: kill switch and pair exclusions.
- `storage/db.py`: wave log, telemetry, zones, and trades tables.
- `notify/cards.py`: pure Telegram message formatting.
- `notify/telegram.py`: Telegram API transport and command polling.

## Operational boundary

The current deployment is paper mode on sera using Docker Compose. The Alpha named volume must be preserved across rebuilds. No live order is implied by a healthy container.

## State ownership

The manager owns open in-memory waves. SQLite owns durable history. Telegram commands must query durable history for historical metrics and in-memory state for currently open positions. This distinction prevents restart-related zero-trade reports.

## Extension points

A future paired engine should create a trade group containing two legs and must not overload the single-leg wave row without explicit mode and group identifiers.