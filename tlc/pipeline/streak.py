"""Streak detection (F11). A streaking lane is flagged and NOT quantified: the position of a
streak is not defined, and any number would be a fabrication (brief §9 anti-pattern 9).

Four statistics, any fires the flag:
  streak_fraction: fraction of analysable rows whose lane profile exceeds 2 sigma_prof
                   (ported from tlc-spec-impl extract.py:102-105, STREAK_LIMIT 0.55)
  run length:      longest contiguous run above 2 sigma_prof exceeds RUN_FWHM_LIMIT x the nominal
                   FWHM (a spot is ~1 FWHM long; a comet/T-streak is many) — catches partial
                   streaks the fraction rule misses (found on synthetic streak lanes)
  tail ratio:      fitted tau/sigma > 3.0 for the dominant peak (spec 03 §7.3.4 E_STREAK)

D-027: the run-length and tail rules are evaluated on the RESIDUAL profile — the lane's densitogram
minus the peaks the ensemble already fitted — because the question a streak asks is whether the
elevation is *unexplained*. Two adjacent 15-sigma spots produce a long contiguous run above 2 sigma
and no streak; a comet leaves the same run behind after its peak is subtracted. Measured on the
Gate 5 tuning split, this removes every false streak flag without missing a true one.
"""

from dataclasses import dataclass

import numpy as np

STREAK_FRACTION_LIMIT = 0.55  # ported constant (tlc-spec-impl)
STREAK_K = 2.0
TAIL_RATIO_LIMIT = 3.0
RUN_FWHM_LIMIT = 2.5
PLATEAU_RUN_FWHM = 1.5        # a shorter run counts as a streak only if it is flat-topped ...
PLATEAU_FRAC_LIMIT = 0.85     # ... >= 85% of the run above half its maximum (a spot is ~0.65)
RESIDUAL_TAIL_MIN_FWHM = 1.0  # D-027: a long fitted tail is only evidence if elevation survives the fit


@dataclass(frozen=True)
class StreakVerdict:
    is_streaking: bool
    streak_fraction: float
    max_run_fwhm: float
    max_tail_ratio: float | None
    reason: str | None
    residual_run_fwhm: float = 0.0      # D-027: run above 2 sigma AFTER the fitted peaks are removed
    residual_fraction: float = 0.0
    plateau_frac: float = 0.0           # flat-toppedness of the longest raw run
    n_peaks_in_run: int = 0


