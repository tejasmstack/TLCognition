"""The ensemble config space and the per-config lane detector (spec 01 §2.2-2.3; D-009).

A config is one defensible way to answer "where are the peaks in this lane":
  radius x background model x sigma-variant x densitogram extraction x peak model
  {8,16,32,64} x {rolling_ball, median, arpls, poly3} x {masked_mad, unmasked_mad,
  gutter_only, autocov_full} x {mean, median, trimmed20} x {emg, bigauss, raw_max}
= 576. CONFIG_GRID_v1 (K=24) is selected from these by measured performance + greedy
max-min diversity (scripts/select_config_grid.py), never by taste.

Detection discipline: matched-filter z (noise.py) -> sigma-variant 3.0 floor (the axis that
spans finding 4's "family of 3-sigma meanings") -> Davison-Hinkley MC p against this plate's
own surrogate nulls -> BH at q=0.10. No bare sigma rule anywhere decides an emitted spot.
"""

from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

from tlc.pipeline.noise import (
    NoiseModel,
    gaussian_template,
    matched_filter_scan,
    prepare_templates,
    profile_autocovariance,
)
from tlc.pipeline.photometry import LANE_HALFWIDTH_FRAC, compute_od
from tlc.pipeline.surrogates import (
    bh_reject,
    gutter_columns,
    mc_p_value,
    s1_gutter_profile,
    s2_iaaft_profile,
)

RADII = (8, 16, 32, 64)
MODELS = ("rolling_ball", "median", "arpls", "poly3")
SIGMA_VARIANTS = ("masked_mad", "unmasked_mad", "gutter_only", "autocov_full")
EXTRACTIONS = ("mean", "median", "trimmed20")
PEAK_MODELS = ("emg", "bigauss", "raw_max")
Z_FLOOR = 3.0
BH_Q = 0.10
MAX_SURROGATES = 400   # adaptive cap: enough for m = 40 candidates at q = 0.10
WIDTH_FRAC_NOMINAL = 0.18  # nominal spot sigma as fraction of lane pitch (corpus-calibrated)


@dataclass(frozen=True)
class Config:
    model: str
    radius: int
    sigma_variant: str
    extraction: str
    peak_model: str

    @property
    def key(self) -> str:
        return f"{self.model}@{self.radius}/{self.sigma_variant}/{self.extraction}/{self.peak_model}"


def all_configs() -> list[Config]:
    return [
        Config(m, r, sv, ex, pm)
        for m in MODELS
        for r in RADII
        for sv in SIGMA_VARIANTS
        for ex in EXTRACTIONS
        for pm in PEAK_MODELS
    ]


@dataclass(frozen=True)
class LanePeak:
    row: float
    z: float
    amplitude: float
    p_mc: float            # FWER-style Davison-Hinkley p vs per-surrogate max z (S1 null)
    s2_exceed: float       # diagnostic: fraction of IAAFT surrogates whose max z >= z_obs
    accepted: bool
    template_fwhm: float


def arpls_baseline(y: np.ndarray, lam: float = 1e5, ratio: float = 1e-6, n_iter: int = 50) -> np.ndarray:
    """Asymmetric least squares baseline (arPLS, Baek 2015). Peaks must point up."""
    n = y.size
    if n < 8:
        return np.zeros_like(y)
    d = sparse.diags([1.0, -2.0, 1.0], [0, 1, 2], shape=(n - 2, n))
    h = lam * (d.T @ d)
    w = np.ones(n)
    z = y.copy()
    for _ in range(n_iter):
        wmat = sparse.diags(w)
        z = spsolve((wmat + h).tocsc(), w * y)
        resid = y - z
        neg = resid[resid < 0]
        if neg.size < 2:
            break
        m, s = float(neg.mean()), float(neg.std())
        if s < 1e-12:
            break
        w_new = 1.0 / (1.0 + np.exp(np.clip(2.0 * (resid - (2 * s - m)) / s, -50, 50)))
        if np.linalg.norm(w - w_new) / max(np.linalg.norm(w), 1e-12) < ratio:
            w = w_new
            break
        w = w_new
    return np.asarray(z)


