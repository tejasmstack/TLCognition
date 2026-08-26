"""Synthetic TLC plate generator — make_plate(spec, seed) -> (image_uint8, GroundTruth).

Physical model (all float64, one seeded PCG64 Generator, no global RNG):
  1. Illumination surface L(x,y) on the plate: low-order polynomial + one off-centre Gaussian
     hotspot, scaled so (max-min)/max == spec.illum_swing exactly.
  2. OD field: spots (Gaussian / EMG / streak / halo), origin dots, pencil lines, handwriting
     ink. OD adds; intensity I = L * 10**(-OD)  (dark quenching zones on a bright plate).
  3. Correlated Gaussian noise added in the intensity domain (sd renormalised after smoothing).
  4. Exposure scaling to hit the requested in-plate clip fraction; quantisation to uint8.
  5. Scene composition: plate rotated by tilt into a dark background, cropped per frame-overrun
     mode, exact corner coordinates tracked analytically.

The generator deliberately shares no code with the measurement pipeline (Phase 3+); it is built
first so it cannot be shaped to flatter the pipeline (spec 05 §12.2).
"""

import math
from dataclasses import replace

import numpy as np
from scipy import ndimage

from tlc.synth.spec import (
    GroundTruth,
    Handwriting,
    Overrun,
    PlateSpec,
    SpotShape,
    SpotSpec,
    SpotTruth,
)

LN10 = math.log(10.0)


# ---------------------------------------------------------------- illumination


