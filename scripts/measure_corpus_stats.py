"""Measure the real-corpus statistics the synthetic generator must reproduce (Gate 1 targets).

Writes reports/corpus_stats.json. Validation anchor: the evaluation report gives P33's
unmasked empty-band sigma as 0.01881 OD — this script's estimator should land near it
(reported in the output as `p33_validation`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tlc.core.determinism  # noqa: F401, E402  (sets BLAS env; MUST import before numpy)

# isort: split

import imageio.v3 as iio  # noqa: E402
import numpy as np  # noqa: E402

from tlc.core.canonical_json import canonical_json  # noqa: E402
from tlc.core.hashing import sha256_file  # noqa: E402
from tlc.synth.stats import measure_plate_stats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVAL_P33_UNMASKED_SD = 0.01881  # reference/EVAL_REPORT_EXTRACT.md §3


def main() -> None:
    files = sorted((ROOT / "dataset").rglob("*.png"))
    seen: set[str] = set()
    records = []
    for p in files:
        digest = sha256_file(p)
        if digest in seen:
            continue
        seen.add(digest)
        st = measure_plate_stats(iio.imread(p))
        rec = {"file": str(p.relative_to(ROOT / "dataset")), **st.to_dict()}
        records.append(rec)

    def clean(vals):
        return [v for v in vals if v is not None and np.isfinite(v)]

    # Aggregates over plates where the estimate is meaningful:
    # illumination swing only where clipping is low (a clipped surface fit is biased),
    # noise from all plates with a finite estimate.
    low_clip = [r for r in records if r["plate_found"] and r["clip_fraction"] <= 0.05]
    swings = clean([r["illum_swing"] for r in low_clip])
    sds = clean([r["empty_band_sd_od"] for r in records if r["plate_found"]])
    sds_low_clip = clean([r["empty_band_sd_od"] for r in low_clip])
    rg = clean([r["red_over_green"] for r in records if r["plate_found"]])
    bg = clean([r["blue_over_green"] for r in records if r["plate_found"]])
    base = clean([r["base_green_norm"] for r in low_clip])
    clips = clean([r["clip_fraction"] for r in records if r["plate_found"]])

    p33 = next((r for r in records if "P33" in r["file"]), None)

    def rng(v):
        return {"min": round(min(v), 5), "median": round(float(np.median(v)), 5), "max": round(max(v), 5), "n": len(v)}

    summary = {
        "n_unique_images": len(records),
        "n_low_clip_plates": len(low_clip),
        "illum_swing_low_clip": rng(swings),
        "empty_band_sd_od_all": rng(sds),
        "empty_band_sd_od_low_clip": rng(sds_low_clip),
        "red_over_green": rng(rg),
        "blue_over_green": rng(bg),
        "base_green_norm_low_clip": rng(base),
        "clip_fraction_observed": rng(clips),
        "p33_validation": {
            "eval_report_unmasked_sd": EVAL_P33_UNMASKED_SD,
            "this_estimator_sd": round(p33["empty_band_sd_od"], 5) if p33 else None,
            "ratio": round(p33["empty_band_sd_od"] / EVAL_P33_UNMASKED_SD, 3) if p33 else None,
        },
    }
    out = {"summary": summary, "images": records}
    (ROOT / "reports" / "corpus_stats.json").write_text(canonical_json(out) + "\n")
    print(canonical_json(summary))


if __name__ == "__main__":
    main()
