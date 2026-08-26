"""Select CONFIG_GRID_v1: 576 configs -> measured-performance pruning -> greedy max-min
diversity -> K=24, with weights and K_eff (spec 01 §2.3; D-009; A-013).

Selection is on a SYNTHETIC dev set (no human labels exist yet — A-013): 14 spotted plates
spanning shapes/amplitudes/clipping/tilt/size plus 10 blanks. Exclusion is by measured
performance (recall < 0.5 on true spots >= 4 sigma, or > 1.0 false peaks per blank), recorded
in the artifact, never by taste.

Writes config/ensemble/CONFIG_GRID_v1.json (frozen, hashed) and reports/grid_selection.json.
Deterministic: fixed seeds; parallelism over plates only (results order-independent).
"""

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tlc.core.determinism  # noqa: F401, E402  (sets BLAS env; MUST import before numpy)

# isort: split

import numpy as np  # noqa: E402

from tlc.core.canonical_json import canonical_json  # noqa: E402
from tlc.core.hashing import sha256_canonical  # noqa: E402
from tlc.pipeline.configs import Config, all_configs, scan_lane_shared  # noqa: E402
from tlc.pipeline.ensemble import config_weights, k_eff  # noqa: E402
from tlc.pipeline.geometry import analyse_geometry  # noqa: E402
from tlc.pipeline.noise import estimate_noise, prepass_exclusion_mask  # noqa: E402
from tlc.pipeline.prep import rectify_and_mask  # noqa: E402
from tlc.synth.generator import make_plate  # noqa: E402
from tlc.synth.spec import Handwriting, PlateSpec, SpotShape, SpotSpec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "reports" / "grid_sweep_cache"
K_SELECT = 24
N_SURR = 30
MIN_RECALL = 0.70   # D-012: spec 01 §2.5 item 2 — systematically blind configs exit
MAX_FP_PER_BLANK = 1.0
TRUE_AMP_MIN = 4.0     # spots below 4 sigma are not counted against recall (marginal by design)


def dev_specs() -> list[tuple[str, PlateSpec, int]]:
    """(name, spec, seed): 14 spotted + 10 blanks. Seeds disjoint from all gate evidence."""
    sp = []
    shapes = [SpotShape.GAUSSIAN, SpotShape.EMG]
    amps = [(12.0, 6.0), (8.0, 4.0), (20.0, 5.0), (6.0, 3.0), (15.0, 10.0), (5.0, 4.5), (25.0, 8.0)]
    sizes = [(90, 160), (120, 200), (150, 290), (75, 130), (110, 190), (130, 240), (100, 170)]
    clips = [0.0, 0.0, 0.14, 0.0, 0.30, 0.0, 0.14]
    tilts = [0.5, 1.5, 2.5, 3.5, 1.0, 2.0, 4.0]
    for i in range(14):
        a1, a2 = amps[i % 7]
        w, h = sizes[i % 7]
        spots = (
            SpotSpec(lane=1, y_frac=0.55, amplitude_sigma=a1, shape=shapes[i % 2]),
            SpotSpec(lane=1, y_frac=0.30, amplitude_sigma=a2, shape=shapes[(i + 1) % 2]),
            SpotSpec(lane=3, y_frac=0.55, amplitude_sigma=max(a1, 10.0), shape=SpotShape.GAUSSIAN),
            SpotSpec(lane=2, y_frac=0.70, amplitude_sigma=a2 * 1.5, shape=shapes[i % 2]),
        )
        spec = PlateSpec(
            plate_w=w, plate_h=h, spots=spots, empty_lanes=(0,),
            clip_fraction=clips[i % 7], tilt_deg=tilts[i % 7],
            handwriting=Handwriting.HEADER_LABELS,
        )
        sp.append((f"dev_spotted_{i:02d}", spec, 5000 + i))
    for i in range(10):
        spec = PlateSpec(
            plate_w=sizes[i % 7][0], plate_h=sizes[i % 7][1], spots=(),
            clip_fraction=[0.0, 0.14][i % 2], tilt_deg=tilts[i % 7],
        )
        sp.append((f"dev_blank_{i:02d}", spec, 6000 + i))
    return sp


