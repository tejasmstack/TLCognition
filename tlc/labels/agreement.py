"""Inter-reviewer agreement between two :class:`ReviewerTruth` objects — §7.7.4.

Matching: greedy nearest-neighbour within a lane at ``|Δy_frac| <= MATCH_TOL_Y_FRAC``
(0.015, inclusive; ≈ 17 px on a 1104-px plate). Pairs are taken in order of increasing
|Δy| so the closest pairs are matched first; each spot is used at most once.

Rst-equivalent position deltas: when both truths carry an origin and an Rst anchor,
``ΔRst = Δy_frac / (origin_y_frac - anchor_y_frac)`` using the mean of the two truths'
scales. When no anchor is known, ``Δy_frac`` is used as the Rst-equivalent directly
(plate scale, i.e. Rst-per-plate-height = 1). ``position_delta_rst.scale`` records
which was used so the reported tolerance is never ambiguous.

Verdict rule (spec, with the ambiguities resolved as follows):

* ``agreed`` iff lane count equal, lane labels equal, per-lane STRONG spot-count
  delta == 0 in every lane (the spec's "≤ 0" is read as "no difference", since the
  metric is symmetric in a and b), Jaccard ≥ 0.8 over matched/only_a/only_b, and
  max matched Rst-equivalent delta ≤ 0.02. Trivially agreed when neither has spots.
* ``agreed_with_trace_dissent`` when the strict rule fails but the same rule passes
  after dropping every spot of strength ``"trace"`` from both truths, AND lane
  count/labels agree. The spec's wording "matched but a disagreement confined to
  trace spots" — a trace spot in a matched with a non-trace spot in b is treated as
  a trace disagreement (the pair is dropped when a's trace is removed).
* ``disputed`` otherwise. Plate-rejected disagreement is always disputed.

Krippendorff's alpha (nominal) for spot presence is computed on a common grid: each
lane split into cells of ``GRID_STEP_Y_FRAC`` (0.01), each cell coded 1 if the
reviewer placed a spot in it, else 0. Lanes are the union (``max(n_lanes)``); a lane
absent for one reviewer is coded all-zero for that reviewer. Empty-lane plates that
both reviewers coded identically all-zero give ``alpha = 1.0`` by convention (De = 0).
"""

from collections.abc import Sequence
from statistics import mean
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict

from tlc.labels.truth import ReviewerTruth, TruthSpot

MATCH_TOL_Y_FRAC = 0.015
MAX_MATCHED_DELTA_RST = 0.02
JACCARD_MIN = 0.8
GRID_STEP_Y_FRAC = 0.01

Verdict = Literal["agreed", "agreed_with_trace_dissent", "disputed"]


class PositionDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mean: float | None
    max: float | None
    n: int


class MatchedPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lane_index: int
    y_frac_a: float
    y_frac_b: float
    strength_a: str
    strength_b: str
    dy_frac: float


class AgreementReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lane_count_agree: bool
    lane_label_agree: str  # "k/n"
    lane_label_agree_k: int
    lane_label_agree_n: int
    spot_count_delta_per_lane: list[int]  # count_a - count_b per lane (over max lanes)
    strong_count_delta_per_lane: list[int]
    matched: int
    only_a: int
    only_b: int
    jaccard: float
    position_delta_y_frac: PositionDelta
    position_delta_rst: PositionDelta
    position_scale: Literal["rst_anchor", "plate_y_frac"]
    origin_delta_frac: float | None
    verdict: Verdict
    krippendorff_alpha_positions: float | None
    match_tolerance_y_frac: float = MATCH_TOL_Y_FRAC
    pairs: list[MatchedPair]

    @property
    def agreed(self) -> bool:
        return self.verdict != "disputed"


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