def _illumination(spec: PlateSpec, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    h, w = spec.plate_h, spec.plate_w
    yy, xx = np.mgrid[0:h, 0:w]
    xn = xx / max(w - 1, 1) * 2.0 - 1.0
    yn = yy / max(h - 1, 1) * 2.0 - 1.0

    # Low-order polynomial part: random gradient + curvature.
    c = rng.normal(0.0, 1.0, size=5)
    poly = c[0] * xn + c[1] * yn + c[2] * xn * yn + c[3] * xn * xn + c[4] * yn * yn

    # One off-centre Gaussian hotspot.
    cx, cy = rng.uniform(-0.7, 0.7, size=2)
    sx = rng.uniform(0.5, 1.2)
    sy = rng.uniform(0.5, 1.2)
    hot = np.exp(-(((xn - cx) / sx) ** 2 + ((yn - cy) / sy) ** 2) / 2.0)

    def normed(a: np.ndarray) -> np.ndarray:
        lo, hi = float(a.min()), float(a.max())
        return (a - lo) / (hi - lo) if hi > lo else np.zeros_like(a)

    shape01 = (1.0 - spec.hotspot_strength) * normed(poly) + spec.hotspot_strength * normed(hot)
    shape01 = normed(shape01)  # 0 at the darkest point, 1 at the brightest

    # L = Lmax * (1 - swing * (1 - shape01)); Lmax = base_green scaled so median ~ base.
    lmax = spec.base_green / max(1e-6, float(np.median(1.0 - spec.illum_swing * (1.0 - shape01))))
    surface = lmax * (1.0 - spec.illum_swing * (1.0 - shape01))
    swing_true = float((surface.max() - surface.min()) / surface.max())
    return surface, swing_true


# ---------------------------------------------------------------- OD content


def _emg_profile(y: np.ndarray, mu: float, sigma: float, tau: float) -> np.ndarray:
    """Exponentially modified Gaussian along +y (tail toward the origin, i.e. downward).

    h(y) = exp(-(y-mu)^2 / 2 sigma^2) convolved with a one-sided exponential decay of constant
    tau in the +y direction; computed with the erfc closed form, normalised to peak 1.
    """
    if tau <= 1e-9:
        return np.exp(-((y - mu) ** 2) / (2.0 * sigma * sigma))
    z = (y - mu) / sigma
    k = sigma / tau
    from scipy.special import erfcx  # local import keeps module load cheap

    # erfcx(x)*exp(-z^2/2) overflows for x = (k-z)/sqrt(2) << 0 (far down the tail: erfcx(x)
    # ~ 2 exp(x^2) -> inf). There, use the exact identity erfcx(x) = 2 exp(x^2) - erfcx(-x):
    # val = 2 exp(k^2/2 - k z) - erfcx(-x) exp(-z^2/2), whose second term is negligible —
    # i.e. the pure exponential tail. (M-006)
    x = (k - z) / math.sqrt(2.0)
    val = np.where(
        x > -20.0,
        erfcx(np.maximum(x, -20.0)) * np.exp(-0.5 * z * z),
        2.0 * np.exp(0.5 * k * k - k * z),
    )
    peak = float(val.max()) if val.size else 1.0
    return val / max(peak, 1e-300)


def _render_spot(od: np.ndarray, s: SpotSpec, spec: PlateSpec, geom: dict, amp_od: float, rng: np.random.Generator) -> SpotTruth:
    h, w = od.shape
    pitch = geom["lane_pitch"]
    x0 = geom["lane_centres_x"][s.lane] + s.x_offset_frac * pitch
    y0 = geom["origin_row"] + s.y_frac * (geom["front_row_pos"] - geom["origin_row"])
    sig_x = max(0.8, s.width_frac * pitch)
    sig_y = max(0.8, sig_x * s.aspect)
    yy, xx = np.mgrid[0:h, 0:w]

    tau = 0.0
    quantifiable = True
    if s.shape is SpotShape.GAUSSIAN:
        prof = np.exp(-(((xx - x0) ** 2) / (2 * sig_x**2) + ((yy - y0) ** 2) / (2 * sig_y**2)))
    elif s.shape is SpotShape.EMG:
        tau = s.tau_frac * sig_y
        prof = np.exp(-((xx - x0) ** 2) / (2 * sig_x**2)) * _emg_profile(yy[:, 0], y0, sig_y, tau)[:, None]
    elif s.shape is SpotShape.HALO:
        r = np.sqrt((xx - x0) ** 2 + (yy - y0) ** 2)
        r0 = 2.5 * sig_x
        ring = np.exp(-((r - r0) ** 2) / (2 * (0.6 * sig_x) ** 2))
        core = 0.5 * np.exp(-(r**2) / (2 * sig_x**2))
        prof = np.maximum(ring, core)
    elif s.shape is SpotShape.STREAK:
        quantifiable = False
        # Ribbon from the origin up to y0 with a ragged smooth envelope.
        top = min(y0, geom["origin_row"] - 2.0)
        envelope = np.zeros(h)
        lo, hi = int(max(0, top)), int(min(h - 1, geom["origin_row"]))
        if hi > lo:
            t = np.linspace(0, 1, hi - lo + 1)
            rough = rng.normal(0, 1, size=t.size)
            rough = ndimage.gaussian_filter1d(rough, max(2.0, (hi - lo) / 8.0))
            rough = (rough - rough.min()) / max(1e-9, rough.max() - rough.min())
            envelope[lo : hi + 1] = 0.55 + 0.45 * rough * (0.4 + 0.6 * t)
        prof = np.exp(-((xx - x0) ** 2) / (2 * (1.3 * sig_x) ** 2)) * envelope[:, None]
    else:  # pragma: no cover - enum is closed
        raise ValueError(s.shape)

    contribution = amp_od * prof
    od += contribution
    # Position convention D-014: the darkest row of THIS spot's contribution at its column,
    # sub-pixel refined by a parabola through the three rows around the argmax.
    col = contribution[:, int(np.clip(round(x0), 0, w - 1))]
    i = int(np.argmax(col))
    if 0 < i < h - 1 and (col[i - 1] - 2 * col[i] + col[i + 1]) < 0:
        y_mode = i + 0.5 * (col[i - 1] - col[i + 1]) / (col[i - 1] - 2 * col[i] + col[i + 1])
    else:
        y_mode = float(i)
    return SpotTruth(
        lane=s.lane,
        x=float(x0),
        y=float(y0),
        y_mode=float(y_mode),
        shape=s.shape.value,
        amplitude_od=float(amp_od),
        amplitude_sigma=float(s.amplitude_sigma),
        sigma_x=float(sig_x),
        sigma_y=float(sig_y),
        tau=float(tau),
        area_od=float(contribution.sum()),
        quantifiable=quantifiable,
    )


def _stroke(od: np.ndarray, rng: np.random.Generator, x0: float, y0: float, length: float, ink_od: float, wiggle: float = 0.6) -> None:
    """One handwriting stroke: a smooth random walk of ~`length` px, 1-2 px wide."""
    n = max(4, int(length))
    ang = rng.uniform(0, 2 * math.pi)
    dang = ndimage.gaussian_filter1d(rng.normal(0, wiggle, size=n), 2.0)
    angs = ang + np.cumsum(dang)
    xs = x0 + np.cumsum(np.cos(angs))
    ys = y0 + np.cumsum(np.sin(angs))
    h, w = od.shape
    ix = np.clip(np.round(xs).astype(int), 0, w - 1)
    iy = np.clip(np.round(ys).astype(int), 0, h - 1)
    od[iy, ix] = np.maximum(od[iy, ix], ink_od)
    if rng.uniform() < 0.5:  # occasionally 2 px wide
        ix2 = np.clip(ix + 1, 0, w - 1)
        od[iy, ix2] = np.maximum(od[iy, ix2], ink_od * 0.8)


def _handwriting(od: np.ndarray, spec: PlateSpec, geom: dict, rng: np.random.Generator) -> None:
    h, w = od.shape
    if spec.handwriting in (Handwriting.HEADER, Handwriting.HEADER_LABELS):
        r0, r1 = geom["header_band"]
        n_strokes = max(3, int(w / 12))
        for _ in range(n_strokes):
            ink = rng.uniform(0.053, 0.162)  # measured ink OD band (F7)
            x = rng.uniform(2, w - 3)
            y = rng.uniform(r0 + 1, max(r0 + 2, r1 - 2))
            _stroke(od, rng, x, y, length=rng.uniform(3, max(4.0, (r1 - r0) * 1.6)), ink_od=ink)
    if spec.handwriting is Handwriting.HEADER_LABELS:
        r0, r1 = geom["label_band"]
        for lane_x in geom["lane_centres_x"]:
            ink = rng.uniform(0.053, 0.162)
            for _ in range(rng.integers(1, 3)):
                x = lane_x + rng.uniform(-2, 2)
                y = rng.uniform(r0 + 1, max(r0 + 2, r1 - 1))
                _stroke(od, rng, x, y, length=rng.uniform(2, max(3.0, (r1 - r0) * 1.2)), ink_od=ink)


# ---------------------------------------------------------------- composition


def _rotate_into_scene(plate_rgb: np.ndarray, spec: PlateSpec, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, list[tuple[float, float]]]:
    """Rotate the plate by tilt into a dark background scene; return (scene, alpha, corners)."""
    h, w = plate_rgb.shape[:2]
    th = math.radians(spec.tilt_deg)
    ct, st = math.cos(th), math.sin(th)

    # Rotated bounding box of the plate. Pixel-CENTRE convention (M-005): the rendered plate
    # is the set of pixels whose plate coords satisfy 0 <= x <= w-1, so its physical corners
    # are at the outermost pixel centres, and rotation is about the centre of that grid.
    corners_plate = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=float)
    centre = np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    rot = np.array([[ct, -st], [st, ct]])
    rotated = (corners_plate - centre) @ rot.T

    mx = spec.margin_frac * max(w, h) + 2.0
    half_w = float(np.abs(rotated[:, 0]).max()) + mx
    half_h = float(np.abs(rotated[:, 1]).max()) + mx
    sw, sh = int(math.ceil(2 * half_w)), int(math.ceil(2 * half_h))
    scentre = np.array([sw / 2.0, sh / 2.0])
    corners_scene = rotated + scentre

    # Inverse map: scene pixel -> plate coords.
    yy, xx = np.mgrid[0:sh, 0:sw]
    d = np.stack([xx - scentre[0], yy - scentre[1]], axis=-1)
    plate_xy = d @ rot  # inverse rotation (rot is orthogonal; @rot == @inv(rot).T ... explicit below)
    # careful: inverse of rot is rot.T; applying to row-vectors: v @ rot means rot.T @ v^T — correct inverse.
    px = plate_xy[..., 0] + centre[0]
    py = plate_xy[..., 1] + centre[1]

    inside = (px >= 0) & (px <= w - 1) & (py >= 0) & (py <= h - 1)
    alpha = ndimage.gaussian_filter(inside.astype(np.float64), 0.6)  # soft 1px edge, realistic blend
    alpha = np.clip(alpha, 0.0, 1.0)

    scene = np.empty((sh, sw, 3), dtype=np.float64)
    coords = np.stack([np.clip(py, 0, h - 1).ravel(), np.clip(px, 0, w - 1).ravel()])
    for ch in range(3):
        sampled = ndimage.map_coordinates(plate_rgb[:, :, ch], coords, order=1, mode="nearest").reshape(sh, sw)
        bgv = spec.background_rgb[ch]
        bg_noise = rng.normal(0.0, 2.0, size=(sh, sw))
        scene[:, :, ch] = alpha * sampled + (1.0 - alpha) * (bgv + bg_noise)
    return scene, alpha, [(float(cx), float(cy)) for cx, cy in corners_scene]


