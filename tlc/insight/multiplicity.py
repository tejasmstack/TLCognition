"""Family-wise control (spec 02 §4.7). Three families, corrected separately, never pooled."""

import math

FAMILIES = {"F1_chemistry": ("benjamini_hochberg", 0.10), "F2_identity": ("holm", 0.05), "F3_confounds": ("none", 0.10)}


def bh_adjusted(ps: list[float]) -> list[float]:
    m = len(ps)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: ps[i])
    adj = [0.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        val = min(prev, ps[i] * m / rank)
        adj[i] = min(1.0, val)
        prev = adj[i]
    return adj


def holm_adjusted(ps: list[float]) -> list[float]:
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    adj = [0.0] * m
    running = 0.0
    for k, i in enumerate(order):
        running = max(running, ps[i] * (m - k))
        adj[i] = min(1.0, running)
    return adj


def adjust(ps: list[float], family: str) -> tuple[list[float], str]:
    proc, _ = FAMILIES[family]
    if proc == "benjamini_hochberg":
        return bh_adjusted(ps), proc
    if proc == "holm":
        return holm_adjusted(ps), proc
    return list(ps), "none"


def max_family_size(n_units: int, q: float = 0.10) -> int:
    """§4.7 unlock arithmetic: a claim is arithmetically possible ⇔ 2/n! ≤ q/m ⇔ m ≤ q·n!/2."""
    return int(math.floor(q * math.factorial(n_units) / 2.0))


def arithmetically_possible(n_units: int, m: int, q: float = 0.10) -> bool:
    return n_units >= 5 and m <= max_family_size(n_units, q) and m >= 1
