"""Rank estimators + permutation inference (spec 02 §4.1–4.3). Pearson is not provided."""

import itertools
import math
from functools import lru_cache

import numpy as np
from scipy import stats

EXACT_MAX_N = 9
MC_B = 100_000


def spearman(x, y) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return float("nan")
    return float(stats.spearmanr(x, y).statistic)


def kendall(x, y) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return float("nan")
    return float(stats.kendalltau(x, y, variant="b").statistic)


def tie_fraction(v) -> float:
    v = np.asarray(v)
    return 1.0 - len(np.unique(v)) / len(v) if len(v) else 0.0


def choose_estimator(y, is_count: bool) -> str:
    """§4.1 choice rule: Kendall for counts or ≥20% ties (and n ≤ 7 generally); Spearman otherwise."""
    return "kendall" if is_count or tie_fraction(y) >= 0.20 or len(y) <= 7 else "spearman"


def _stat(name: str):
    return kendall if name == "kendall" else spearman


def _rank(v) -> np.ndarray:
    return stats.rankdata(np.asarray(v, float))


def _spearman_all_permutations(rx: np.ndarray, ry: np.ndarray) -> np.ndarray:
    """Exact null for Spearman: rho over every permutation of y, vectorised (Pearson on ranks, which
    is exactly Spearman and stays correct under ties)."""
    n = len(rx)
    perms = np.array(list(itertools.permutations(range(n))), dtype=np.int16)
    Y = ry[perms]
    xc = rx - rx.mean()
    Yc = Y - Y.mean(axis=1, keepdims=True)
    den = np.linalg.norm(xc) * np.linalg.norm(Yc, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, (Yc @ xc) / np.where(den > 0, den, 1.0), np.nan)


@lru_cache(maxsize=64)
def _cached_null(estimator: str, rx_key: tuple, ry_key: tuple) -> tuple:
    """The permutation null depends only on the two rank vectors, so identical designs (a shuffle
    battery, a repeated cohort) pay for the enumeration once."""
    rx, ry = np.array(rx_key, float), np.array(ry_key, float)
    if estimator == "spearman":
        return tuple(_spearman_all_permutations(rx, ry))
    f = _stat(estimator)
    return tuple(f(rx, ry[list(p)]) for p in itertools.permutations(range(len(rx))))


def null_distribution(x, y, estimator: str = "spearman", rng: np.random.Generator | None = None) -> tuple[np.ndarray, str]:
    """Exact enumeration of all n! permutations for n ≤ 9; otherwise B=100k Monte-Carlo."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x)
    if n <= EXACT_MAX_N:
        rx, ry = _rank(x), _rank(y)
        return np.array(_cached_null(estimator, tuple(rx), tuple(ry)), float), "exact"
    f = _stat(estimator)
    rng = rng or np.random.Generator(np.random.PCG64(0))
    return np.array([f(x, rng.permutation(y)) for _ in range(MC_B)]), "monte_carlo"


def permutation_p(x, y, estimator: str = "spearman", sidedness: str = "two_sided", expected_sign: str | None = None,
                  rng: np.random.Generator | None = None) -> dict:
    """p by permutation. Exact for n ≤ 9; add-one estimator (Phipson & Smyth) for MC. Never 0."""
    f = _stat(estimator)
    obs = f(x, y)
    if not np.isfinite(obs):
        return {"statistic": None, "p": None, "method": None, "permutations": 0}
    null, method = null_distribution(x, y, estimator, rng)
    null = null[np.isfinite(null)]
    if sidedness == "one_sided":
        sgn = -1.0 if expected_sign == "negative" else 1.0
        hits = int(np.sum(sgn * null >= sgn * obs - 1e-12))
    else:
        hits = int(np.sum(np.abs(null) >= abs(obs) - 1e-12))
    B = len(null)
    p = hits / B if method == "exact" else (1 + hits) / (1 + B)
    return {"statistic": float(obs), "p": float(p), "method": f"{estimator}_{method}_permutation", "permutations": B}


def exact_floor(n: int, sidedness: str = "two_sided") -> float:
    """Minimum attainable p for untied ranks: 2/n! two-sided, 1/n! one-sided."""
    return (2.0 if sidedness == "two_sided" else 1.0) / math.factorial(n)


def rho_required(n: int, alpha: float, sidedness: str = "two_sided") -> float | None:
    """Smallest |ρ| (Spearman, untied) attaining p ≤ alpha at this n; None if unattainable (§4.3)."""
    if n < 3 or n > EXACT_MAX_N:
        return None
    x = np.arange(n, dtype=float)
    null, _ = null_distribution(x, x, "spearman")
    side = np.abs(null) if sidedness == "two_sided" else null
    for v in np.sort(np.unique(np.round(np.abs(null), 6))):        # smallest |rho| that still clears alpha
        if np.mean(side >= v - 1e-9) <= alpha:
            return float(v)
    return None


def permutation_inverted_interval(x, y, estimator: str = "spearman", level: float = 0.95) -> tuple[float, float] | None:
    """Exact-rank interval for ρ at n ≤ 10 (§4.5): the set of ρ0 not rejected by the permutation test,
    approximated by shifting the observed statistic by the null's central quantiles."""
    r = _stat(estimator)(x, y)
    if not np.isfinite(r):
        return None
    null, _ = null_distribution(x, y, estimator)
    lo, hi = np.quantile(null, [(1 - level) / 2, 1 - (1 - level) / 2])
    return (max(-1.0, float(r + lo)), min(1.0, float(r + hi)))
