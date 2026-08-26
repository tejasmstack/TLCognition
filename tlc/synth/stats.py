"""Corpus-statistics measurement for generator calibration (Gate 1).

These functions are applied IDENTICALLY to real and synthetic plates; Gate 1 compares their
outputs. They are calibration instruments, not the measurement pipeline (Phase 3 builds that
against the specs; sharing code between generator calibration and the pipeline is deliberately
avoided so the generator cannot be shaped to flatter the pipeline).

Estimator choices the evaluation report leaves unstated (ASSUMPTIONS A-007): the empty band is
the horizontal in-plate band (of 8) with the lowest p95 OD residual; sigma is the plain standard
deviation over that band's unclipped pixels (unmasked, matching the report's 0.01881 figure for
plate P33), with the MAD-based robust value reported alongside.
"""

from dataclasses import asdict, dataclass

import numpy as np
from scipy import ndimage
from skimage.filters import threshold_otsu


@dataclass(frozen=True)
class PlateStats:
    plate_found: bool
    plate_area_fraction: float
    clip_fraction: float
    base_green_norm: float
    illum_swing: float
    empty_band_sd_od: float
    empty_band_mad_od: float
    empty_band_row_range: tuple[int, int]
    red_over_green: float
    blue_over_green: float
    background_rgb: tuple[float, float, float]
    tonal_range_p99_p01: float

    def to_dict(self) -> dict:
        return asdict(self)


def plate_mask(green_u8: np.ndarray, erode_px: int = 2) -> np.ndarray:
    """Otsu on green -> largest component -> fill holes -> erode to stay off the edge blend."""
    t = threshold_otsu(green_u8)
    mask = green_u8 >= t
    lab, n = ndimage.label(mask)
    if n == 0:
        return np.zeros_like(mask)
    largest = 1 + int(np.argmax(ndimage.sum_labels(mask, lab, index=range(1, n + 1))))
    mask = ndimage.binary_fill_holes(lab == largest)
    if erode_px > 0:
        mask = ndimage.binary_erosion(mask, iterations=erode_px)
    return mask


def fit_poly3_surface(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Least-squares 3rd-order 2-D polynomial (10 coefficients) fit to values over mask.

    Returns the fitted surface over the full frame. Coordinates normalised to [-1, 1] for
    conditioning. Deterministic (lstsq on a fixed design matrix).
    """
    h, w = values.shape
    yy, xx = np.mgrid[0:h, 0:w]
    xn = (xx / max(w - 1, 1)) * 2.0 - 1.0
    yn = (yy / max(h - 1, 1)) * 2.0 - 1.0

    def design(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return np.stack(
            [np.ones_like(x), x, y, x * x, x * y, y * y, x**3, x * x * y, x * y * y, y**3],
            axis=-1,
        )

    a = design(xn[mask], yn[mask])
    coef, *_ = np.linalg.lstsq(a, values[mask], rcond=None)
    return design(xn, yn) @ coef


def measure_plate_stats(rgb: np.ndarray, n_bands: int = 8, min_band_px: int = 200) -> PlateStats:
    """Measure the Gate 1 calibration statistics on one plate image (HxWx3 or HxWx4 uint8)."""
    img = rgb[:, :, :3].astype(np.float64)
    green_u8 = rgb[:, :, 1]
    mask = plate_mask(green_u8)
    nan = float("nan")
    if mask.mean() < 0.05:
        return PlateStats(False, float(mask.mean()), nan, nan, nan, nan, nan, (0, 0), nan, nan, (nan, nan, nan), nan)

    g_norm = img[:, :, 1] / 255.0
    unclipped = mask & (green_u8 < 255)

    # Illumination surface on unclipped in-plate pixels only (clipped pixels have unknown truth).
    surface = fit_poly3_surface(g_norm, unclipped)
    s_in = surface[mask]
    swing = float((s_in.max() - s_in.min()) / s_in.max())

    # OD residual field (defined where unclipped and positive).
    valid = unclipped & (g_norm > 1e-3) & (surface > 1e-3)
    od = np.full(g_norm.shape, np.nan)
    od[valid] = np.log10(surface[valid] / g_norm[valid])

    # Empty band: split the plate's row extent into n_bands; pick lowest p95(OD).
    rows = np.where(mask.any(axis=1))[0]
    edges = np.linspace(rows[0], rows[-1] + 1, n_bands + 1).astype(int)
    best, best_p95 = None, np.inf
    for i in range(n_bands):
        band = od[edges[i] : edges[i + 1]]
        vals = band[np.isfinite(band)]
        if vals.size < min_band_px:
            continue
        p95 = float(np.percentile(vals, 95))
        if p95 < best_p95:
            best_p95, best = p95, i
    if best is None:
        sd = mad = nan
        band_range = (0, 0)
    else:
        vals = od[edges[best] : edges[best + 1]]
        vals = vals[np.isfinite(vals)]
        sd = float(np.std(vals))
        mad = float(1.4826 * np.median(np.abs(vals - np.median(vals))))
        band_range = (int(edges[best]), int(edges[best + 1]))

    # Channel ratios in-plate (unclipped, away from black).
    ok = unclipped & (green_u8 > 30)
    rg = float(np.median(img[:, :, 0][ok] / np.maximum(img[:, :, 1][ok], 1)))
    bg = float(np.median(img[:, :, 2][ok] / np.maximum(img[:, :, 1][ok], 1)))

    bg_mask = ~ndimage.binary_dilation(mask, iterations=3)
    if bg_mask.sum() >= 50:
        bg_rgb = tuple(float(np.median(img[:, :, c][bg_mask])) for c in range(3))
    else:
        bg_rgb = (nan, nan, nan)

    g_in = green_u8[mask].astype(np.float64)
    return PlateStats(
        plate_found=True,
        plate_area_fraction=float(mask.mean()),
        clip_fraction=float(np.mean(green_u8[mask] >= 255)),
        base_green_norm=float(np.median(g_norm[unclipped])) if unclipped.any() else nan,
        illum_swing=swing,
        empty_band_sd_od=sd,
        empty_band_mad_od=mad,
        empty_band_row_range=band_range,
        red_over_green=rg,
        blue_over_green=bg,
        background_rgb=bg_rgb,
        tonal_range_p99_p01=float(np.percentile(g_in, 99) - np.percentile(g_in, 1)),
    )
