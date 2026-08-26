"""Phase 1 unit tests: the synthetic generator's determinism and ground-truth honesty."""

import numpy as np
import pytest

from tlc.synth.generator import make_plate, random_spec
from tlc.synth.spec import Handwriting, Overrun, PlateSpec, SpotShape, SpotSpec

SPOTS = (
    SpotSpec(lane=0, y_frac=0.5, amplitude_sigma=12.0, shape=SpotShape.GAUSSIAN),
    SpotSpec(lane=1, y_frac=0.3, amplitude_sigma=8.0, shape=SpotShape.EMG, tau_frac=2.0),
    SpotSpec(lane=2, y_frac=0.7, amplitude_sigma=20.0, shape=SpotShape.STREAK),
)
BASE = PlateSpec(spots=SPOTS, empty_lanes=(3,))


def test_same_seed_byte_identical():
    img1, gt1 = make_plate(BASE, seed=1234)
    img2, gt2 = make_plate(BASE, seed=1234)
    assert img1.dtype == np.uint8
    assert np.array_equal(img1, img2)
    assert gt1.to_dict() == gt2.to_dict()


def test_different_seed_differs():
    img1, _ = make_plate(BASE, seed=1)
    img2, _ = make_plate(BASE, seed=2)
    assert not np.array_equal(img1, img2)


@pytest.mark.parametrize("target", [0.0, 0.14, 0.30, 0.60])
def test_clip_knob_hits_target(target):
    spec = PlateSpec(spots=SPOTS[:2], clip_fraction=target, tilt_deg=0.0)
    _, gt = make_plate(spec, seed=7)
    assert abs(gt.clip_fraction_actual - target) <= 0.03
    if target == 0.0:
        assert gt.clip_fraction_actual == 0.0


def test_ground_truth_geometry_consistent():
    _, gt = make_plate(BASE, seed=99)
    # Corners form a rectangle congruent with the plate (rotation preserves side lengths).
    c = np.array(gt.corners_xy)
    top = np.linalg.norm(c[1] - c[0])
    bottom = np.linalg.norm(c[2] - c[3])
    left = np.linalg.norm(c[3] - c[0])
    right = np.linalg.norm(c[2] - c[1])
    # Pixel-centre convention (M-005): corner-to-corner distance is plate_w-1 / plate_h-1.
    assert top == pytest.approx(gt.plate_w - 1, abs=1e-6)
    assert bottom == pytest.approx(gt.plate_w - 1, abs=1e-6)
    assert left == pytest.approx(gt.plate_h - 1, abs=1e-6)
    assert right == pytest.approx(gt.plate_h - 1, abs=1e-6)
    # Lane centres inside the plate, spots between origin and front.
    assert all(0 < x < gt.plate_w for x in gt.lane_centres_x)
    for s in gt.spots:
        assert 0 <= s.y <= gt.plate_h


def test_streaks_flagged_unquantifiable():
    _, gt = make_plate(BASE, seed=5)
    assert gt.streak_lanes == (2,)
    streak = [s for s in gt.spots if s.shape == "streak"]
    assert streak and all(not s.quantifiable for s in streak)
    others = [s for s in gt.spots if s.shape != "streak"]
    assert all(s.quantifiable for s in others)


def test_spot_amplitude_truth_is_exact():
    _, gt = make_plate(BASE, seed=11)
    for s in gt.spots:
        assert s.amplitude_od == pytest.approx(s.amplitude_sigma * gt.sigma_od_analytic)
        assert s.area_od > 0


def test_spot_in_empty_lane_rejected():
    bad = PlateSpec(spots=(SpotSpec(lane=3, y_frac=0.5, amplitude_sigma=5.0),), empty_lanes=(3,))
    with pytest.raises(ValueError):
        make_plate(bad, seed=0)


def test_front_line_recorded_only_when_drawn():
    _, gt_no = make_plate(BASE, seed=3)
    assert gt_no.front_row is None
    _, gt_yes = make_plate(PlateSpec(spots=(), front_line=True), seed=3)
    assert gt_yes.front_row is not None


def test_overrun_cuts_plate_out_of_frame():
    spec = PlateSpec(spots=(), frame_overrun=Overrun.BOTH, tilt_deg=1.0)
    _, gt = make_plate(spec, seed=21)
    ys = [cy for _, cy in gt.corners_xy]
    assert min(ys) < 0 and max(ys) > gt.image_h - 1


def test_random_spec_deterministic_and_in_range():
    r1 = random_spec(np.random.Generator(np.random.PCG64(42)))
    r2 = random_spec(np.random.Generator(np.random.PCG64(42)))
    assert r1 == r2
    assert 71 <= r1.plate_w <= 400
    assert 3 <= r1.n_lanes <= 6
    assert 0.0 <= r1.tilt_deg <= 12.0
    assert 0.10 <= r1.illum_swing <= 0.25
    assert r1.handwriting in list(Handwriting)
