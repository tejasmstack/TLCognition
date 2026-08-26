"""The pipeline orchestrator (spec 03 §7.1.2: the only stateful file; still PURE — numpy in,
dataclasses out; no I/O, no schema objects; the assembler outside tlc/pipeline builds the
Result). Every quantity carries its provenance tag as data so NN2 is structural downstream.

Order: geometry -> capture QC gates -> rectify/mask -> annotation bands (convention or operator)
-> lanes (operator map or uniform grid; NEVER from signal, F10) -> noise unit -> OD
-> densitograms -> origin -> ensemble detection (grid and tiers come from the RunConfig the caller
   built from the shipped pipeline config; this module names no config file)
-> EMG fits -> streak -> Rst against the standard-lane anchor -> refusals/flags.
"""

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from tlc.pipeline import flags as F
from tlc.pipeline.configs import Config
from tlc.pipeline.ensemble import EnsembleSpot, run_ensemble_lane
from tlc.pipeline.geometry import PlateGeometry, analyse_geometry, valid_erosion_px
from tlc.pipeline.noise import (
    VIF_ABSTAIN,
    NoiseModel,
    estimate_noise,
    prepass_exclusion_mask,
    profile_autocovariance,
)
from tlc.pipeline.origin import OriginEstimate, find_origin
from tlc.pipeline.peaks import EMGFit, emg, fit_emg
from tlc.pipeline.photometry import (
    LANE_HALFWIDTH_FRAC,
    compute_od,
    lane_densitogram,
    sigma_od_prespot,
)
from tlc.pipeline.prep import CLIP_LEVEL, rectify_and_mask
from tlc.pipeline.rst import combine_position_variance, rst_with_interval
from tlc.pipeline.streak import StreakVerdict, assess_streak

WIDTH_FRAC_NOMINAL = 0.18
STANDARD_LABELS = ("sd", "s")   # reference lane candidates, in preference order
POSITION_MIN_HITS = 3           # D-026: below this the ensemble average is one or two configs, not a consensus
SPOTLIKE_SIGMA_MAX = 1.5        # D-027: a fit wider than 1.5x nominal is not a spot explanation
SPOTLIKE_TAIL_MAX = 2.0         # D-027: nor is one with a tail longer than 2 sigma


def position_estimate(fit, ensemble) -> float:
    """The reported band position (D-014 convention: the darkest row; D-026 estimator).

    The weighted average of the rows found by every config that detected the band beats the primary
    config's EMG mode in the tail (Gate 5 tuning split: p95 0.0091 vs 0.0105 Rst, max 0.017 vs 0.031)
    at the cost of a slightly larger median error (0.0022 vs 0.0007) — far below the 0.05 Rst that is
    the smallest difference of chemical interest. With fewer than POSITION_MIN_HITS configs there is
    no consensus to average, so the fit is used.
    """
    if ensemble.n_hit >= POSITION_MIN_HITS:
        return float(ensemble.row)
    return float(fit.mode if fit.ok else ensemble.row)


@dataclass(frozen=True)
class RunConfig:
    grid: tuple[Config, ...]
    weights: tuple[float, ...]
    grid_id: str
    reported_agreement_min: float
    candidate_agreement_min: float
    p_med_max: float
    z_med_min: float
    n_surrogates: int = 60
    n_lanes: int | None = None                       # operator-provided lane count (None = unknown)
    lane_labels: tuple[str, ...] | None = None       # operator-provided labels (None = refused)
    lane_x_frac: tuple[float, ...] | None = None     # operator-provided lane centres (frac of W)
    header_frac: float = 0.16                        # convention band (chosen)
    label_row_frac: float = 0.90                     # convention band (chosen)
    origin_zone_frac: tuple[float, float] = (0.80, 0.90)  # where to look for origin dots


@dataclass(frozen=True)
class LaneOut:
    index: int
    label: str
    label_provenance: str
    x_center_px: float
    x_center_method: str
    half_width_px: int
    x_seed_provenance: str
    is_empty: bool
    is_streaking: bool
    streak: StreakVerdict
    clip_frac: float
    quantified: bool
    suppression: F.Refusal | None
    at_plate_edge: bool
    profile: np.ndarray
    n_valid_columns: np.ndarray


