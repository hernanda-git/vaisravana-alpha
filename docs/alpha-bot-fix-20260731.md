# Alpha Bot: Restart Loop Fix
## vaisravana_alpha | sera | 2026-07-31

---

## Root Cause

Alpha bot (`bots-vaisravana-alpha`) was in a **rapid restart loop**.
Each cycle: boot → detect stale `alpha_stop.flag` → refuse to trade → halt (18s) → restart → repeat.

The flag persisted in the docker bind-mounted volume `/opt/bots/vaisravana-alpha/data/`.
The container's `clear_stop()` removes young flags (<60s) but stale flags from prior crashes persist indefinitely until manually removed.

## Fix

```bash
rm -f /opt/bots/vaisravana-alpha/data/alpha_stop.flag
docker restart bots-vaisravana-alpha
```

## Post-Fix Status

- **Container:** UP, healthy (22 min+)
- **Engine:** EXIT_ENGINE=true, 15 pairs, real-time exit monitoring
- **Feed:** REST poller (5s interval) + FeedMux streams
- **Wallet:** $9.7567 balance, 44 prior trades, 0.143 total fees
- **Warmup:** 90sec — first trades expected after warmup completes
- **Exit tick interval:** 200ms
- **Alpha data dir:** `/data` (persistent volume)

## Exit Engine

The alpha bot has a real-time exit engine that evaluates every 200ms per pair.
It uses regime-detection, factor scoring, and CVD divergence as exit factors.
When `ALPHA_EXIT_ENGINE=true`, the engine monitors all 15 pairs simultaneously
and may close waves early before structural TP/SL would trigger.

Exit actions:
- `CLOSE_50`: close 50% at detected regime shift
- `CLOSE_100`: close full position at regime + confidence

## No Stop Flag at Boot

Confirmed: `alpha_stop.flag` is not present after fix. Bot boots cleanly.
The auto-clear feature (`clear_stop()` removes flag if older than 60s) handles
stale flags from crashes, but only if the flag hasn't been re-created by a
rapid restart cycle before the clear check runs.

## Monitoring

```bash
# Check if engine is running (not in stop-flag halt)
docker logs bots-vaisravana-alpha --tail 3 | grep "engine running"

# Check for stop flag warnings
docker logs bots-vaisravana-alpha --tail 20 | grep "stop flag"

# Check open trades
docker exec bots-vaisravana-alpha python3 -c "
import sqlite3; c=sqlite3.connect('/data/vaisravana-alpha.db').cursor()
c.execute('SELECT COUNT(*) FROM wave_log WHERE state=\"SURFING\"')
print('Open:', c.fetchone()[0])
"
```