def match_spots(
    a: Sequence[TruthSpot], b: Sequence[TruthSpot], tol: float = MATCH_TOL_Y_FRAC
) -> tuple[list[tuple[TruthSpot, TruthSpot]], list[TruthSpot], list[TruthSpot]]:
    """Greedy nearest-neighbour matching within lanes; ``|dy| <= tol`` inclusive."""
    cands: list[tuple[float, int, int]] = []
    for i, sa in enumerate(a):
        for j, sb in enumerate(b):
            if sa.lane_index != sb.lane_index:
                continue
            dy = abs(sa.y_frac - sb.y_frac)
            if dy <= tol + 1e-12:
                cands.append((dy, i, j))
    cands.sort()
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs: list[tuple[TruthSpot, TruthSpot]] = []
    for _, i, j in cands:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((a[i], b[j]))
    only_a = [s for i, s in enumerate(a) if i not in used_a]
    only_b = [s for j, s in enumerate(b) if j not in used_b]
    pairs.sort(key=lambda p: (p[0].lane_index, p[0].y_frac, p[1].y_frac))
    return pairs, only_a, only_b


def _rst_scale(a: ReviewerTruth, b: ReviewerTruth) -> tuple[float, str]:
    scales = []
    for t in (a, b):
        if t.origin_y_frac is not None and t.anchor_y_frac is not None:
            d = t.origin_y_frac - t.anchor_y_frac
            if d > 1e-9:
                scales.append(1.0 / d)
    if len(scales) == 2:
        return mean(scales), "rst_anchor"
    return 1.0, "plate_y_frac"


def _pdelta(vals: list[float]) -> PositionDelta:
    if not vals:
        return PositionDelta(mean=None, max=None, n=0)
    return PositionDelta(mean=float(np.mean(vals)), max=float(max(vals)), n=len(vals))


def _per_lane_counts(spots: Sequence[TruthSpot], n: int, strong_only: bool) -> list[int]:
    out = [0] * n
    for s in spots:
        if s.lane_index < n and (not strong_only or s.strength == "strong"):
            out[s.lane_index] += 1
    return out


def _spot_rule(
    sa: Sequence[TruthSpot], sb: Sequence[TruthSpot], n_lanes: int, scale: float
) -> bool:
    pairs, oa, ob = match_spots(sa, sb)
    if not sa and not sb:
        return True
    denom = len(pairs) + len(oa) + len(ob)
    jac = len(pairs) / denom if denom else 1.0
    strong_ok = _per_lane_counts(sa, n_lanes, True) == _per_lane_counts(sb, n_lanes, True)
    max_d = max((abs(p.y_frac - q.y_frac) * scale for p, q in pairs), default=0.0)
    return strong_ok and jac >= JACCARD_MIN and max_d <= MAX_MATCHED_DELTA_RST + 1e-12


# ---------------------------------------------------------------------------
# Krippendorff's alpha
# ---------------------------------------------------------------------------


def krippendorff_alpha_nominal(data: Sequence[Sequence[Any]]) -> float | None:
    """Krippendorff's alpha, nominal metric.

    ``data[coder][unit]`` holds a category label or ``None`` for a missing value.
    Standard coincidence-matrix formulation (Krippendorff 2011):

        o_ck  = sum over units u with m_u >= 2 of  (#pairs (c,k) in u) / (m_u - 1)
        n_c   = sum_k o_ck ;  n = sum_c n_c
        D_o   = (1/n) * sum_{c != k} o_ck
        D_e   = (1/(n(n-1))) * sum_{c != k} n_c n_k
        alpha = 1 - D_o / D_e

    Units with fewer than two coded values are dropped. Returns ``None`` when n < 2.
    Returns ``1.0`` when D_e == 0 (every value identical: perfect, trivially).
    """
    if not data:
        return None
    n_units = max(len(row) for row in data)
    cats: dict[Any, int] = {}
    for row in data:
        for v in row:
            if v is not None and v not in cats:
                cats[v] = len(cats)
    k = len(cats)
    if k == 0:
        return None
    o = np.zeros((k, k), dtype=float)
    for u in range(n_units):
        vals = [cats[row[u]] for row in data if u < len(row) and row[u] is not None]
        m = len(vals)
        if m < 2:
            continue
        for i in range(m):
            for j in range(m):
                if i != j:
                    o[vals[i], vals[j]] += 1.0 / (m - 1)
    n_c = o.sum(axis=1)
    n = n_c.sum()
    if n < 2:
        return None
    d_o = (o.sum() - np.trace(o)) / n
    d_e = (n_c.sum() ** 2 - (n_c**2).sum()) / (n * (n - 1))
    if d_e <= 0:
        return 1.0
    return float(1.0 - d_o / d_e)