def process_plate(args: tuple[str, PlateSpec, int]) -> dict:
    name, spec, seed = args
    img, gt = make_plate(spec, seed=seed)
    geo = analyse_geometry(img)
    pp = rectify_and_mask(img, geo)
    band = (int(gt.header_band[1] + 2), int(gt.origin_row - 4))
    excl = prepass_exclusion_mask(
        pp.green, pp.valid, 1.18 * 0.18 * gt.lane_pitch,
        ((0, gt.header_band[1]), (gt.label_band[0], pp.green.shape[0])),
    )
    noise = estimate_noise(pp.green, pp.valid, excl)
    tol = 0.4 * 2.355 * max(1.0, 0.18 * gt.lane_pitch)

    truths = [
        (s.lane, s.y) for s in gt.spots if s.quantifiable and s.amplitude_sigma >= TRUE_AMP_MIN
    ]
    base_cfgs = [
        Config(m, r, "autocov_full", ex, pm)
        for m in ("rolling_ball", "median", "arpls", "poly3")
        for r in (8, 16, 32, 64)
        for ex in ("mean", "median", "trimmed20")
        for pm in ("emg", "bigauss", "raw_max")
    ]
    # detection record: cfg_key -> {"hits": [bool per truth], "fp": int, "bins": set}
    rec: dict[str, dict] = {}
    n_bins_per_lane = int(np.ceil(pp.green.shape[0] / max(tol, 1.0)))
    od_cache: dict = {}   # 16 distinct (model, radius) fits per plate, not 576 (M-008)
    t0 = time.time()
    for lane in range(spec.n_lanes):
        xc = gt.lane_centres_x[lane]
        for bi, bcfg in enumerate(base_cfgs):
            rng = np.random.Generator(np.random.PCG64(seed ^ (bi * 0x9E3779B9 + lane)))
            shared = scan_lane_shared(
                bcfg, pp.green, pp.valid, noise, xc, gt.lane_pitch,
                list(gt.lane_centres_x), band, rng, n_surrogates=N_SURR, od_cache=od_cache,
            )
            for sv in ("masked_mad", "unmasked_mad", "gutter_only", "autocov_full"):
                cfg = Config(bcfg.model, bcfg.radius, sv, bcfg.extraction, bcfg.peak_model)
                peaks = shared.accept(cfg, excl, noise)
                r = rec.setdefault(cfg.key, {"hits": [False] * len(truths), "fp": 0, "bins": set()})
                for pk in peaks:
                    if not pk.accepted:
                        continue
                    matched = False
                    for ti, (tl, ty) in enumerate(truths):
                        if tl == lane and abs(pk.row - ty) <= tol:
                            r["hits"][ti] = True
                            matched = True
                    if not matched:
                        r["fp"] += 1
                    r["bins"].add(lane * n_bins_per_lane + int(pk.row / max(tol, 1.0)))
    for r in rec.values():
        r["bins"] = sorted(r["bins"])
    out = {"name": name, "is_blank": not truths, "n_truth": len(truths), "rec": rec,
           "n_bins_total": spec.n_lanes * n_bins_per_lane, "seconds": round(time.time() - t0, 1)}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{name}.json").write_text(json.dumps(out, sort_keys=True))
    return out


def process_plate_cached(args: tuple[str, PlateSpec, int]) -> dict:
    """Checkpointed: a finished plate is never recomputed after an interruption (M-008)."""
    name = args[0]
    f = CACHE_DIR / f"{name}.json"
    if f.exists():
        return json.loads(f.read_text())
    return process_plate(args)


