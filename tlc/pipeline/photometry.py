"""Photometry: OD conversion, the noise unit, densitograms (Phase 3).

The one number everything downstream trusts is sigma_od, and it is measured ONCE, on the raw
analysable band, with no background model and no spot masking (D-008; F4). OD = log10(I0/I)
with I0 from a named background model (D-007: poly3 primary); Kubelka-Munk is not permitted in
this regime (F5).

Pure module: numpy/scipy in, dataclasses out. Clipped pixels (raw green >= CLIP_LEVEL) never
inform a fit and are excluded from the OD validity mask (F1).
"""

from dataclasses import dataclass

import numpy as np

from tlc.pipeline.background import fit_background

LANE_HALFWIDTH_FRAC = 0.275  # eval: lane collapse width = 55% of nominal pitch
SIGMA_METHOD = "mad_diff_1.4826_prespot"  # D-008


@dataclass(frozen=True)
class ODResult:
    od: np.ndarray              # float64; OD = log10(I0/I); 0 where invalid
    od_valid: np.ndarray        # bool; True where OD is defined
    i0: np.ndarray              # the fitted illumination surface
    background_model: str
    background_radius_px: int


def sigma_od_prespot(
    green: np.ndarray, valid: np.ndarray, analysable_rows: tuple[int, int]
) -> float:
    """The noise unit (D-008): robust sd of horizontal first differences of log10(green),
    over pixel pairs that are both valid (in-plate, unclipped at source), inside the
    analysable band. `green` is rectified, float, in [0, 1].

    Background-free (a smooth illumination surface differentiates to ~0 at 1 px), unmasked
    (spots occupy few pixel pairs and the median ignores them without any masking decision),
    parameter-free (no radius anywhere). Measured once, before anything else sees the image.
    """
    r0, r1 = analysable_rows
    g = green[r0:r1]
    ok = valid[r0:r1] & (g > 1e-3)
    log_g = np.where(ok, np.log10(np.maximum(g, 1e-3)), np.nan)
    d = log_g[:, 1:] - log_g[:, :-1]
    d = d[np.isfinite(d)]
    if d.size < 100:
        return float("nan")
    mad = float(np.median(np.abs(d - np.median(d))))
    return max(1.4826 * mad / np.sqrt(2.0), 1e-6)


def compute_od(
    green: np.ndarray,
    valid: np.ndarray,
    model: str,
    radius_px: int,
) -> ODResult:
    """OD = log10(I0/I) against the named background model. `green` rectified float in [0,1];
    `valid` must already exclude source-clipped pixels (tlc.pipeline.prep)."""
    weight = valid & (green > 1e-3)
    i0 = fit_background(model, green, weight, radius_px)
    od_valid = weight & (i0 > 0)
    od = np.zeros_like(green)
    od[od_valid] = np.log10(i0[od_valid] / np.maximum(green[od_valid], 1e-4))
    return ODResult(od, od_valid, i0, model, radius_px)


@dataclass(frozen=True)
class Densitogram:
    lane_index: int
    profile: np.ndarray         # float64, len = plate height; mean OD over valid lane columns
    n_valid_columns: np.ndarray  # int per row
    x_lo: int
    x_hi: int
    sampling: str


def lane_densitogram(odr: ODResult, lane_index: int, x_center: float, lane_pitch: float) -> Densitogram:
    """Mean OD over valid pixels in [x_center - hw, x_center + hw] per row (spec 03 §7.3.3)."""
    hw = LANE_HALFWIDTH_FRAC * lane_pitch
    h, w = odr.od.shape
    x_lo = max(0, int(round(x_center - hw)))
    x_hi = min(w, int(round(x_center + hw)) + 1)
    band = odr.od[:, x_lo:x_hi]
    bandv = odr.od_valid[:, x_lo:x_hi]
    n = bandv.sum(axis=1)
    prof = np.where(n > 0, (band * bandv).sum(axis=1) / np.maximum(n, 1), 0.0)
    return Densitogram(
        lane_index=lane_index,
        profile=prof,
        n_valid_columns=n.astype(np.int64),
        x_lo=x_lo,
        x_hi=x_hi,
        sampling=f"mean over valid px in [{x_lo}, {x_hi})",
    )


def strongest_peak_row(profile: np.ndarray, rows: tuple[int, int]) -> float | None:
    """Sub-pixel row of the strongest maximum in [rows) — parabolic refinement of the argmax.

    Phase 3 helper for the metamorphic gate; the real detector (ensemble + EMG) is Phases 4-5.
    """
    r0, r1 = int(rows[0]), int(rows[1])
    seg = profile[r0:r1]
    if seg.size < 3 or not np.isfinite(seg).all() or float(seg.max()) <= 0:
        return None
    i = int(np.argmax(seg))
    if i == 0 or i == seg.size - 1:
        return float(r0 + i)
    y0, y1, y2 = seg[i - 1], seg[i], seg[i + 1]
    denom = y0 - 2 * y1 + y2
    delta = 0.0 if abs(denom) < 1e-12 else 0.5 * (y0 - y2) / denom
    return float(r0 + i + np.clip(delta, -0.5, 0.5))


def relative_position(y_spot: float, y_origin: float, y_anchor: float) -> float:
    """Rst arithmetic: migration relative to the anchor spot (F2). Pure function of rows."""
    denom = y_origin - y_anchor
    if abs(denom) < 1e-9:
        return float("nan")
    return float((y_origin - y_spot) / denom)
