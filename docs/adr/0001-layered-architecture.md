# ADR 0001: Layered package with a strict import direction

Status: accepted
Date: 2026-07-30

## Context

`vaisravana-wave` grew organically into a flat `src/wave/` package alongside
a `src/` root holding main-bot leftovers. There was no declared import
direction, so modules imported each other freely. Three consequences:

- Nothing could be imported in isolation. Importing the bias calculator
  pulled in the database, the notifier, and the exchange client, so a unit
  test of pure arithmetic needed a network stub.
- The blast radius of a change was unknowable without reading everything.
- The wave engine imported main-bot modules (`symbols`, `mode`, `execution`,
  `config`) that carried features the wave engine never used, including a
  three-profile strategy system for timescales it does not trade.

## Decision

Reorganise into layers with a single permitted import direction:

```
engine/                     orchestration; the only layer that wires others
  strategy/ execution/ marketdata/ notify/ storage/
    core/                   models, params, settings; imports nothing local
```

A layer may import downward. It may not import sideways or upward.
`evaluation/` sits outside entirely and imports no engine code.

## Consequences

Good:

- Every layer below `engine/` is testable without the others. The current
  suite runs with no network and no sleeps because of this.
- Strategy code has no path to order flow, which is what makes it safe for
  an autonomous loop to tune gates.
- The dependency graph is checkable. A sideways import is a review-visible
  mistake, not an invisible one.

Costs:

- More files, and a small amount of indirection when tracing a call.
- `engine/runtime.py` is necessarily the largest module, since wiring has to
  live somewhere. That is a deliberate concentration of coupling in one
  reviewable place rather than a diffusion of it everywhere.

## Alternatives considered

**Keep the flat layout, add tests.** Rejected: the tests would need heavy
mocking precisely because the coupling is real, and mock-heavy tests mostly
assert that the mocks were called.

**Split into separate packages.** Rejected as premature. The layers are not
independently versioned or deployed, so the packaging overhead would buy
nothing today.
