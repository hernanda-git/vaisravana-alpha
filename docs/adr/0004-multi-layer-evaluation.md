# ADR 0004: Multi-layer evaluation, not one score

Status: accepted
Date: 2026-07-30

## Context

The predecessor evaluated trading runs with a single number: mostly
expectancy or net PnL, occasionally a Sharpe. A scalar is the wrong output
for this decision. Four runs that all score "profitable" actually demand
opposite responses:

- Profitable on nine trades. Statistically meaningless; promoting on it
  converges the loop on noise. The predecessor did exactly this, repeatedly.
- Sound but unprofitable. The edge may be real and the costing wrong, or the
  market regime may have shifted. Different fix from "it does not work".
- Profitable because one gate is silently rejecting everything, so the few
  trades that slip through are all the same easy setup. A scalar hides the
  monoculture.
- Profitable but on a 55% drawdown. Ruin is not recoverable, so the final
  balance being green is the least important fact.

A single score collapses all four into one line and forces the loop to
guess which situation it is in. It cannot guess; it must be told.

## Decision

Evaluate in six layers, each returning its own verdict (pass / warn / fail /
insufficient), run in a fixed order with short-circuiting:

    L0 integrity    L1 execution    L2 statistical
    L3 economic     L4 robustness   L5 risk

L0 runs first and, on failure, blocks all downstream layers. The reasoning:
a profit figure computed from broken accounting is worse than no figure, it
is a confident lie. Wave once billed 360 fees against 27 real trades, and
every performance number in that period was fiction. Integrity checks fee
consistency, net == gross - fees, timestamp ordering, and duplicate ids.

L1 (execution) exists because of that same failure: the bot ran a full day
looking healthy while one gate rejected 100% of candidates. Trade tables
cannot express that; rejection counters can. L1 flags it.

Promotion requires L0-L3 to pass and no layer to fail. `insufficient_data`
is explicitly not a pass: "we do not know yet" must never be read as "yes".
A `warn` is survivable; a `fail` anywhere blocks promotion.

The statistical layer uses the deflated Sharpe ratio, which discounts for the
number of variants the loop has tried. Searching a parameter space inflates
the best observed Sharpe, and correcting for it is the honest correction,
not a nice-to-have.

## Consequences

Good:

- The loop gets a structured verdict it can branch on: promote, reject,
  gather more data, or escalate to a human.
- Each layer is independently testable. The test suite asserts L0 blocks on
  fee mismatch and that insufficient sample is never a silent pass.
- A profitable run that is statistically meaningless is now rejected by L2
  instead of promoted by L3, which is the bug that drove most of wave's bad
  promotions.

Costs:

- More code than `return expectancy > 0`. Justified: the decision it serves
  is the one that compounds or destroys the account.
- Thresholds (MIN_TRADES=20, DSR>=0.95) are judgement calls. They are
  constants at the top of `layers.py`, not buried, so they can be argued
  about and changed in one place.

## Alternatives considered

**Single composite score with weights.** Rejected: the weights encode the
exact judgement the loop needs to recover, and once collapsed the
information is gone. You cannot un-average a scalar back into "profitable
but on a 55% drawdown".

**Bayesian optimisation over PnL.** Rejected for now: needs more trades than
this paper account will generate for a long time, and optimises the wrong
thing (one number) for the reasons above. Revisit when the loop has hundreds
of closed trades per surface.
