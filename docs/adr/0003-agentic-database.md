# ADR 0003: Agentic workflow database, separate from the trading DB

Status: accepted
Date: 2026-07-30

## Context

An autonomous improvement loop needs to reason about *why a change was made
and whether it worked*, not just what happened. The trading tables record
fills; they do not record hypotheses, rejections, or run identity.

The predecessor's loop kept its reasoning in markdown logs. That made three
questions unanswerable by query:

- Did iteration N beat iteration N-1, or did the market shift? Without the
  exact surface attached to every trade, a result is unattributable.
- Why did we take zero trades for six hours? A trade table shows absence;
  it cannot distinguish "no signal" from "gate silently vetoing 100%", which
  is exactly the failure that cost wave a full trading day.
- Have we tried this before? Without a hypothesis ledger, the loop
  rediscovers dead ends forever.

## Decision

Add a second sqlite database, `alpha-agentic.db`, separate from
`vaisravana-alpha.db` (the trading DB). It holds: `runs`, `iterations`,
`trades`, `gate_rejections`, `evaluations`, `decisions`, `schema_meta`.

Separation is deliberate. The improvement loop runs analytical queries
against the agentic DB continuously while the engine appends to it. If they
shared a file, a slow aggregate scan would contend with the tick path. WAL
plus `busy_timeout=30000` lets both happen at once.

Run identity is the key abstraction. One row per engine start, carrying the
full surface JSON, its hash, the git sha, and the iteration under test. Every
trade references its `run_id`, so a verdict is always "this run versus that
run", never a floating average over a window whose parameters changed
mid-stream.

Rejections are counted, not logged. `gate_rejections` upserts per
(run, gate, pair), so a bot rejecting thousands of candidates an hour
produces a small, queryable table rather than megabytes of log lines, and
"no trades" becomes distinguishable from "one gate blocked everything".

Append-only by default. The only non-append write is a run's terminal
`status`. An agent that can rewrite its history cannot be audited, so
history is not writable.

## Consequences

Good:

- The loop can answer all three questions above with SQL.
- A crashed run still shows its progress via per-minute heartbeats, so
  "died immediately" and "ran six hours then OOM'd" are distinguishable.
- The trading DB stays lean and fast; analytical load never touches it.

Costs:

- Two databases to back up and reason about.
- Surface JSON is duplicated into runs; acceptable because it makes a run
  self-describing and reproducible without the trading DB.

## Alternatives considered

**Put everything in the trading DB.** Rejected: contention, and mixing
analytical tables with hot-path tables invites a slow query to stall a tick.

**Use a separate process (Postgres).** Rejected as disproportionate. SQLite
WAL already gives concurrent read/write within one machine, which is all
this deployment needs. Revisit only if the loop goes multi-host.