def _extract_profile(field: np.ndarray, valid: np.ndarray, x_lo: int, x_hi: int, how: str) -> np.ndarray:
    band = field[:, x_lo:x_hi].astype(np.float64)
    bandv = valid[:, x_lo:x_hi]
    n = bandv.sum(axis=1)
    if how == "mean":
        out = np.where(n > 0, (band * bandv).sum(axis=1) / np.maximum(n, 1), 0.0)
        return out
    masked = np.where(bandv, band, np.nan)
    if how == "median":
        out = np.zeros(band.shape[0])
        has = n > 0
        if has.any():
            out[has] = np.nanmedian(masked[has], axis=1)
        return out
    # trimmed20: drop the lowest/highest 20% of valid values per row
    srt = np.sort(masked, axis=1)  # NaNs sort to the end
    out = np.zeros(band.shape[0])
    for i in range(band.shape[0]):
        m = int(n[i])
        if m == 0:
            continue
        k = max(1, int(0.2 * m))
        vals = srt[i, :m]
        out[i] = float(vals[k:-k].mean()) if m > 2 * k else float(vals.mean())
    return out


def _cached_od(cache: dict | None, green: np.ndarray, valid: np.ndarray, model: str, radius: int):
    key = (model, radius)
    if cache is not None and key in cache:
        return cache[key]
    odr = compute_od(green, valid, model, radius)
    if cache is not None:
        cache[key] = odr
    return odr