def _crop_for_overrun(scene: np.ndarray, corners: list[tuple[float, float]], spec: PlateSpec, rng: np.random.Generator) -> tuple[np.ndarray, list[tuple[float, float]]]:
    sh, sw = scene.shape[:2]
    ys = [c[1] for c in corners]
    top_cut = bottom_cut = 0
    plate_top, plate_bot = min(ys), max(ys)
    if spec.frame_overrun in (Overrun.TOP, Overrun.BOTH):
        top_cut = int(plate_top + rng.uniform(0.05, 0.15) * (plate_bot - plate_top))
    if spec.frame_overrun in (Overrun.BOTTOM, Overrun.BOTH):
        bottom_cut = int(sh - (plate_bot - rng.uniform(0.05, 0.15) * (plate_bot - plate_top)))
    y0, y1 = top_cut, sh - bottom_cut
    cropped = scene[y0:y1]
    new_corners = [(cx, cy - y0) for cx, cy in corners]
    return cropped, new_corners


# ---------------------------------------------------------------- main entry


def make_plate(spec: PlateSpec, seed: int) -> tuple[np.ndarray, GroundTruth]:
    rng = np.random.Generator(np.random.PCG64(seed))
    h, w = spec.plate_h, spec.plate_w

    # -- structure geometry (plate coords)
    pitch = w / spec.n_lanes
    lane_centres = tuple((i + 0.5) * pitch for i in range(spec.n_lanes))
    origin_row = spec.origin_row_frac * h
    front_pos = spec.front_row_frac * h
    header_band = (0, int(spec.header_frac * h)) if spec.handwriting is not Handwriting.NONE else (0, 0)
    label_band = (int(min(h - 1, origin_row + 0.04 * h)), int(min(h, origin_row + 0.11 * h)))
    geom = {
        "lane_pitch": pitch,
        "lane_centres_x": lane_centres,
        "origin_row": origin_row,
        "front_row_pos": front_pos,
        "header_band": header_band,
        "label_band": label_band,
    }

    surface, swing_true = _illumination(spec, rng)
    sigma_od = spec.noise_sd / (spec.base_green * LN10)

    # -- OD field
    od = np.zeros((h, w), dtype=np.float64)
    spot_truths: list[SpotTruth] = []
    streak_lanes: set[int] = set()
    for s in spec.spots:
        if s.lane in spec.empty_lanes:
            raise ValueError(f"spot assigned to empty lane {s.lane}")
        amp_od = s.amplitude_sigma * sigma_od
        truth = _render_spot(od, s, spec, geom, amp_od, rng)
        spot_truths.append(truth)
        if s.shape is SpotShape.STREAK:
            streak_lanes.add(s.lane)

    dot_truth: list[tuple[float, float]] = []
    if spec.origin_dots:
        yy, xx = np.mgrid[0:h, 0:w]
        for lane in range(spec.n_lanes):
            if lane in spec.empty_lanes:
                continue
            n_dots = int(rng.integers(1, 3))
            for _ in range(n_dots):
                dx = float(rng.uniform(-0.08, 0.08) * pitch)
                x0, y0 = lane_centres[lane] + dx, origin_row + float(rng.uniform(-1.0, 1.0))
                r = max(0.9, pitch / 14.0)
                dot = rng.uniform(0.15, 0.35) * np.exp(-(((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * r * r)))
                od += dot
                dot_truth.append((x0, y0))

    front_row: float | None = None
    if spec.front_line:
        front_row = front_pos
        r = int(round(front_pos))
        if 0 <= r < h:
            od[r, :] = np.maximum(od[r, :], rng.uniform(0.06, 0.15))

    _handwriting(od, spec, geom, rng)

    # -- intensity, noise, exposure, quantisation (green channel carries the signal)
    intensity = surface * np.power(10.0, -od)
    noise = rng.normal(0.0, 1.0, size=(h, w))
    if spec.noise_corr_px > 0.05:
        noise = ndimage.gaussian_filter(noise, spec.noise_corr_px)
        noise /= max(1e-12, float(noise.std()))
    g = intensity + spec.noise_sd * noise

    # Exposure scale for the clip target (measured on plate pixels, pre-quantisation).
    if spec.clip_fraction > 0.0:
        q = float(np.quantile(g, 1.0 - spec.clip_fraction))
        scale = (255.0 / 255.0) / max(q, 1e-6)  # value q maps exactly to 1.0 (=255)
    else:
        scale = min(1.0, (252.0 / 255.0) / max(float(g.max()), 1e-6))
    g_u8 = np.clip(np.round(g * scale * 255.0), 0, 255)

    # Red/blue: scaled copies of the *unclipped* green signal with their own noise.
    plate_rgb = np.empty((h, w, 3), dtype=np.float64)
    for ch, ratio in ((0, spec.red_over_green), (2, spec.blue_over_green)):
        cn = rng.normal(0.0, 1.0, size=(h, w))
        if spec.noise_corr_px > 0.05:
            cn = ndimage.gaussian_filter(cn, spec.noise_corr_px)
            cn /= max(1e-12, float(cn.std()))
        plate_rgb[:, :, ch] = np.clip((g * ratio + spec.noise_sd * cn) * scale * 255.0, 0, 255)
    plate_rgb[:, :, 1] = g_u8
    clip_actual = float(np.mean(g_u8 >= 255))

    # -- scene composition
    scene, _alpha, corners = _rotate_into_scene(plate_rgb, spec, rng)
    scene, corners = _crop_for_overrun(scene, corners, spec, rng)
    image = np.clip(np.round(scene), 0, 255).astype(np.uint8)

    truth = GroundTruth(
        corners_xy=tuple(corners),
        plate_w=w,
        plate_h=h,
        tilt_deg=float(spec.tilt_deg),
        image_w=int(image.shape[1]),
        image_h=int(image.shape[0]),
        lane_centres_x=lane_centres,
        lane_pitch=float(pitch),
        origin_row=float(origin_row),
        front_row=front_row,
        header_band=header_band,
        label_band=label_band,
        origin_dot_xy=tuple(dot_truth),
        spots=tuple(spot_truths),
        empty_lanes=tuple(spec.empty_lanes),
        streak_lanes=tuple(sorted(streak_lanes)),
        sigma_od_analytic=float(sigma_od),
        noise_sd=float(spec.noise_sd),
        illum_swing_true=float(swing_true),
        base_green=float(spec.base_green),
        exposure_scale=float(scale),
        clip_fraction_actual=clip_actual,
    )
    return image, truth


def make_textured_blank(
    spec: PlateSpec, residual_tile: np.ndarray, seed: int
) -> tuple[np.ndarray, GroundTruth]:
    """A blank plate carrying REAL noise texture (eval Null B; brief Gate 4).

    The given residual patch (from a blank band of a real plate, in normalised-green units) is
    tiled mirror-wise over the synthetic illumination surface at a seeded phase shift, replacing
    the generator's synthetic noise. No spots, no dots, no ink — provably nothing to detect,
    with the plate's own spatial correlation and compression texture preserved.
    """
    blank = PlateSpec(
        plate_w=spec.plate_w,
        plate_h=spec.plate_h,
        n_lanes=spec.n_lanes,
        tilt_deg=spec.tilt_deg,
        frame_overrun=spec.frame_overrun,
        margin_frac=spec.margin_frac,
        base_green=spec.base_green,
        illum_swing=spec.illum_swing,
        hotspot_strength=spec.hotspot_strength,
        noise_sd=0.0,
        noise_corr_px=0.0,
        clip_fraction=spec.clip_fraction,
        spots=(),
        empty_lanes=(),
        origin_dots=False,
        front_line=False,
        handwriting=Handwriting.NONE,
        red_over_green=spec.red_over_green,
        blue_over_green=spec.blue_over_green,
        background_rgb=spec.background_rgb,
    )
    rng = np.random.Generator(np.random.PCG64(seed))
    h, w = blank.plate_h, blank.plate_w
    # Mirror-tile the residual to cover the plate, with a seeded phase shift.
    th, tw = residual_tile.shape
    reps_y = h // th + 2
    reps_x = w // tw + 2
    rows = []
    for iy in range(reps_y):
        row = []
        for ix in range(reps_x):
            t = residual_tile
            if iy % 2 == 1:
                t = t[::-1]
            if ix % 2 == 1:
                t = t[:, ::-1]
            row.append(t)
        rows.append(np.concatenate(row, axis=1))
    big = np.concatenate(rows, axis=0)
    oy = int(rng.integers(0, th))
    ox = int(rng.integers(0, tw))
    field = big[oy : oy + h, ox : ox + w]

    # Rebuild make_plate's photometric path with the real texture as the noise term.
    rng2 = np.random.Generator(np.random.PCG64(seed))
    surface, swing_true = _illumination(blank, rng2)
    g = surface + field
    if blank.clip_fraction > 0.0:
        q = float(np.quantile(g, 1.0 - blank.clip_fraction))
        scale = 1.0 / max(q, 1e-6)
    else:
        scale = min(1.0, (252.0 / 255.0) / max(float(g.max()), 1e-6))
    g_u8 = np.clip(np.round(g * scale * 255.0), 0, 255)
    plate_rgb = np.empty((h, w, 3), dtype=np.float64)
    for ch, ratio in ((0, blank.red_over_green), (2, blank.blue_over_green)):
        plate_rgb[:, :, ch] = np.clip(g * ratio * scale * 255.0, 0, 255)
    plate_rgb[:, :, 1] = g_u8
    clip_actual = float(np.mean(g_u8 >= 255))

    scene, _alpha, corners = _rotate_into_scene(plate_rgb, blank, rng2)
    scene, corners = _crop_for_overrun(scene, corners, blank, rng2)
    image = np.clip(np.round(scene), 0, 255).astype(np.uint8)

    pitch = w / blank.n_lanes
    truth = GroundTruth(
        corners_xy=tuple(corners),
        plate_w=w,
        plate_h=h,
        tilt_deg=float(blank.tilt_deg),
        image_w=int(image.shape[1]),
        image_h=int(image.shape[0]),
        lane_centres_x=tuple((i + 0.5) * pitch for i in range(blank.n_lanes)),
        lane_pitch=float(pitch),
        origin_row=float(blank.origin_row_frac * h),
        front_row=None,
        header_band=(0, 0),
        label_band=(int(min(h - 1, blank.origin_row_frac * h + 0.04 * h)), int(min(h, blank.origin_row_frac * h + 0.11 * h))),
        origin_dot_xy=(),
        spots=(),
        empty_lanes=tuple(range(blank.n_lanes)),
        streak_lanes=(),
        sigma_od_analytic=float(np.std(field) / (blank.base_green * LN10)),
        noise_sd=float(np.std(field)),
        illum_swing_true=float(swing_true),
        base_green=float(blank.base_green),
        exposure_scale=float(scale),
        clip_fraction_actual=clip_actual,
    )
    return image, truth


def random_spec(rng: np.random.Generator, **overrides) -> PlateSpec:
    """A PlateSpec drawn from the corpus-calibrated ranges (spec 05 §12.2 table)."""
    w = int(rng.integers(71, 401))
    h = int(rng.integers(max(120, int(w * 1.4)), min(761, int(w * 2.4))))
    n_lanes = int(rng.integers(3, 7))
    n_spots = int(rng.integers(0, 5))
    empty = tuple(int(x) for x in rng.choice(n_lanes, size=rng.integers(0, 2), replace=False))
    spots = []
    for _ in range(n_spots):
        lane = int(rng.integers(0, n_lanes))
        if lane in empty:
            continue
        shapes = (SpotShape.GAUSSIAN, SpotShape.EMG, SpotShape.EMG, SpotShape.STREAK, SpotShape.HALO)
        shape = shapes[int(rng.choice(len(shapes), p=[0.35, 0.25, 0.2, 0.1, 0.1]))]
        spots.append(
            SpotSpec(
                lane=lane,
                y_frac=float(rng.uniform(0.05, 0.95)),
                amplitude_sigma=float(rng.uniform(1.0, 30.0)),
                shape=shape,
                width_frac=float(rng.uniform(0.10, 0.28)),
                aspect=float(rng.uniform(0.7, 1.6)),
                tau_frac=float(rng.uniform(0.5, 3.0)),
                x_offset_frac=float(rng.uniform(-0.06, 0.06)),
            )
        )
    base = PlateSpec(
        plate_w=w,
        plate_h=h,
        n_lanes=n_lanes,
        tilt_deg=float(rng.uniform(0.0, 12.0)),
        frame_overrun=(Overrun.NONE, Overrun.NONE, Overrun.NONE, Overrun.TOP, Overrun.BOTTOM, Overrun.BOTH)[int(rng.integers(0, 6))],
        base_green=float(rng.uniform(0.78, 0.95)),
        illum_swing=float(rng.uniform(0.11, 0.25)),  # floor 0.11: A-007 estimator reads ~0.01 low
        hotspot_strength=float(rng.uniform(0.2, 0.8)),
        noise_sd=float(rng.uniform(0.007, 0.05)),
        noise_corr_px=float(rng.uniform(0.4, 1.0)),
        clip_fraction=float(rng.uniform(0.14, 0.60)) if rng.uniform() < 1.0 / 3.0 else 0.0,
        spots=tuple(spots),
        empty_lanes=empty,
        origin_dots=bool(rng.uniform() < 0.9),
        front_line=bool(rng.uniform() < 0.15),
        handwriting=(Handwriting.NONE, Handwriting.HEADER, Handwriting.HEADER_LABELS)[int(rng.choice(3, p=[0.1, 0.2, 0.7]))],
    )
    return replace(base, **overrides) if overrides else base