@dataclass(frozen=True)
class SpotOut:
    id: str
    lane_index: int
    status: str                      # confirmed | candidate | suppressed_streak
    ensemble: EnsembleSpot
    fit: EMGFit
    y_px: float                      # mode (D-014)
    y_var: float                     # Rubin-combined variance (px^2)
    rst: object | None               # RstEstimate | None
    rst_refusal: F.Refusal | None
    amplitude_od: float | None
    area_od_px: float | None
    area_frac_of_lane: float | None
    area_refusal: F.Refusal | None
    snr: float
    box_clip_frac: float
    flags: tuple[str, ...]


@dataclass(frozen=True)
class RunOutput:
    status: str                      # succeeded | refused | degraded
    geometry: PlateGeometry | None
    capture: dict
    verdict: str
    gates_fired: tuple[str, ...]
    bands: tuple[dict, ...]
    lanes: tuple[LaneOut, ...]
    noise: NoiseModel | None
    sigma_od: float | None
    sigma_stability: float | None
    photometry_mode: str
    clip_frac_analysable: float | None
    origin: OriginEstimate | None
    origin_refusal: F.Refusal | None
    anchor: dict | None
    anchor_refusal: F.Refusal | None
    spots: tuple[SpotOut, ...]
    refusals: tuple[F.Refusal, ...]
    flags: tuple[F.Flag, ...]
    idempotency_px: float | None
    valid_frac: float | None
    px_per_lane: float | None
    vif: float | None
    od: np.ndarray | None = None
    od_valid: np.ndarray | None = None
    rectified_shape: tuple[int, int] | None = None
    seed: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


def _focus_metric(green: np.ndarray, mask: np.ndarray) -> float:
    lap = ndimage.laplace(green.astype(np.float64))
    m = mask & np.isfinite(lap)
    if m.sum() < 10:
        return float("nan")
    mean = float(green[m].mean())
    return float(lap[m].var() / max(mean * mean, 1e-12))


def _refine_lane_center(od: np.ndarray, od_valid: np.ndarray, x_seed: float, pitch: float, rows: tuple[int, int]) -> tuple[float, str]:
    """Refine within +/-0.25 pitch of the seed by the OD column-mass centroid; never invents a
    lane (F10) — with no signal the seed is kept."""
    r0, r1 = rows
    lo, hi = int(max(0, x_seed - 0.25 * pitch)), int(min(od.shape[1], x_seed + 0.25 * pitch + 1))
    if hi - lo < 3:
        return x_seed, "seed_kept"
    col = np.where(od_valid[r0:r1, lo:hi], np.maximum(od[r0:r1, lo:hi], 0.0), 0.0).sum(axis=0)
    if col.sum() <= 1e-9 or col.max() <= 0:
        return x_seed, "seed_kept_no_signal"
    xs = np.arange(lo, hi)
    return float((xs * col).sum() / col.sum()), "od_column_mass_centroid_in_window"


