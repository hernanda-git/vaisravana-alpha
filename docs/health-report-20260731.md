# Alpha Bot Health Fix Report
## Date: 2026-07-31
## Machine: sera (43.157.208.115)

---

## Root Cause

Alpha bot (`bots-vaisravana-alpha`) was stuck in a **rapid restart loop**.
Each cycle: boot → detect stale `alpha_stop.flag` → refuse to trade → stop → restart → repeat.

The `alpha_stop.flag` file in `/opt/bots/vaisravana-alpha/data/alpha_stop.flag` (bind-mounted volume)
was from a prior crash. The container's `clear_stop()` removes young flags (<60s) but this stale
flag persisted indefinitely.

## Fix Applied

```bash
rm -f /opt/bots/vaisravana-alpha/data/alpha_stop.flag
docker restart bots-vaisravana-alpha
```

## Post-Fix Status

- **Status:** Healthy, running
- **Pairs:** 15 active (REST poller 5s + FeedMux streams)
- **Mode:** Paper, $9.7567 balance  
- **Exit engine:** EXIT_ENGINE=true, all 15 pairs, 200ms tick interval
- **Warmup:** 90s active

## Known State

- 44 prior trades in DB, net PnL negative (max_age losses dominate)
- Wallet: 9.7567 balance, 44 trades, 0.143 fees paid
- No trades opened yet post-fix (warmup still active)
