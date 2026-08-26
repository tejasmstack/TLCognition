"""Phase 2 unit tests: deterministic geometry on synthetic plates with exact ground truth."""

import numpy as np
import pytest

from tlc.pipeline.geometry import (
    analyse_geometry,
    idempotency_residual_px,
    rectified_valid_mask,
    valid_erosion_px,
    warp_rectify,
)
from tlc.synth.generator import make_plate
from tlc.synth.spec import Overrun, PlateSpec, SpotSpec

SPEC = PlateSpec(spots=(SpotSpec(lane=1, y_frac=0.5, amplitude_sigma=10.0),))


@pytest.mark.parametrize("tilt", [0.0, 2.0, 6.0, 12.0])
def test_corner_recovery_within_gate(tilt):
    img, gt = make_plate(PlateSpec(spots=SPEC.spots, tilt_deg=tilt), seed=42)
    geo = analyse_geometry(img)
    assert geo.found
    err = np.linalg.norm(geo.corners_src - np.array(gt.corners_xy), axis=1)
    assert err.max() < 1.5


@pytest.mark.parametrize("tilt", [2.0, 8.0])
def test_tilt_measured_accurately(tilt):
    img, _ = make_plate(PlateSpec(spots=SPEC.spots, tilt_deg=tilt), seed=7)
    geo = analyse_geometry(img)
    assert geo.tilt_deg == pytest.approx(tilt, abs=0.3)


def test_rectification_idempotent():
    img, _ = make_plate(PlateSpec(spots=SPEC.spots, tilt_deg=5.0), seed=3)
    geo = analyse_geometry(img)
    rect, _ = warp_rectify(img, geo.homography, geo.rectified_shape)
    resid = idempotency_residual_px(np.clip(np.round(rect), 0, 255).astype(np.uint8))
    assert resid <= 0.5


def test_overrun_flagged_on_synthetic_cut_plate():
    img, _ = make_plate(PlateSpec(spots=(), frame_overrun=Overrun.BOTH, tilt_deg=1.0), seed=21)
    geo = analyse_geometry(img)
    assert geo.frame_overrun["top"] > 0.02
    assert geo.frame_overrun["bottom"] > 0.02


def test_no_overrun_on_contained_plate():
    img, _ = make_plate(SPEC, seed=5)
    geo = analyse_geometry(img)
    assert all(v <= 0.02 for v in geo.frame_overrun.values())


def test_geometry_deterministic():
    img, _ = make_plate(SPEC, seed=11)
    g1 = analyse_geometry(img)
    g2 = analyse_geometry(img)
    assert np.array_equal(g1.corners_src, g2.corners_src)
    assert np.array_equal(g1.homography, g2.homography)
    r1, _ = warp_rectify(img, g1.homography, g1.rectified_shape)
    r2, _ = warp_rectify(img, g2.homography, g2.rectified_shape)
    assert np.array_equal(r1, r2)


def test_corner_order_canonical():
    img, _ = make_plate(PlateSpec(spots=SPEC.spots, tilt_deg=3.0), seed=13)
    geo = analyse_geometry(img)
    tl, tr, br, bl = geo.corners_src
    assert tl[0] < tr[0] and bl[0] < br[0]  # left of
    assert tl[1] < bl[1] and tr[1] < br[1]  # above


def test_valid_mask_erosion_scales_with_tilt():
    assert valid_erosion_px(0.0) == 2
    assert valid_erosion_px(4.0) > valid_erosion_px(0.5)
    img, _ = make_plate(PlateSpec(spots=SPEC.spots, tilt_deg=6.0), seed=9)
    geo = analyse_geometry(img)
    valid = rectified_valid_mask(geo.mask, geo.homography, geo.rectified_shape, geo.tilt_deg)
    assert valid.shape == geo.rectified_shape
    # eroded: no valid pixel on the frame border
    assert not valid[0, :].any() and not valid[:, 0].any()
    assert valid.mean() > 0.5  # but most of the plate remains analysable


def test_not_a_plate_refuses():
    rng = np.random.Generator(np.random.PCG64(0))
    noise = rng.integers(0, 60, size=(120, 90, 3), dtype=np.uint8)  # dark non-green junk
    geo = analyse_geometry(noise)
    assert not geo.found


def test_rectified_frame_matches_plate_size_and_rows_map_exactly():
    """M-011: a plate w x h rectifies to w x h, and a ground-truth row lands on the same row."""
    from tlc.pipeline.prep import rectify_and_mask

    spec = PlateSpec(spots=(SpotSpec(lane=1, y_frac=0.5, amplitude_sigma=20.0),), tilt_deg=3.0)
    img, gt = make_plate(spec, seed=77)
    geo = analyse_geometry(img)
    assert abs(geo.rectified_shape[0] - gt.plate_h) <= 1 and abs(geo.rectified_shape[1] - gt.plate_w) <= 1
    pp = rectify_and_mask(img, geo)
    s = gt.spots[0]
    col = pp.green[:, int(round(s.x))]
    r0, r1 = int(s.y_mode - 8), int(s.y_mode + 9)
    seg = col[r0:r1]
    i = int(np.argmin(seg))  # darkest row of the spot in the rectified frame
    assert abs((r0 + i) - s.y_mode) <= 1.0
