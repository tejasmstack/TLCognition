"""Does ensemble agreement collapse near the edges of the analysable band?

Motivation: on MEHQ-P33 — the cleanest plate in the corpus, 0% clipping, sigma_od 0.0017 — a feature
at 87 sigma sitting just below the header band scored only 0.41 agreement and was reported as a
candidate, not a band. If agreement is position-dependent, recall is not a property of the spot but of
where the spot happens to sit, and the shipped operating point means different things at different
heights.

Method: one 15-sigma Gaussian spot per plate, swept from just under the header band to just above the
origin, everything else held fixed. For each position, record the ensemble agreement, the median z,
the median surrogate p, and whether the spot clears the shipped reported tier.

Writes reports/exp_edge_agreement.json.
"""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import tlc.core.determinism  # noqa: F401  (sets BLAS env; MUST import before numpy)

# isort: split

import numpy as np

from scripts.exp_position_rule import run_config
from tlc.pipeline.runner import run_plate
from tlc.synth.generator import make_plate
from tlc.synth.spec import Handwriting, PlateSpec, SpotSpec

ROOT = Path(__file__).resolve().parents[1]


def shipped_tier() -> dict:
    from tlc.config.loader import load_pipeline
    from tlc.jobs.service import DEFAULT_PIPELINE_VERSION

    doc, _, _ = load_pipeline(DEFAULT_PIPELINE_VERSION)
    return json.loads((ROOT / doc["operating_point"]["ref"]).read_text())["tiers"]["reported"]


def one(job: tuple[int, float]) -> dict:
    seed, y_frac = job
    spec = PlateSpec(plate_w=120, plate_h=240, tilt_deg=1.5, clip_fraction=0.0,
                     handwriting=Handwriting.HEADER_LABELS,
                     spots=(SpotSpec(lane=1, y_frac=y_frac, amplitude_sigma=15.0),
                            SpotSpec(lane=3, y_frac=0.55, amplitude_sigma=18.0)))
    img, gt = make_plate(spec, seed=seed)
    out = run_plate(img, run_config(4, ("S", "R", "co", "sd")), seed=seed)
    truth = next(s for s in gt.spots if s.lane == 1)
    band_lo, band_hi = float(gt.header_band[1] + 2), float(gt.origin_row - 4)
    tol = max(0.4 * 2.355 * 0.18 * gt.lane_pitch, 3.0)
    near = [sp for sp in out.spots if sp.lane_index == 1 and abs(sp.y_px - truth.y_mode) <= tol]
    best = max(near, key=lambda sp: sp.ensemble.agreement, default=None)
    return {
        "seed": seed, "y_frac": round(y_frac, 3), "truth_row": round(truth.y_mode, 1),
        "band": [round(band_lo, 1), round(band_hi, 1)],
        "dist_to_top_px": round(truth.y_mode - band_lo, 1),
        "dist_to_bottom_px": round(band_hi - truth.y_mode, 1),
        "fwhm_nom_px": round(2.355 * 0.18 * gt.lane_pitch, 1),
        "found": best is not None,
        "agreement": None if best is None else round(best.ensemble.agreement, 4),
        "n_hit": None if best is None else best.ensemble.n_hit,
        "z_med": None if best is None else round(best.ensemble.z_med, 2),
        "p_med": None if best is None else round(best.ensemble.p_med, 4),
        "status": None if best is None else best.status,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args()
    tier = shipped_tier()
    fracs = [round(0.06 + 0.04 * i, 3) for i in range(23)]        # 0.06 .. 0.94 of migration
    jobs = [(41000 + r * 100 + i, f) for i, f in enumerate(fracs) for r in range(a.repeats)]
    with ProcessPoolExecutor() as ex:
        rows = list(ex.map(one, jobs, chunksize=2))

    by_frac: dict[float, list[dict]] = {}
    for r in rows:
        by_frac.setdefault(r["y_frac"], []).append(r)
    curve = []
    for f in sorted(by_frac):
        rs = by_frac[f]
        ag = [r["agreement"] for r in rs if r["agreement"] is not None]
        curve.append({"y_frac": f, "n": len(rs), "found": sum(r["found"] for r in rs),
                      "agreement_mean": round(float(np.mean(ag)), 4) if ag else None,
                      "reported": sum(1 for r in rs if r["status"] == "confirmed"),
                      "dist_to_top_fwhm": round(np.mean([r["dist_to_top_px"] / r["fwhm_nom_px"] for r in rs]), 2),
                      "dist_to_bottom_fwhm": round(np.mean([r["dist_to_bottom_px"] / r["fwhm_nom_px"] for r in rs]), 2)})
    interior = [c for c in curve if c["dist_to_top_fwhm"] >= 2 and c["dist_to_bottom_fwhm"] >= 2]
    edge = [c for c in curve if c["dist_to_top_fwhm"] < 2 or c["dist_to_bottom_fwhm"] < 2]

    def rate(cs, key):
        tot = sum(c["n"] for c in cs)
        return round(sum(c[key] for c in cs) / max(tot, 1), 4)

    res = {"operating_point": tier, "repeats": a.repeats, "curve": curve,
           "interior": {"n_positions": len(interior), "confirmed_rate": rate(interior, "reported"),
                        "found_rate": rate(interior, "found"),
                        "agreement_mean": round(float(np.mean([c["agreement_mean"] for c in interior
                                                               if c["agreement_mean"] is not None])), 4)},
           "within_2_fwhm_of_an_edge": {"n_positions": len(edge), "confirmed_rate": rate(edge, "reported"),
                                        "found_rate": rate(edge, "found"),
                                        "agreement_mean": round(float(np.mean([c["agreement_mean"] for c in edge
                                                                               if c["agreement_mean"] is not None])), 4)
                                        if any(c["agreement_mean"] is not None for c in edge) else None}}
    (ROOT / "reports" / "exp_edge_agreement.json").write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print(f"{'y_frac':>7} {'top(FWHM)':>10} {'bot(FWHM)':>10} {'found':>6} {'confirmed':>10} {'agreement':>10}")
    for c in curve:
        print(f"{c['y_frac']:>7} {c['dist_to_top_fwhm']:>10} {c['dist_to_bottom_fwhm']:>10} "
              f"{c['found']}/{c['n']:<4} {c['reported']}/{c['n']:<9} {c['agreement_mean']}")
    print("\ninterior:", res["interior"])
    print("within 2 FWHM of an edge:", res["within_2_fwhm_of_an_edge"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
