"""Multi-layer evaluation -- six independent verdicts, deliberately not one score.

A single number is the wrong output for this problem. "Profitable but on
nine trades", "sound but unprofitable", and "profitable because one gate is
silently rejecting everything" all collapse to the same scalar while
demanding completely different responses.

So each layer answers one question and returns its own verdict:

    L0  integrity    Is the data trustworthy at all?
    L1  execution    Did the machine behave correctly?
    L2  statistical  Is the result distinguishable from luck?
    L3  economic     Does it make money after real costs?
    L4  robustness   Would it survive out of sample?
    L5  risk         Can it ruin the account?

Layers run in order and short-circuit. If L0 fails, every downstream number
is computed from untrustworthy data and reporting them would manufacture
false confidence. This ordering is the single most important design choice
here: the predecessor evaluated profit first, and repeatedly promoted
parameter sets on results produced by broken accounting.

Promotion requires L0-L3 to pass and no layer to fail. A `warn` is
survivable; a `fail` anywhere blocks promotion.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

PASS, WARN, FAIL, INSUFFICIENT = "pass", "warn", "fail", "insufficient_data"

# Below this, sample statistics are noise. Chosen because the strategy's
# own R:R implies a ~33% breakeven win rate, and separating 33% from 45%
# with any confidence needs a sample of roughly this order.
MIN_TRADES_FOR_STATS = 20
MIN_TRADES_FOR_ROBUSTNESS = 40


@dataclass
class LayerResult:
    """One layer's verdict. `reasons` carries the human-readable evidence."""

    layer: int
    name: str
    verdict: str
    score: float | None = None
    metrics: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == PASS

    @property
    def failed(self) -> bool:
        return self.verdict == FAIL


@dataclass
class EvaluationReport:
    """The full stack of verdicts plus the promotion decision."""

    run_id: str
    layers: list[LayerResult] = field(default_factory=list)
    promote: bool = False
    summary: str = ""

    def layer(self, n: int) -> LayerResult | None:
        return next((r for r in self.layers if r.layer == n), None)

    @property
    def failed_layers(self) -> list[LayerResult]:
        return [r for r in self.layers if r.failed]


# ── helpers ──────────────────────────────────────────────────────────────


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list[float]) -> float:
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _max_drawdown(equity: list[float]) -> float:
    """Largest peak-to-trough fall, as a fraction of the peak."""
    if not equity:
        return 0.0
    peak, worst = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


# ── L0: integrity ────────────────────────────────────────────────────────


def evaluate_integrity(run: dict, trades: list[dict]) -> LayerResult:
    """Is the data trustworthy?

    Runs first and blocks everything downstream, because a profit figure
    computed from broken accounting is worse than no figure: it looks
    actionable. Wave once billed 360 fees against 27 real trades, and every
    performance number in that period was fiction.
    """
    reasons: list[str] = []
    verdict = PASS

    if not trades:
        return LayerResult(
            0, "integrity", INSUFFICIENT,
            metrics={"trades": 0},
            reasons=["no closed trades to verify"],
        )

    # Fees must be consistent with the recorded notional, not merely present.
    fee_mismatches = 0
    for t in trades:
        notional = t.get("notional") or 0.0
        if notional <= 0:
            continue
        expected = notional * 0.0006      # 2bps maker open + 4bps taker close
        actual = (t.get("open_fee") or 0.0) + (t.get("close_fee") or 0.0)
        if actual > expected * 3 or (actual < expected * 0.3 and actual >= 0):
            fee_mismatches += 1
    if fee_mismatches:
        verdict = FAIL
        reasons.append(
            f"{fee_mismatches}/{len(trades)} trades have fees inconsistent "
            f"with their notional"
        )

    # net must equal gross - fees. If this drifts, the two are computed in
    # different places and one of them is wrong.
    accounting_errors = 0
    for t in trades:
        gross = t.get("gross_usd") or 0.0
        fees = (t.get("open_fee") or 0.0) + (t.get("close_fee") or 0.0)
        net = t.get("net_usd") or 0.0
        if abs((gross - fees) - net) > 1e-6:
            accounting_errors += 1
    if accounting_errors:
        verdict = FAIL
        reasons.append(
            f"{accounting_errors} trades where net != gross - fees"
        )

    # A trade that closes before it opens indicates clock or ordering bugs.
    bad_timestamps = sum(
        1 for t in trades
        if (t.get("close_ts") or 0) and (t.get("open_ts") or 0)
        and t["close_ts"] < t["open_ts"]
    )
    if bad_timestamps:
        verdict = FAIL
        reasons.append(f"{bad_timestamps} trades close before they open")

    # Duplicate ids mean the writer ran twice for one event.
    ids = [t.get("trade_id") for t in trades if t.get("trade_id")]
    if len(ids) != len(set(ids)):
        verdict = FAIL
        reasons.append(f"{len(ids) - len(set(ids))} duplicate trade ids")

    # Zero-notional trades are phantom fills; they distort every average.
    zero_notional = sum(1 for t in trades if (t.get("notional") or 0.0) <= 0)
    if zero_notional:
        verdict = FAIL if zero_notional > len(trades) * 0.1 else WARN
        reasons.append(f"{zero_notional} trades with zero notional")

    if verdict == PASS:
        reasons.append(f"{len(trades)} trades pass all integrity checks")

    return LayerResult(
        0, "integrity", verdict,
        score=1.0 - (fee_mismatches + accounting_errors) / max(len(trades), 1),
        metrics={
            "trades": len(trades),
            "fee_mismatches": fee_mismatches,
            "accounting_errors": accounting_errors,
            "bad_timestamps": bad_timestamps,
            "zero_notional": zero_notional,
        },
        reasons=reasons,
    )


