"""Gate 3 evidence: photometry and the noise unit.

Criteria (brief §6):
  1. Rst invariant to a global exposure scale of 0.7-1.3 within 0.005 (metamorphic; two plates
     x six scale factors).
  2. Recovered spot amplitude monotonic in synthetic amplitude, Spearman rho > 0.98
     (3 seeds x 10 amplitudes spanning 1-30 sigma, deliberately below the detection floor).
  3. Sigma stable within 15% across background radii — the D-008 estimator applied to every
     ensemble member's OD residual (poly3 + 4 models x 4 radii = 17 members), on two synthetic
     plates and on the cleanest real plate (P33).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tlc.core.determinism  # noqa: F401, E402  (sets BLAS env; MUST import before numpy)

# isort: split

import imageio.v3 as iio  # noqa: E402
import numpy as np  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

from tlc.core.canonical_json import canonical_json  # noqa: E402
from tlc.pipeline.geometry import analyse_geometry  # noqa: E402
from tlc.pipeline.photometry import (  # noqa: E402
    compute_od,
    lane_densitogram,
    relative_position,
    sigma_od_prespot,
    strongest_peak_row,
)
from tlc.pipeline.prep import rectify_and_mask  # noqa: E402
from tlc.synth.generator import make_plate  # noqa: E402
from tlc.synth.spec import PlateSpec, SpotSpec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SPOTS = (
    SpotSpec(lane=1, y_frac=0.55, amplitude_sigma=12.0),
    SpotSpec(lane=3, y_frac=0.55, amplitude_sigma=15.0),
)
GRID_RADII = [12, 20, 35, 55]
GRID_MODELS = ["iterative", "gaussian", "median", "rolling_ball"]


def _prep(img):
    geo = analyse_geometry(img)
    return rectify_and_mask(img, geo)


def _rst(img, gt) -> float:
    pp = _prep(img)
    odr = compute_od(pp.green, pp.valid, "poly3", 0)
    rows = (int(gt.header_band[1] + 2), int(gt.origin_row - 3))
    y1 = strongest_peak_row(lane_densitogram(odr, 1, gt.lane_centres_x[1], gt.lane_pitch).profile, rows)
    y3 = strongest_peak_row(lane_densitogram(odr, 3, gt.lane_centres_x[3], gt.lane_pitch).profile, rows)
    return relative_position(y1, gt.origin_row, y3)


def exposure_invariance() -> dict:
    rows = []
    worst = 0.0
    for seed, tilt in [(77, 2.0), (78, 3.5)]:
        img, gt = make_plate(PlateSpec(spots=SPOTS, base_green=0.62, clip_fraction=0.0, tilt_deg=tilt), seed=seed)
        base = _rst(img, gt)
        for k in [0.7, 0.8, 0.9, 1.1, 1.2, 1.3]:
            scaled = np.clip(np.round(img[:, :, :3].astype(np.float64) * k), 0, 255).astype(np.uint8)
            drift = abs(_rst(scaled, gt) - base)
            worst = max(worst, drift)
            rows.append({"seed": seed, "k": k, "rst_base": round(base, 5), "drift": round(drift, 6)})
    return {"rows": rows, "worst_drift": round(worst, 6), "bound": 0.005, "pass": worst <= 0.005}


def amplitude_monotonicity() -> dict:
    true_amp, rec_amp = [], []
    for seed in [1, 2, 3]:
        for amp in [1, 2, 3, 5, 7, 10, 14, 19, 25, 30]:
            sp = PlateSpec(
                spots=(SpotSpec(lane=1, y_frac=0.5, amplitude_sigma=float(amp)),),
                base_green=0.85, tilt_deg=1.5,
            )
            img, gt = make_plate(sp, seed=1000 + seed)
            pp = _prep(img)
            odr = compute_od(pp.green, pp.valid, "poly3", 0)
            den = lane_densitogram(odr, 1, gt.lane_centres_x[1], gt.lane_pitch)
            s = gt.spots[0]
            rec_amp.append(float(den.profile[int(s.y - 3 * s.sigma_y) : int(s.y + 3 * s.sigma_y)].max()))
            true_amp.append(s.amplitude_od)
    rho = float(spearmanr(true_amp, rec_amp).statistic)
    return {"n_points": len(true_amp), "spearman_rho": round(rho, 5), "bound": 0.98, "pass": rho > 0.98}


def _sigma_grid(green, valid, band) -> dict:
    vals = {}
    for model in ["poly3", *GRID_MODELS]:
        for radius in [0] if model == "poly3" else GRID_RADII:
            odr = compute_od(green, valid, model, radius)
            o = np.where(odr.od_valid, odr.od, np.nan)[band[0] : band[1]]
            d = o[:, 1:] - o[:, :-1]
            d = d[np.isfinite(d)]
            vals[f"{model}@{radius}"] = round(1.4826 * float(np.median(np.abs(d - np.median(d)))) / np.sqrt(2.0), 6)
    arr = np.array(list(vals.values()))
    return {
        "members": vals,
        "sigma_raw_prespot": round(sigma_od_prespot(green, valid, band), 6),
        "spread": round(float((arr.max() - arr.min()) / np.median(arr)), 5),
    }


def sigma_stability() -> dict:
    cases = []
    for seed, tilt in [(77, 2.0), (79, 4.0)]:
        img, gt = make_plate(PlateSpec(spots=SPOTS, base_green=0.62, clip_fraction=0.0, tilt_deg=tilt), seed=seed)
        pp = _prep(img)
        band = (int(gt.header_band[1] + 2), int(gt.origin_row - 3))
        cases.append({"case": f"synthetic_seed{seed}", **_sigma_grid(pp.green, pp.valid, band)})
    img = iio.imread(ROOT / "dataset" / "MEHQ-P33 4hr_31st July26.png")
    pp = _prep(img)
    h = pp.green.shape[0]
    band = (int(0.25 * h), int(0.80 * h))  # convention band on the real plate ('chosen')
    cases.append({"case": "real_P33", **_sigma_grid(pp.green, pp.valid, band)})
    worst = max(c["spread"] for c in cases)
    return {"cases": cases, "worst_spread": worst, "bound": 0.15, "pass": worst <= 0.15}


def main() -> None:
    evidence = {
        "exposure_invariance": exposure_invariance(),
        "amplitude_monotonicity": amplitude_monotonicity(),
        "sigma_stability": sigma_stability(),
    }
    evidence["gate3_pass"] = all(evidence[k]["pass"] for k in ("exposure_invariance", "amplitude_monotonicity", "sigma_stability"))
    (ROOT / "reports" / "gate3_evidence.json").write_text(canonical_json(evidence) + "\n")
    print(canonical_json({
        "exposure_worst_drift": evidence["exposure_invariance"]["worst_drift"],
        "spearman_rho": evidence["amplitude_monotonicity"]["spearman_rho"],
        "sigma_worst_spread": evidence["sigma_stability"]["worst_spread"],
        "sigma_p33_raw": evidence["sigma_stability"]["cases"][-1]["sigma_raw_prespot"],
        "gate3_pass": evidence["gate3_pass"],
    }))


if __name__ == "__main__":
    main()
