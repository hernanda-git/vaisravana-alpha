# Telegram operations

## Alpha bot

The Alpha bot is `@vaisravana_alpha_bot`.

## Registered commands

- `/alpha_status`
- `/alpha_performance`
- `/alpha_positions`
- `/alpha_trades`
- `/alpha_universe`
- `/alpha_stop`
- `/alpha_resume`
- `/alpha_help`
- `/alpha_ping`
- `/alpha_health`

## Command behavior

- Status reports process liveness and feed state.
- Alpha also sends a health status card every five minutes, including during quiet periods with no trades.
- Performance reads persistent closed-trade history from SQLite.
- Positions reads current in-memory exposure and wallet snapshot.
- Trades reads recent persistent closed-trade history.
- Universe reports current ranked symbols.
- Stop and resume change owner control state.

## Verification

```bash
TOKEN=$(docker exec bots-vaisravana-alpha env | awk -F= '/TELEGRAM_BOT_TOKEN/{print $2}')
curl -s "https://api.telegram.org/bot${TOKEN}/getMe"
curl -s "https://api.telegram.org/bot${TOKEN}/getMyCommands"
```

Do not print or commit the token. If commands are stale, rebuild the Alpha container so startup registration runs again, then verify `getMyCommands`.

Do not manually call `getUpdates` while Alpha is running. Telegram allows only
one long-poll consumer and returns HTTP 409 when a second consumer competes
with Alpha's listener. Use `getMe`, `getMyCommands`, or the Telegram client for
verification instead.

## Reporting rule

Telegram numbers must be derived from the same persistent database query used by offline reports. A message saying no closed trades is a defect when the database contains completed rows.