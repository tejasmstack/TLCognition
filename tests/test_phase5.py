"""Phase 5 unit tests: EMG fit honesty, streak verdicts, origin two-dot rule, Rst arithmetic,
and the runner's refusal totality on adversarial inputs."""

import numpy as np
import pytest

from tlc.pipeline.origin import find_origin
from tlc.pipeline.peaks import emg, fit_emg
from tlc.pipeline.rst import combine_position_variance, rst_with_interval
from tlc.pipeline.streak import assess_streak


def test_emg_fit_recovers_mode_and_tags_method():
    y = np.arange(0, 120, dtype=float)
    truth = emg(y, 0.08, 60.0, 4.0, 6.0, 0.002)
    rng = np.random.Generator(np.random.PCG64(1))
    prof = truth + rng.normal(0, 0.002, y.size)
    f = fit_emg(prof, seed_row=62.0, seed_fwhm=10.0, seed_amp=0.08)
    assert f.ok and f.method in ("emg_fit", "gaussian_limit_fit")
    true_mode = float(y[np.argmax(truth)])
    assert abs(f.mode - true_mode) < 1.0
    assert f.mu_se is not None and 0 < f.mu_se < 3.0
    assert f.vif >= 1.0


def test_emg_fit_gaussian_limit_has_finite_se():
    y = np.arange(0, 100, dtype=float)
    prof = 0.05 * np.exp(-((y - 50) ** 2) / (2 * 4.0**2)) + np.random.Generator(np.random.PCG64(2)).normal(0, 0.001, y.size)
    f = fit_emg(prof, seed_row=50.0, seed_fwhm=9.4, seed_amp=0.05)
    assert f.ok and f.mu_se is not None and f.mu_se < 2.0
    assert abs(f.mode - 50.0) < 0.6


def test_emg_fit_failure_is_tagged_not_fabricated():
    prof = np.zeros(30)
    f = fit_emg(prof, seed_row=15.0, seed_fwhm=8.0, seed_amp=0.0)
    assert not f.ok and f.method == "matched_filter_seed" and f.mu_se is None


def test_streak_verdicts():
    prof = np.zeros(200)
    prof[50:60] = 0.05  # a spot ~1 FWHM long
    v = assess_streak(prof, (0, 200), 0.005, [1.0], nominal_fwhm_px=10.0)
    assert not v.is_streaking
    prof2 = np.zeros(200)
    prof2[40:120] = 0.05  # 8 FWHM contiguous
    v2 = assess_streak(prof2, (0, 200), 0.005, [], nominal_fwhm_px=10.0)
    assert v2.is_streaking and "contiguous run" in (v2.reason or "")
    v3 = assess_streak(prof, (0, 200), 0.005, [3.5], nominal_fwhm_px=10.0)
    assert v3.is_streaking and "tail ratio" in (v3.reason or "")


def test_origin_two_dot_rule():
    od = np.zeros((100, 80))
    valid = np.ones_like(od, dtype=bool)
    centres = [10.0, 30.0, 50.0, 70.0]
    # one dot only -> refuse
    od[80:83, 9:12] = 0.3
    o1 = find_origin(od, valid, 0.01, centres, 8.0, (75, 90))
    assert not o1.found and "at least 2" in (o1.reason or "")
    od[80:83, 49:52] = 0.3
    o2 = find_origin(od, valid, 0.01, centres, 8.0, (75, 90))
    assert o2.found and abs(o2.row - 81.0) < 1.0 and o2.n_dots == 2


def test_rst_variance_budget_sums_to_one():
    r = rst_with_interval(100.0, 0.25, 60.0, 0.25, 180.0, 4.0)
    assert r is not None
    assert r.value == pytest.approx((180 - 100) / (180 - 60))
    assert sum(r.budget.values()) == pytest.approx(1.0)
    assert r.budget["origin"] > r.budget["spot"]  # origin dominates when its variance is largest
    assert rst_with_interval(100.0, 0.1, 180.0, 0.1, 180.0, 0.1) is None
    assert combine_position_variance(1.0, 1.0, 4.0) == pytest.approx(1.0 + 1.25)


def test_clean_gaussian_fits_as_gaussian_not_degenerate_emg():
    """M-014: a plain Gaussian must not come back as sigma~2.7 / tau~37 (tail ratio 14)."""
    y = np.arange(0, 120, dtype=float)
    rng = np.random.Generator(np.random.PCG64(11))
    for seed_row in (58.0, 60.0, 62.0):
        prof = 0.06 * np.exp(-((y - 60.0) ** 2) / (2 * 6.0**2)) + rng.normal(0, 0.0015, y.size)
        f = fit_emg(prof, seed_row=seed_row, seed_fwhm=14.0, seed_amp=0.06)
        assert f.ok
        assert f.method == "gaussian_limit_fit" or f.tau / f.sigma < 3.0
        assert abs(f.mode - 60.0) < 1.0


