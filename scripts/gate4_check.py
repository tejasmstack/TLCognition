"""Gate 4 evidence: the null battery and the dual criterion (brief §6; spec 05 §12.3).

  blank_plate_false_positives_per_plate <= 0.2   (mean over >= 200 blanks: 120 synthetic
                                                  generator blanks + 80 REAL-noise-texture
                                                  blanks tiled from P33's empty band)
  AND recall >= 0.95 on synthetic spots of amplitude >= 5 sigma
Both at ONE shipped operating point, chosen on a TUNING split (disjoint seeds) and evaluated
on the EVAL split reported here. The full FP-vs-recall curve is committed either way; if the
two cannot be met together, that is a finding, not a knob to quietly turn (§10).

Operating point = thresholds on the ensemble evidence: agreement >= A* and median MC-p <= P*.
"""

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tlc.core.determinism  # noqa: F401, E402  (sets BLAS env; MUST import before numpy)

# isort: split

import json  # noqa: E402

import imageio.v3 as iio  # noqa: E402
import numpy as np  # noqa: E402

from tlc.core.canonical_json import canonical_json  # noqa: E402
from tlc.pipeline.configs import Config  # noqa: E402
from tlc.pipeline.ensemble import run_ensemble_lane  # noqa: E402
from tlc.pipeline.geometry import analyse_geometry  # noqa: E402
from tlc.pipeline.noise import estimate_noise, prepass_exclusion_mask  # noqa: E402
from tlc.pipeline.photometry import compute_od  # noqa: E402
from tlc.pipeline.prep import rectify_and_mask  # noqa: E402
from tlc.synth.generator import make_plate, make_textured_blank  # noqa: E402
from tlc.synth.spec import Handwriting, PlateSpec, SpotShape, SpotSpec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GRID_PATH = ROOT / "config" / "ensemble" / "CONFIG_GRID_v1.json"
N_SURR = 60
A_GRID = [0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]  # D-015 scale
P_GRID = [0.0164, 0.0656]  # 1/61 floor and 4x floor at N_SURR=60 (p barely moves the curve)
Z_GRID = [0.0, 6.0, 8.0, 10.0]  # spec 01 §2.4: agreement alone is low-resolution; combine with z_med
FP_BOUND = 0.2
RECALL_BOUND = 0.95


def load_grid() -> tuple[list[Config], np.ndarray]:
    doc = json.loads(GRID_PATH.read_text())
    cfgs, weights = [], []
    for row in doc["configs"]:
        m, rest = row["key"].split("@")
        r, sv, ex, pm = rest.split("/")
        cfgs.append(Config(m, int(r), sv, ex, pm))
        weights.append(row["weight"])
    return cfgs, np.array(weights)


TILE_SCREEN_MAD = 3.0    # pre-registered (M-010, attempt 3): every lane-window spot-scale bump < 3 MAD
TILE_MIN_ROWS = 80
TILE_REPORT: dict = {}


