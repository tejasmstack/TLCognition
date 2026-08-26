"""Illumination (I0) models — the background axis of the Phase 4 ensemble.

Primary: poly3 (D-007). Ensemble members: iterative-masked box blur (ported from
tlc-spec-impl/tlccore/normalize.py:143-153 per D-005), Gaussian, median, rolling-ball
(eval report §2, M3-M7). Kubelka-Munk is deliberately absent (F5: diverges on faint
quenching zones — 9 spots where others found 2).

Contract: fit_background(green, weight, radius_px) -> I0 (float64, same shape, > 0).
`green` is the rectified normalised green channel in [0, 1]; `weight` marks pixels the fit may
trust (valid AND unclipped). Clipped pixels never inform any fit (F1). All models are
deterministic; none may consume the noise unit (F4: sigma is measured independently, before and
outside of any of this).
"""

import numpy as np
from scipy import ndimage
from skimage.restoration import rolling_ball as _sk_rolling_ball

I0_FLOOR = 1e-3
ITER_N = 3            # ported: BG_ITERS (normalize.py)
ITER_MASK_K = 0.5     # ported: keep = g > bg - 0.5 * mean|bg - g| (normalize.py:150)


def _poly3_design(h: int, w: int) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    x = (xx / max(w - 1, 1)) * 2.0 - 1.0
    y = (yy / max(h - 1, 1)) * 2.0 - 1.0
    return np.stack(
        [np.ones_like(x), x, y, x * x, x * y, y * y, x**3, x * x * y, x * y * y, y**3],
        axis=-1,
    )


def poly3(green: np.ndarray, weight: np.ndarray, radius_px: int = 0) -> np.ndarray:
    """3rd-order 2-D polynomial surface, least squares over trusted pixels. 10 coefficients."""
    design = _poly3_design(*green.shape)
    coef, *_ = np.linalg.lstsq(design[weight], green[weight], rcond=None)
    return np.maximum(design @ coef, I0_FLOOR)


def _normalized_blur(green: np.ndarray, weight: np.ndarray, blur) -> np.ndarray:
    """Weighted smoothing: untrusted pixels contribute nothing, holes are filled smoothly."""
    w = weight.astype(np.float64)
    num = blur(green * w)
    den = blur(w)
    out = np.where(den > 1e-9, num / np.maximum(den, 1e-9), 0.0)
    if (den <= 1e-9).any():
        # a hole larger than the kernel: fall back to the global trusted median there
        out = np.where(den > 1e-9, out, float(np.median(green[weight])) if weight.any() else I0_FLOOR)
    return out


def gaussian(green: np.ndarray, weight: np.ndarray, radius_px: int) -> np.ndarray:
    """Single-pass heavy Gaussian blur as the lighting estimate (eval M4). sigma = radius/2 (A-012)."""
    sig = max(1.0, radius_px / 2.0)
    out = _normalized_blur(green, weight, lambda a: ndimage.gaussian_filter(a, sig))
    return np.maximum(out, I0_FLOOR)


def iterative_masked(green: np.ndarray, weight: np.ndarray, radius_px: int) -> np.ndarray:
    """Blur, mark pixels much darker than the guess as spots, refill, repeat (eval M5; ported).

    Box blur (not median — tlc-spec-impl trap 12.2), kernel = 2*radius+1. Converges to "what
    the plate would look like empty". The masking here shapes I0 only; the noise unit is NEVER
    measured on this model's residual (F4).
    """
    size = 2 * max(1, radius_px) + 1
    blur = lambda a: ndimage.uniform_filter(a, size=size, mode="nearest")  # noqa: E731
    bg = _normalized_blur(green, weight, blur)
    filled = np.where(weight, green, bg)
    for _ in range(ITER_N - 1):
        keep = filled > bg - ITER_MASK_K * float(np.abs(bg - filled).mean())
        filled = np.where(keep & weight, green, bg)
        bg = blur(filled)
    return np.maximum(bg, I0_FLOOR)


def median(green: np.ndarray, weight: np.ndarray, radius_px: int) -> np.ndarray:
    """Large median filter (eval M7); untrusted pixels pre-filled from the Gaussian estimate."""
    size = 2 * max(1, radius_px) + 1
    filled = np.where(weight, green, gaussian(green, weight, radius_px))
    return np.maximum(ndimage.median_filter(filled, size=size, mode="nearest"), I0_FLOOR)


def rolling_ball(green: np.ndarray, weight: np.ndarray, radius_px: int) -> np.ndarray:
    """Sternberg rolling ball under the inverted surface (eval M3; kept for the ensemble even
    though it sits 3.3 px off the M4-M7 cluster — disagreement is information).

    Run in the ImageJ 8-bit convention (0-255 grays): skimage's ball couples intensity and
    spatial units, and on a [0,1] float image the envelope hugs every noise pixel (OD becomes
    identically 0 — signal destroyed). The eval ran Fiji Analyze>Gels, i.e. this convention.
    """
    filled = np.where(weight, green, gaussian(green, weight, radius_px))
    inv = (filled.max() - filled) * 255.0
    r = max(1, radius_px)
    # ImageJ-style shrink for large balls (O(N R^2) otherwise — M-008): roll a proportionally
    # smaller ball on a block-mean-reduced image, then bilinearly re-expand the background.
    shrink = 1 if r < 16 else (2 if r < 32 else 4)
    if shrink > 1:
        h, w = inv.shape
        hs, ws = (h // shrink) * shrink, (w // shrink) * shrink
        small = inv[:hs, :ws].reshape(hs // shrink, shrink, ws // shrink, shrink).mean(axis=(1, 3))
        bg_small = _sk_rolling_ball(small, radius=max(1, r // shrink))
        yy, xx = np.mgrid[0:h, 0:w]
        coords = np.stack([(yy / shrink - 0.5 + 0.5 / shrink).ravel(), (xx / shrink - 0.5 + 0.5 / shrink).ravel()])
        bg_inv = ndimage.map_coordinates(bg_small, coords, order=1, mode="nearest").reshape(h, w) / 255.0
    else:
        bg_inv = _sk_rolling_ball(inv, radius=r) / 255.0
    return np.maximum(filled.max() - bg_inv, I0_FLOOR)


MODELS = {
    "poly3": poly3,
    "iterative": iterative_masked,
    "gaussian": gaussian,
    "median": median,
    "rolling_ball": rolling_ball,
}


def fit_background(name: str, green: np.ndarray, weight: np.ndarray, radius_px: int) -> np.ndarray:
    return MODELS[name](green, weight, radius_px)