def _longest_run(mask: np.ndarray) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def assess_streak(
    profile: np.ndarray, rows: tuple[int, int], sigma_prof: float, tail_ratios: list[float],
    nominal_fwhm_px: float = 12.0, peak_rows: list[float] | None = None,
    fitted_peaks: np.ndarray | None = None, dominant_mu: float | None = None, dominant_tau: float | None = None,
) -> StreakVerdict:
    """tail_ratios: ONLY the dominant peak's non-degenerate tau/sigma should be passed (M-014);
    peak_rows: tiered peak positions — a long run containing >= 2 peaks is adjacent spots,
    not a streak, unless it is also flat-topped;
    fitted_peaks: the sum of the fitted peak curves over the SAME rows as `profile` (no baseline),
    subtracted before the run and tail rules are applied (D-027). None means no peaks were fitted,
    in which case the residual is the profile itself;
    dominant_mu/dominant_tau: the tailed fit's centre and tail constant. A long fitted tail with
    ANOTHER detected peak sitting within one tail constant of it is two spots, not a comet, so the
    tail rule is vetoed (D-027)."""
    r0, r1 = rows
    seg = profile[r0:r1]
    above = seg > STREAK_K * max(sigma_prof, 1e-12)
    frac = float(above.mean()) if seg.size else 0.0
    run_len = _longest_run(above)
    run_fwhm = run_len / max(nominal_fwhm_px, 1.0)
    # plateau fraction of the longest run: comet/T-streaks are flat-topped, spots are peaked
    plateau = 0.0
    if run_len >= 3:
        best = cur = start = 0
        bstart = 0
        for i, v in enumerate(above):
            if v:
                if cur == 0:
                    start = i
                cur += 1
                if cur > best:
                    best, bstart = cur, start
            else:
                cur = 0
        seg_run = seg[bstart : bstart + best]
        plateau = float(np.mean(seg_run >= 0.5 * seg_run.max())) if seg_run.size else 0.0
        n_peaks_in_run = sum(1 for pr in (peak_rows or []) if bstart <= pr - r0 < bstart + best)
    else:
        n_peaks_in_run = 0
    # D-027: what the fitted peaks do not explain
    resid = seg if fitted_peaks is None else seg - fitted_peaks[r0:r1] if fitted_peaks.shape[0] == profile.shape[0] else seg - fitted_peaks
    r_above = resid > STREAK_K * max(sigma_prof, 1e-12)
    resid_frac = float(r_above.mean()) if resid.size else 0.0
    resid_run_fwhm = _longest_run(r_above) / max(nominal_fwhm_px, 1.0)

    max_tail = max(tail_ratios) if tail_ratios else None
    reasons = []
    if frac > STREAK_FRACTION_LIMIT:
        reasons.append(f"{frac:.0%} of the lane is above 2 sigma (limit {STREAK_FRACTION_LIMIT:.0%})")
    # D-027: a long run is a streak only when it is FLAT-TOPPED. Two adjacent spots make a long run
    # with a dip between them (measured: plateau <= 0.75 on every clean lane, 1.00 on every streak).
    if resid_run_fwhm > RUN_FWHM_LIMIT and plateau >= PLATEAU_FRAC_LIMIT:
        reasons.append(f"a flat-topped contiguous run {resid_run_fwhm:.1f} FWHM long is above 2 sigma after the "
                       f"fitted peaks are removed (limit {RUN_FWHM_LIMIT})")
    elif resid_run_fwhm >= PLATEAU_RUN_FWHM and plateau >= PLATEAU_FRAC_LIMIT:
        reasons.append(f"a flat-topped run ({plateau:.0%} of {run_fwhm:.1f} FWHM above half-max) — comet/streak, not a peak")
    tail_is_another_spot = (
        dominant_mu is not None and dominant_tau is not None
        # a peak closer than one nominal FWHM is the same feature split across ensemble tiers, not a
        # second spot: it cannot be what the tail is made of
        and any(dominant_mu + nominal_fwhm_px < pr <= dominant_mu + dominant_tau
                for pr in (peak_rows or []))
    )
    # A fitted tail longer than the elevated region itself is extrapolation, not measurement: the EMG
    # has degenerated into "Gaussian plus a baseline ramp" (M-006/M-014 family). Such a tail is not
    # evidence of a comet.
    tail_unsupported = dominant_tau is not None and dominant_tau > resid_run_fwhm * nominal_fwhm_px
    # A tailed fit on a lane that is neither flat-topped nor smeared over more than RUN_FWHM_LIMIT is
    # a peaked spot whose fitted tail is noise: the material is still concentrated, so the lane stays
    # quantifiable and the tail is reported per band instead.
    tail_corroborated = plateau >= PLATEAU_FRAC_LIMIT or resid_run_fwhm > RUN_FWHM_LIMIT
    if (max_tail is not None and max_tail > TAIL_RATIO_LIMIT and resid_run_fwhm >= RESIDUAL_TAIL_MIN_FWHM
            and tail_corroborated and not tail_is_another_spot and not tail_unsupported):
        reasons.append(f"tail ratio tau/sigma {max_tail:.1f} > {TAIL_RATIO_LIMIT} with {resid_run_fwhm:.1f} FWHM "
                       "of unexplained elevation")
    return StreakVerdict(bool(reasons), frac, float(run_fwhm), max_tail, "; ".join(reasons) or None,
                         float(resid_run_fwhm), resid_frac, float(plateau), int(n_peaks_in_run))
