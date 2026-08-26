"""Diagnostic for the Gate 5 position tail: which position estimator has the smallest p95?

The shipped rule reports the primary config's EMG mode (D-014). The ensemble also produces a
weight-averaged row over every config that found the band. This script measures three rules on the
same runs, so the choice is made on evidence rather than on taste:

  mode        y = fit.mode                                   (shipped)
  consensus   y = ensemble weighted-average row
  hybrid      y = fit.mode if |fit.mode - ensemble row| <= FRAC x FWHM else ensemble row

Rst is recomputed under each rule for BOTH the band and the anchor, so the comparison is like for
like. Writes reports/exp_position_rule.json.
"""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import tlc.core.determinism  # noqa: F401  (sets BLAS env; MUST import before numpy)

# isort: split

import numpy as np

from tlc.config.loader import load_pipeline
from tlc.pipeline.configs import Config
from tlc.pipeline.runner import RunConfig, run_plate
from tlc.synth.generator import make_plate
from tlc.synth.spec import Handwriting, PlateSpec, SpotShape, SpotSpec

ROOT = Path(__file__).resolve().parents[1]
RESOLVED_FWHM = 2.0
RULES = ("mode", "consensus") + tuple(f"hybrid_{f}" for f in (0.05, 0.1, 0.15, 0.2, 0.3, 0.5))


def run_config(n_lanes, labels) -> RunConfig:
    doc, _, _ = load_pipeline("0.5.0")
    grid_doc = json.loads((ROOT / doc["ensemble"]["grid_ref"]).read_text())
    op = json.loads((ROOT / doc["operating_point"]["ref"]).read_text())
    cfgs, ws = [], []
    for row in grid_doc["configs"]:
        m, rest = row["key"].split("@")
        r, sv, ex, pm = rest.split("/")
        cfgs.append(Config(m, int(r), sv, ex, pm))
        ws.append(row["weight"])
    t = op["tiers"]
    return RunConfig(grid=tuple(cfgs), weights=tuple(ws), grid_id=grid_doc["id"],
                     reported_agreement_min=t["reported"]["agreement_min"],
                     candidate_agreement_min=t["candidate"]["agreement_min"],
                     p_med_max=t["reported"]["p_med_max"], z_med_min=t["reported"]["z_med_min"],
                     n_surrogates=int(doc["ensemble"]["n_surrogates"]), n_lanes=n_lanes, lane_labels=labels,
                     header_frac=float(doc["bands"]["header_frac"]), label_row_frac=float(doc["bands"]["label_row_frac"]))


def synth_spec(seed: int) -> PlateSpec:
    """Identical to scripts/gate5_check.py's generator, so the numbers are comparable to the gate."""
    rng = np.random.Generator(np.random.PCG64(seed))
    shapes = [SpotShape.GAUSSIAN, SpotShape.EMG]
    spots = [SpotSpec(lane=3, y_frac=float(rng.uniform(0.45, 0.75)), amplitude_sigma=float(rng.uniform(10, 25)))]
    for lane in (1, 2):
        if rng.uniform() < 0.25 and lane == 2:
            spots.append(SpotSpec(lane=2, y_frac=float(rng.uniform(0.2, 0.5)), amplitude_sigma=25.0, shape=SpotShape.STREAK))
            continue
        for _ in range(int(rng.integers(1, 3))):
            spots.append(SpotSpec(lane=lane, y_frac=float(rng.uniform(0.15, 0.85)), amplitude_sigma=float(rng.uniform(6, 20)),
                                  shape=shapes[int(rng.integers(0, 2))], tau_frac=float(rng.uniform(0.8, 2.0))))
    return PlateSpec(plate_w=int(rng.integers(100, 160)), plate_h=int(rng.integers(180, 300)), spots=tuple(spots),
                     clip_fraction=0.0, tilt_deg=float(rng.uniform(0.4, 4.1)), handwriting=Handwriting.HEADER_LABELS)