# ── L1: execution ────────────────────────────────────────────────────────


def evaluate_execution(run: dict, trades: list[dict],
                       rejections: list[dict]) -> LayerResult:
    """Did the machine behave correctly, regardless of profit?

    This layer exists because of a specific failure: the bot ran for a full
    day looking healthy while one gate rejected 100% of candidates. Trade
    tables cannot express that; rejection counters can.
    """
    reasons: list[str] = []
    verdict = PASS

    ticks = run.get("ticks") or 0
    opens = run.get("opens") or 0
    duration_h = 0.0
    if run.get("ended_ts") and run.get("started_ts"):
        duration_h = max(0.0, (run["ended_ts"] - run["started_ts"]) / 3600.0)

    if ticks == 0:
        return LayerResult(
            1, "execution", FAIL,
            metrics={"ticks": 0},
            reasons=["no ticks received: the feed was dead for the whole run"],
        )

    total_rejections = sum(r.get("total", 0) for r in rejections)
    top_gate = rejections[0] if rejections else None

    # A single gate owning nearly all rejections is a misconfiguration
    # signature, not selectivity.
    if top_gate and total_rejections > 100:
        share = top_gate["total"] / total_rejections
        if share > 0.95 and opens == 0:
            verdict = FAIL
            reasons.append(
                f"gate '{top_gate['gate']}' rejected {share:.0%} of "
                f"{total_rejections} candidates and nothing traded: "
                f"almost certainly misconfigured"
            )
        elif share > 0.90:
            verdict = WARN if verdict == PASS else verdict
            reasons.append(
                f"gate '{top_gate['gate']}' dominates rejections ({share:.0%})"
            )

    if opens == 0 and duration_h > 1.0:
        verdict = FAIL
        reasons.append(f"no positions opened in {duration_h:.1f}h of running")

    # Opens that never close mean the exit path is not firing.
    closes = run.get("closes") or 0
    if opens > 0 and closes == 0 and duration_h > 2.0:
        verdict = FAIL
        reasons.append(f"{opens} opened, none closed after {duration_h:.1f}h")

    if run.get("status") == "crashed":
        verdict = FAIL
        reasons.append(f"run crashed: {run.get('halt_reason', 'unknown')}")

    if verdict == PASS:
        reasons.append(
            f"{ticks} ticks, {opens} opens, {closes} closes over "
            f"{duration_h:.1f}h"
        )

    return LayerResult(
        1, "execution", verdict,
        score=min(1.0, opens / max(duration_h, 1.0) / 2.0) if duration_h else None,
        metrics={
            "ticks": ticks, "opens": opens, "closes": closes,
            "duration_h": round(duration_h, 2),
            "rejections": total_rejections,
            "top_gate": top_gate["gate"] if top_gate else "",
            "top_gate_share": (
                round(top_gate["total"] / total_rejections, 3)
                if top_gate and total_rejections else 0.0
            ),
        },
        reasons=reasons,
    )


# ── L2: statistical ──────────────────────────────────────────────────────


