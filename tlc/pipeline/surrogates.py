"""Per-plate surrogate nulls for the empirical detection threshold (spec 01 §2.2; A-015).

Every operating decision about "is there a peak" is made against these, never against a bare
sigma multiple: the identical profile -> matched-filter machinery runs on N surrogate
realizations of THIS plate, and a peak's p-value is its Davison-Hinkley Monte-Carlo rank.

  S1 gutter transplant — lane band rebuilt from inter-lane gutter strips of the same OD field
     (flipped/rolled): preserves local texture, illumination residue, clipping; breaks spots.
  S2 IAAFT — iterative amplitude-adjusted Fourier transform of the lane profile: preserves the
     power spectrum and the amplitude distribution; breaks the phase coherence that makes a spot.
  S3 vertical roll — circular shift by >= 3 FWHM: breaks positions only (position-specificity).

Pure module; one seeded Generator passed in, no global RNG.
"""

import numpy as np


def gutter_columns(width: int, lane_centres: list[float], lane_halfwidth: float) -> np.ndarray:
    """Column indices belonging to no lane window (the gutters), 2 px margin."""
    cols = np.ones(width, dtype=bool)
    for xc in lane_centres:
        lo = max(0, int(np.floor(xc - lane_halfwidth - 2)))
        hi = min(width, int(np.ceil(xc + lane_halfwidth + 2)) + 1)
        cols[lo:hi] = False
    return np.nonzero(cols)[0]


def s1_gutter_profile(
    od: np.ndarray,
    od_valid: np.ndarray,
    gutters: np.ndarray,
    band_width: int,
    rows: tuple[int, int],
    rng: np.random.Generator,
) -> np.ndarray | None:
    """A null lane profile over the analysable rows [r0, r1): mean over `band_width` gutter
    column SEGMENTS, each independently flipped/rolled WITHIN the band. Restricting to the
    band matters: rolling whole columns rotates header/label ink into the chemistry zone and
    manufactures phantom null peaks (found empirically; the ink is darker than chemistry, F7).
    Texture-true null — it is the plate's own signal-free content."""
    if gutters.size < max(4, band_width // 3):
        return None
    r0, r1 = rows
    n_rows = r1 - r0
    if n_rows < 8:
        return None
    # Contiguous gutter STRIPS (the spec's word, and it matters): one flip + one roll per
    # strip, identical for all its columns, so the cross-column noise correlation the real
    # lane band has is preserved. Independent per-column rolls average the correlated noise
    # away and make the null too light (anti-conservative p) — found empirically.
    runs: list[np.ndarray] = []
    start = 0
    for i in range(1, gutters.size + 1):
        if i == gutters.size or gutters[i] != gutters[i - 1] + 1:
            runs.append(gutters[start:i])
            start = i
    # ONE flip and ONE roll per surrogate, applied to every strip: keeps the plate's
    # row-coherent texture (sensor/compression rows, residual illumination) aligned across the
    # assembled band exactly as it is across a real lane. A spot is lane-local; a row-coherent
    # bump is plate-wide and must be present in the null too (found on real-texture blanks:
    # independent per-strip rolls made the null anti-conservatively light).
    cols_parts: list[np.ndarray] = []
    flip_all = bool(rng.uniform() < 0.5)
    shift_all = int(rng.integers(0, n_rows))
    flips: list[bool] = []
    shifts: list[int] = []
    total = 0
    while total < band_width:
        run = runs[int(rng.integers(0, len(runs)))]
        cols_parts.append(run)
        flips.append(flip_all)
        shifts.append(shift_all)
        total += run.size
    band = np.empty((n_rows, total))
    bandv = np.empty((n_rows, total), dtype=bool)
    j = 0
    for run, flip, shift in zip(cols_parts, flips, shifts, strict=True):
        seg = od[r0:r1, run].copy()
        segv = od_valid[r0:r1, run].copy()
        if flip:
            seg = seg[::-1]
            segv = segv[::-1]
        seg = np.roll(seg, shift, axis=0)
        segv = np.roll(segv, shift, axis=0)
        band[:, j : j + run.size] = seg
        bandv[:, j : j + run.size] = segv
        j += run.size
    band = band[:, :band_width]
    bandv = bandv[:, :band_width]
    n = bandv.sum(axis=1)
    return np.where(n > 0, (band * bandv).sum(axis=1) / np.maximum(n, 1), 0.0)


def s2_iaaft_profile(profile: np.ndarray, rng: np.random.Generator, n_iter: int = 100) -> np.ndarray:
    """IAAFT (Schreiber-Schmitz): random-phase surrogate preserving spectrum + amplitudes."""
    x = np.asarray(profile, dtype=np.float64)
    n = x.size
    sorted_x = np.sort(x)
    target_amp = np.abs(np.fft.rfft(x))
    y = rng.permutation(x)
    for _ in range(n_iter):
        # impose spectrum
        yf = np.fft.rfft(y)
        phase = np.angle(yf)
        y = np.fft.irfft(target_amp * np.exp(1j * phase), n=n)
        # impose amplitude distribution
        ranks = np.argsort(np.argsort(y))
        y_new = sorted_x[ranks]
        if np.array_equal(y_new, y):
            break
        y = y_new
    return y


def s3_roll_profile(profile: np.ndarray, fwhm_px: float, rng: np.random.Generator) -> np.ndarray:
    n = profile.size
    min_shift = max(1, int(np.ceil(3 * fwhm_px)))
    if 2 * min_shift >= n:
        min_shift = max(1, n // 4)
    shift = int(rng.integers(min_shift, n - min_shift))
    return np.roll(profile, shift)


def mc_p_value(z_obs: float, null_peak_zs: np.ndarray) -> float:
    """Davison-Hinkley Monte-Carlo p: (1 + #{null >= obs}) / (1 + #null). Exact; never 0."""
    n = null_peak_zs.size
    return float((1 + int((null_peak_zs >= z_obs).sum())) / (1 + n))


def bh_reject(p_values: list[float], q: float = 0.10) -> list[bool]:
    """Benjamini-Hochberg at level q (PRDS holds for positively dependent peaks)."""
    m = len(p_values)
    if m == 0:
        return []
    order = np.argsort(p_values)
    keep = np.zeros(m, dtype=bool)
    k_max = -1
    for rank, idx in enumerate(order, start=1):
        if p_values[idx] <= rank * q / m:
            k_max = rank
    if k_max > 0:
        for rank, idx in enumerate(order, start=1):
            if rank <= k_max:
                keep[idx] = True
    return keep.tolist()
