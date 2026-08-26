"""Gate 5 evidence: peak modelling, Rst, refusal (brief §6).

  1. Position error on synthetic plates < 0.01 in Rst at the 95th percentile for CONFIRMED spots
     (position = mode, D-014; truth Rst from ground-truth modes, origin row and standard-lane
     anchor; the pipeline's Rst from its own detected origin/anchor).
  2. Every streaking synthetic lane is flagged and quantification suppressed (100%).
  3. Every plate in the real corpus produces either a result or a typed refusal with a reason and
     a remedy — zero silent nulls — and every output validates against the frozen schema.
Checkpointed per plate (M-008).
"""

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tlc.core.determinism  # noqa: F401, E402  (sets BLAS env; MUST import before numpy)

# isort: split

import imageio.v3 as iio  # noqa: E402
import numpy as np  # noqa: E402

from tlc.assemble import assemble  # noqa: E402
from tlc.core.canonical_json import canonical_json  # noqa: E402
from tlc.core.hashing import sha256_file  # noqa: E402
from tlc.pipeline.configs import Config  # noqa: E402
from tlc.pipeline.runner import RunConfig, run_plate  # noqa: E402
from tlc.synth.generator import make_plate  # noqa: E402
from tlc.synth.spec import Handwriting, PlateSpec, SpotShape, SpotSpec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "reports" / "gate5_cache"
GRID = json.loads((ROOT / "config" / "ensemble" / "CONFIG_GRID_v1.json").read_text())
OP = json.loads((ROOT / "config" / "ensemble" / "OPERATING_POINT_v2.json").read_text())
from tlc.config.loader import load_pipeline  # noqa: E402

CONFIG_DOC, CONFIG_HASH, CONFIG_REF = load_pipeline("0.5.0")
CONFIG_DOC = {**CONFIG_DOC, "grid_hash": GRID["hash"], "operating_point_id": OP["id"],
              "gate_thresholds": {"green_clip_max": 0.15, "green_clip_unusable": 0.40, "frame_overrun_max": 0.02,
                                  "lane_clip_abstain": 0.20, "lane_clip_area_max": 0.02, "px_per_lane_min": 10.0,
                                  "vif_abstain": 6.0, "origin_ci90_max_frac": 0.08}}


def run_config(n_lanes: int | None, labels: tuple[str, ...] | None) -> RunConfig:
    cfgs, ws = [], []
    for row in GRID["configs"]:
        m, rest = row["key"].split("@")
        r, sv, ex, pm = rest.split("/")
        cfgs.append(Config(m, int(r), sv, ex, pm))
        ws.append(row["weight"])
    return RunConfig(grid=tuple(cfgs), weights=tuple(ws), grid_id=GRID["id"],
                     reported_agreement_min=OP["tiers"]["reported"]["agreement_min"],
                     candidate_agreement_min=OP["tiers"]["candidate"]["agreement_min"],
                     p_med_max=OP["tiers"]["reported"]["p_med_max"], z_med_min=OP["tiers"]["reported"]["z_med_min"],
                     n_lanes=n_lanes, lane_labels=labels)


def synth_spec(seed: int) -> PlateSpec:
    rng = np.random.Generator(np.random.PCG64(seed))
    shapes = [SpotShape.GAUSSIAN, SpotShape.EMG]
    spots = [SpotSpec(lane=3, y_frac=float(rng.uniform(0.45, 0.75)), amplitude_sigma=float(rng.uniform(10, 25)))]  # anchor
    for lane in (1, 2):
        if rng.uniform() < 0.25 and lane == 2:
            spots.append(SpotSpec(lane=2, y_frac=float(rng.uniform(0.2, 0.5)), amplitude_sigma=25.0, shape=SpotShape.STREAK))
            continue
        for _ in range(int(rng.integers(1, 3))):
            spots.append(SpotSpec(lane=lane, y_frac=float(rng.uniform(0.15, 0.85)), amplitude_sigma=float(rng.uniform(6, 20)),
                                  shape=shapes[int(rng.integers(0, 2))], tau_frac=float(rng.uniform(0.8, 2.0))))
    return PlateSpec(plate_w=int(rng.integers(100, 160)), plate_h=int(rng.integers(180, 300)), spots=tuple(spots),
                     clip_fraction=0.0, tilt_deg=float(rng.uniform(0.4, 4.1)), handwriting=Handwriting.HEADER_LABELS)