def evaluate_statistical(trades: list[dict], n_trials: int = 1) -> LayerResult:
    """Is this distinguishable from luck?

    Deliberately placed before the profit layer. A positive return on twelve
    trades is not evidence, and promoting on it is how a loop converges on
    noise. The deflated Sharpe discounts for how many variants were tried,
    which is the correct correction when an agent is searching a parameter
    space.
    """
    nets = [t.get("net_usd") or 0.0 for t in trades]
    n = len(nets)

    if n < MIN_TRADES_FOR_STATS:
        return LayerResult(
            2, "statistical", INSUFFICIENT,
            metrics={"trades": n, "required": MIN_TRADES_FOR_STATS},
            reasons=[
                f"{n} trades is below the {MIN_TRADES_FOR_STATS} needed to "
                f"separate signal from noise at this strategy's R:R"
            ],
        )

    mean, sd = _mean(nets), _stdev(nets)
    if sd == 0:
        return LayerResult(
            2, "statistical", FAIL,
            metrics={"trades": n, "stdev": 0.0},
            reasons=["zero variance across trades: results are not real"],
        )

    sharpe = mean / sd
    # Standard error of the Sharpe estimate, skew/kurtosis-adjusted.
    skew = (_mean([((x - mean) / sd) ** 3 for x in nets])) if sd else 0.0
    kurt = (_mean([((x - mean) / sd) ** 4 for x in nets])) if sd else 3.0
    se = math.sqrt(
        max(1e-12, (1 - skew * sharpe + (kurt - 1) / 4 * sharpe ** 2) / (n - 1))
    )
    t_stat = sharpe / se

    # Deflate for multiple testing: trying many variants inflates the best
    # observed Sharpe even when no variant has an edge.
    expected_max = 0.0
    if n_trials > 1:
        euler = 0.5772156649
        expected_max = se * (
            (1 - euler) * _norm_cdf(1 - 1.0 / n_trials)
            + euler * _norm_cdf(1 - 1.0 / (n_trials * math.e))
        )
    dsr = _norm_cdf((sharpe - expected_max) / se) if se > 0 else 0.0

    reasons = []
    if dsr >= 0.95:
        verdict = PASS
        reasons.append(f"deflated Sharpe probability {dsr:.3f} over {n} trades")
    elif dsr >= 0.80:
        verdict = WARN
        reasons.append(
            f"deflated Sharpe probability {dsr:.3f}: suggestive, not conclusive"
        )
    else:
        verdict = FAIL
        reasons.append(
            f"deflated Sharpe probability {dsr:.3f} is indistinguishable "
            f"from luck after correcting for {n_trials} trials"
        )

    return LayerResult(
        2, "statistical", verdict, score=dsr,
        metrics={
            "trades": n, "sharpe": round(sharpe, 4),
            "t_stat": round(t_stat, 3), "dsr": round(dsr, 4),
            "n_trials": n_trials, "skew": round(skew, 3),
            "kurtosis": round(kurt, 3),
        },
        reasons=reasons,
    )


# ── L3: economic ─────────────────────────────────────────────────────────


def evaluate_economic(trades: list[dict], start_balance: float) -> LayerResult:
    """Does it actually make money after real costs?

    Reports the fee burden explicitly. A strategy whose gross is positive
    and net negative is not "nearly profitable", it is unprofitable with a
    misleading headline, and that distinction drove most of wave's bad
    promotions.
    """
    if not trades:
        return LayerResult(
            3, "economic", INSUFFICIENT, reasons=["no trades"],
            metrics={"trades": 0},
        )

    nets = [t.get("net_usd") or 0.0 for t in trades]
    gross_total = sum(t.get("gross_usd") or 0.0 for t in trades)
    fees_total = sum(
        (t.get("open_fee") or 0.0) + (t.get("close_fee") or 0.0) for t in trades
    )
    net_total = sum(nets)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]

    win_rate = len(wins) / len(nets)
    avg_win = _mean(wins) if wins else 0.0
    avg_loss = abs(_mean(losses)) if losses else 0.0
    profit_factor = (
        sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")
    )
    expectancy = _mean(nets)
    fee_ratio = fees_total / abs(gross_total) if gross_total else float("inf")

    reasons = []
    verdict = PASS

    if net_total <= 0:
        verdict = FAIL
        reasons.append(f"net {net_total:+.4f}$ over {len(nets)} trades")
        if gross_total > 0:
            reasons.append(
                f"gross was positive ({gross_total:+.4f}$) but "
                f"{fees_total:.4f}$ of fees consumed it: the edge is smaller "
                f"than its own cost"
            )
    elif expectancy <= 0:
        verdict = FAIL
        reasons.append(f"per-trade expectancy {expectancy:+.6f}$ is not positive")
    else:
        reasons.append(
            f"net {net_total:+.4f}$, expectancy {expectancy:+.6f}$/trade"
        )

    if fee_ratio > 0.5 and verdict == PASS:
        verdict = WARN
        reasons.append(
            f"fees are {fee_ratio:.0%} of gross: the strategy is trading too "
            f"often for its edge"
        )

    return LayerResult(
        3, "economic", verdict,
        score=expectancy,
        metrics={
            "trades": len(nets),
            "net_usd": round(net_total, 6),
            "gross_usd": round(gross_total, 6),
            "fees_usd": round(fees_total, 6),
            "fee_ratio": round(fee_ratio, 4) if fee_ratio != float("inf") else None,
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 6),
            "avg_loss": round(avg_loss, 6),
            "profit_factor": (
                round(profit_factor, 4) if profit_factor != float("inf") else None
            ),
            "expectancy": round(expectancy, 6),
            "return_pct": (
                round(net_total / start_balance * 100, 3) if start_balance else None
            ),
        },
        reasons=reasons,
    )