def run_plate(rgb: np.ndarray, cfg: RunConfig, seed: int) -> RunOutput:
    refusals: list[F.Refusal] = []
    flags: list[F.Flag] = []
    gates: list[str] = []
    notes: list[str] = []

    geo = analyse_geometry(rgb)
    capture: dict = {"plate_area_frac": geo.plate_area_frac, "frame_overrun": geo.frame_overrun,
                     "tilt_deg": geo.tilt_deg}
    if not geo.found:
        refusals.append(F.e_no_plate(geo.plate_area_frac))
        return RunOutput("refused", geo, capture, "unusable", ("E_NO_PLATE",), (), (), None, None, None,
                         "refused", None, None, None, None, None, (), tuple(refusals), (), None, None, None, None, seed=seed)

    pp = rectify_and_mask(rgb, geo)
    green_src = rgb[:, :, 1]
    in_plate = geo.mask
    capture.update({
        "green_clip_frac_in_plate": float((green_src[in_plate] >= CLIP_LEVEL).mean()) if in_plate.any() else 0.0,
        "green_clip_frac_frame": float((green_src >= CLIP_LEVEL).mean()),
        "black_clip_frac_in_plate": float((green_src[in_plate] <= 1).mean()) if in_plate.any() else 0.0,
        "channel_sat_frac": float((rgb[:, :, :3][in_plate] >= CLIP_LEVEL).any(axis=1).mean()) if in_plate.any() else 0.0,
        "mean_green_in_plate": float(green_src[in_plate].mean() / 255.0) if in_plate.any() else 0.0,
        "focus_metric": _focus_metric(green_src, in_plate),
    })
    h, w = pp.green.shape
    clip_plate = capture["green_clip_frac_in_plate"]

    # --- capture QC gates (spec 01 §6.2; spec 03 §7.3.4)
    verdict = "ok"
    photometry_mode = "full"
    for edge, frac in geo.frame_overrun.items():
        if frac > F.OVERRUN_MAX:
            refusals.append(F.e_frame_overrun(edge, frac))
            flags.append(F.Flag("frame_overrun", "warn", f"The plate runs off the {edge} edge ({frac:.0%} of that border).",
                                "Re-shoot with the whole plate inside the frame.", {edge: round(frac, 4), "gate": F.OVERRUN_MAX}))
            gates.append("E_FRAME_OVERRUN")
    if clip_plate > F.CLIP_UNUSABLE:
        refusals.append(F.e_clip_unusable(clip_plate))
        gates.append("E_CLIP_UNUSABLE")
        verdict, photometry_mode = "unusable", "refused"
    elif clip_plate > F.CLIP_PHOTOMETRY:
        refusals.append(F.e_clip_photometry(clip_plate))
        flags.append(F.Flag("green_clipping_high", "block", f"{clip_plate:.1%} of in-plate green pixels read >= 254. Optical density is undefined there.",
                            "Re-shoot 1-2 stops darker or with a shorter shutter.", {"green_clip_frac_in_plate": round(clip_plate, 4), "gate": F.CLIP_PHOTOMETRY}))
        gates.append("E_CLIP_PHOTOMETRY")
        verdict, photometry_mode = "positions_only", "positions_only"

    # --- lanes: operator map or uniform grid (never from signal, F10)
    n_lanes = cfg.n_lanes
    if n_lanes is None:
        refusals.append(F.e_lane_count_unknown())
        gates.append("E_LANE_COUNT_UNKNOWN")
        n_lanes = 4
        notes.append("lane count unknown: 4-lane uniform grid assumed for densitograms only; lane labels refused")
    pitch = w / n_lanes
    px_per_lane = pitch
    if px_per_lane < F.PX_PER_LANE_MIN:
        refusals.append(F.e_resolution(px_per_lane))
        gates.append("SPOT_RESOLUTION")
        verdict = "unusable"

    header_rows = (0, int(cfg.header_frac * h))
    label_rows = (int(cfg.label_row_frac * h), h)
    # analysable band: below the header, above the ORIGIN ZONE (dots are not migrated analyte)
    band = (header_rows[1] + 2, max(header_rows[1] + 10, int(cfg.origin_zone_frac[0] * h) - 2))
    bands = (
        {"kind": "header", "y0_frac": 0.0, "y1_frac": cfg.header_frac, "provenance": "convention", "source_detail": "config.default_bands"},
        {"kind": "label_row", "y0_frac": cfg.label_row_frac, "y1_frac": 1.0, "provenance": "convention", "source_detail": "config.default_bands"},
    )

    # --- noise unit and OD
    excl = prepass_exclusion_mask(pp.green, pp.valid, 1.18 * WIDTH_FRAC_NOMINAL * pitch, (header_rows, label_rows))
    noise = estimate_noise(pp.green, pp.valid, excl)
    sigma_od = sigma_od_prespot(pp.green, pp.valid, band)
    odr = compute_od(pp.green, pp.valid, "poly3", 0)
    od_cache = {("poly3", 0): odr}
    clip_analysable = float((~pp.valid & pp.valid_geom)[band[0]:band[1]].sum() / max(pp.valid_geom[band[0]:band[1]].sum(), 1))
    hw = LANE_HALFWIDTH_FRAC * pitch
    window_cols = max(1, int(round(2 * hw)))
    c_prof = profile_autocovariance(noise, window_cols)
    sigma_prof = float(math.sqrt(max(c_prof[0], 1e-18)))
    # plate-level VIF (spec 01 §2.1): correlated / white matched-filter noise at the nominal width
    from tlc.pipeline.noise import gaussian_template, prepare_templates
    plate_vif = prepare_templates([gaussian_template(max(1.0, WIDTH_FRAC_NOMINAL * pitch))], c_prof, sigma_prof)[0].vif

    # sigma stability across the grid's radii (D-008 tripwire), diff-estimator on member residuals
    stab_vals = []
    for model, radius in {(c.model, c.radius) for c in cfg.grid if c.model in ("median", "rolling_ball")}:
        o = compute_od(pp.green, pp.valid, model, radius)
        od_cache[(model, radius)] = o
        seg = np.where(o.od_valid, o.od, np.nan)[band[0]:band[1]]
        d = seg[:, 1:] - seg[:, :-1]
        d = d[np.isfinite(d)]
        if d.size > 100:
            stab_vals.append(1.4826 * float(np.median(np.abs(d - np.median(d)))) / math.sqrt(2.0))
    sigma_stability = float((max(stab_vals) - min(stab_vals)) / np.median(stab_vals)) if len(stab_vals) >= 2 else None

    # --- lane seeds and refinement
    seeds = [(i + 0.5) * pitch for i in range(n_lanes)]
    x_seed_prov = "uniform_grid"
    if cfg.lane_x_frac is not None and len(cfg.lane_x_frac) == n_lanes:
        seeds = [f * w for f in cfg.lane_x_frac]
        x_seed_prov = "operator"
    labels = list(cfg.lane_labels) if cfg.lane_labels and len(cfg.lane_labels) == n_lanes else None
    lane_centres = []
    lane_methods = []
    for xs in seeds:
        xc, meth = _refine_lane_center(odr.od, odr.od_valid, xs, pitch, band)
        lane_centres.append(xc)
        lane_methods.append(meth)

    # --- origin (before the band is finalised: the band ends just above the detected origin so
    # comet tails from the origin are seen whole while the spotting dots themselves stay out)
    zone = (int(cfg.origin_zone_frac[0] * h), int(cfg.origin_zone_frac[1] * h))
    origin = find_origin(odr.od, odr.od_valid, max(sigma_od, 1e-6), lane_centres, hw, zone)
    if origin.found:
        dot_r = max(1.0, pitch / 14.0)
        band = (band[0], int(max(band[0] + 10, origin.row - 3.0 * dot_r)))
    origin_refusal = None
    if not origin.found:
        origin_refusal = F.e_no_origin(origin.n_dots, 2)
        refusals.append(origin_refusal)
        gates.append("E_NO_ORIGIN")
    elif origin.row_sd is not None and 2 * 1.645 * origin.row_sd / h > F.ORIGIN_CI_MAX_FRAC:
        origin_refusal = F.e_origin_uncertain(2 * 1.645 * origin.row_sd / h)
        refusals.append(origin_refusal)
        gates.append("ORIGIN_UNCERTAIN")

    # --- per-lane detection, fits, streaks
    fwhm_nom = 2.355 * WIDTH_FRAC_NOMINAL * pitch
    lanes_out: list[LaneOut] = []
    raw_spots: list[tuple[int, EnsembleSpot, EMGFit, str]] = []
    grid = list(cfg.grid)
    weights = np.array(cfg.weights)
    vif_max = float(plate_vif)
    for li, xc in enumerate(lane_centres):
        den = lane_densitogram(odr, li, xc, pitch)
        x_lo, x_hi = den.x_lo, den.x_hi
        lane_valid_geom = pp.valid_geom[band[0]:band[1], x_lo:x_hi]
        lane_clip = float((~pp.valid[band[0]:band[1], x_lo:x_hi] & lane_valid_geom).sum() / max(lane_valid_geom.sum(), 1))
        ens, _ = run_ensemble_lane(grid, weights, pp.green, pp.valid, noise, excl, li, xc, pitch, lane_centres, band,
                                   seed=seed, n_surrogates=cfg.n_surrogates, od_cache=od_cache)
        tiered = [s for s in ens if s.agreement >= cfg.candidate_agreement_min and s.p_med <= cfg.p_med_max and s.z_med >= cfg.z_med_min]
        fits = []
        for s in tiered:
            fits.append(fit_emg(den.profile, s.row, fwhm_nom, s.amplitude_med))
        # Tail statistic from the DOMINANT peak only, and only if its fit is non-degenerate (M-014).
        # Dominant = largest AREA, not largest amplitude (M-015): a streak's smear carries the lane's
        # material while a narrow spike can be taller, and it is the material that decides whether the
        # lane can be quantified at all.
        sigma_nom_px = WIDTH_FRAC_NOMINAL * pitch
        dom = max((f for f in fits if f.ok and f.sigma >= 0.5 * sigma_nom_px), key=lambda f: f.area, default=None)
        tails = [dom.tau / dom.sigma] if dom is not None else []
        # D-027: subtract only the peaks that look like SPOTS (narrow, untailed); a streak-shaped fit
        # is not an explanation of a streak, it is the streak, so it stays in the residual.
        fitted_sum = np.zeros_like(den.profile)
        yy = np.arange(den.profile.shape[0], dtype=float)
        for f in fits:
            if f.ok and f.sigma <= SPOTLIKE_SIGMA_MAX * sigma_nom_px and f.tau <= SPOTLIKE_TAIL_MAX * f.sigma:
                fitted_sum += emg(yy, f.amp, f.mu, f.sigma, f.tau, 0.0)
        streak = assess_streak(den.profile, band, sigma_prof, tails, fwhm_nom, peak_rows=[s.row for s in tiered],
                               fitted_peaks=fitted_sum,
                               dominant_mu=dom.mu if dom is not None else None,
                               dominant_tau=dom.tau if dom is not None else None)
        is_empty = not tiered
        suppression = None
        quantified = photometry_mode == "full"
        if lane_clip > F.CLIP_LANE_ABSTAIN:
            suppression = F.e_lane_clip(li, lane_clip)
            quantified = False
        elif lane_clip > F.CLIP_LANE_AREA and quantified:
            suppression = F.e_area_clip(li, lane_clip)
            quantified = False
        if streak.is_streaking:
            suppression = F.e_streak(li, streak.reason or "streak")
            quantified = False
            flags.append(F.Flag("streaking_lane", "warn", suppression.message, suppression.remedy, {"lane": li}))
        if not quantified and suppression is None:
            suppression = F.e_clip_photometry(clip_plate)
        at_edge = bool(xc - hw < valid_erosion_px(geo.tilt_deg or 0.0) or xc + hw > w - valid_erosion_px(geo.tilt_deg or 0.0))
        label = labels[li] if labels else "UNREADABLE"
        lanes_out.append(LaneOut(li, label, "operator" if labels else "refused", float(xc), lane_methods[li], int(round(hw)),
                                 x_seed_prov, is_empty, streak.is_streaking, streak, lane_clip, quantified, suppression,
                                 at_edge, den.profile, den.n_valid_columns))
        for s, f in zip(tiered, fits, strict=True):
            status = "suppressed_streak" if streak.is_streaking else ("confirmed" if s.agreement >= cfg.reported_agreement_min else "candidate")
            raw_spots.append((li, s, f, status))

    noise_structured = vif_max > VIF_ABSTAIN
    if noise_structured:
        # D-016: measured and flagged, NOT actionable until the VIF estimator is calibrated
        flags.append(F.Flag("noise_structured", "warn", f"Structured residual statistic VIF {vif_max:.1f} > {VIF_ABSTAIN} (uncalibrated estimator, D-016).",
                            "Re-shoot in the cabinet at native resolution if spots look doubtful.", {"vif": round(vif_max, 3)}))

    # --- Rst anchor: highest-agreement confirmed spot in the standard lane
    anchor = None
    anchor_refusal = None
    if labels is None:
        anchor_refusal = F.e_no_reference(["UNREADABLE"] * n_lanes)
    else:
        std_lanes = [i for pref in STANDARD_LABELS for i, lab in enumerate(labels) if lab.lower() == pref]
        cands = [(li, s, f) for (li, s, f, st) in raw_spots if li in std_lanes and st == "confirmed" and f.ok]
        if not cands:
            anchor_refusal = F.e_no_reference(labels)
        else:
            li, s, f = max(cands, key=lambda t: (t[1].agreement, t[1].z_med))
            anchor = {"lane_index": li, "lane_label": labels[li], "y_px": position_estimate(f, s),
                      "var": (f.mu_se or 0.0) ** 2, "ensemble": s}
    if anchor_refusal is not None:
        refusals.append(anchor_refusal)
        gates.append("E_NO_REFERENCE_LANE")
    refusals.append(F.e_no_front())
    flags.append(F.Flag("no_solvent_front", "warn", "No solvent front is drawn, so Rf is not reported. Positions are given as Rst against the standard lane.",
                        "Draw the front in pencil before imaging.", {}))
    flags.append(F.Flag("uncalibrated_confidence", "info", "Confidence values are withheld until the calibration set exists.", "Label 30 plates.",
                        {"labelled_plates": 0.0, "required": 30.0}))

    # --- assemble spots with Rst, areas, refusals
    spots_out: list[SpotOut] = []
    ordered = sorted(raw_spots, key=lambda t: (t[0], t[1].row))
    # anchor spot id assigned after ordering
    anchor_id = None
    lane_total_area: dict[int, float] = {}
    for li, _s, f, st in ordered:
        if st != "suppressed_streak" and f.ok:
            lane_total_area[li] = lane_total_area.get(li, 0.0) + max(f.area, 0.0)
    for idx, (li, s, _f, _st) in enumerate(ordered):
        sid = f"sp_{idx + 1:02d}"
        if anchor is not None and anchor["lane_index"] == li and anchor["ensemble"] is s:
            anchor_id = sid
    for idx, (li, s, f, st) in enumerate(ordered):
        sid = f"sp_{idx + 1:02d}"
        lane = lanes_out[li]
        y_px = position_estimate(f, s)
        within = (f.mu_se or 0.0) ** 2 if f.ok else float("nan")
        between = s.row_spread**2
        y_var = combine_position_variance(within if np.isfinite(within) else between, between, max(1.0, s.n_hit))
        rst_est = None
        rst_ref = None
        if st == "suppressed_streak":
            rst_ref = F.e_streak(li, lane.streak.reason or "streak")
        elif anchor is None or origin_refusal is not None or not origin.found:
            rst_ref = anchor_refusal or origin_refusal
        else:
            rst_est = rst_with_interval(y_px, y_var, anchor["y_px"], anchor["var"], origin.row, (origin.row_sd or 0.5) ** 2)
        box_r0, box_r1 = int(max(0, y_px - 2 * f.sigma - 3)), int(min(h, y_px + 2 * f.sigma + 4))
        box = pp.valid_geom[box_r0:box_r1, int(max(0, lane.x_center_px - hw)):int(min(w, lane.x_center_px + hw + 1))]
        boxv = pp.valid[box_r0:box_r1, int(max(0, lane.x_center_px - hw)):int(min(w, lane.x_center_px + hw + 1))]
        box_clip = float((box & ~boxv).sum() / max(box.sum(), 1))
        amp = area = area_frac = None
        area_ref = None
        if st == "suppressed_streak":
            area_ref = F.e_streak(li, lane.streak.reason or "streak")
        elif not lane.quantified:
            area_ref = lane.suppression
        elif box_clip > 0:
            area_ref = F.e_box_clip(sid)
        elif f.ok:
            amp, area = f.amp, f.area
            tot = lane_total_area.get(li, 0.0)
            area_frac = area / tot if tot > 0 else None
        else:
            area_ref = F.Refusal("E_FIT_FAILED", "The peak model did not converge; amplitude and area are withheld.",
                                 "Inspect the spot in the review screen.", {"spot": sid})
        snr = float(f.amp / max(sigma_prof, 1e-12)) if f.ok else float(s.amplitude_med / max(sigma_prof, 1e-12))
        sflags = []
        if box_clip > 0:
            sflags.append("clipped_neighbourhood")
        if origin.found and abs(y_px - origin.row) < 2 * fwhm_nom:
            sflags.append("near_origin")
        if st == "candidate":
            sflags.append("below_confirm_agreement" if not noise_structured else "noise_structured")
        spots_out.append(SpotOut(sid, li, st, s, f, float(y_px), float(y_var), rst_est, rst_ref, amp, area, area_frac,
                                 area_ref, snr, box_clip, tuple(sflags)))

    idem = None  # the re-warp check is a Phase 10 replay-mode measurement; not run per plate here
    status = "refused" if verdict == "unusable" else ("degraded" if (gates or any(not L.quantified for L in lanes_out)) else "succeeded")
    return RunOutput(status, geo, capture, verdict, tuple(gates), bands, tuple(lanes_out), noise, sigma_od, sigma_stability,
                     photometry_mode, clip_analysable, origin, origin_refusal,
                     {k: v for k, v in anchor.items() if k != "ensemble"} | {"spot_id": anchor_id} if anchor else None,
                     anchor_refusal, tuple(spots_out), tuple(refusals), tuple(flags), idem, float(pp.valid_geom.mean()),
                     float(px_per_lane), float(vif_max), odr.od, odr.od_valid, (h, w), seed, tuple(notes))