def cleanest_real_region() -> np.ndarray:
    """Real noise texture from the corpus region that a PRE-REGISTERED rule selects as blank.

    Rule (A-017, second amendment): over every unique low-clip (<= 2%) plate, rectify, take the
    poly3 residual, and score each row by the worst lane-window row-mean bump (detrended at
    5 FWHM, FWHM-smoothed, in MAD units of that statistic) — the detector's own statistic. Keep
    the longest full-width run of rows with score < TILE_SCREEN_MAD; the plate with the longest
    qualifying run (>= TILE_MIN_ROWS) is the source. Recorded in TILE_REPORT. P33's gutters
    failed this rule (spot halos at row ~168); one region in the corpus passes.
    """
    from scipy import ndimage

    audit = json.loads((ROOT / "dataset" / "audit.json").read_text())
    cands = [r for r in audit["images"] if r["duplicate_of"] is None and r["in_plate_green_clip_fraction"] <= 0.02]
    best = None
    for rec in sorted(cands, key=lambda r: r["file"]):
        p = ROOT / "dataset" / rec["file"]
        img = iio.imread(p)
        geo = analyse_geometry(img)
        if not geo.found:
            continue
        pp = rectify_and_mask(img, geo)
        odr = compute_od(pp.green, pp.valid, "poly3", 0)
        h, w = pp.green.shape
        if w < 90 or h < 160:
            continue
        resid = np.where(odr.od_valid, pp.green - odr.i0, 0.0)
        pitch = w / 4.0
        fwhm = 2.355 * 0.18 * pitch
        hw = 0.275 * pitch
        b0, b1 = int(0.20 * h), int(0.82 * h)
        bumps = []
        for i in range(4):
            xc = (i + 0.5) * pitch
            rm = ndimage.gaussian_filter1d(resid[b0:b1, int(xc - hw) : int(xc + hw) + 1].mean(axis=1), fwhm / 2.355)
            tr = ndimage.median_filter(rm, size=max(11, int(5 * fwhm) | 1), mode="nearest")
            bumps.append(rm - tr)
        bmat = np.stack(bumps)
        mad = 1.4826 * float(np.median(np.abs(bmat - np.median(bmat))))
        score = np.abs(bmat).max(axis=0) / max(mad, 1e-12)
        ok = score < TILE_SCREEN_MAD
        runs, start = [], None
        for k, v_ in enumerate(list(ok) + [False]):
            if v_ and start is None:
                start = k
            elif not v_ and start is not None:
                runs.append((start, k))
                start = None
        if not runs:
            continue
        r0, r1 = max(runs, key=lambda t: t[1] - t[0])
        if r1 - r0 >= TILE_MIN_ROWS and (best is None or r1 - r0 > best[0]):
            tile = resid[b0 + r0 : b0 + r1]
            best = (r1 - r0, rec["file"], b0 + r0, b0 + r1, w, float(score[r0:r1].max()), tile - float(np.median(tile)))
    if best is None:
        raise RuntimeError("no corpus region passes the pre-registered blank screen; no honest real-texture null available")
    TILE_REPORT.update({"rule": f"longest full-width run with every lane-window spot-scale bump < {TILE_SCREEN_MAD} MAD, >= {TILE_MIN_ROWS} rows, over unique plates with clip <= 2%",
                        "source_file": best[1], "rows": [int(best[2]), int(best[3])], "width_px": int(best[4]),
                        "max_score_mad": round(best[5], 3), "n_candidate_plates": len(cands)})
    return best[6]


def p33_residual_tile() -> np.ndarray:  # name kept for callers; the source is now rule-selected
    return cleanest_real_region()


def blank_specs() -> list[tuple[str, int]]:
    out = [("synth", 7000 + i) for i in range(120)]
    out += [("textured", 8000 + i) for i in range(80)]
    return out


def spotted_specs() -> list[int]:
    return [9000 + i for i in range(250)]  # n >= 300 observable 5-sigma spots (reviewer: n=128 cannot decide 0.95)


def _spotted_plate(seed: int) -> tuple[PlateSpec, int]:
    rng = np.random.Generator(np.random.PCG64(seed))
    amps = [5.0, 6.0, 8.0, 12.0, 20.0]
    shapes = [SpotShape.GAUSSIAN, SpotShape.EMG]
    spots = []
    lanes = [1, 3] if rng.uniform() < 0.5 else [1, 2, 3]
    for i, lane in enumerate(lanes):
        spots.append(
            SpotSpec(
                lane=lane,
                y_frac=float(rng.uniform(0.15, 0.85)),
                amplitude_sigma=float(amps[int(rng.integers(0, len(amps)))]),
                shape=shapes[i % 2],
                tau_frac=float(rng.uniform(0.8, 2.0)),
            )
        )
    spec = PlateSpec(
        plate_w=int(rng.integers(85, 160)),
        plate_h=int(rng.integers(150, 300)),
        spots=tuple(spots),
        clip_fraction=float(rng.choice([0.0, 0.0, 0.14])),
        tilt_deg=float(rng.uniform(0.4, 4.1)),
        handwriting=Handwriting.HEADER_LABELS,
    )
    return spec, seed


_TILE: np.ndarray | None = None


def _init_worker(tile: np.ndarray) -> None:
    global _TILE
    _TILE = tile


CACHE_DIR = ROOT / "reports" / "gate4_cache"


def process_one(job: tuple[str, str, int]) -> dict:
    """Checkpointed per plate (M-008): a finished plate is never recomputed."""
    kind, family, seed = job
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{kind}_{family}_{seed}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    out = _process_one(job)
    cache_file.write_text(json.dumps(out, sort_keys=True))
    return out


