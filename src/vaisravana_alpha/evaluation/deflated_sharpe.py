"""Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

Standard Sharpe rewards a strategy that was SELECTED as the best among many
trials — exactly what an autonomous loop does (it picks the best candidate).
DSR corrects for:
  * selection bias (multiple testing — N independent trials),
  * non-Normal PnL distribution (skewness + kurtosis),
and returns a PROBABILITY that the strategy's Sharpe is positive in truth
(not just by luck of the draw).

We keep the standard (non-annualized, trade-normalized) Sharpe from metrics as
the input, then deflate it. This makes KEEP/REJECT statistically defensible
instead of "n>=20 looked better".
"""
from __future__ import annotations
import math
from .metrics import _norm_cdf


def _moments(xs: list[float]) -> tuple[float, float, float, float]:
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return mean, sd, 0.0, 0.0
    skew = sum((x - mean) ** 3 for x in xs) / (n * sd ** 3)
    kurt = sum((x - mean) ** 4 for x in xs) / (n * sd ** 4) - 3.0
    return mean, sd, skew, kurt


def deflated_sharpe(
    sr_obs: float,
    n_obs: int,
    n_trials: int = 1,
    sr_star: float = 0.0,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    r"""Probability that the true Sharpe exceeds the benchark sr_star.

    Args:
      sr_obs   : observed (trade-normalized) Sharpe of the candidate.
      n_obs    : number of independent observations (trades).
      n_trials : number of strategies tested (selection bias). The loop's
                 effective N_trials = number of candidates ever tried + live.
                 Use >= number of distinct changes evaluated.
      sr_star  : benchmark Sharpe we must beat (0 = "is it positive at all").
      skew,kurt: PnL distribution moments (from the trade series).
    Returns:
      p_value in [0,1]: Prob(SR_true > sr_star). Higher = more trustworthy.
    """
    if n_obs < 2:
        return 0.0
    if n_trials < 1:
        n_trials = 1
    # expected max Sharpe across n_trials (approximation, Bailety & LdP 2014)
    # E[max] ≈ sr_star + (1 - gamma) * z * (1/sqrt(n_obs))
    # we compute the deflated statistic for sr_obs directly.
    gamma = 0.5772156649  # Euler-Mascheroni
    # variance of the Sharpe estimator under non-Normality
    var_sr = (1.0 - skew * sr_obs + (kurt - 1.0) / 4.0 * sr_obs ** 2) / n_obs
    if var_sr <= 0:
        var_sr = 1.0 / n_obs
    # point estimate of the DEFLATED sharpe (corrected for selection + shape)
    sr_defl = (sr_obs - sr_star) / math.sqrt(var_sr)
    # selection-bias correction: shrink toward 0 as n_trials grows
    sel = math.sqrt(2.0 * math.log(n_trials)) if n_trials > 1 else 0.0
    sr_defl -= sel / math.sqrt(n_obs)
    # p-value that true SR > sr_star
    p = _norm_cdf(sr_defl)
    return max(0.0, min(1.0, p))


def dsr_from_trades(trades: list[dict], n_trials: int = 1) -> dict:
    """Convenience: compute DSR straight from a trade list (pure stdlib)."""
    from .metrics import trade_sharpe  # noqa: F401
    xs = [t["pnl"] for t in trades]
    n = len(xs)
    if n < 2:
        return {"dsr_p": 0.0, "sr_obs": 0.0, "n": n, "n_trials": n_trials}
    sr = trade_sharpe(trades)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    skew = sum((x - mean) ** 3 for x in xs) / (n * sd ** 3) if sd else 0.0
    kurt = sum((x - mean) ** 4 for x in xs) / (n * sd ** 4) - 3.0 if sd else 0.0
    p = deflated_sharpe(sr, n, n_trials=n_trials, skew=skew, kurt=kurt + 3.0)
    return {"dsr_p": p, "sr_obs": sr, "n": n, "n_trials": n_trials,
            "skew": skew, "kurt": kurt + 3.0}
