# AGENTS.md

## Scope

This repository contains Vaiśravaṇa Alpha, an aggressive paper-trading research bot for Binance market data. It is deployed locally on sera through Docker Compose.

## Rules

- Preserve paper mode unless the owner explicitly changes the boundary.
- Never commit tokens, `.env` files, database files, or generated artifacts.
- Preserve the named SQLite volume during rebuilds.
- Treat net PnL after all costs as authoritative.
- Keep aggressive trading active; do not add broad blocking gates as a convenience fix.
- Separate single-leg, paired, and basis strategy statistics.
- Make changes reversible and document the experiment.
- Verify Telegram and Docker health after deployment.

## Required checks

```bash
python3 -m py_compile src/vaisravana_alpha/**/*.py
git diff --check
docker compose config
docker ps --filter name=bots-vaisravana-alpha
git status --short
```

For strategy changes, run the relevant test suite and query SQLite before and after deployment.

## Commit and push

Inspect the remote and branch before pushing. Review staged files and ensure no secrets or persistent data are staged. Report the final commit SHA and remote branch.

## Do not

- delete historical paper data;
- claim arbitrage without a hedge/payoff proof;
- use win rate alone;
- silently change live/paper mode;
- overwrite unrelated bot repositories.