"""Run every real plate through the shipped pipeline with the lab's standard lane roles and record
what a chemist would see: counted bands per lane, candidates, refusals, photometry mode.

This is the real-plate regression surface. Synthetic gates measure the detector against ground truth;
this measures what changes on the plates the lab actually shot when the code changes. Two scans
diffed against each other are how a "fix" proves it did not cost anything elsewhere (M-025).

    uv run python scripts/corpus_scan.py --out reports/corpus_scan_<tag>.json
"""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import tlc.core.determinism  # noqa: F401  (sets BLAS env; MUST import before numpy)

# isort: split

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
LABELS = ("S", "R", "co", "sd")


def _config():
    from tlc.config.loader import load_pipeline
    from tlc.jobs.service import DEFAULT_PIPELINE_VERSION
    from tlc.pipeline.configs import Config
    from tlc.pipeline.runner import RunConfig

    doc, _, _ = load_pipeline(DEFAULT_PIPELINE_VERSION)
    grid = json.loads((ROOT / doc["ensemble"]["grid_ref"]).read_text())
    op = json.loads((ROOT / doc["operating_point"]["ref"]).read_text())
    cfgs, ws = [], []
    for row in grid["configs"]:
        m, rest = row["key"].split("@")
        r, sv, ex, pm = rest.split("/")
        cfgs.append(Config(m, int(r), sv, ex, pm))
        ws.append(row["weight"])
    t = op["tiers"]
    return RunConfig(grid=tuple(cfgs), weights=tuple(ws), grid_id=grid["id"],
                     reported_agreement_min=t["reported"]["agreement_min"],
                     candidate_agreement_min=t["candidate"]["agreement_min"],
                     p_med_max=t["reported"]["p_med_max"], z_med_min=t["reported"]["z_med_min"],
                     n_surrogates=int(doc["ensemble"]["n_surrogates"]), n_lanes=4, lane_labels=LABELS,
                     header_frac=float(doc["bands"]["header_frac"]), label_row_frac=float(doc["bands"]["label_row_frac"]))


def one(path_str: str) -> dict:
    from tlc.core.hashing import sha256_bytes
    from tlc.pipeline.runner import run_plate

    p = Path(path_str)
    data = p.read_bytes()
    im = ImageOps.exif_transpose(Image.open(p))
    rgb = np.asarray(im.convert("RGB"))
    seed = int(sha256_bytes(data)[:16], 16) ^ 20260826
    out = run_plate(rgb, _config(), seed=seed)
    lanes = {}
    for L in out.lanes:
        sp = [s for s in out.spots if s.lane_index == L.index]
        lanes[LABELS[L.index]] = {
            "confirmed": sum(1 for s in sp if s.status == "confirmed"),
            "candidate": sum(1 for s in sp if s.status == "candidate"),
            "quantified": bool(L.quantified), "streaking": bool(L.is_streaking), "empty": bool(L.is_empty),
            "bands": [{"id": s.id, "status": s.status, "y": round(float(s.y_px), 1),
                       "rst": None if s.rst is None else round(float(s.rst.value), 4),
                       "agree": round(float(s.ensemble.agreement), 3), "n_hit": s.ensemble.n_hit,
                       "snr": round(float(s.snr), 1)} for s in sp],
        }
    return {"file": p.name, "status": out.status, "photometry": out.photometry_mode,
            "refusals": sorted({r.code for r in out.refusals}), "lanes": lanes,
            "n_confirmed": sum(v["confirmed"] for v in lanes.values()),
            "n_candidate": sum(v["candidate"] for v in lanes.values()),
            "anchor": bool(out.anchor)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="dataset")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    files = sorted(str(p) for p in Path(a.images).rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if a.limit:
        files = files[: a.limit]
    with ProcessPoolExecutor() as ex:
        rows = list(ex.map(one, files, chunksize=1))
    summary = {
        "n_plates": len(rows),
        "status_counts": {s: sum(1 for r in rows if r["status"] == s) for s in ("succeeded", "degraded", "refused")},
        "plates_with_any_confirmed_band": sum(1 for r in rows if r["n_confirmed"] > 0),
        "plates_with_anchor": sum(1 for r in rows if r["anchor"]),
        "total_confirmed": sum(r["n_confirmed"] for r in rows),
        "total_candidate": sum(r["n_candidate"] for r in rows),
        "confirmed_by_lane": {k: sum(r["lanes"][k]["confirmed"] for r in rows if k in r["lanes"]) for k in LABELS},
    }
    Path(a.out).write_text(json.dumps({"summary": summary, "plates": rows}, indent=1, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
