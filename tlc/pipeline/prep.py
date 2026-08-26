"""Rectified-plate preparation: the bridge from geometry to photometry.

Clipping is a property of SOURCE pixels (the sensor saturated there); warping first and
thresholding after smears clipped regions through interpolation (tlc-spec-impl survey, F1).
So the clip mask is computed on the raw source green and warped as a mask.
"""

from dataclasses import dataclass

import numpy as np

from tlc.pipeline.geometry import PlateGeometry, rectified_valid_mask, warp_rectify

CLIP_LEVEL = 254  # spec 03: green_clip counts raw G >= 254


@dataclass(frozen=True)
class PlatePrep:
    green: np.ndarray            # float64 in [0,1], rectified
    valid: np.ndarray            # bool: in-plate, in-bounds, tilt-eroded, NOT source-clipped
    valid_geom: np.ndarray       # bool: as above but before clip exclusion
    clip_frac_in_plate: float    # source-clipped fraction of the geometric valid region


def rectify_and_mask(img_u8: np.ndarray, geo: PlateGeometry) -> PlatePrep:
    rgb = img_u8[:, :, :3]
    rect, _ = warp_rectify(rgb, geo.homography, geo.rectified_shape)
    green = rect[:, :, 1] / 255.0
    clip_src = (rgb[:, :, 1] >= CLIP_LEVEL).astype(np.float64)
    clip_rect, _ = warp_rectify(clip_src, geo.homography, geo.rectified_shape)
    valid_geom = rectified_valid_mask(geo.mask, geo.homography, geo.rectified_shape, geo.tilt_deg)
    unclipped = clip_rect < 0.5
    valid = valid_geom & unclipped
    clip_frac = float((valid_geom & ~unclipped).sum() / max(valid_geom.sum(), 1))
    return PlatePrep(green=green, valid=valid, valid_geom=valid_geom, clip_frac_in_plate=clip_frac)
