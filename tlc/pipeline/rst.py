"""Rst: position relative to the standard-lane reference spot (F2), with its interval.

Rst = (y_origin - y_spot) / (y_origin - y_anchor),  D = y_origin - y_anchor
Delta method (spec 01 §5.3), carrying the variances of spot, anchor and origin:
Var(Rst) = (1/D^2) [ Var(y_s) + Rst^2 Var(y_r) + (Rst-1)^2 Var(y_0) ]
and the published variance budget (spot / origin / reference fractions summing to 1).
Never Rf: there is no solvent front on this corpus (C5).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RstEstimate:
    value: float
    sd: float
    ci90: tuple[float, float]
    budget: dict[str, float]   # fractions: spot, origin, reference
    method: str = "(y_origin-y_spot)/(y_origin-y_anchor)"


def rst_with_interval(
    y_spot: float, var_spot: float, y_anchor: float, var_anchor: float, y_origin: float, var_origin: float
) -> RstEstimate | None:
    d = y_origin - y_anchor
    if abs(d) < 1e-9:
        return None
    r = (y_origin - y_spot) / d
    v_s = var_spot / (d * d)
    v_r = (r * r) * var_anchor / (d * d)
    v_o = ((r - 1.0) ** 2) * var_origin / (d * d)
    var = v_s + v_r + v_o
    tot = max(var, 1e-18)
    sd = tot**0.5
    return RstEstimate(
        value=float(r),
        sd=float(sd),
        ci90=(float(r - 1.645 * sd), float(r + 1.645 * sd)),
        budget={"spot": float(v_s / tot), "origin": float(v_o / tot), "reference": float(v_r / tot)},
    )


def combine_position_variance(within_var: float, between_var: float, k_eff: float) -> float:
    """Rubin's rules over the config ensemble (spec 01 §5.2): W + (1 + 1/K_eff) B."""
    return float(within_var + (1.0 + 1.0 / max(k_eff, 1.0)) * between_var)