def lane_od_profile(
    cfg: Config,
    green: np.ndarray,
    valid: np.ndarray,
    x_center: float,
    pitch: float,
    od_cache: dict | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(od_profile, od_field, od_valid) for this config's background path.

    2-D models: OD field then extraction. arPLS: extraction of -log10(green) then a 1-D
    baseline per lane (its OD field for surrogate building is the poly3 field — the gutter
    transplant needs SOME 2-D residual; recorded in A-015).
    """
    hw = LANE_HALFWIDTH_FRAC * pitch
    x_lo = max(0, int(round(x_center - hw)))
    x_hi = min(green.shape[1], int(round(x_center + hw)) + 1)
    if cfg.model == "arpls":
        neglog = np.where(valid & (green > 1e-3), -np.log10(np.maximum(green, 1e-3)), 0.0)
        raw = _extract_profile(neglog, valid, x_lo, x_hi, cfg.extraction)
        base = arpls_baseline(raw)
        odr = _cached_od(od_cache, green, valid, "poly3", 0)
        return raw - base, odr.od, odr.od_valid
    odr = _cached_od(od_cache, green, valid, cfg.model, cfg.radius)
    prof = _extract_profile(odr.od, odr.od_valid, x_lo, x_hi, cfg.extraction)
    return prof, odr.od, odr.od_valid


def sigma_variant_value(
    cfg: Config,
    od: np.ndarray,
    od_valid: np.ndarray,
    exclusion: np.ndarray,
    gutters: np.ndarray,
    noise: NoiseModel,
    window_cols: int,
) -> float:
    """The config's own noise unit for the z-floor — the axis spanning finding 4's family of
    '3 sigma' meanings. Values are per-PROFILE units (per-pixel MAD scaled by 1/sqrt(W))."""
    w_scale = 1.0 / np.sqrt(max(window_cols, 1))
    if cfg.sigma_variant == "autocov_full":
        c_prof = profile_autocovariance(noise, window_cols, max_dy=0)
        return float(np.sqrt(max(c_prof[0], 1e-18)))
    if cfg.sigma_variant == "gutter_only":
        vals = od[:, gutters][od_valid[:, gutters]] if gutters.size else np.array([])
    elif cfg.sigma_variant == "masked_mad":
        ok = od_valid & ~exclusion
        vals = od[ok]
    else:  # unmasked_mad
        vals = od[od_valid]
    if vals.size < 50:
        return float("nan")
    mad = float(np.median(np.abs(vals - np.median(vals))))
    return max(1.4826 * mad * w_scale, 1e-9)


def emg_template(sigma_px: float, tau_over_sigma: float = 1.5) -> np.ndarray:
    """EMG-shaped template, tail toward +y (the origin side), unit peak."""
    tau = tau_over_sigma * sigma_px
    m = max(7, int(round(6 * sigma_px + 4 * tau)) | 1)
    y = np.arange(m, dtype=np.float64)
    mu = (m - 1) / 2.0 - tau / 2.0
    z = (y - mu) / sigma_px
    k = sigma_px / tau
    from scipy.special import erfcx

    x = (k - z) / np.sqrt(2.0)
    val = np.where(
        x > -20.0,
        erfcx(np.maximum(x, -20.0)) * np.exp(-0.5 * z * z),
        2.0 * np.exp(0.5 * k * k - k * z),
    )
    return val / max(float(val.max()), 1e-300)


def bigauss_template(sigma_px: float, right_over_left: float = 1.6) -> np.ndarray:
    """Asymmetric bi-Gaussian: sharper front edge, broader tail. Unit peak."""
    sl, sr = sigma_px, right_over_left * sigma_px
    m = max(7, int(round(3 * sl + 3 * sr)) | 1)
    y = np.arange(m, dtype=np.float64)
    mu = 3.0 * sl
    left = np.exp(-((y - mu) ** 2) / (2 * sl * sl))
    right = np.exp(-((y - mu) ** 2) / (2 * sr * sr))
    return np.where(y <= mu, left, right)


def template_bank(peak_model: str, sigma_nom: float) -> list[np.ndarray]:
    """The peak-model axis: three shape families, each at three widths (spec 01 §2.3)."""
    factories = {
        "raw_max": gaussian_template,
        "emg": emg_template,
        "bigauss": bigauss_template,
    }
    make = factories[peak_model]
    out = []
    for f in (0.5, 1.0, 2.0):
        t = make(sigma_nom * f)
        out.append(t / max(float(t.max()), 1e-300))  # unit peak regardless of grid alignment
    return out


@dataclass(frozen=True)
class SharedLaneDetection:
    """Everything about one (model, radius, extraction, peak_model) lane run that the four
    sigma-variants share: candidates, S1-null FWER p-values, BH keeps, S2 diagnostic."""

    peaks: tuple
    p_list: tuple[float, ...]
    bh_keep: tuple[bool, ...]
    s2_exceeds: tuple[float, ...]
    od: np.ndarray
    od_valid: np.ndarray
    gutters: np.ndarray
    window_cols: int

    def accept(self, cfg: Config, exclusion: np.ndarray, noise: NoiseModel) -> list[LanePeak]:
        sv = sigma_variant_value(cfg, self.od, self.od_valid, exclusion, self.gutters, noise, self.window_cols)
        out = []
        for pk, p, keep, s2x in zip(self.peaks, self.p_list, self.bh_keep, self.s2_exceeds, strict=True):
            floor = bool(np.isfinite(sv) and pk.amplitude / max(sv, 1e-12) >= Z_FLOOR)
            out.append(
                LanePeak(
                    row=pk.row, z=pk.z, amplitude=pk.amplitude, p_mc=p, s2_exceed=s2x,
                    accepted=bool(floor and keep), template_fwhm=pk.template_fwhm,
                )
            )
        return out


def scan_lane_shared(
    cfg: Config,
    green: np.ndarray,
    valid: np.ndarray,
    noise: NoiseModel,
    x_center: float,
    pitch: float,
    lane_centres: list[float],
    analysable_rows: tuple[int, int],
    rng: np.random.Generator,
    n_surrogates: int = 60,
    od_cache: dict | None = None,
) -> SharedLaneDetection:
    """The sigma-variant-independent part of the per-config lane detector."""
    prof, od, od_valid = lane_od_profile(cfg, green, valid, x_center, pitch, od_cache)
    hw = LANE_HALFWIDTH_FRAC * pitch
    window_cols = max(1, int(round(2 * hw)))
    sigma_nom = max(1.0, WIDTH_FRAC_NOMINAL * pitch)
    c_prof = profile_autocovariance(noise, window_cols)
    sigma0_prof = float(np.sqrt(max(c_prof[0], 1e-18)))
    templates = prepare_templates(template_bank(cfg.peak_model, sigma_nom), c_prof, sigma0_prof)

    # Candidates: positive-amplitude maxima only (a negative-going maximum is not a spot
    # hypothesis, and junk candidates both inflate BH's m and shrink MC denominators).
    peaks = [
        pk
        for pk in matched_filter_scan(prof, templates, c_prof, sigma0_prof, analysable_rows)
        if pk.z > 0 and pk.amplitude > 0
    ]
    gutters = gutter_columns(green.shape[1], lane_centres, hw)
    if not peaks:
        return SharedLaneDetection((), (), (), (), od, od_valid, gutters, window_cols)

    def _max_pos_z(profile: np.ndarray) -> float:
        zs = [
            pk.z
            for pk in matched_filter_scan(profile, templates, c_prof, sigma0_prof, (0, profile.size))
            if pk.z > 0 and pk.amplitude > 0
        ]
        return max(zs) if zs else -np.inf

    # Primary null: S1 gutter transplant (texture-true, signal-free by construction),
    # per-surrogate MAX z -> FWER-style Davison-Hinkley p (spec 01 §2.2). S2 IAAFT is a
    # recorded diagnostic, not the operative null: on spotted lanes it inherits the strongest
    # spot's spectral power and suppresses real neighbours (D-010, measured).
    r0, r1 = analysable_rows
    # The Davison-Hinkley floor 1/(N+1) must sit below BH's strictest threshold q/m, or a
    # candidate that beats EVERY surrogate is rejected for lack of p-resolution, not evidence
    # (found: a 20-sigma spot on a clipped lane with m=7 candidates, N=60). Draw more when needed.
    n_needed = max(n_surrogates, int(np.ceil(len(peaks) / BH_Q)) + 1)
    n_draw = min(n_needed, MAX_SURROGATES)
    s1_max: list[float] = []
    for _ in range(n_draw):
        nprof = s1_gutter_profile(od, od_valid, gutters, window_cols, analysable_rows, rng)
        if nprof is None:
            nprof = s2_iaaft_profile(prof[r0:r1], rng)
        s1_max.append(_max_pos_z(nprof))
    s1_arr = np.array(s1_max)
    n_s2 = max(10, n_surrogates // 5)
    s2_arr = np.array([_max_pos_z(s2_iaaft_profile(prof[r0:r1], rng)) for _ in range(n_s2)])

    p_list = tuple(mc_p_value(pk.z, s1_arr) for pk in peaks)
    bh = tuple(bh_reject(list(p_list), BH_Q))
    s2x = tuple(float((s2_arr >= pk.z).mean()) if s2_arr.size else 1.0 for pk in peaks)
    return SharedLaneDetection(tuple(peaks), p_list, bh, s2x, od, od_valid, gutters, window_cols)


def detect_lane(
    cfg: Config,
    green: np.ndarray,
    valid: np.ndarray,
    noise: NoiseModel,
    exclusion: np.ndarray,
    x_center: float,
    pitch: float,
    lane_centres: list[float],
    analysable_rows: tuple[int, int],
    rng: np.random.Generator,
    n_surrogates: int = 60,
    od_cache: dict | None = None,
) -> list[LanePeak]:
    """The per-config lane detector: sigma-variant floor AND BH(FWER MC-p) at q=0.10."""
    shared = scan_lane_shared(
        cfg, green, valid, noise, x_center, pitch, lane_centres, analysable_rows, rng,
        n_surrogates, od_cache,
    )
    return shared.accept(cfg, exclusion, noise)