def _process_one(job: tuple[str, str, int]) -> dict:
    kind, family, seed = job
    if kind == "blank" and family == "textured":
        rng = np.random.Generator(np.random.PCG64(seed))
        spec = PlateSpec(
            plate_w=int(rng.integers(85, 160)), plate_h=int(rng.integers(150, 300)),
            clip_fraction=float(rng.choice([0.0, 0.14])), tilt_deg=float(rng.uniform(0.4, 4.1)),
        )
        img, gt = make_textured_blank(spec, _TILE, seed)
    elif kind == "blank":
        rng = np.random.Generator(np.random.PCG64(seed))
        spec = PlateSpec(
            plate_w=int(rng.integers(85, 160)), plate_h=int(rng.integers(150, 300)),
            spots=(),
            clip_fraction=float(rng.choice([0.0, 0.14, 0.30])),
            tilt_deg=float(rng.uniform(0.4, 4.1)),
            handwriting=Handwriting.HEADER_LABELS if seed % 2 else Handwriting.NONE,
        )
        img, gt = make_plate(spec, seed=seed)
    else:
        spec, seed = _spotted_plate(seed)
        img, gt = make_plate(spec, seed=seed)

    geo = analyse_geometry(img)
    pp = rectify_and_mask(img, geo)
    band = (int(gt.header_band[1] + 2), int(gt.origin_row - 4))
    excl = prepass_exclusion_mask(
        pp.green, pp.valid, 1.18 * 0.18 * gt.lane_pitch,
        ((0, gt.header_band[1]), (gt.label_band[0], pp.green.shape[0])),
    )
    noise = estimate_noise(pp.green, pp.valid, excl)
    grid, weights = load_grid()
    # Truth-matching tolerance per spec 05 §12.6 (|dRst| <= 0.03), in px (D-012).
    migration = gt.origin_row - spec.front_row_frac * gt.plate_h
    tol = max(0.4 * 2.355 * max(1.0, 0.18 * gt.lane_pitch), 0.03 * migration)
    clip_src = (img[:, :, 1] >= 254).astype(np.float64)
    from tlc.pipeline.geometry import warp_rectify
    clip_rect, _ = warp_rectify(clip_src, geo.homography, geo.rectified_shape)

    rows = []
    od_cache: dict = {}  # one per plate: 16 distinct background fits, not 24 x lanes (M-008)
    for lane in range(spec.n_lanes):
        spots, _ = run_ensemble_lane(
            grid, weights, pp.green, pp.valid, noise, excl, lane,
            gt.lane_centres_x[lane], gt.lane_pitch, list(gt.lane_centres_x), band,
            seed=seed, n_surrogates=N_SURR, od_cache=od_cache,
        )
        for s in spots:
            true_amp = None
            for t in gt.spots:
                if t.lane == lane and abs(s.row - t.y) <= tol:
                    true_amp = max(true_amp or 0.0, t.amplitude_sigma)
            rows.append({"lane": lane, "row": round(s.row, 2), "a": round(s.agreement, 4),
                         "p_med": round(s.p_med, 4), "z_med": round(s.z_med, 2),
                         "true_amp": true_amp})
    truths_5s = []
    unobservable = 0
    hw = 0.275 * gt.lane_pitch
    for tr in gt.spots:
        if not tr.quantifiable or tr.amplitude_sigma < 5.0:
            continue
        y0, y1 = int(max(0, tr.y - 2 * tr.sigma_y)), int(min(clip_rect.shape[0], tr.y + 2 * tr.sigma_y + 1))
        x0, x1 = int(max(0, tr.x - hw)), int(min(clip_rect.shape[1], tr.x + hw + 1))
        box = clip_rect[y0:y1, x0:x1]
        box_clip = float((box >= 0.5).mean()) if box.size else 1.0
        if box_clip >= 0.5:
            unobservable += 1  # F1: nobody can see a spot under saturated pixels (D-012)
            continue
        truths_5s.append({"lane": tr.lane, "y": round(tr.y, 2), "amp": tr.amplitude_sigma,
                          "box_clip": round(box_clip, 3)})
    return {"kind": kind, "family": family, "seed": seed, "spots": rows,
            "truths_5s": truths_5s, "n_unobservable_5s": unobservable, "tol": tol}


