"""Gate 9 — the label-shuffle null battery (BUILD_BRIEF §Phase 9; spec 02 §6 N1/N2).

On label-shuffled data the number of surfaced findings must be at most the nominal false-discovery
rate over >= 500 shuffles. Nothing about the images changes; only the metadata that carries the
design (reaction time, plate order) is permuted, which is exactly N1 (break X-Y) and N2 (break the
image-metadata link).

Run:  uv run python scripts/gate9_check.py [--shuffles 500] [--plates 8]
Writes reports/gate9.json and prints PASS/FAIL.
"""

import argparse
import json
import math
from pathlib import Path

import tlc.core.determinism  # noqa: F401, E402  (sets BLAS env; MUST import before numpy)

# isort: split

import numpy as np

from tlc.insight import variables as V
from tlc.insight.cohort import Q_F1, analyse_cohort

ROOT = Path(__file__).resolve().parents[1]
SURFACED = ("reported", "tentative")


def _band(bid, lane, role, rst, agree, snr):
    return V.Band(id=bid, lane_index=lane, lane_label=role, lane_role=role, rst=rst,
                  rst_ci=(rst - 0.012, rst + 0.012), y_frac=rst * 0.8, agree=agree, snr=snr, area_od=1.0,
                  peak_od=0.25, clip_frac=0.0, tail_factor=0.8, fwhm_px=6.0, shape_class="gaussian",
                  in_annotation_band=False, status="confirmed")


def synth_cohort(n_plates: int, rng: np.random.Generator) -> list[V.PlateVars]:
    """A cohort with no chemistry in it: band counts, positions and optical densities are drawn
    independently of the metadata, but capture variables vary the way they do on the real corpus."""
    plates = []
    for i in range(n_plates):
        n_bands = int(rng.integers(1, 5))
        bands = [_band(f"sp_{i}_{j}", 1, "R", float(rng.uniform(0.2, 0.95)), float(rng.uniform(0.6, 0.78)),
                       float(rng.uniform(5.0, 14.0))) for j in range(n_bands)]
        bands.append(_band(f"sd_{i}", 3, "sd", 1.0, 0.74, 12.0))
        bands.append(_band(f"s_{i}", 0, "S", float(rng.uniform(0.2, 0.95)), 0.70, 10.0))
        plates.append(V.PlateVars(
            run_id=f"run_{i:024d}", image_sha=f"{i:064d}", created_at="2026-08-26T00:00:00Z",
            campaign_id=f"P{i}", reaction_time_h=float(i), solvent_system_id="A", operator="AK",
            n_lanes=4, lane_roles=["S", "R", "co", "sd"], bands=bands, quantified_lanes=[0, 1, 2, 3],
            capture={"sigma_od": float(rng.uniform(0.004, 0.017)), "green_clip_frac": float(rng.uniform(0, 0.5)),
                     "mpix": float(rng.uniform(0.009, 0.05)), "lane_px": float(rng.uniform(17, 38)),
                     "tilt_deg": float(rng.uniform(0.3, 4.2)), "plate_area_frac": float(rng.uniform(0.79, 0.94)),
                     "focus_metric": float(rng.uniform(0.5, 2.0)), "capture_order": float(i)},
            photometry_mode="full"))
    return plates


def shuffle_metadata(plates: list[V.PlateVars], rng: np.random.Generator) -> list[V.PlateVars]:
    """N1 + N2: permute the metadata records across plates, leaving the image analysis untouched."""
    import dataclasses

    perm = rng.permutation(len(plates))
    out = []
    for i, p in enumerate(plates):
        src = plates[perm[i]]
        out.append(dataclasses.replace(p, reaction_time_h=src.reaction_time_h, campaign_id=src.campaign_id,
                                       operator=src.operator))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shuffles", type=int, default=500)
    ap.add_argument("--plates", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20260826)
    a = ap.parse_args()

    rng = np.random.Generator(np.random.PCG64(a.seed))
    base = synth_cohort(a.plates, rng)
    observed = analyse_cohort(base)
    fired, per_hyp = 0, {}
    for k in range(a.shuffles):
        r = np.random.Generator(np.random.PCG64(a.seed + 1 + k))
        out = analyse_cohort(shuffle_metadata(base, r))
        hits = [f.hypothesis_id for f in out if f.verdict in SURFACED]
        fired += int(bool(hits))
        for h in hits:
            per_hyp[h] = per_hyp.get(h, 0) + 1
    rate = fired / a.shuffles
    # binomial 95% upper bound (Wilson)
    z = 1.959964
    n = a.shuffles
    centre = (rate + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    upper = centre + half
    per_hyp_rate = {h: c / n for h, c in per_hyp.items()}
    disabled = {h: r for h, r in per_hyp_rate.items() if r > 0.05}      # spec 02 §6 N3 tolerance
    verdict = {
        "gate": 9, "shuffles": n, "plates": a.plates, "seed": a.seed,
        "surfaced_finding_rate": rate, "wilson_upper95": round(upper, 4), "nominal_q": Q_F1,
        "per_hypothesis_fire_rate": per_hyp_rate, "hypotheses_over_5pct": disabled,
        "observed_cohort": {"reported": [f.hypothesis_id for f in observed if f.verdict in SURFACED],
                            "suppressed": [f.hypothesis_id for f in observed if f.verdict == "suppressed"],
                            "insufficient_data": [f.hypothesis_id for f in observed if f.verdict == "insufficient_data"]},
        "passed": bool(rate <= Q_F1 and upper <= 0.15 and not disabled),
    }
    out_path = ROOT / "reports" / "gate9.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n")
    print(json.dumps(verdict, indent=2, sort_keys=True))
    print("GATE 9:", "PASS" if verdict["passed"] else "FAIL")
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
