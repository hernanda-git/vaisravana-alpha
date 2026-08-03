# Operations runbook

## Inspect

```bash
docker ps --filter name=bots-vaisravana-alpha
docker logs bots-vaisravana-alpha --tail 100
docker inspect bots-vaisravana-alpha
```

## Build and deploy Alpha

```bash
cd /root/vaisravana-alpha
python3 -m py_compile src/vaisravana_alpha/**/*.py
docker compose up -d --build vaisravana-alpha
```

Preserve the `vaisravana-alpha-data` volume. Do not delete it during ordinary rebuilds.

## Health verification

- container is healthy;
- logs show feed heartbeats;
- no repeated traceback or reconnect loop;
- database modification time advances;
- Telegram `getMe` succeeds;
- Telegram `getMyCommands` contains all Alpha commands;
- `/alpha_status`, `/alpha_performance`, `/alpha_positions`, and `/alpha_trades` return responses.

## Stop and resume

Use `/alpha_stop` for a graceful owner stop and `/alpha_resume` to clear the stop flag. Confirm the process state in Docker logs. A stop command must never be reported as successful without verification.

## Rollback

1. Record current SHA and container logs.
2. Check out the last verified commit.
3. Rebuild only Alpha.
4. Preserve the data volume.
5. Re-run health and Telegram checks.

## Safety

Never stage `.env`, tokens, database files, caches, or Docker secrets. Paper mode is the default evaluation boundary.