def main() -> None:
    plates = dev_specs()
    with ProcessPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(process_plate_cached, plates))
    results.sort(key=lambda r: r["name"])
    print("per-plate seconds:", {r["name"]: r.get("seconds") for r in results})

    keys = [c.key for c in all_configs()]
    n_truth_total = sum(r["n_truth"] for r in results)
    n_blanks = sum(1 for r in results if r["is_blank"])

    recall = {}
    fp_per_blank = {}
    for key in keys:
        hits = sum(sum(r["rec"][key]["hits"]) for r in results if key in r["rec"])
        recall[key] = hits / max(n_truth_total, 1)
        fp = sum(r["rec"][key]["fp"] for r in results if r["is_blank"] and key in r["rec"])
        fp_per_blank[key] = fp / max(n_blanks, 1)

    survivors = [k for k in keys if recall[k] >= MIN_RECALL and fp_per_blank[k] <= MAX_FP_PER_BLANK]
    excluded = [
        {"key": k, "recall": round(recall[k], 3), "fp_per_blank": round(fp_per_blank[k], 3)}
        for k in keys if k not in survivors
    ]

    # Detection vectors over the concatenated bin space for Hamming diversity.
    offsets = {}
    off = 0
    for r in results:
        offsets[r["name"]] = off
        off += r["n_bins_total"]
    dim = off
    vec = {}
    for k in survivors:
        v = np.zeros(dim, dtype=np.int8)
        for r in results:
            if k in r["rec"]:
                for b in r["rec"][k]["bins"]:
                    v[offsets[r["name"]] + b] = 1
        vec[k] = v

    # Greedy k-center (farthest point) on Hamming distance; start from best-recall survivor.
    chosen = [max(survivors, key=lambda k: (recall[k], -fp_per_blank[k], k))]
    while len(chosen) < min(K_SELECT, len(survivors)):
        best_k, best_d = None, -1
        for k in survivors:
            if k in chosen:
                continue
            d = min(int(np.sum(vec[k] != vec[c])) for c in chosen)
            if d > best_d or (d == best_d and best_k is not None and k < best_k):
                best_k, best_d = k, d
        chosen.append(best_k)
    chosen_sorted = sorted(chosen)

    mat = np.stack([vec[k] for k in chosen_sorted])
    weights = config_weights(mat)
    keff = k_eff(mat)
    # G10 grid health on NULL vectors (blank plates only): correlation of configs' mistakes (D-011)
    null_cols = np.zeros(dim, dtype=bool)
    for r in results:
        if r["is_blank"]:
            null_cols[offsets[r["name"]] : offsets[r["name"]] + r["n_bins_total"]] = True
    keff_null = k_eff(mat[:, null_cols])

    grid = {
        "id": "CONFIG_GRID_v1",
        "k": len(chosen_sorted),
        "k_eff_dev_all_bins": round(keff, 3),
        "k_eff_dev_null_bins": round(keff_null, 3),
        "g10_grid_health_pass": bool(keff_null >= 4.0),
        "configs": [
            {"key": k, "weight": round(float(w), 6), "recall_dev": round(recall[k], 3),
             "fp_per_blank_dev": round(fp_per_blank[k], 3)}
            for k, w in zip(chosen_sorted, weights, strict=True)
        ],
        "selection": {
            "dev_set": "synthetic (A-013): 14 spotted + 10 blank plates, seeds 5000-5013/6000-6009",
            "pruning": {"min_recall": MIN_RECALL, "max_fp_per_blank": MAX_FP_PER_BLANK,
                        "n_excluded": len(excluded), "n_survivors": len(survivors)},
            "n_surrogates": N_SURR,
            "true_amp_min_sigma": TRUE_AMP_MIN,
        },
    }
    grid["hash"] = sha256_canonical({k: grid[k] for k in ("id", "configs")})
    out_dir = ROOT / "config" / "ensemble"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "CONFIG_GRID_v1.json").write_text(canonical_json(grid) + "\n")
    (ROOT / "reports" / "grid_selection.json").write_text(
        canonical_json({"recall": {k: round(v, 3) for k, v in recall.items()},
                        "fp_per_blank": {k: round(v, 3) for k, v in fp_per_blank.items()},
                        "excluded": excluded, "chosen": chosen_sorted}) + "\n"
    )
    print(canonical_json({
        "n_survivors": len(survivors), "n_excluded": len(excluded), "k": len(chosen_sorted),
        "k_eff_all": grid["k_eff_dev_all_bins"], "k_eff_null": grid["k_eff_dev_null_bins"],
        "recall_range_survivors": [round(min(recall[k] for k in survivors), 3),
                                    round(max(recall[k] for k in survivors), 3)],
        "hash": grid["hash"][:16],
    }))


if __name__ == "__main__":
    main()