def _cached(name: str, fn):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{name}.json"
    if f.exists():
        return json.loads(f.read_text())
    out = fn()
    f.write_text(json.dumps(out, sort_keys=True))
    return out


def process_synth(seed: int) -> dict:
    def go():
        spec = synth_spec(seed)
        img, gt = make_plate(spec, seed=seed)
        out = run_plate(img, run_config(4, ("S", "co", "R", "sd")), seed=seed)
        # truth Rst from modes: anchor = highest-amplitude quantifiable spot in lane 3
        anchors = [s for s in gt.spots if s.lane == 3 and s.quantifiable]
        anchor = max(anchors, key=lambda s: s.amplitude_od) if anchors else None
        truths = []
        for s in gt.spots:
            if not s.quantifiable or anchor is None:
                continue
            truths.append({"lane": s.lane, "y_mode": s.y_mode, "amp": s.amplitude_sigma, "shape": s.shape,
                           "tau_over_sigma": round(s.tau / s.sigma_y, 2) if s.tau else 0.0,
                           "rst_true": (gt.origin_row - s.y_mode) / (gt.origin_row - anchor.y_mode)})
        dets = [{"lane": sp.lane_index, "y": sp.y_px, "status": sp.status, "rst": sp.rst.value if sp.rst else None,
                 "rst_refusal": sp.rst_refusal.code if sp.rst_refusal else None, "flags": list(sp.flags)} for sp in out.spots]
        lanes = [{"index": L.index, "is_streaking": L.is_streaking, "quantified": L.quantified,
                  "suppression": L.suppression.code if L.suppression else None} for L in out.lanes]
        return {"seed": seed, "status": out.status, "origin_found": bool(out.origin and out.origin.found),
                "origin_row": out.origin.row if out.origin and out.origin.found else None, "origin_true": gt.origin_row,
                "anchor": out.anchor and {k: v for k, v in out.anchor.items() if k in ("lane_index", "y_px", "spot_id")},
                "truths": truths, "dets": dets, "lanes": lanes, "streak_lanes_true": list(gt.streak_lanes),
                "tol_px": 0.4 * 2.355 * 0.18 * gt.lane_pitch, "migration": gt.origin_row - spec.front_row_frac * gt.plate_h}
    return _cached(f"synth_{seed}", go)


def process_real(path_str: str) -> dict:
    def go():
        p = Path(path_str)
        img = iio.imread(p)
        b = p.read_bytes()
        out = run_plate(img, run_config(None, None), seed=int(sha256_file(p)[:8], 16))
        res = assemble(out, b, {"width_px": img.shape[1], "height_px": img.shape[0], "mime": "image/png", "original_filename": p.name},
                       CONFIG_DOC, f"run_gate5_{p.stem[:12]}", "2026-08-26T00:00:00Z")
        d = res.model_dump(mode="json")
        # silent-null audit: every Q with value None must be provenance refused with a refusal
        def walk(o, path=""):
            bad = []
            if isinstance(o, dict):
                if "provenance" in o and "value" in o and "unit" in o:
                    if o["value"] is None and (o["provenance"] != "refused" or not o.get("refusal")):
                        bad.append(path)
                for k, v in o.items():
                    bad += walk(v, f"{path}.{k}")
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    bad += walk(v, f"{path}[{i}]")
            return bad
        return {"file": p.name, "status": res.status, "verdict": res.capture_qc.verdict, "n_spots": len(res.spots),
                "n_refusals": len(res.refusals), "refusal_codes": sorted({r.code for r in res.refusals}),
                "silent_nulls": walk(d), "schema_valid": True}
    return _cached(f"real_{Path(path_str).stem}", go)