def sweep_operating_points(plates: list[dict]) -> list[dict]:
    curve = []
    for a_star in A_GRID:
      for z_star in Z_GRID:
        for p_star in P_GRID:
            fp_n = 0
            blanks = 0
            hit = 0
            total_true = 0
            for pl in plates:
                accepted = [s for s in pl["spots"] if s["a"] >= a_star and s["p_med"] <= p_star and s["z_med"] >= z_star]
                if pl["kind"] == "blank":
                    blanks += 1
                    fp_n += len(accepted)
                else:
                    total_true += len(pl["truths_5s"])
                    for t in pl["truths_5s"]:
                        if any(s["lane"] == t["lane"] and abs(s["row"] - t["y"]) <= pl["tol"] for s in accepted):
                            hit += 1
                    fp_n += sum(1 for s in accepted if s["true_amp"] is None)
            fam_fp = {}
            for fam in ("synth", "textured"):
                fam_plates = [pl for pl in plates if pl["kind"] == "blank" and pl["family"] == fam]
                fam_fp[fam] = round(sum(len([s for s in pl["spots"] if s["a"] >= a_star and s["p_med"] <= p_star and s["z_med"] >= z_star]) for pl in fam_plates) / max(len(fam_plates), 1), 4)
            curve.append({
                "a_star": a_star, "p_star": p_star, "z_star": z_star, "fp_per_blank_by_family": fam_fp,
                "n_unobservable_5s": sum(pl.get("n_unobservable_5s", 0) for pl in plates if pl["kind"] != "blank"),
                "unmatched_detections_per_plate_all": round(fp_n / max(len(plates), 1), 4),
                "fp_per_blank_blanksonly": round((sum(len([s for s in pl["spots"] if s["a"] >= a_star and s["p_med"] <= p_star and s["z_med"] >= z_star]) for pl in plates if pl["kind"] == "blank")) / max(blanks, 1), 4),
                "recall_5s": round(hit / max(total_true, 1), 4),
                "n_blanks": blanks, "n_true_5s": total_true,
            })
    return curve


def main() -> None:
    tile = p33_residual_tile()
    jobs = [("blank", fam, seed) for fam, seed in blank_specs()]
    jobs += [("spotted", "synth", s) for s in spotted_specs()]
    tune_jobs = [j for j in jobs if j[2] % 2 == 0]
    eval_jobs = [j for j in jobs if j[2] % 2 == 1]

    with ProcessPoolExecutor(max_workers=8, initializer=_init_worker, initargs=(tile,)) as ex:
        tune = list(ex.map(process_one, tune_jobs, chunksize=4))
        evalp = list(ex.map(process_one, eval_jobs, chunksize=4))

    tune_curve = sweep_operating_points(tune)
    feasible = [c for c in tune_curve if c["fp_per_blank_blanksonly"] <= FP_BOUND and c["recall_5s"] >= RECALL_BOUND]
    if feasible:
        op = max(feasible, key=lambda c: (c["recall_5s"], -c["fp_per_blank_blanksonly"]))
    else:
        op = min(tune_curve, key=lambda c: (max(0, c["fp_per_blank_blanksonly"] - FP_BOUND) * 5 + max(0, RECALL_BOUND - c["recall_5s"])))

    eval_curve = sweep_operating_points(evalp)
    ev = next(c for c in eval_curve if c["a_star"] == op["a_star"] and c["p_star"] == op["p_star"] and c["z_star"] == op["z_star"])
    fp_eval = ev["fp_per_blank_blanksonly"]
    rc_eval = ev["recall_5s"]
    gate4_pass = bool(feasible) and fp_eval <= FP_BOUND and rc_eval >= RECALL_BOUND

    evidence = {
        "operating_point": {"a_star": op["a_star"], "p_star": op["p_star"], "z_star": op["z_star"],
                            "chosen_on": "tuning split (even seeds)"},
        "tuning": {"curve": tune_curve, "feasible_points": len(feasible)},
        "eval": {"fp_per_blank": fp_eval, "recall_5sigma": rc_eval,
                 "n_blanks": ev["n_blanks"], "n_true_spots_5s": ev["n_true_5s"],
                 "curve": eval_curve},
        "battery": {"n_blank_total": 200, "n_textured": 80, "n_spotted": 250,
                    "n_surrogates": N_SURR, "grid": "CONFIG_GRID_v1", "tile_screen": TILE_REPORT},
        "pooled_all_300_blanks_curve": sweep_operating_points(tune + evalp),
        "bounds": {"fp_per_blank": FP_BOUND, "recall_5sigma": RECALL_BOUND},
        "gate4_pass": gate4_pass,
    }
    (ROOT / "reports" / "gate4_evidence.json").write_text(canonical_json(evidence) + "\n")
    print(canonical_json({"operating_point": evidence["operating_point"],
                          "eval_fp_per_blank": fp_eval, "eval_recall_5sigma": rc_eval,
                          "feasible_points_tuning": len(feasible), "gate4_pass": gate4_pass}))


if __name__ == "__main__":
    main()
