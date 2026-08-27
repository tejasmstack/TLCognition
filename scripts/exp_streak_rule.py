"""Diagnostic for the Gate 5 false-streak arm: which rule fires on clean lanes, and at what margin?

Runs the gate's own synthetic plates and records, for every lane, the streak statistics and the
rule that fired, split by whether the lane really is a streak. The point is to see whether the
false flags cluster against one statistic (fixable) or sit just over several limits (a threshold
question that must then be answered on a tuning split).

Writes reports/exp_streak_rule.json.
"""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import tlc.core.determinism  # noqa: F401  (sets BLAS env; MUST import before numpy)

# isort: split

import numpy as np

from scripts.exp_position_rule import run_config, synth_spec
from tlc.pipeline.runner import run_plate
from tlc.synth.generator import make_plate

ROOT = Path(__file__).resolve().parents[1]


def one(seed: int) -> list[dict]:
    spec = synth_spec(seed)
    img, gt = make_plate(spec, seed=seed)
    out = run_plate(img, run_config(4, ("S", "co", "R", "sd")), seed=seed)
    rows = []
    for L in out.lanes:
        st = L.streak
        n_conf = sum(1 for sp in out.spots if sp.lane_index == L.index and sp.status == "confirmed")
        rows.append({"seed": seed, "lane": L.index, "true_streak": L.index in gt.streak_lanes,
                     "flagged": bool(L.is_streaking), "quantified": bool(L.quantified),
                     "frac": None if st is None else round(st.streak_fraction, 4),
                     "run_fwhm": None if st is None else round(st.max_run_fwhm, 3),
                     "tail": None if st is None or st.max_tail_ratio is None else round(st.max_tail_ratio, 3),
                     "reason": None if st is None else st.reason,
                     "resid_run": None if st is None else round(st.residual_run_fwhm, 3),
                     "plateau": None if st is None else round(st.plateau_frac, 3),
                     "shape": None if st is None or st.shape_ratio is None else round(st.shape_ratio, 3),
                     "runown": None if st is None or st.run_over_own_width is None else round(st.run_over_own_width, 3),
                     "n_peaks_in_run": None if st is None else st.n_peaks_in_run,
                     "fits": [{"sig": round(sp.fit.sigma, 2), "tau_over_sig": round(sp.fit.tau / max(sp.fit.sigma, 1e-9), 2),
                               "amp": round(sp.fit.amp, 4)}
                              for sp in out.spots if sp.lane_index == L.index and sp.fit.ok],
                     "peak_rows": [round(sp.ensemble.row, 1) for sp in out.spots if sp.lane_index == L.index],
                     "dom": (lambda fs: None if not fs else {"mu": round(max(fs, key=lambda f: f.amp).mu, 1),
                                                             "tau": round(max(fs, key=lambda f: f.amp).tau, 1),
                                                             "sig": round(max(fs, key=lambda f: f.amp).sigma, 1)})(
                         [sp.fit for sp in out.spots if sp.lane_index == L.index and sp.fit.ok]),
                     "n_confirmed_spots": n_conf})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed0", type=int, default=11000)
    a = ap.parse_args()
    seeds = [a.seed0 + i for i in range(a.n)]
    with ProcessPoolExecutor() as ex:
        recs = [r for chunk in ex.map(one, seeds, chunksize=2) for r in chunk]
    clean = [r for r in recs if not r["true_streak"]]
    real = [r for r in recs if r["true_streak"]]
    false_flags = [r for r in clean if r["flagged"]]
    missed = [r for r in real if not r["flagged"]]

    def which(reason: str | None) -> str:
        if not reason:
            return "none"
        if "of the lane is above" in reason:
            return "fraction"
        if "flat-topped" in reason:
            return "plateau"
        if "contiguous run" in reason:
            return "run_length"
        if "tail ratio" in reason:
            return "tail_ratio"
        return "other"

    by_rule: dict[str, int] = {}
    for r in false_flags:
        by_rule[which(r["reason"])] = by_rule.get(which(r["reason"]), 0) + 1
    stats = {}
    for key in ("frac", "run_fwhm", "tail", "resid_run", "plateau", "shape", "runown"):
        for name, sel in (("clean", clean), ("false_flags", false_flags), ("true_streak", real)):
            v = np.array([r[key] for r in sel if r[key] is not None], float)
            stats[f"{key}_{name}"] = {"n": int(v.size),
                                      "p50": round(float(np.median(v)), 4) if v.size else None,
                                      "p90": round(float(np.percentile(v, 90)), 4) if v.size else None,
                                      "max": round(float(v.max()), 4) if v.size else None}
    half = a.seed0 + a.n // 2
    res = {"n_plates": a.n, "n_lanes": len(recs), "n_clean": len(clean), "n_true_streak": len(real),
           "false_flag_rate": round(len(false_flags) / max(len(clean), 1), 4),
           "missed_true_streaks": len(missed), "false_flags_by_rule": by_rule, "stats": stats,
           "split": {"tuning_seeds": [a.seed0, half - 1], "eval_seeds": [half, a.seed0 + a.n - 1]},
           "false_flag_rate_tuning": round(sum(1 for r in false_flags if r["seed"] < half)
                                           / max(sum(1 for r in clean if r["seed"] < half), 1), 4),
           "false_flag_rate_eval": round(sum(1 for r in false_flags if r["seed"] >= half)
                                         / max(sum(1 for r in clean if r["seed"] >= half), 1), 4),
           "false_flag_cases": false_flags, "missed_cases": missed}
    (ROOT / "reports" / "exp_streak_rule.json").write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k not in ("false_flag_cases", "missed_cases")}, indent=2, sort_keys=True))
    print("\nfalse-flag cases:")
    for r in false_flags:
        print(f"  seed {r['seed']} lane {r['lane']} spots={r['n_confirmed_spots']} frac={r['frac']} "
              f"run={r['run_fwhm']} tail={r['tail']} :: {r['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
