"""Origin row from spotting dots (eval §5 P7; port of the fill-ratio discriminator idea).

Origin = median row of compact dark blobs found in the origin zone of each lane window; the
two-dot rule: refuse (E_NO_ORIGIN) rather than guess when fewer than two lanes show a dot.
Provenance: measured (detected_dots) or refused — never "assumed_plate_bottom" silently.
"""

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

MIN_DOTS = 2
DOT_COMPACT_MIN = 0.60        # fill ratio of the blob within its bbox (ported cut 0.68, loosened
                              # for sub-2px dots at corpus resolution — A-018)
DOT_K = 3.0                   # dot pixels exceed K * sigma_od (per-pixel)


@dataclass(frozen=True)
class OriginEstimate:
    found: bool
    row: float | None
    row_sd: float | None
    n_dots: int
    dot_xy: tuple[tuple[float, float], ...]
    support_sigma: float | None
    reason: str | None


def find_origin(
    od: np.ndarray,
    od_valid: np.ndarray,
    sigma_od_px: float,
    lane_centres: list[float],
    lane_halfwidth: float,
    zone_rows: tuple[int, int],
) -> OriginEstimate:
    """Search the origin zone (rows) in each lane window for one compact dark blob."""
    r0, r1 = int(max(0, zone_rows[0])), int(min(od.shape[0], zone_rows[1]))
    if r1 - r0 < 3:
        return OriginEstimate(False, None, None, 0, (), None, "origin zone empty")
    dots: list[tuple[float, float, float]] = []
    thr = DOT_K * max(sigma_od_px, 1e-9)
    for xc in lane_centres:
        x0, x1 = int(max(0, xc - lane_halfwidth)), int(min(od.shape[1], xc + lane_halfwidth + 1))
        win = od[r0:r1, x0:x1]
        winv = od_valid[r0:r1, x0:x1]
        mask = (win > thr) & winv
        lab, n = ndimage.label(mask)
        best = None
        for i in range(1, n + 1):
            comp = lab == i
            area = int(comp.sum())
            if area < 2:
                continue
            ys, xs = np.nonzero(comp)
            bbox_area = (ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1)
            compact = area / bbox_area
            if compact < DOT_COMPACT_MIN:
                continue
            peak = float(win[comp].max())
            if best is None or peak > best[2]:
                cy = float(np.average(ys, weights=win[comp]))
                cx = float(np.average(xs, weights=win[comp]))
                best = (x0 + cx, r0 + cy, peak)
        if best is not None:
            dots.append(best)
    if len(dots) < MIN_DOTS:
        return OriginEstimate(False, None, None, len(dots), tuple((d[0], d[1]) for d in dots), None,
                              f"origin dots found in {len(dots)} lane(s); at least {MIN_DOTS} required")
    rows = np.array([d[1] for d in dots])
    med = float(np.median(rows))
    sd = float(1.4826 * np.median(np.abs(rows - med))) if rows.size > 2 else float(np.std(rows))
    support = float(np.median([d[2] for d in dots]) / max(sigma_od_px, 1e-9))
    return OriginEstimate(True, med, sd, len(dots), tuple((d[0], d[1]) for d in dots), support, None)
