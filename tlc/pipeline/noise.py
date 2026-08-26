"""The noise reference scale and the matched-filter detection statistic (spec 01 §2.1).

Nothing in Phase 4+ works before this. The unit is defined ONCE per plate, at a FIXED scale,
independent of every background model:

  1. Permissive pre-pass (2σ_diff, iterative@16) -> spot-exclusion mask M, dilated 1.5x the
     nominal spot radius, plus annotation bands, plus a 4 px border ring. M excludes SIGNAL
     from the noise estimate; it is not the F4 masking pathology (the unit below depends on no
     tuning radius, and the operating thresholds come from surrogate nulls, not from σ).
  2. High-pass at a fixed scale: r = L - G_{σ=6px} * L, with L = log10(green). For any smooth
     illumination surface, high-passed OD == -high-passed L, so C estimated here transfers to
     every config's OD residual.
  3. Robust biweight midvariance/midcovariance autocovariance C(dy, dx) on r restricted to ¬M,
     out to |Δ| = 24 px. σ0 = sqrt(C(0,0)).
  4. Every detection statistic is a matched-filter amplitude in units of the filter's own
     correlated noise: z = A / σ_A with σ_A² = kᵀ C k / (Σk²)² — the white-noise formula
     understates σ_A by 1.5–2.5x at these scales, which is most of finding 4's circularity.

Pure module: numpy/scipy only.
"""

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

HIGHPASS_SIGMA_PX = 6.0    # FIXED — never tied to any background radius
MAX_LAG_PX = 24
PREPASS_RADIUS = 16        # permissive pre-pass background radius (mask only, not the unit)
PREPASS_K = 2.0            # ... at 2x the D-008 diff-sigma
BORDER_RING_PX = 4
VIF_STRUCTURED = 4.0       # spec 01 §2.1: flag NOISE_STRUCTURED and tighten
VIF_ABSTAIN = 6.0          # spec 01 §6.2 row 14: abstain


@dataclass(frozen=True)
class NoiseModel:
    sigma0: float                 # sqrt(C(0,0)) — the per-pixel unit on the fixed-scale residual
    cov: np.ndarray               # C(dy, dx), shape (MAX_LAG+1, MAX_LAG+1); cov[0,0] = sigma0^2
    mask_excluded_frac: float     # fraction of valid px excluded by the pre-pass mask
    n_pairs_min: int              # smallest pair count entering any lag estimate


def _biweight_midcov(x: np.ndarray, y: np.ndarray, c: float = 9.0) -> float:
    """Robust covariance (biweight midcovariance) of paired samples."""
    xm, ym = np.median(x), np.median(y)
    madx = np.median(np.abs(x - xm)) or 1e-12
    mady = np.median(np.abs(y - ym)) or 1e-12
    u = (x - xm) / (c * madx)
    v = (y - ym) / (c * mady)
    wu = (1 - u * u) ** 2 * (np.abs(u) < 1)
    wv = (1 - v * v) ** 2 * (np.abs(v) < 1)
    num = np.sum(wu * wv * (x - xm) * (y - ym))
    dux = np.sum(wu * (1 - 5 * u * u))
    dvy = np.sum(wv * (1 - 5 * v * v))
    if dux <= 0 or dvy <= 0:
        return float(np.mean((x - xm) * (y - ym)))
    n = x.size
    return float(n * num / (dux * dvy))


def _log_green(green: np.ndarray, valid: np.ndarray) -> np.ndarray:
    return np.where(valid & (green > 1e-3), np.log10(np.maximum(green, 1e-3)), np.nan)