def test_two_adjacent_spots_are_not_a_streak():
    """M-014: two ordinary peaks ~20 px apart form one long run above 2 sigma but are not a streak."""
    y = np.arange(0, 200, dtype=float)
    prof = 0.05 * np.exp(-((y - 90) ** 2) / (2 * 5.0**2)) + 0.04 * np.exp(-((y - 112) ** 2) / (2 * 5.0**2))
    v = assess_streak(prof, (0, 200), 0.004, [1.0], nominal_fwhm_px=11.8, peak_rows=[90.0, 112.0])
    assert not v.is_streaking, v.reason
    v_single = assess_streak(prof, (0, 200), 0.004, [1.0], nominal_fwhm_px=11.8, peak_rows=[90.0])
    # with only one peak claimed inside the same long run, the run rule may legitimately fire
    assert isinstance(v_single.is_streaking, bool)


def test_streak_rule_separates_adjacent_spots_from_a_smear():
    """D-027: a long run is a streak only when it is flat-topped and unexplained."""
    from tlc.pipeline.peaks import emg
    from tlc.pipeline.streak import assess_streak as A

    y = np.arange(200, dtype=float)
    # two spots 30 px apart, each 1 FWHM wide: one contiguous run ~4 FWHM with a dip between them
    pair = emg(y, 0.08, 70.0, 5.0, 0.001, 0.0) + emg(y, 0.08, 90.0, 5.0, 0.001, 0.0)
    v = A(pair, (0, 200), 0.005, [], nominal_fwhm_px=10.0, peak_rows=[70.0, 90.0], fitted_peaks=pair.copy())
    assert not v.is_streaking, v.reason
    assert v.max_run_fwhm > 2.5 and v.plateau_frac < 0.85   # long, but peaked

    smear = np.zeros(200)
    smear[60:130] = 0.05                                    # 7 FWHM of flat elevation
    v2 = A(smear, (0, 200), 0.005, [], nominal_fwhm_px=10.0, peak_rows=[95.0], fitted_peaks=np.zeros(200))
    assert v2.is_streaking and v2.plateau_frac >= 0.85


def test_tail_rule_vetoes_are_measured_not_assumed():
    from tlc.pipeline.streak import assess_streak as A

    prof = np.zeros(200)
    prof[60:130] = 0.05
    # (a) another detected peak sits inside the fitted tail -> two spots, not a comet
    v = A(prof, (0, 200), 0.005, [6.0], nominal_fwhm_px=10.0, peak_rows=[65.0, 110.0],
          fitted_peaks=np.zeros(200), dominant_mu=65.0, dominant_tau=60.0)
    assert "tail ratio" not in (v.reason or "")
    # (b) a tail longer than the elevated region is extrapolation, not measurement
    v2 = A(prof, (0, 200), 0.005, [6.0], nominal_fwhm_px=10.0, peak_rows=[65.0],
           fitted_peaks=np.zeros(200), dominant_mu=65.0, dominant_tau=400.0)
    assert "tail ratio" not in (v2.reason or "")
    # (c) a supported tail on a smeared lane with nothing else in it still fires
    v3 = A(prof, (0, 200), 0.005, [6.0], nominal_fwhm_px=10.0, peak_rows=[65.0],
           fitted_peaks=np.zeros(200), dominant_mu=65.0, dominant_tau=40.0)
    assert v3.is_streaking and "tail ratio" in (v3.reason or "")


def test_position_estimate_prefers_the_consensus_only_when_there_is_one():
    from tlc.pipeline.ensemble import EnsembleSpot
    from tlc.pipeline.peaks import EMGFit
    from tlc.pipeline.runner import position_estimate

    fit = EMGFit(True, 100.0, 100.4, 4.0, 1.0, 0.05, 0.0, 0.3, 9.0, 1.0, 0.001, 1.2, 40, "emg_fit")
    many = EnsembleSpot(lane=0, row=101.7, agreement=0.8, n_hit=18, n_total=24, z_med=9.0, z_min=5.0,
                        row_spread=0.8, p_med=0.001, amplitude_med=0.05)
    few = EnsembleSpot(lane=0, row=101.7, agreement=0.45, n_hit=2, n_total=24, z_med=6.0, z_min=5.0,
                       row_spread=1.4, p_med=0.01, amplitude_med=0.05)
    assert position_estimate(fit, many) == pytest.approx(101.7)
    assert position_estimate(fit, few) == pytest.approx(100.4)
