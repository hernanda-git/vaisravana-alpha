# ADR 0002: The paper/live boundary is structural, not procedural

Status: accepted
Date: 2026-07-30

## Context

The bot trades a simulated account. At some point it may trade a real one.
The failure mode that matters is not "live trading loses money" but "code
believed to be in paper mode reaches a real venue".

The predecessor's `ModeGuard` checked a `mode` string and returned either a
simulated or a real exchange. It worked, but the guarantee rested on every
call site asking the guard rather than constructing an exchange directly.
That is a convention, and conventions are not enforceable.

Compounding it, the guard was duplicated: one copy in `mode.py` and another
in `wave/risk.py`. Two implementations of a safety boundary is one too many.

## Decision

Make the boundary structural, in one place, with two independent conditions.

An order can reach a real venue only if:

1. a real adapter was injected, and
2. the symbol is in a human-approved `(pair, tf, side)` set.

In paper mode, `broker_for()` *raises* when handed a live adapter rather
than ignoring it. There is no code path from `mode="paper"` to a real
network call, so the property holds by control flow rather than discipline.

`LiveBroker` then re-checks every symbol on every call as a second line of
defence, so even a bypassed entry gate cannot put an unapproved symbol on
the wire.

The duplicate guard is deleted. `broker.py` is the only module that decides
this question.

## Consequences

Good:

- The guarantee is testable, and is tested: passing a live adapter to a
  paper guard raises, and an unapproved symbol never reaches the adapter.
- `MODE=live` currently refuses to boot, because no adapter is wired. The
  failure is loud at startup rather than a silent fallback to paper.
- One place to audit.

Costs:

- Enabling live trading requires touching the approval set deliberately.
  This is the intended friction.

## Alternatives considered

**A boolean `dry_run` flag on the exchange client.** Rejected: a flag that
defaults wrong, or is dropped in one call path, fails silently. The whole
point is to make the unsafe state unconstructable.

**Separate paper and live entrypoints.** Rejected: it duplicates the wiring
and lets the two drift, which reintroduces the risk elsewhere.