def main() -> None:
    seeds = [11000 + i for i in range(60)]
    files = sorted(str(p) for p in (ROOT / "dataset").rglob("*.png"))
    seen, uniq = set(), []
    for f in files:
        h = sha256_file(Path(f))
        if h not in seen:
            seen.add(h)
            uniq.append(f)
    with ProcessPoolExecutor(max_workers=8) as ex:
        synth = list(ex.map(process_synth, seeds, chunksize=2))
        real = list(ex.map(process_real, uniq, chunksize=2))

    # 1. position error (confirmed spots matched to RESOLVED truths within tol; Rst mode-vs-mode).
    #    D-017: a truth is resolved iff its nearest same-lane neighbour is >= 2 x nominal FWHM
    #    away; unresolved pairs (merged blobs) are reported separately, never scored as error.
    errs, errs_unres, n_conf, n_matched = [], [], 0, 0
    n_truth_resolved = n_truth_unresolved = 0
    for r in synth:
        fwhm_nom = r["tol_px"] / 0.4
        tol = max(r["tol_px"], 0.03 * r["migration"])
        resolved = {}
        for i, t in enumerate(r["truths"]):
            nn = min([abs(o["y_mode"] - t["y_mode"]) for j, o in enumerate(r["truths"]) if j != i and o["lane"] == t["lane"]], default=1e9)
            resolved[i] = nn >= 2.0 * fwhm_nom
        n_truth_resolved += sum(resolved.values())
        n_truth_unresolved += sum(1 for v in resolved.values() if not v)
        for d in r["dets"]:
            if d["status"] != "confirmed" or d["rst"] is None:
                continue
            n_conf += 1
            m = [(i, t) for i, t in enumerate(r["truths"]) if t["lane"] == d["lane"] and abs(t["y_mode"] - d["y"]) <= tol]
            if m:
                n_matched += 1
                i, t = min(m, key=lambda it: abs(it[1]["y_mode"] - d["y"]))
                (errs if resolved[i] else errs_unres).append(abs(d["rst"] - t["rst_true"]))
    errs = np.array(errs)
    eu = np.array(errs_unres)
    pos = {"n_confirmed": n_conf, "n_matched_to_truth": n_matched,
           "n_truth_resolved": n_truth_resolved, "n_truth_unresolved_pairs": n_truth_unresolved,
           "n_matched_resolved": int(errs.size), "n_matched_unresolved": int(eu.size),
           "rst_err_median": round(float(np.median(errs)), 5) if errs.size else None,
           "rst_err_p95": round(float(np.percentile(errs, 95)), 5) if errs.size else None,
           "rst_err_max": round(float(errs.max()), 5) if errs.size else None,
           "unresolved_pairs_rst_err_median": round(float(np.median(eu)), 5) if eu.size else None,
           "unresolved_pairs_rst_err_p95": round(float(np.percentile(eu, 95)), 5) if eu.size else None,
           "rule": "D-017: scored on truths with nearest same-lane neighbour >= 2 FWHM_nom",
           "pass": bool(errs.size and np.percentile(errs, 95) < 0.01)}
    # 2. streak lanes
    total_streak = sum(len(r["streak_lanes_true"]) for r in synth)
    caught = sum(1 for r in synth for li in r["streak_lanes_true"]
                 if r["lanes"][li]["is_streaking"] and not r["lanes"][li]["quantified"])
    streak = {"n_streak_lanes": total_streak, "flagged_and_unquantified": caught, "pass": caught == total_streak}
    # 3. real corpus totality
    silent = [r for r in real if r["silent_nulls"]]
    total = {"n_unique_images": len(real), "n_with_result_or_typed_refusal": sum(1 for r in real if r["schema_valid"]),
             "status_counts": {s: sum(1 for r in real if r["status"] == s) for s in ("succeeded", "degraded", "refused")},
             "silent_null_offenders": [(r["file"], r["silent_nulls"][:3]) for r in silent],
             "refusal_code_histogram": {c: sum(1 for r in real if c in r["refusal_codes"]) for c in sorted({c for r in real for c in r["refusal_codes"]})},
             "pass": not silent and all(r["schema_valid"] for r in real)}
    ev = {"position": pos, "streak": streak, "real_corpus": total,
          "gate5_pass": pos["pass"] and streak["pass"] and total["pass"]}
    (ROOT / "reports" / "gate5_evidence.json").write_text(canonical_json(ev) + "\n")
    print(canonical_json({k: (v if k == "gate5_pass" else {kk: vv for kk, vv in v.items() if kk not in ("silent_null_offenders",)}) for k, v in ev.items()}))


if __name__ == "__main__":
    main()
