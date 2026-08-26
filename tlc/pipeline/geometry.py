"""Deterministic plate geometry: detection, corner extraction, rectification (Phase 2).

Ported per D-005 from tlc-spec-impl/tlccore/normalize.py:72-103 (HSV bright-green mask ->
largest component -> min-area rectangle -> projective warp) and the evaluation report §5
(hue-plus-value mask with an Otsu brightness split, because the teal bench shares the hue band),
re-implemented cv2-free (NN5: cv2 build variance) with two upgrades:
  - sub-pixel corners from least-squares line fits to the hull edges (the min-area rectangle
    alone cannot meet Gate 2's 1.5 px p95 corner error),
  - explicit valid-mask machinery (erosion derived from measured tilt — the M-007 class guard)
    instead of the prior implementation's silent border crop.

Pure module: numpy/scipy/skimage only, no I/O, no RNG, no wall clock (enforced by
tests/test_pipeline_purity.py).
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull
from skimage.color import rgb2hsv
from skimage.filters import threshold_otsu

# Detection constants (chosen; echoed into the result config in Phase 10)
HUE_LO, HUE_HI = 0.18, 0.58        # green .. cyan band; the teal bench also lands here (eval §5)
SAT_FLOOR = 0.12                   # plate glow is strongly coloured; near-greys excluded
VAL_FLOOR = 0.25                   # the plate GLOWS; dark green-ish clutter is not a plate
MIN_PLATE_FRAC = 0.05              # below this the "plate" is noise
OTSU_MIN_CLASS_RATIO = 1.35        # brightness split only when class means differ this much
BORDER_BAND_PX = 3                 # eval §5: overrun measured on the 3-px border band
DETECTION_METHOD = "hsv_bright_green_largest_cc"


@dataclass(frozen=True)
class PlateGeometry:
    found: bool
    mask: np.ndarray                  # bool, source frame, filled largest component
    corners_src: np.ndarray | None    # (4,2) float64, TL,TR,BR,BL, sub-pixel
    homography: np.ndarray | None     # (3,3) float64, rectified -> source
    rectified_shape: tuple[int, int] | None  # (H, W)
    tilt_deg: float | None
    frame_overrun: dict[str, float]   # 3-px band coverage per edge: top/bottom/left/right
    plate_area_frac: float
    detection_method: str


# ---------------------------------------------------------------- detection


def detect_plate_mask(rgb: np.ndarray) -> tuple[np.ndarray, bool]:
    """Bright strongly-green largest component. Returns (mask, found)."""
    img = rgb[:, :, :3]
    hsv = rgb2hsv(img)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    hue_mask = (hue >= HUE_LO) & (hue <= HUE_HI) & (sat >= SAT_FLOOR) & (val >= VAL_FLOOR)
    hue_frac = float(hue_mask.mean())
    if hue_frac < 1e-4:
        return np.zeros(hue.shape, dtype=bool), False

    vals = val[hue_mask]
    mask = hue_mask
    if vals.size > 1 and float(vals.min()) < float(vals.max()):
        t = threshold_otsu(vals)
        bright_sel = vals >= t
        if bright_sel.any() and (~bright_sel).any():
            # Apply the brightness split only when it separates two genuinely different
            # populations (plate vs bench, class-mean ratio ~2). A plate filling the frame
            # splits its own illumination gradient at ratio ~1.1 — keep the hue mask then
            # (M-005 fix; the eval's "Otsu on brightness added to the hue mask", §5).
            ratio = float(vals[bright_sel].mean()) / max(float(vals[~bright_sel].mean()), 1e-6)
            if ratio >= OTSU_MIN_CLASS_RATIO:
                mask = hue_mask & (val >= t)

    mask = ndimage.binary_closing(mask, structure=np.ones((5, 5), dtype=bool))
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3), dtype=bool))
    lab, n = ndimage.label(mask)
    if n == 0:
        return np.zeros(hue.shape, dtype=bool), False
    largest = 1 + int(np.argmax(ndimage.sum_labels(mask, lab, index=range(1, n + 1))))
    mask = ndimage.binary_fill_holes(lab == largest)
    return mask, bool(mask.mean() >= MIN_PLATE_FRAC)


# ---------------------------------------------------------------- corners


def _min_area_rect(points: np.ndarray) -> np.ndarray:
    """Rotating-calipers minimum-area rectangle over a point set. Returns (4,2) corners."""
    hull = points[ConvexHull(points).vertices]
    n = len(hull)
    best_area, best = np.inf, None
    for i in range(n):
        edge = hull[(i + 1) % n] - hull[i]
        norm = np.linalg.norm(edge)
        if norm < 1e-9:
            continue
        ux = edge / norm
        uy = np.array([-ux[1], ux[0]])
        proj_x = hull @ ux
        proj_y = hull @ uy
        w = proj_x.max() - proj_x.min()
        h = proj_y.max() - proj_y.min()
        area = w * h
        if area < best_area:
            best_area = area
            c = np.array(
                [
                    proj_x.min() * ux + proj_y.min() * uy,
                    proj_x.max() * ux + proj_y.min() * uy,
                    proj_x.max() * ux + proj_y.max() * uy,
                    proj_x.min() * ux + proj_y.max() * uy,
                ]
            )
            best = c
    return best


def _order_corners(quad: np.ndarray) -> np.ndarray:
    """Canonical TL, TR, BR, BL (valid for tilts well below 45 deg)."""
    s = quad.sum(axis=1)
    d = quad[:, 0] - quad[:, 1]
    tl = quad[int(np.argmin(s))]
    br = quad[int(np.argmax(s))]
    tr = quad[int(np.argmax(d))]
    bl = quad[int(np.argmin(d))]
    return np.array([tl, tr, br, bl], dtype=np.float64)


def _line_intersection(p1: np.ndarray, d1: np.ndarray, p2: np.ndarray, d2: np.ndarray) -> np.ndarray:
    a = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]])
    b = p2 - p1
    t = np.linalg.solve(a, b)
    return p1 + t[0] * d1


def _tls_line(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centroid = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - centroid, full_matrices=False)
    return centroid, vt[0]


def _edge_crossings(green: np.ndarray, base_pts: np.ndarray, n_out: np.ndarray) -> np.ndarray:
    """Sub-pixel 50%-crossing points of the green channel along outward normals.

    For each base point, samples green from 4 px inside to 4 px outside the edge; the physical
    edge is where intensity crosses midway between the inside (plate) and outside (background)
    levels — for a symmetrically blurred step this is the true boundary.
    """
    s = np.arange(-4.0, 4.0 + 1e-9, 0.25)
    px = base_pts[:, None, 0] + s[None, :] * n_out[0]
    py = base_pts[:, None, 1] + s[None, :] * n_out[1]
    h, w = green.shape
    coords = np.stack([np.clip(py, 0, h - 1).ravel(), np.clip(px, 0, w - 1).ravel()])
    prof = ndimage.map_coordinates(green, coords, order=1).reshape(len(base_pts), len(s))
    inside = np.median(prof[:, s <= -2.0], axis=1)
    outside = np.median(prof[:, s >= 2.0], axis=1)
    mid = 0.5 * (inside + outside)
    out: list[np.ndarray] = []
    for k in range(len(base_pts)):
        if inside[k] - outside[k] < 20.0:  # no real contrast (overrun edge / clipped scene)
            continue
        below = np.nonzero(prof[k] <= mid[k])[0]
        below = below[below > 0]
        if below.size == 0:
            continue
        j = int(below[0])
        p0, p1 = prof[k, j - 1], prof[k, j]
        frac = 0.0 if p0 == p1 else (p0 - mid[k]) / (p0 - p1)
        s_star = s[j - 1] + frac * 0.25
        out.append(base_pts[k] + s_star * n_out)
    return np.array(out) if out else np.empty((0, 2))


def extract_corners(mask: np.ndarray, green: np.ndarray | None = None) -> np.ndarray | None:
    """Sub-pixel corners: min-area rect for structure, then per-side refinement.

    Primary refinement fits a total-least-squares line to sub-pixel 50%-intensity crossings
    sampled along the central 80% of each side (rounded/cut plate corners must not bend the
    fit). Where an edge has no intensity contrast (frame overrun), the mask-boundary pixel fit
    is the fallback. Adjacent side lines intersect at the corners.
    """
    boundary = mask & ~ndimage.binary_erosion(mask)
    ys, xs = np.nonzero(boundary)
    if xs.size < 20:
        return None
    pts = np.stack([xs, ys], axis=1).astype(np.float64)
    rect = _order_corners(_min_area_rect(pts))

    lines: list[tuple[np.ndarray, np.ndarray]] = []
    for i in range(4):
        a, b = rect[i], rect[(i + 1) % 4]
        side = b - a
        length = float(np.linalg.norm(side))
        u = side / max(length, 1e-9)
        n_out = np.array([u[1], -u[0]])  # outward for TL,TR,BR,BL clockwise in y-down coords

        fitted = False
        if green is not None and length >= 12:
            t = np.linspace(0.10 * length, 0.90 * length, max(9, int(length * 0.4)))
            base = a[None, :] + t[:, None] * u[None, :]
            crossings = _edge_crossings(green, base, n_out)
            if len(crossings) >= max(5, 0.3 * len(base)):
                lines.append(_tls_line(crossings))
                fitted = True
        if not fitted:
            rel = pts - a
            along = rel @ u
            dist = np.abs(rel @ np.array([-u[1], u[0]]))
            sel = (dist <= 1.5) & (along >= 0.10 * length) & (along <= 0.90 * length)
            edge_pts = pts[sel]
            if len(edge_pts) >= max(5, 0.05 * length):
                lines.append(_tls_line(edge_pts))
            else:
                lines.append((a, u))  # too few points: keep the calipers side

    corners = np.array(
        [_line_intersection(*lines[(i - 1) % 4], *lines[i]) for i in range(4)]
    )
    # lines[i] runs corner i -> i+1, so intersection of lines[i-1] and lines[i] is corner i.
    return _order_corners(corners)


# ---------------------------------------------------------------- homography


def homography_from_corners(corners_src: np.ndarray, rect_w: int, rect_h: int) -> np.ndarray:
    """DLT for the 4-point map rectified->source. Rectified corners: (0,0),(W-1,0),(W-1,H-1),(0,H-1)."""
    dst = corners_src.astype(np.float64)
    src = np.array([[0, 0], [rect_w - 1, 0], [rect_w - 1, rect_h - 1], [0, rect_h - 1]], dtype=np.float64)
    a = []
    b = []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    h8 = np.linalg.solve(np.array(a), np.array(b))
    return np.array(
        [[h8[0], h8[1], h8[2]], [h8[3], h8[4], h8[5]], [h8[6], h8[7], 1.0]], dtype=np.float64
    )


def rectified_size(corners: np.ndarray) -> tuple[int, int]:
    tl, tr, br, bl = corners
    w = 0.5 * (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl))
    h = 0.5 * (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr))
    return max(8, int(round(h))), max(8, int(round(w)))


def warp_rectify(
    image: np.ndarray, homography: np.ndarray, shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """Sample the source at homography(rectified grid). Returns (rectified float64, in-bounds mask)."""
    rect_h, rect_w = shape
    yy, xx = np.mgrid[0:rect_h, 0:rect_w]
    ones = np.ones_like(xx, dtype=np.float64)
    pts = np.stack([xx.astype(np.float64), yy.astype(np.float64), ones])
    src = homography @ pts.reshape(3, -1)
    sx = (src[0] / src[2]).reshape(rect_h, rect_w)
    sy = (src[1] / src[2]).reshape(rect_h, rect_w)
    h0, w0 = image.shape[:2]
    in_bounds = (sx >= 0) & (sx <= w0 - 1) & (sy >= 0) & (sy <= h0 - 1)
    coords = np.stack([np.clip(sy, 0, h0 - 1).ravel(), np.clip(sx, 0, w0 - 1).ravel()])
    if image.ndim == 2:
        out = ndimage.map_coordinates(image.astype(np.float64), coords, order=1).reshape(rect_h, rect_w)
    else:
        out = np.stack(
            [
                ndimage.map_coordinates(image[:, :, c].astype(np.float64), coords, order=1).reshape(rect_h, rect_w)
                for c in range(image.shape[2])
            ],
            axis=-1,
        )
    return out, in_bounds


SNAP_PX = 2.0


def _snap_to_frame(corners: np.ndarray, w: int, h: int, snap: float = SNAP_PX) -> np.ndarray:
    """Snap corner coordinates within `snap` px of the frame boundary onto the boundary.

    At the frame edge the plate boundary evidence is one-sided (there is no background beyond
    the image), so a sub-pixel estimate slightly inside the frame over-reads a blend ring; the
    best estimate of a cut or frame-filling edge is the frame itself. Required for Gate 2's
    re-warp idempotency and correct for genuine frame-overrun plates.
    """
    c = corners.copy()
    c[:, 0] = np.where(np.abs(c[:, 0]) <= snap, 0.0, c[:, 0])
    c[:, 0] = np.where(np.abs(c[:, 0] - (w - 1)) <= snap, float(w - 1), c[:, 0])
    c[:, 1] = np.where(np.abs(c[:, 1]) <= snap, 0.0, c[:, 1])
    c[:, 1] = np.where(np.abs(c[:, 1] - (h - 1)) <= snap, float(h - 1), c[:, 1])
    return c


# ---------------------------------------------------------------- derived quantities


def tilt_from_corners(corners: np.ndarray) -> float:
    tl, tr, br, bl = corners
    top = math.degrees(math.atan2(tr[1] - tl[1], tr[0] - tl[0]))
    bottom = math.degrees(math.atan2(br[1] - bl[1], br[0] - bl[0]))
    return float(0.5 * (abs(top) + abs(bottom)))


def valid_erosion_px(tilt_deg: float) -> int:
    """Erosion derived from measured tilt, never a constant (M-007; spec 03 worked example)."""
    return int(math.ceil(2.0 + 0.55 * tilt_deg))


def frame_overrun_fractions(mask: np.ndarray, band_px: int = BORDER_BAND_PX) -> dict[str, float]:
    b = band_px
    return {
        "top": float(mask[:b, :].mean()),
        "bottom": float(mask[-b:, :].mean()),
        "left": float(mask[:, :b].mean()),
        "right": float(mask[:, -b:].mean()),
    }


def analyse_geometry(rgb: np.ndarray) -> PlateGeometry:
    mask, found = detect_plate_mask(rgb)
    area = float(mask.mean())
    overrun = frame_overrun_fractions(mask) if found else {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}
    if not found:
        return PlateGeometry(False, mask, None, None, None, None, overrun, area, DETECTION_METHOD)
    corners = extract_corners(mask, green=rgb[:, :, 1].astype(np.float64))
    if corners is not None:
        corners = _snap_to_frame(corners, rgb.shape[1], rgb.shape[0])
    if corners is None:
        return PlateGeometry(False, mask, None, None, None, None, overrun, area, DETECTION_METHOD)
    shape = rectified_size(corners)
    hmat = homography_from_corners(corners, shape[1], shape[0])
    tilt = tilt_from_corners(corners)
    return PlateGeometry(True, mask, corners, hmat, shape, tilt, overrun, area, DETECTION_METHOD)


def rectified_valid_mask(
    mask_src: np.ndarray, homography: np.ndarray, shape: tuple[int, int], tilt_deg: float
) -> np.ndarray:
    """Valid analysable pixels in the rectified frame: inside the source image, inside the
    detected plate mask, eroded by the tilt-derived radius (M-007: never a fixed constant)."""
    m, in_bounds = warp_rectify(mask_src.astype(np.float64), homography, shape)
    valid = (m >= 0.5) & in_bounds
    return ndimage.binary_erosion(valid, iterations=valid_erosion_px(tilt_deg))


def idempotency_residual_px(rectified_rgb_u8: np.ndarray) -> float:
    """Re-detect on the rectified plate; corners must land on the frame corners (<=0.5 px, Gate 2)."""
    geo = analyse_geometry(rectified_rgb_u8)
    if not geo.found or geo.corners_src is None:
        return float("inf")
    h, w = rectified_rgb_u8.shape[:2]
    ideal = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float64)
    return float(np.max(np.linalg.norm(geo.corners_src - ideal, axis=1)))
