"""Diagnostic for the Gate 4 recall arm: which 5-sigma spots does the ensemble miss, and why?

Reads the gate's own per-plate cache (no re-running) and buckets every true >=5 sigma spot by
amplitude, lane, clipping and position, split by whether any ensemble cluster reached the shipped
operating point within the matching tolerance. Also reports, for the missed ones, the best cluster
that WAS found nearby — which separates "nothing detected here" from "detected but below the bar".

Run: uv run python scripts/exp_recall_miss.py [--split eval|tuning|all]
Writes reports/exp_recall_miss.json.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import tlc.core.determinism  # noqa: F401  (sets BLAS env; MUST import before numpy)

# isort: split

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "reports" / "gate4_cache"


def latest_cache_dir() -> Path:
    dirs = [d for d in CACHE.iterdir() if d.is_dir()]
    if not dirs:
        raise SystemExit("no gate4 cache; run scripts/gate4_check.py first")
    return max(dirs, key=lambda d: (len(list(d.glob("*.json"))), d.stat().st_mtime))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=("eval", "tuning", "all"), default="eval")
    a = ap.parse_args()
    op = json.loads((ROOT / "config" / "ensemble" / "OPERATING_POINT_v2.json").read_text())["tiers"]["reported"]
    a_star, p_star, z_star = op["agreement_min"], op["p_med_max"], op["z_med_min"]
    cdir = latest_cache_dir()

    rows = []
    for f in sorted(cdir.glob("spotted_synth_*.json")):
        d = json.loads(f.read_text())
        seed = d["seed"]
        if a.split == "eval" and seed % 2 == 0:
            continue
        if a.split == "tuning" and seed % 2 == 1:
            continue
        tol = d["tol"]
        for t in d["truths_5s"]:
            near = [s for s in d["spots"] if s["lane"] == t["lane"] and abs(s["row"] - t["y"]) <= tol]
            passing = [s for s in near if s["a"] >= a_star and s["p_med"] <= p_star and s["z_med"] >= z_star]
            best = max(near, key=lambda s: s["a"], default=None)
            rows.append({"seed": seed, "lane": t["lane"], "amp": t["amp"], "box_clip": t.get("box_clip", 0.0),
                         "y": t["y"], "found": bool(passing),
                         "best_a": None if best is None else best["a"],
                         "best_p": None if best is None else best["p_med"],
                         "best_z": None if best is None else best["z_med"],
                         "n_near": len(near)})
    n = len(rows)
    miss = [r for r in rows if not r["found"]]
    by_amp: dict[str, dict] = defaultdict(lambda: {"n": 0, "missed": 0})
    for r in rows:
        k = ("5-6" if r["amp"] < 6 else "6-8" if r["amp"] < 8 else "8-12" if r["amp"] < 12 else "12+")
        by_amp[k]["n"] += 1
        by_amp[k]["missed"] += int(not r["found"])
    for v in by_amp.values():
        v["recall"] = round(1 - v["missed"] / max(v["n"], 1), 4)

    def why(r: dict) -> str:
        if r["n_near"] == 0:
            return "no cluster within tolerance"
        if r["best_a"] is not None and r["best_a"] < a_star:
            return "agreement below the bar"
        if r["best_p"] is not None and r["best_p"] > p_star:
            return "surrogate p above the bar"
        return "z below the bar"

    reasons: dict[str, int] = defaultdict(int)
    for r in miss:
        reasons[why(r)] += 1
    near_misses = sorted((r for r in miss if r["best_a"] is not None), key=lambda r: -r["best_a"])[:15]
    res = {"split": a.split, "cache": cdir.name, "operating_point": {"a": a_star, "p": p_star, "z": z_star},
           "n_true_5s": n, "n_missed": len(miss), "recall": round(1 - len(miss) / max(n, 1), 4),
           "recall_by_amplitude": dict(by_amp), "miss_reasons": dict(reasons),
           "missed_agreement_p50": round(float(np.median([r["best_a"] for r in miss if r["best_a"] is not None])), 4)
           if any(r["best_a"] is not None for r in miss) else None,
           "missed_clip_p50": round(float(np.median([r["box_clip"] for r in miss])), 4) if miss else None,
           "all_clip_p50": round(float(np.median([r["box_clip"] for r in rows])), 4) if rows else None,
           "near_misses": near_misses}
    (ROOT / "reports" / "exp_recall_miss.json").write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "near_misses"}, indent=2, sort_keys=True))
    print("\nclosest misses (best cluster found at the truth's position):")
    for r in near_misses[:10]:
        print(f"  seed {r['seed']} lane {r['lane']} amp {r['amp']:.1f} clip {r['box_clip']:.3f} "
              f"a={r['best_a']} p={r['best_p']} z={r['best_z']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
