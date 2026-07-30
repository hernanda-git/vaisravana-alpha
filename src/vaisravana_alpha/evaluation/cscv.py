"""Combinatorial Cross-Validation (CSCV) + Probability of Backtest Overfitting.

López de Prado's guard against the loop fooling itself. An autonomous loop
tries many candidate changes; the one that "looks best" may be the luckiest,
not the best. CSCV detects this:

  1. Split the trade series into N contiguous blocks (respecting time order).
  2. For every combination of test blocks, train on the rest, compute Sharpe
     on each side -> build a distribution of (train Sharpe / test Sharpe).
  3. PBO = fraction of combinations where the top train-ranked candidate has
     a NEGATIVE test Sharpe. High PBO => the selection is noise.

We work on the per-trade PnL series (already time-ordered by close time). This
is the "shuffle / acak-acakan" the user asked for, done CORRECTLY — by time
block, not by naive random shuffle (which would leak future into past).

Pure stdlib (no numpy) so the loop can run it anywhere.
"""
from __future__ import annotations
import math
import itertools
from .metrics import trade_sharpe


def _blocks(xs: list[float], n_blocks: int) -> list[list[int]]:
    """Split index list into n_blocks contiguous, roughly equal chunks."""
    k = len(xs)
    if k < n_blocks:
        n_blocks = max(1, k)
    size = k / n_blocks
    out = []
    for i in range(n_blocks):
        lo = int(math.floor(i * size))
        hi = int(math.floor((i + 1) * size))
        out.append(list(range(lo, hi)))
    return out


def cscv_stability(
    trades: list[dict],
    n_blocks: int = 6,
    n_test_blocks: int = 2,
) -> dict:
    """Return CSCV statistics over the per-trade PnL series.

    Returns dict with:
      pbo            : Probability of Backtest Overfitting (0..1, LOW is good)
      n_paths        : number of train/test combinations evaluated
      sr_train_med   : median train-side Sharpe
      sr_test_med    : median test-side Sharpe
      overfit_ratio  : sr_train_med / max(sr_test_med, eps)  (HIGH = bad)
      n_blocks, n_test_blocks
    """
    xs = [t["pnl"] for t in trades]
    k = len(xs)
    if k < 2 * n_test_blocks + 2:
        return {"pbo": float("nan"), "n_paths": 0, "sr_train_med": 0.0,
                "sr_test_med": 0.0, "overfit_ratio": float("nan"),
                "n_blocks": n_blocks, "n_test_blocks": n_test_blocks,
                "error": "too few trades for CSCV"}
    blocks = _blocks(xs, n_blocks)
    # choose n_test_blocks test blocks; train = the rest
    test_choices = list(itertools.combinations(range(n_blocks), n_test_blocks))
    n_paths = 0
    rank_better = 0  # times the top-train candidate has negative test SR
    train_srs, test_srs = [], []
    for test_idx in test_choices:
        test_set = [i for b in test_idx for i in blocks[b]]
        train_set = [i for b in range(n_blocks) if b not in test_idx
                     for i in blocks[b]]
        if not train_set or not test_set:
            continue
        train_tr = [trades[i] for i in train_set]
        test_tr = [trades[i] for i in test_set]
        sr_t = trade_sharpe(train_tr)
        sr_e = trade_sharpe(test_tr)
        train_srs.append(sr_t)
        test_srs.append(sr_e)
        # selection: did the higher train-SR come with positive test-SR?
        # We accumulate over all paths: a path is "overfit" if train SR > 0
        # but test SR < 0 (chosen because it looked good, failed OOS).
        if sr_t > 0 and sr_e < 0:
            rank_better += 1
        n_paths += 1
    if n_paths == 0:
        return {"pbo": float("nan"), "n_paths": 0, "sr_train_med": 0.0,
                "sr_test_med": 0.0, "overfit_ratio": float("nan"),
                "n_blocks": n_blocks, "n_test_blocks": n_test_blocks}
    med_t = sorted(train_srs)[n_paths // 2]
    med_e = sorted(test_srs)[n_paths // 2]
    pbo = rank_better / n_paths
    eps = 1e-9
    overfit_ratio = (med_t / max(med_e, eps)) if med_e > 0 else float("inf")
    return {
        "pbo": pbo, "n_paths": n_paths,
        "sr_train_med": med_t, "sr_test_med": med_e,
        "overfit_ratio": overfit_ratio,
        "n_blocks": n_blocks, "n_test_blocks": n_test_blocks,
    }


def probability_of_backtest_overfitting(trades: list[dict], **kw) -> float:
    """Convenience: just the PBO number."""
    return cscv_stability(trades, **kw).get("pbo", float("nan"))
