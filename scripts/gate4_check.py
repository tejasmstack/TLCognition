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
from tlc.synth.stats import measure_plate_stats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GRID_PATH = ROOT / "config" / "ensemble" / "CONFIG_GRID_v1.json"
N_SURR = 60
A_GRID = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80]
P_GRID = [0.0164, 0.033, 0.0492, 0.0656]  # multiples of the 1/61 floor at N_SURR=60
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


def p33_residual_tile() -> np.ndarray:
    """Real residual from P33's empty band (normalised green minus poly3 surface)."""
    img = iio.imread(ROOT / "dataset" / "MEHQ-P33 4hr_31st July26.png")
    st = measure_plate_stats(img)
    geo = analyse_geometry(img)
    pp = rectify_and_mask(img, geo)
    odr = compute_od(pp.green, pp.valid, "poly3", 0)
    r0, r1 = st.empty_band_row_range
    # residual in normalised-green units, restricted to the empty band's valid interior
    resid = np.where(odr.od_valid, pp.green - odr.i0, 0.0)
    h = pp.green.shape[0]
    r0 = int(np.clip(r0, 0, h - 8))
    r1 = int(np.clip(max(r1, r0 + 8), r0 + 8, h))
    tile = resid[r0:r1, 6:-6]
    return tile - float(np.median(tile))


def blank_specs() -> list[tuple[str, int]]:
    out = [("synth", 7000 + i) for i in range(120)]
    out += [("textured", 8000 + i) for i in range(80)]
    return out


def spotted_specs() -> list[int]:
    return [9000 + i for i in range(100)]


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
    tol = 0.4 * 2.355 * max(1.0, 0.18 * gt.lane_pitch)

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
    truths_5s = [
        {"lane": t.lane, "y": round(t.y, 2), "amp": t.amplitude_sigma}
        for t in gt.spots if t.quantifiable and t.amplitude_sigma >= 5.0
    ]
    return {"kind": kind, "family": family, "seed": seed, "spots": rows,
            "truths_5s": truths_5s, "tol": tol}


def sweep_operating_points(plates: list[dict]) -> list[dict]:
    curve = []
    for a_star in A_GRID:
        for p_star in P_GRID:
            fp_n = 0
            blanks = 0
            hit = 0
            total_true = 0
            for pl in plates:
                accepted = [s for s in pl["spots"] if s["a"] >= a_star and s["p_med"] <= p_star]
                if pl["kind"] == "blank":
                    blanks += 1
                    fp_n += len(accepted)
                else:
                    total_true += len(pl["truths_5s"])
                    for t in pl["truths_5s"]:
                        if any(s["lane"] == t["lane"] and abs(s["row"] - t["y"]) <= pl["tol"] for s in accepted):
                            hit += 1
                    fp_n += sum(1 for s in accepted if s["true_amp"] is None)
            curve.append({
                "a_star": a_star, "p_star": p_star,
                "fp_per_blank": round(fp_n and fp_n / max(blanks + sum(1 for p in plates if p["kind"] != "blank"), 1) or 0.0, 4),
                "fp_per_blank_blanksonly": round((sum(len([s for s in pl["spots"] if s["a"] >= a_star and s["p_med"] <= p_star]) for pl in plates if pl["kind"] == "blank")) / max(blanks, 1), 4),
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
    ev = next(c for c in eval_curve if c["a_star"] == op["a_star"] and c["p_star"] == op["p_star"])
    fp_eval = ev["fp_per_blank_blanksonly"]
    rc_eval = ev["recall_5s"]
    gate4_pass = bool(feasible) and fp_eval <= FP_BOUND and rc_eval >= RECALL_BOUND

    evidence = {
        "operating_point": {"a_star": op["a_star"], "p_star": op["p_star"],
                            "chosen_on": "tuning split (even seeds)"},
        "tuning": {"curve": tune_curve, "feasible_points": len(feasible)},
        "eval": {"fp_per_blank": fp_eval, "recall_5sigma": rc_eval,
                 "n_blanks": ev["n_blanks"], "n_true_spots_5s": ev["n_true_5s"],
                 "curve": eval_curve},
        "battery": {"n_blank_total": 200, "n_textured": 80, "n_spotted": 100,
                    "n_surrogates": N_SURR, "grid": "CONFIG_GRID_v1"},
        "bounds": {"fp_per_blank": FP_BOUND, "recall_5sigma": RECALL_BOUND},
        "gate4_pass": gate4_pass,
    }
    (ROOT / "reports" / "gate4_evidence.json").write_text(canonical_json(evidence) + "\n")
    print(canonical_json({"operating_point": evidence["operating_point"],
                          "eval_fp_per_blank": fp_eval, "eval_recall_5sigma": rc_eval,
                          "feasible_points_tuning": len(feasible), "gate4_pass": gate4_pass}))


if __name__ == "__main__":
    main()
