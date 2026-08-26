"""Projection helpers shared by the gate and by phase C.

Trap 12.7: peak prominence must be NOISE-RELATIVE (3*MAD of the profile), never
relative to the profile maximum, and edge margins must be trimmed before the MAD is
computed - otherwise warp-edge glow inflates MAD and hides real lanes.
"""
from __future__ import annotations
import numpy as np, cv2
from scipy.signal import find_peaks

EDGE_MARGIN   = 0.04      # spec: trim 4% both sides
MAD_K         = 3.0       # spec: 3 * MAD
DETREND_FRAC  = 0.25      # box-blur width for the slow trend, as fraction of profile

def mad(x: np.ndarray) -> float:
    x = np.asarray(x, np.float64)
    return float(np.median(np.abs(x - np.median(x))))

def robust_sigma(x: np.ndarray) -> float:
    return max(1.4826 * mad(x), 1e-9)

def detrend(profile: np.ndarray, frac: float | None = None) -> np.ndarray:
    frac = DETREND_FRAC if frac is None else frac
    n = len(profile)
    k = max(3, int(frac * n) | 1)
    trend = cv2.blur(profile.reshape(1, -1).astype(np.float32), (k, 1)).ravel()
    return profile - trend

def x_projection(od: np.ndarray, y0: float, y1: float, frac: float | None = None):
    """Mean OD over a horizontal band -> (x indices kept, trimmed+detrended profile)."""
    h, w = od.shape
    band = od[max(0, int(y0 * h)): min(h, int(y1 * h))]
    raw = band.mean(0)
    m = int(EDGE_MARGIN * w)
    idx = np.arange(m, w - m)
    return idx, detrend(raw[m:w - m], frac)

def noise_peaks(profile: np.ndarray, w_full: int, k: float = MAD_K,
                min_sep_frac: float = 0.05):
    sig = robust_sigma(profile)
    pk, props = find_peaks(profile, prominence=k * sig,
                           distance=max(3, int(min_sep_frac * w_full)))
    return pk, props, sig