# ── L4: robustness ───────────────────────────────────────────────────────


def evaluate_robustness(trades: list[dict]) -> LayerResult:
    """Would this survive out of sample?

    Splits the run in half and checks the second half independently. An edge
    that exists only in the first half is a fitted artefact. Also checks
    concentration: profit that depends on one trade or one pair is not a
    strategy, it is a lucky position.
    """
    n = len(trades)
    if n < MIN_TRADES_FOR_ROBUSTNESS:
        return LayerResult(
            4, "robustness", INSUFFICIENT,
            metrics={"trades": n, "required": MIN_TRADES_FOR_ROBUSTNESS},
            reasons=[f"{n} trades is too few to split and test out of sample"],
        )

    ordered = sorted(trades, key=lambda t: t.get("close_ts") or 0)
    mid = n // 2
    first = [t.get("net_usd") or 0.0 for t in ordered[:mid]]
    second = [t.get("net_usd") or 0.0 for t in ordered[mid:]]

    reasons = []
    verdict = PASS

    if _mean(first) > 0 >= _mean(second):
        verdict = FAIL
        reasons.append(
            f"edge decayed: first half {_mean(first):+.6f}$/trade, "
            f"second half {_mean(second):+.6f}$/trade"
        )
    elif _mean(second) > 0:
        reasons.append(
            f"holds out of sample: second half {_mean(second):+.6f}$/trade"
        )

    # Single-trade dependency.
    nets = [t.get("net_usd") or 0.0 for t in ordered]
    total = sum(nets)
    if total > 0:
        best = max(nets)
        if best / total > 0.5:
            verdict = FAIL if verdict == PASS else verdict
            reasons.append(
                f"one trade contributes {best / total:.0%} of all profit"
            )

    # Single-pair dependency.
    by_pair: dict[str, float] = {}
    for t in ordered:
        by_pair[t.get("pair", "")] = by_pair.get(t.get("pair", ""), 0.0) + (
            t.get("net_usd") or 0.0
        )
    if total > 0 and by_pair:
        top_pair, top_net = max(by_pair.items(), key=lambda kv: kv[1])
        if top_net / total > 0.7:
            verdict = WARN if verdict == PASS else verdict
            reasons.append(
                f"{top_pair} accounts for {top_net / total:.0%} of profit"
            )

    return LayerResult(
        4, "robustness", verdict,
        score=_mean(second),
        metrics={
            "trades": n,
            "first_half_expectancy": round(_mean(first), 6),
            "second_half_expectancy": round(_mean(second), 6),
            "pairs_traded": len(by_pair),
        },
        reasons=reasons,
    )


# ── L5: risk ─────────────────────────────────────────────────────────────


