"""Gate 4 recall arm: can a better DECISION RULE over the same ensemble evidence close the gap?

The shipped rule is a conjunction — agreement >= a*, median surrogate p <= p*, median z >= z*. The
recall diagnostic (`scripts/exp_recall_miss.py`) shows the misses are not weak: several carry z_med
8-15 with agreement 0.17-0.53, i.e. a few pipelines are very sure and the rest never looked there.
A conjunction cannot express that; a score can.

This script re-scores the gate's own cached clusters (no pipeline re-run) under:

  conjunction   accept iff a >= a*        and p_med <= p*  and z_med >= z*
  score         accept iff a + w*g(z) >= s* and p_med <= p*,  g(z) = clip((z - Z0) / (Z1 - Z0), 0, 1)

and reports recall on >=5 sigma truths and false bands per synthetic-noise blank, on the tuning
split (odd/even seeds, as the gate defines them) and the eval split separately. Nothing is chosen
here — the tuning split chooses, the eval split reports.

Writes reports/exp_decision_rule.json.
"""

import argparse
import json
from pathlib import Path

import tlc.core.determinism  # noqa: F401  (sets BLAS env; MUST import before numpy)

# isort: split

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "reports" / "gate4_cache"
Z0, Z1 = 5.0, 20.0
FP_BOUND, RECALL_BOUND = 0.2, 0.95


def shipped_operating_point() -> dict:
    """M-018: the operating point is whatever the SHIPPED pipeline config resolves to, never a
    filename written into a harness."""
    from tlc.config.loader import load_pipeline
    from tlc.jobs.service import DEFAULT_PIPELINE_VERSION

    doc, _, _ = load_pipeline(DEFAULT_PIPELINE_VERSION)
    return json.loads((ROOT / doc["operating_point"]["ref"]).read_text())


def latest_cache_dir(name: str | None = None) -> Path:
    if name:
        return CACHE / name
    dirs = [d for d in CACHE.iterdir() if d.is_dir()]
    if not dirs:
        raise SystemExit("no gate4 cache; run scripts/gate4_check.py first")
    return max(dirs, key=lambda d: (len(list(d.glob("*.json"))), d.stat().st_mtime))


def g(z: float) -> float:
    return float(np.clip((z - Z0) / (Z1 - Z0), 0.0, 1.0))


def load(cdir: Path) -> tuple[list[dict], list[dict]]:
    spotted, blanks = [], []
    for f in sorted(cdir.glob("*.json")):
        d = json.loads(f.read_text())
        (spotted if d["kind"] == "spotted" else blanks).append(d)
    return spotted, blanks


def evaluate(spotted: list[dict], blanks: list[dict], rule, split: str) -> dict:
    def keep(d: dict) -> bool:
        return (d["seed"] % 2 == 0) if split == "tuning" else (d["seed"] % 2 == 1) if split == "eval" else True

    n_true = n_found = 0
    for d in (x for x in spotted if keep(x)):
        tol = d["tol"]
        for t in d["truths_5s"]:
            n_true += 1
            near = [s for s in d["spots"] if s["lane"] == t["lane"] and abs(s["row"] - t["y"]) <= tol]
            n_found += int(any(rule(s) for s in near))
    fp = n_blank = 0
    for d in (x for x in blanks if keep(x) and x["family"] == "synth"):
        n_blank += 1
        fp += sum(1 for s in d["spots"] if rule(s))
    return {"recall_5s": round(n_found / max(n_true, 1), 4), "n_true_5s": n_true,
            "fp_per_blank": round(fp / max(n_blank, 1), 4), "n_blanks": n_blank}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None)
    a = ap.parse_args()
    cdir = latest_cache_dir(a.cache)
    spotted, blanks = load(cdir)
    op = shipped_operating_point()["tiers"]["reported"]
    p_star = op["p_med_max"]

    def conjunction(a_star: float):
        def rule(s: dict) -> bool:
            return s["a"] >= a_star and s["p_med"] <= p_star and s["z_med"] >= op["z_med_min"]
        return rule

    def score_rule(w: float, s_star: float):
        def rule(s: dict) -> bool:
            return s["a"] + w * g(s["z_med"]) >= s_star and s["p_med"] <= p_star
        return rule

    rows = []
    for a_star in (0.4, 0.45, 0.5, 0.55, 0.6):
        rule = conjunction(a_star)
        rows.append({"kind": "conjunction", "a_star": a_star, "w": None, "s_star": None,
                     "tuning": evaluate(spotted, blanks, rule, "tuning"),
                     "eval": evaluate(spotted, blanks, rule, "eval")})
    for w in (0.1, 0.2, 0.3, 0.4, 0.5):
        for s_star in (0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8):
            rule = score_rule(w, s_star)
            rows.append({"kind": "score", "a_star": None, "w": w, "s_star": s_star,
                         "tuning": evaluate(spotted, blanks, rule, "tuning"),
                         "eval": evaluate(spotted, blanks, rule, "eval")})

    feasible = [r for r in rows if r["tuning"]["recall_5s"] >= RECALL_BOUND and r["tuning"]["fp_per_blank"] <= FP_BOUND]
    best = max(feasible, key=lambda r: (r["tuning"]["recall_5s"], -r["tuning"]["fp_per_blank"]), default=None)
    res = {"cache": cdir.name, "z_scale": [Z0, Z1], "p_star": p_star, "bounds": {"fp": FP_BOUND, "recall": RECALL_BOUND},
           "rows": rows, "n_feasible_on_tuning": len(feasible), "best_on_tuning": best,
           "best_eval": None if best is None else best["eval"]}
    (ROOT / "reports" / "exp_decision_rule.json").write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print(f"cache {cdir.name}: {len(spotted)} spotted, {len(blanks)} blank plates")
    print(f"{'rule':38s} {'tuning recall':>14s} {'tuning fp':>10s} {'eval recall':>12s} {'eval fp':>8s}")
    for r in rows:
        label = (f"a>={r['a_star']}" if r["kind"] == "conjunction" else f"a+{r['w']}*g(z)>={r['s_star']}")
        print(f"{label:38s} {r['tuning']['recall_5s']:>14.4f} {r['tuning']['fp_per_blank']:>10.3f} "
              f"{r['eval']['recall_5s']:>12.4f} {r['eval']['fp_per_blank']:>8.3f}")
    print(f"\nfeasible on tuning: {len(feasible)}; best: {best and (best['kind'], best['w'], best['s_star'], best['a_star'])}")
    if best:
        print("eval at that point:", best["eval"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