def _highpass(field: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """r = L - G6*L with normalized (mask-aware) smoothing; NaN outside valid."""
    f = np.where(valid, field, 0.0)
    w = valid.astype(np.float64)
    num = ndimage.gaussian_filter(f, HIGHPASS_SIGMA_PX)
    den = ndimage.gaussian_filter(w, HIGHPASS_SIGMA_PX)
    smooth = np.where(den > 1e-6, num / np.maximum(den, 1e-6), np.nan)
    return np.where(valid, field - smooth, np.nan)


def prepass_exclusion_mask(
    green: np.ndarray,
    valid: np.ndarray,
    nominal_spot_radius_px: float,
    annotation_rows: tuple[tuple[int, int], ...] = (),
) -> np.ndarray:
    """M: candidate zones from a permissive pre-pass (2x the pre-pass residual's own robust
    sigma, iterative@16), dilated 1.5x the nominal spot radius; plus annotation bands and a
    border ring. True = EXCLUDED from noise estimation.

    Fallback ladder (deterministic): if the mask would exclude > 60% of valid pixels the
    threshold tightens to 3x and dilation drops to 2 px; if still > 80%, only the bands and
    border ring are excluded — sigma0 must always have ground to stand on.
    """
    from tlc.pipeline.photometry import compute_od

    odr = compute_od(green, valid, "iterative", PREPASS_RADIUS)
    resid = odr.od[odr.od_valid]
    s_r = 1.4826 * float(np.median(np.abs(resid - np.median(resid)))) if resid.size else 0.0

    def bands_and_border() -> np.ndarray:
        m = np.zeros_like(valid)
        for r0, r1 in annotation_rows:
            m[max(0, r0) : max(0, r1), :] = True
        b = BORDER_RING_PX
        m[:b, :] = True
        m[-b:, :] = True
        m[:, :b] = True
        m[:, -b:] = True
        return m

    base = bands_and_border()
    n_valid = max(int(valid.sum()), 1)
    for k, dil in ((PREPASS_K, max(1, int(round(1.5 * nominal_spot_radius_px)))), (3.0, 2)):
        cand = odr.od_valid & (odr.od > k * s_r)
        m = base | ndimage.binary_dilation(cand, iterations=dil)
        if (m & valid).sum() / n_valid <= 0.60:
            return m
    return base


def estimate_noise(
    green: np.ndarray,
    valid: np.ndarray,
    exclusion: np.ndarray,
    max_lag: int = MAX_LAG_PX,
) -> NoiseModel:
    """C(dy, dx) via biweight midcovariance on the fixed-scale high-passed log-green."""
    L = _log_green(green, valid)
    r = _highpass(L, valid & np.isfinite(L))
    ok = np.isfinite(r) & ~exclusion
    h, w = r.shape
    max_lag = int(min(max_lag, h - 1, w - 1))
    cov = np.zeros((max_lag + 1, max_lag + 1))
    n_min = np.inf
    base = np.where(ok, r, np.nan)
    for dy in range(max_lag + 1):
        for dx in range(max_lag + 1):
            a = base[: h - dy if dy else h, : w - dx if dx else w]
            b = base[dy:, dx:]
            pair_ok = np.isfinite(a) & np.isfinite(b)
            n = int(pair_ok.sum())
            if n < 200:
                cov[dy, dx] = 0.0
                n_min = min(n_min, n)
                continue
            cov[dy, dx] = _biweight_midcov(a[pair_ok], b[pair_ok])
            n_min = min(n_min, n)
    sigma0 = float(np.sqrt(max(cov[0, 0], 1e-12)))
    return NoiseModel(
        sigma0=sigma0,
        cov=cov,
        mask_excluded_frac=float((exclusion & valid).sum() / max(valid.sum(), 1)),
        n_pairs_min=int(n_min if np.isfinite(n_min) else 0),
    )


def profile_autocovariance(noise: NoiseModel, window_cols: int, max_dy: int | None = None) -> np.ndarray:
    """C_prof(dy) for a lane profile averaged over `window_cols` columns.

    C_prof(dy) = (1/W²) Σ_dx (W − |dx|) · C(dy, |dx|), dx over ±(W−1) truncated at MAX_LAG.
    """
    cov = noise.cov
    if max_dy is None:
        max_dy = cov.shape[0] - 1
    w = max(1, int(window_cols))
    dxs = np.arange(-(w - 1), w)
    weights = w - np.abs(dxs)
    out = np.zeros(max_dy + 1)
    for dy in range(max_dy + 1):
        vals = np.array([cov[dy, min(abs(dx), cov.shape[1] - 1)] for dx in dxs])
        out[dy] = float((weights * vals).sum() / (w * w))
    return out


@dataclass(frozen=True)
class MatchedPeak:
    row: float          # sub-pixel template centre
    amplitude: float    # matched-filter amplitude (OD units)
    z: float            # amplitude / correlated-noise sigma_A
    template_fwhm: float
    vif: float


@dataclass(frozen=True)
class PreparedTemplate:
    """A unit-peak template with its correlated-noise sigma_A precomputed against C_prof."""

    k: np.ndarray
    ksum2: float
    sigma_a: float
    vif: float
    fwhm: float


def prepare_templates(
    templates: list[np.ndarray], c_prof: np.ndarray, sigma0_prof: float
) -> list[PreparedTemplate]:
    """Precompute sigma_A^2 = k^T C k / (sum k^2)^2 per template (vectorised lag lookup).
    Done once per lane scan; the surrogate loop reuses it (M-008)."""
    out = []
    lmax = c_prof.size - 1
    for k in templates:
        k = k / max(float(k.max()), 1e-12)
        ksum2 = float((k * k).sum())
        m = k.size
        lags = np.minimum(np.abs(np.subtract.outer(np.arange(m), np.arange(m))), lmax)
        cmat = c_prof[lags]
        sig_a2 = float(k @ cmat @ k) / (ksum2 * ksum2)
        white = (sigma0_prof**2) / ksum2
        # Estimated C can carry negative lobes (finite-sample, compression texture); a matched
        # filter's noise cannot fall far below its white-noise level, so clamp (found as a
        # z ~ 4e7 blow-up on a real-texture blank).
        sig_a2 = max(sig_a2, 0.25 * white)
        out.append(
            PreparedTemplate(
                k=k,
                ksum2=ksum2,
                sigma_a=float(np.sqrt(max(sig_a2, 1e-18))),
                vif=float(sig_a2 / max(white, 1e-18)),
                fwhm=float(2.355 * (m / 6.0)),
            )
        )
    return out


def matched_filter_scan(
    profile: np.ndarray,
    templates: list,
    c_prof: np.ndarray,
    sigma0_prof: float,
    valid_rows: tuple[int, int],
) -> list[MatchedPeak]:
    """Local maxima of the best-template matched-filter z over the valid row range.

    `templates` may be raw arrays (prepared here) or PreparedTemplate objects (fast path).
    The identical function runs on real profiles and on surrogate-null profiles; every
    threshold downstream comes from that comparison, never from z alone.
    """
    r0, r1 = valid_rows
    seg = profile[r0:r1].astype(np.float64)
    n = seg.size
    if n < 8:
        return []
    prepared = templates if templates and isinstance(templates[0], PreparedTemplate) else prepare_templates(templates, c_prof, sigma0_prof)
    best_z = np.full(n, -np.inf)
    best_a = np.zeros(n)
    best_f = np.zeros(n)
    best_vif = np.zeros(n)
    for pt in prepared:
        if pt.k.size > n:
            continue  # a template longer than the analysable band is not a spot hypothesis
        amp = np.correlate(seg, pt.k, mode="same") / pt.ksum2
        z = amp / pt.sigma_a
        upd = z > best_z
        best_z[upd] = z[upd]
        best_a[upd] = amp[upd]
        best_f[upd] = pt.fwhm
        best_vif[upd] = pt.vif
    if not np.isfinite(best_z).any():
        return []
    # interior local maxima (vectorised), then parabolic sub-pixel refinement
    inner = best_z[1:-1]
    is_max = (inner > best_z[:-2]) & (inner >= best_z[2:]) & np.isfinite(inner)
    peaks: list[MatchedPeak] = []
    for i in np.nonzero(is_max)[0] + 1:
        y0, y1, y2 = best_z[i - 1], best_z[i], best_z[i + 1]
        den = y0 - 2 * y1 + y2
        delta = 0.0 if abs(den) < 1e-12 else float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
        peaks.append(
            MatchedPeak(
                row=float(r0 + i + delta),
                amplitude=float(best_a[i]),
                z=float(best_z[i]),
                template_fwhm=float(best_f[i]),
                vif=float(best_vif[i]),
            )
        )
    return peaks


def gaussian_template(sigma_px: float) -> np.ndarray:
    m = max(5, int(round(6 * sigma_px)) | 1)
    x = np.arange(m) - (m - 1) / 2.0
    return np.exp(-(x * x) / (2 * sigma_px * sigma_px))