def one(seed: int) -> list[dict]:
    spec = synth_spec(seed)
    img, gt = make_plate(spec, seed=seed)
    out = run_plate(img, run_config(4, ("S", "co", "R", "sd")), seed=seed)
    if out.origin is None or not out.origin.found or out.anchor is None:
        return []
    anchors = [s for s in gt.spots if s.lane == 3 and s.quantifiable]
    if not anchors:
        return []
    a_true = max(anchors, key=lambda s: s.amplitude_od)
    fwhm_nom = 2.355 * 0.18 * gt.lane_pitch

    def y_of(sp, rule: str) -> float:
        m = sp.fit.mode if sp.fit.ok else sp.ensemble.row
        c = sp.ensemble.row
        if rule == "mode":
            return m
        if rule == "consensus":
            return c
        frac = float(rule.split("_")[1])          # hybrid_<frac>: trust the fit only while it agrees
        return m if abs(m - c) <= frac * fwhm_nom else c

    anchor_sp = next((sp for sp in out.spots if sp.id == out.anchor.get("spot_id")), None)
    if anchor_sp is None:
        return []
    rows = []
    for sp in out.spots:
        if sp.status != "confirmed":
            continue
        tol = max(0.4 * fwhm_nom, 0.03 * (gt.origin_row - spec.front_row_frac * gt.plate_h))
        cands = [t for t in gt.spots if t.lane == sp.lane_index and t.quantifiable and abs(t.y_mode - sp.y_px) <= tol]
        if not cands:
            continue
        t = min(cands, key=lambda x: abs(x.y_mode - sp.y_px))
        nn = min([abs(o.y_mode - t.y_mode) for o in gt.spots if o is not t and o.lane == t.lane], default=1e9)
        rst_true = (gt.origin_row - t.y_mode) / (gt.origin_row - a_true.y_mode)
        rec = {"seed": seed, "resolved": bool(nn >= RESOLVED_FWHM * fwhm_nom), "amp": t.amplitude_sigma,
               "agree": sp.ensemble.agreement, "spread": sp.ensemble.row_spread, "n_hit": sp.ensemble.n_hit,
               "fit_ok": bool(sp.fit.ok), "shape": t.shape}
        for rule in RULES:
            ya, y = y_of(anchor_sp, rule), y_of(sp, rule)
            den = out.origin.row - ya
            rec[f"err_{rule}"] = abs((out.origin.row - y) / den - rst_true) if abs(den) > 1e-6 else None
        rows.append(rec)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed0", type=int, default=11000)   # same seeds as the gate
    a = ap.parse_args()
    seeds = [a.seed0 + i for i in range(a.n)]
    with ProcessPoolExecutor() as ex:
        recs = [r for chunk in ex.map(one, seeds, chunksize=2) for r in chunk]
    half = a.seed0 + a.n // 2
    res = {"n_plates": a.n, "n_spots": len(recs), "resolved_only": {}, "all": {}, "by_agreement": {},
           "split": {"tuning_seeds": [a.seed0, half - 1], "eval_seeds": [half, a.seed0 + a.n - 1]}}
    for scope, sel in (("resolved_only", [r for r in recs if r["resolved"]]), ("all", recs),
                       ("tuning", [r for r in recs if r["resolved"] and r["seed"] < half]),
                       ("eval", [r for r in recs if r["resolved"] and r["seed"] >= half])):
        res.setdefault(scope, {})
        for rule in RULES:
            e = np.array([r[f"err_{rule}"] for r in sel if r.get(f"err_{rule}") is not None])
            res[scope][rule] = {"n": int(e.size), "median": round(float(np.median(e)), 5) if e.size else None,
                                "p95": round(float(np.percentile(e, 95)), 5) if e.size else None,
                                "max": round(float(e.max()), 5) if e.size else None,
                                "frac_over_gate": round(float((e > 0.01).mean()), 4) if e.size else None}
    sel = [r for r in recs if r["resolved"]]
    for lo, hi in ((0.0, 0.6), (0.6, 0.75), (0.75, 1.0)):
        s = [r for r in sel if lo <= (r["agree"] or 0) < hi]
        e = np.array([r["err_mode"] for r in s if r.get("err_mode") is not None])
        res["by_agreement"][f"{lo}-{hi}"] = {"n": int(e.size),
                                             "p95_mode": round(float(np.percentile(e, 95)), 5) if e.size else None}
    worst = sorted((r for r in sel if r.get("err_mode")), key=lambda r: -r["err_mode"])[:12]
    res["worst_mode_cases"] = [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()} for r in worst]
    (ROOT / "reports" / "exp_position_rule.json").write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
