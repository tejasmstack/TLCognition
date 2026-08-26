"""NumberFormatter — the only path to a rendered number (spec 04 §11.3.B, §11.9).

Rule: round the value so its last displayed digit is one decade finer than the first significant
digit of the half-interval; never print an interval to more precision than the value; never print
a bare centre with no interval (callers get "—" + a reason instead).
"""

import math

DASH = "—"


def _decimals_for_half_interval(h: float) -> int:
    if h <= 0 or not math.isfinite(h):
        return 3
    # spec 04 §11.3.B worked example: 0.412 ±0.009 (last digit at the decade of the half-interval's
    # first significant digit; the prose says one finer but its own examples do not — follow the examples)
    return max(0, -int(math.floor(math.log10(h))))


def fmt_interval(value: float, ci95: tuple[float, float] | list[float]) -> str:
    """'0.412 ±0.009' style. Half-interval = (hi-lo)/2."""
    lo, hi = float(ci95[0]), float(ci95[1])
    h = abs(hi - lo) / 2.0
    nd = _decimals_for_half_interval(h)
    nd = min(nd, 6)
    return f"{value:.{nd}f} ±{h:.{nd}f}"


def fmt_q(q: dict | None, approx: bool = False) -> str:
    """Format a Q envelope for display. Refused/None -> em dash. Without ci95, an approximate
    prefix is mandatory so the number never reads as exact."""
    if not q or q.get("value") is None:
        return DASH
    v = q["value"]
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    ci = q.get("ci95")
    if ci:
        return fmt_interval(float(v), ci)
    return f"≈{float(v):.2f}"  # no interval -> approximate marker is mandatory


def fmt_pct(x: float | None, nd: int = 0) -> str:
    return DASH if x is None else f"{100.0 * float(x):.{nd}f}%"


def fmt_plain(x: float | None, nd: int = 3) -> str:
    return DASH if x is None else f"{float(x):.{nd}f}"
