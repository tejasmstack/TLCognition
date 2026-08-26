"""Streak detection (F11). A streaking lane is flagged and NOT quantified: the position of a
streak is not defined, and any number would be a fabrication (brief §9 anti-pattern 9).

Three statistics, any fires the flag:
  streak_fraction: fraction of analysable rows whose lane profile exceeds 2 sigma_prof
                   (ported from tlc-spec-impl extract.py:102-105, STREAK_LIMIT 0.55)
  run length:      longest contiguous run above 2 sigma_prof exceeds RUN_FWHM_LIMIT x the nominal
                   FWHM (a spot is ~1 FWHM long; a comet/T-streak is many) — catches partial
                   streaks the fraction rule misses (found on synthetic streak lanes)
  tail ratio:      fitted tau/sigma > 3.0 for the dominant peak (spec 03 §7.3.4 E_STREAK)
"""

from dataclasses import dataclass

import numpy as np

STREAK_FRACTION_LIMIT = 0.55  # ported constant (tlc-spec-impl)
STREAK_K = 2.0
TAIL_RATIO_LIMIT = 3.0
RUN_FWHM_LIMIT = 2.5


@dataclass(frozen=True)
class StreakVerdict:
    is_streaking: bool
    streak_fraction: float
    max_run_fwhm: float
    max_tail_ratio: float | None
    reason: str | None


def _longest_run(mask: np.ndarray) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def assess_streak(
    profile: np.ndarray, rows: tuple[int, int], sigma_prof: float, tail_ratios: list[float],
    nominal_fwhm_px: float = 12.0,
) -> StreakVerdict:
    r0, r1 = rows
    seg = profile[r0:r1]
    above = seg > STREAK_K * max(sigma_prof, 1e-12)
    frac = float(above.mean()) if seg.size else 0.0
    run_fwhm = _longest_run(above) / max(nominal_fwhm_px, 1.0)
    max_tail = max(tail_ratios) if tail_ratios else None
    reasons = []
    if frac > STREAK_FRACTION_LIMIT:
        reasons.append(f"{frac:.0%} of the lane is above 2 sigma (limit {STREAK_FRACTION_LIMIT:.0%})")
    if run_fwhm > RUN_FWHM_LIMIT:
        reasons.append(f"a contiguous run {run_fwhm:.1f} FWHM long is above 2 sigma (limit {RUN_FWHM_LIMIT})")
    if max_tail is not None and max_tail > TAIL_RATIO_LIMIT:
        reasons.append(f"tail ratio tau/sigma {max_tail:.1f} > {TAIL_RATIO_LIMIT}")
    return StreakVerdict(bool(reasons), frac, float(run_fwhm), max_tail, "; ".join(reasons) or None)