def presence_grid(
    spots: Sequence[TruthSpot], n_lanes: int, step: float = GRID_STEP_Y_FRAC
) -> list[int]:
    """Flattened per-lane presence grid (lane-major); cell = min(floor(y/step), cells-1)."""
    cells = int(round(1.0 / step))
    grid = [0] * (n_lanes * cells)
    for s in spots:
        if s.lane_index >= n_lanes:
            continue
        c = min(int(s.y_frac / step), cells - 1)
        grid[s.lane_index * cells + c] = 1
    return grid


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def compare_truths(a: ReviewerTruth, b: ReviewerTruth) -> AgreementReport:
    n_lanes = max(a.n_lanes, b.n_lanes)
    lane_count_agree = a.n_lanes == b.n_lanes
    n_lab = max(len(a.lane_labels), len(b.lane_labels))
    k_lab = sum(
        1
        for i in range(n_lab)
        if i < len(a.lane_labels)
        and i < len(b.lane_labels)
        and a.lane_labels[i] == b.lane_labels[i]
    )
    labels_agree = lane_count_agree and k_lab == n_lab

    scale, scale_kind = _rst_scale(a, b)
    pairs, only_a, only_b = match_spots(a.spots, b.spots)
    denom = len(pairs) + len(only_a) + len(only_b)
    jaccard = len(pairs) / denom if denom else 1.0
    dys = [abs(p.y_frac - q.y_frac) for p, q in pairs]

    origin_delta = None
    if a.origin_y_frac is not None and b.origin_y_frac is not None:
        origin_delta = abs(a.origin_y_frac - b.origin_y_frac)

    structural = lane_count_agree and labels_agree and a.plate_rejected == b.plate_rejected
    verdict: Verdict
    if structural and _spot_rule(a.spots, b.spots, n_lanes, scale):
        verdict = "agreed"
    elif structural and _spot_rule(
        [s for s in a.spots if s.strength != "trace"],
        [s for s in b.spots if s.strength != "trace"],
        n_lanes,
        scale,
    ):
        verdict = "agreed_with_trace_dissent"
    else:
        verdict = "disputed"

    alpha = krippendorff_alpha_nominal(
        [presence_grid(a.spots, n_lanes), presence_grid(b.spots, n_lanes)]
    )

    ca, cb = _per_lane_counts(a.spots, n_lanes, False), _per_lane_counts(b.spots, n_lanes, False)
    sa, sb = _per_lane_counts(a.spots, n_lanes, True), _per_lane_counts(b.spots, n_lanes, True)
    return AgreementReport(
        lane_count_agree=lane_count_agree,
        lane_label_agree=f"{k_lab}/{n_lab}",
        lane_label_agree_k=k_lab,
        lane_label_agree_n=n_lab,
        spot_count_delta_per_lane=[x - y for x, y in zip(ca, cb, strict=True)],
        strong_count_delta_per_lane=[x - y for x, y in zip(sa, sb, strict=True)],
        matched=len(pairs),
        only_a=len(only_a),
        only_b=len(only_b),
        jaccard=jaccard,
        position_delta_y_frac=_pdelta(dys),
        position_delta_rst=_pdelta([d * scale for d in dys]),
        position_scale=scale_kind,
        origin_delta_frac=origin_delta,
        verdict=verdict,
        krippendorff_alpha_positions=alpha,
        pairs=[
            MatchedPair(
                lane_index=p.lane_index,
                y_frac_a=p.y_frac,
                y_frac_b=q.y_frac,
                strength_a=p.strength,
                strength_b=q.strength,
                dy_frac=abs(p.y_frac - q.y_frac),
            )
            for p, q in pairs
        ],
    )
