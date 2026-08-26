"""Phase 4 unit tests: noise unit, surrogate nulls, per-config detection, ensemble."""

import numpy as np
import pytest

from tlc.pipeline.configs import (
    Config,
    all_configs,
    arpls_baseline,
    bigauss_template,
    detect_lane,
    emg_template,
    template_bank,
)
from tlc.pipeline.ensemble import config_weights, k_eff, run_ensemble_lane
from tlc.pipeline.geometry import analyse_geometry
from tlc.pipeline.noise import (
    estimate_noise,
    gaussian_template,
    prepass_exclusion_mask,
    profile_autocovariance,
)
from tlc.pipeline.prep import rectify_and_mask
from tlc.pipeline.surrogates import bh_reject, mc_p_value, s2_iaaft_profile
from tlc.synth.generator import make_plate
from tlc.synth.spec import PlateSpec, SpotSpec

SPOTS = (
    SpotSpec(lane=1, y_frac=0.55, amplitude_sigma=10.0),
    SpotSpec(lane=1, y_frac=0.30, amplitude_sigma=6.0),
    SpotSpec(lane=3, y_frac=0.55, amplitude_sigma=15.0),
)


def _prep(img, gt):
    geo = analyse_geometry(img)
    pp = rectify_and_mask(img, geo)
    band = (int(gt.header_band[1] + 2), int(gt.origin_row - 4))
    excl = prepass_exclusion_mask(
        pp.green, pp.valid, 1.18 * 0.18 * gt.lane_pitch,
        ((0, gt.header_band[1]), (gt.label_band[0], pp.green.shape[0])),
    )
    return pp, band, excl, estimate_noise(pp.green, pp.valid, excl)


@pytest.fixture(scope="module")
def spotted():
    img, gt = make_plate(PlateSpec(spots=SPOTS, tilt_deg=2.0), seed=42)
    return (gt, *_prep(img, gt))


def test_grid_has_576_configs():
    assert len(all_configs()) == 576
    assert len({c.key for c in all_configs()}) == 576


def test_noise_model_sane(spotted):
    gt, pp, band, excl, noise = spotted
    assert 0.001 < noise.sigma0 < 0.05
    assert noise.mask_excluded_frac < 0.6
    assert noise.n_pairs_min >= 200
    cp = profile_autocovariance(noise, 16)
    assert cp[0] > 0 and cp[0] >= abs(cp[5])  # variance dominates distant covariance


def test_detection_finds_true_spots_rejects_empty(spotted):
    gt, pp, band, excl, noise = spotted
    cfg = Config("poly3", 32, "autocov_full", "mean", "raw_max")
    tol = 0.4 * 2.355 * 0.18 * gt.lane_pitch
    accepted = {}
    for lane in range(4):
        rng = np.random.Generator(np.random.PCG64(lane + 1))
        pks = detect_lane(
            cfg, pp.green, pp.valid, noise, excl, gt.lane_centres_x[lane], gt.lane_pitch,
            list(gt.lane_centres_x), band, rng, n_surrogates=60,
        )
        accepted[lane] = [p for p in pks if p.accepted]
    for s in gt.spots:
        assert any(abs(p.row - s.y) <= tol for p in accepted[s.lane]), f"missed {s}"
    assert not accepted[0] and not accepted[2]  # empty lanes stay empty


def test_detection_deterministic(spotted):
    gt, pp, band, excl, noise = spotted
    cfg = Config("median", 16, "gutter_only", "median", "emg")
    out = []
    for _ in range(2):
        rng = np.random.Generator(np.random.PCG64(7))
        pks = detect_lane(
            cfg, pp.green, pp.valid, noise, excl, gt.lane_centres_x[1], gt.lane_pitch,
            list(gt.lane_centres_x), band, rng, n_surrogates=30,
        )
        out.append([(p.row, p.z, p.p_mc, p.accepted) for p in pks])
    assert out[0] == out[1]