def evaluate_risk(trades: list[dict], start_balance: float) -> LayerResult:
    """Can this ruin the account?

    Evaluated last but with veto power, because ruin is not recoverable. A
    profitable run that survived a 60% drawdown got lucky with ordering, and
    the loop must not promote it just because the final balance was green.
    """
    if not trades:
        return LayerResult(
            5, "risk", INSUFFICIENT, reasons=["no trades"], metrics={"trades": 0},
        )

    ordered = sorted(trades, key=lambda t: t.get("close_ts") or 0)
    equity, balance = [start_balance], start_balance
    for t in ordered:
        balance += t.get("net_usd") or 0.0
        equity.append(balance)

    max_dd = _max_drawdown(equity)
    nets = [t.get("net_usd") or 0.0 for t in ordered]
    worst = min(nets) if nets else 0.0

    # Longest run of consecutive losses.
    streak = worst_streak = 0
    for x in nets:
        streak = streak + 1 if x < 0 else 0
        worst_streak = max(worst_streak, streak)

    reasons = []
    verdict = PASS

    if min(equity) <= 0:
        verdict = FAIL
        reasons.append("account reached zero: total ruin")
    elif max_dd > 0.5:
        verdict = FAIL
        reasons.append(f"max drawdown {max_dd:.0%} exceeds the 50% ruin threshold")
    elif max_dd > 0.3:
        verdict = WARN
        reasons.append(f"max drawdown {max_dd:.0%} is uncomfortably deep")
    else:
        reasons.append(f"max drawdown {max_dd:.0%}")

    if start_balance > 0 and abs(worst) > start_balance * 0.25:
        verdict = FAIL if verdict != FAIL else verdict
        reasons.append(
            f"single worst trade lost {abs(worst) / start_balance:.0%} of "
            f"the starting account"
        )

    return LayerResult(
        5, "risk", verdict,
        score=1.0 - max_dd,
        metrics={
            "max_drawdown": round(max_dd, 4),
            "worst_trade": round(worst, 6),
            "worst_loss_streak": worst_streak,
            "final_balance": round(equity[-1], 4),
            "min_balance": round(min(equity), 4),
        },
        reasons=reasons,
    )


# ── orchestration ────────────────────────────────────────────────────────


def evaluate_run(run: dict, trades: list[dict], rejections: list[dict],
                 n_trials: int = 1) -> EvaluationReport:
    """Run every layer in order and decide whether to promote.

    Short-circuits on an L0 failure: with untrustworthy data, downstream
    numbers are not merely unreliable, they are actively misleading.
    """
    report = EvaluationReport(run_id=run.get("run_id", ""))
    start_balance = run.get("start_balance") or 0.0

    l0 = evaluate_integrity(run, trades)
    report.layers.append(l0)
    if l0.failed:
        report.promote = False
        report.summary = (
            "Integrity failed: the recorded data cannot be trusted, so no "
            "performance verdict is meaningful. Fix accounting before reading "
            "any other number."
        )
        return report

    report.layers.append(evaluate_execution(run, trades, rejections))
    report.layers.append(evaluate_statistical(trades, n_trials))
    report.layers.append(evaluate_economic(trades, start_balance))
    report.layers.append(evaluate_robustness(trades))
    report.layers.append(evaluate_risk(trades, start_balance))

    # Promotion needs the first four layers affirmatively passing and no
    # failure anywhere. INSUFFICIENT is not a pass: "we do not know yet" must
    # never be read as "yes".
    gates = [report.layer(i) for i in range(4)]
    report.promote = (
        all(g is not None and g.passed for g in gates)
        and not report.failed_layers
    )

    if report.promote:
        report.summary = "All gating layers passed. Safe to promote."
    elif report.failed_layers:
        names = ", ".join(f"L{r.layer} {r.name}" for r in report.failed_layers)
        report.summary = f"Blocked by: {names}."
    else:
        pending = [
            f"L{r.layer} {r.name}" for r in report.layers
            if r.verdict == INSUFFICIENT
        ]
        report.summary = (
            f"Inconclusive, awaiting more data: {', '.join(pending)}."
            if pending else "Not promoted."
        )
    return report


def format_report(report: EvaluationReport) -> str:
    """Render a report as plain text for logs or Telegram."""
    icons = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL", INSUFFICIENT: "N/A "}
    lines = [
        f"Evaluation for {report.run_id}",
        "=" * 58,
    ]
    for r in report.layers:
        score = f"{r.score:+.4f}" if r.score is not None else "  --  "
        lines.append(f"[{icons[r.verdict]}] L{r.layer} {r.name:<12} {score}")
        for reason in r.reasons:
            lines.append(f"        {reason}")
    lines += [
        "=" * 58,
        f"PROMOTE: {'yes' if report.promote else 'no'}",
        report.summary,
    ]
    return "\n".join(lines)
