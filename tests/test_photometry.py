"""Phase 3 tests: OD conversion, the noise unit, and the Gate 3 metamorphic properties."""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tlc.pipeline.geometry import analyse_geometry
from tlc.pipeline.photometry import (
    compute_od,
    lane_densitogram,
    relative_position,
    sigma_od_prespot,
    strongest_peak_row,
)
from tlc.pipeline.prep import rectify_and_mask
from tlc.synth.generator import make_plate
from tlc.synth.spec import PlateSpec, SpotSpec

SPOTS = (
    SpotSpec(lane=1, y_frac=0.55, amplitude_sigma=12.0),
    SpotSpec(lane=3, y_frac=0.55, amplitude_sigma=15.0),  # standard-lane anchor
)
BASE_SPEC = PlateSpec(spots=SPOTS, base_green=0.62, clip_fraction=0.0, tilt_deg=2.0)
GRID_RADII = [12, 20, 35, 55]
GRID_MODELS = ["iterative", "gaussian", "median", "rolling_ball"]


@pytest.fixture(scope="module")
def base():
    img, gt = make_plate(BASE_SPEC, seed=77)
    geo = analyse_geometry(img)
    pp = rectify_and_mask(img, geo)
    band = (int(gt.header_band[1] + 2), int(gt.origin_row - 3))
    return img, gt, pp, band


def _rst(img, gt):
    geo = analyse_geometry(img)
    pp = rectify_and_mask(img, geo)
    odr = compute_od(pp.green, pp.valid, "poly3", 0)
    rows = (int(gt.header_band[1] + 2), int(gt.origin_row - 3))
    y1 = strongest_peak_row(lane_densitogram(odr, 1, gt.lane_centres_x[1], gt.lane_pitch).profile, rows)
    y3 = strongest_peak_row(lane_densitogram(odr, 3, gt.lane_centres_x[3], gt.lane_pitch).profile, rows)
    assert y1 is not None and y3 is not None
    return relative_position(y1, gt.origin_row, y3)


@settings(max_examples=12, deadline=None, derandomize=True)
@given(k=st.floats(0.7, 1.3))
def test_exposure_invariance_metamorphic(k):
    """Gate 3: Rst(k*I) == Rst(I) within 0.005 for k in [0.7, 1.3] on unclipped synthetic."""
    img, gt = make_plate(BASE_SPEC, seed=77)
    base_rst = _rst(img, gt)
    scaled = np.clip(np.round(img[:, :, :3].astype(np.float64) * k), 0, 255).astype(np.uint8)
    assert abs(_rst(scaled, gt) - base_rst) <= 0.005


def test_sigma_measured_prespot_is_radius_free(base):
    """Gate 3 / F4: the same estimator applied to every grid member's OD residual spreads <15%.

    Any member collapsing to ~0 means a background model ate the noise (M-007)."""
    _, gt, pp, band = base
    sig_raw = sigma_od_prespot(pp.green, pp.valid, band)
    assert np.isfinite(sig_raw) and sig_raw > 1e-4
    vals = []
    for model in ["poly3", *GRID_MODELS]:
        for radius in [0] if model == "poly3" else GRID_RADII:
            odr = compute_od(pp.green, pp.valid, model, radius)
            o = np.where(odr.od_valid, odr.od, np.nan)[band[0] : band[1]]
            d = o[:, 1:] - o[:, :-1]
            d = d[np.isfinite(d)]
            vals.append(1.4826 * float(np.median(np.abs(d - np.median(d)))) / np.sqrt(2.0))
    arr = np.array(vals)
    assert arr.min() > 1e-4, "a background model ate the noise"
    assert (arr.max() - arr.min()) / np.median(arr) <= 0.15


def test_amplitude_monotonicity(base):
    """Gate 3: recovered amplitude monotonic in true amplitude, Spearman rho > 0.98."""
    from scipy.stats import spearmanr

    true_amp, rec_amp = [], []
    for seed in [1, 2, 3]:
        for amp in [1, 2, 3, 5, 7, 10, 14, 19, 25, 30]:
            sp = PlateSpec(
                spots=(SpotSpec(lane=1, y_frac=0.5, amplitude_sigma=float(amp)),),
                base_green=0.85,
                tilt_deg=1.5,
            )
            img, gt = make_plate(sp, seed=1000 + seed)
            geo = analyse_geometry(img)
            pp = rectify_and_mask(img, geo)
            odr = compute_od(pp.green, pp.valid, "poly3", 0)
            den = lane_densitogram(odr, 1, gt.lane_centres_x[1], gt.lane_pitch)
            s = gt.spots[0]
            w0, w1 = int(s.y - 3 * s.sigma_y), int(s.y + 3 * s.sigma_y)
            rec_amp.append(float(den.profile[w0:w1].max()))
            true_amp.append(s.amplitude_od)
    rho = float(spearmanr(true_amp, rec_amp).statistic)
    assert rho > 0.98


def test_od_near_zero_on_empty_plate():
    img, gt = make_plate(PlateSpec(spots=(), origin_dots=False, handwriting=BASE_SPEC.handwriting.__class__("none"), tilt_deg=1.0), seed=5)
    geo = analyse_geometry(img)
    pp = rectify_and_mask(img, geo)
    odr = compute_od(pp.green, pp.valid, "poly3", 0)
    inner = odr.od[odr.od_valid]
    assert abs(float(np.median(inner))) < 0.01


def test_clipped_pixels_excluded_from_valid():
    img, _ = make_plate(PlateSpec(spots=SPOTS, clip_fraction=0.3, tilt_deg=2.0), seed=9)
    geo = analyse_geometry(img)
    pp = rectify_and_mask(img, geo)
    assert pp.clip_frac_in_plate > 0.2  # the clipping is seen at source level
    assert pp.valid.sum() < pp.valid_geom.sum()


def test_relative_position_arithmetic():
    assert relative_position(50.0, 100.0, 50.0) == pytest.approx(1.0)
    assert relative_position(100.0, 100.0, 50.0) == pytest.approx(0.0)
    assert relative_position(75.0, 100.0, 50.0) == pytest.approx(0.5)
    assert np.isnan(relative_position(75.0, 100.0, 100.0))


def test_sigma_estimator_reads_known_white_noise():
    """On synthetic uncorrelated log-noise the estimator recovers the injected sd within 15%."""
    rng = np.random.Generator(np.random.PCG64(3))
    sd = 0.01
    log_g = -0.05 + rng.normal(0.0, sd, size=(200, 150))
    green = np.power(10.0, log_g)
    valid = np.ones_like(green, dtype=bool)
    est = sigma_od_prespot(green, valid, (0, 200))
    assert est == pytest.approx(sd, rel=0.15)