def test_ensemble_agreement_and_position(spotted):
    gt, pp, band, excl, noise = spotted
    grid = [
        Config("poly3", 32, "autocov_full", "mean", "raw_max"),
        Config("median", 16, "unmasked_mad", "median", "emg"),
        Config("rolling_ball", 32, "gutter_only", "mean", "bigauss"),
        Config("arpls", 8, "masked_mad", "trimmed20", "emg"),
    ]
    spots, per_config = run_ensemble_lane(
        grid, None, pp.green, pp.valid, noise, excl, 1, gt.lane_centres_x[1], gt.lane_pitch,
        list(gt.lane_centres_x), band, seed=99, n_surrogates=30,
    )
    strong = [s for s in spots if s.agreement > 0.5]
    assert strong, "no ensemble spot with majority agreement"
    true_rows = sorted(s.y for s in gt.spots if s.lane == 1)
    best = min(strong, key=lambda s: abs(s.row - true_rows[1]))
    assert abs(best.row - true_rows[1]) <= 3.0
    assert 0.0 < best.agreement < 1.0  # Jeffreys shrinkage keeps a off 0 and 1


def test_k_eff_duplicates_collapse():
    v = np.array([[1, 0, 1, 0, 1, 1, 0, 0]] * 4)
    assert k_eff(v) == pytest.approx(1.0, abs=0.01)
    rng = np.random.Generator(np.random.PCG64(0))
    v2 = rng.integers(0, 2, size=(4, 200))
    assert k_eff(v2) > 2.5
    w = config_weights(v)
    assert np.allclose(w, 1.0)  # D-015: weights sum to K


def test_mc_p_value_floor_and_monotone():
    null = np.array([1.0, 2.0, 3.0, 4.0])
    assert mc_p_value(5.0, null) == pytest.approx(1 / 5)
    assert mc_p_value(0.5, null) == pytest.approx(1.0)
    assert mc_p_value(2.5, null) == pytest.approx(3 / 5)


def test_bh_reject_basic():
    assert bh_reject([0.001, 0.5, 0.9], q=0.10) == [True, False, False]
    assert bh_reject([], q=0.10) == []
    assert bh_reject([0.9, 0.95], q=0.10) == [False, False]


def test_iaaft_preserves_spectrum_and_amplitudes():
    rng = np.random.Generator(np.random.PCG64(5))
    x = rng.normal(0, 1, 128) + np.sin(np.arange(128) / 5.0)
    y = s2_iaaft_profile(x, rng)
    assert np.allclose(np.sort(y), np.sort(x))  # amplitude distribution exact
    px = np.abs(np.fft.rfft(x))
    py = np.abs(np.fft.rfft(y))
    assert np.corrcoef(px, py)[0, 1] > 0.98  # spectrum preserved
    assert not np.allclose(x, y)


def test_arpls_baseline_under_peak():
    y = 0.01 * np.arange(100) / 100 + np.exp(-((np.arange(100) - 50.0) ** 2) / 30.0)
    base = arpls_baseline(y, lam=1e5)
    assert abs(base[50] - 0.005) < 0.2  # baseline stays near the ramp, not the peak
    assert (y - base)[50] > 0.5


def test_templates_shapes():
    for t in template_bank("emg", 3.0) + template_bank("bigauss", 3.0) + template_bank("raw_max", 3.0):
        assert t.max() == pytest.approx(1.0)
        assert t.min() >= 0.0
    e = emg_template(3.0, 1.5)
    peak = int(np.argmax(e))
    assert e[min(len(e) - 1, peak + 6)] > e[max(0, peak - 6)]  # tail toward +y
    b = bigauss_template(3.0)
    peak = int(np.argmax(b))
    assert b[min(len(b) - 1, peak + 4)] > b[max(0, peak - 4)]
    g = gaussian_template(3.0)
    assert abs(np.argmax(g) - (len(g) - 1) / 2) <= 0.5
