"""How wide is a real band, in units of the width the pipeline assumes?

Gate 1 calibrated the synthetic generator against the corpus for illumination, noise, clipping and
geometry. It never calibrated spot WIDTH — and M-025 showed that is the axis that decides whether the
ensemble counts a band at all. This measures the distribution the batteries should be drawing from.

For every plate: rectify, build the OD field, take each lane's trace, and measure the FWHM of every
band the pipeline confirms or lists as a candidate, excluding the handwriting and origin zones.
Report the ratio to the nominal band width the pipeline assumes (2.355 x 0.18 x lane pitch).

    uv run python scripts/measure_band_widths.py --out reports/band_widths.json
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
WIDTH_FRAC_NOMINAL = 0.18


def _fwhm_around(profile: np.ndarray, row: float, span: int) -> float | None:
    """Full width at half maximum around one band, with the local baseline taken from the ends of the
    window. Returns None when the window holds no peak above its own baseline."""
    r = int(round(row))
    lo, hi = max(0, r - span), min(profile.size, r + span)
    seg = np.asarray(profile[lo:hi], float)
    if seg.size < 9:
        return None
    edge = max(3, seg.size // 8)
    base = float(np.median(np.concatenate([seg[:edge], seg[-edge:]])))
    peak = float(seg.max()) - base
    if peak <= 0:
        return None
    above = np.where(seg >= base + peak / 2.0)[0]
    if above.size == 0:
        return None
    # the contiguous run containing the maximum, so a second band in the window cannot inflate it
    top = int(np.argmax(seg))
    lo_i = hi_i = top
    while lo_i - 1 in above:
        lo_i -= 1
    while hi_i + 1 in above:
        hi_i += 1
    return float(hi_i - lo_i + 1)


def one(path_str: str) -> dict:
    from scripts.corpus_scan import _config
    from tlc.core.hashing import sha256_bytes
    from tlc.pipeline.geometry import analyse_geometry
    from tlc.pipeline.photometry import compute_od, lane_densitogram
    from tlc.pipeline.prep import rectify_and_mask
    from tlc.pipeline.runner import run_plate

    p = Path(path_str)
    data = p.read_bytes()
    rgb = np.asarray(ImageOps.exif_transpose(Image.open(p)).convert("RGB"))
    geo = analyse_geometry(rgb)
    if not geo.found:
        return {"file": p.name, "bands": [], "reason": "no plate found"}
    pp = rectify_and_mask(rgb, geo)
    h, w = pp.green.shape
    pitch = w / 4.0
    nominal_fwhm = 2.355 * WIDTH_FRAC_NOMINAL * pitch
    seed = int(sha256_bytes(data)[:16], 16) ^ 20260826
    out = run_plate(rgb, _config(), seed=seed)
    odr = compute_od(pp.green, pp.valid, "poly3", 32)
    dens = {i: lane_densitogram(odr, i, (i + 0.5) * pitch, pitch).profile for i in range(4)}

    rows = []
    for sp in out.spots:
        if sp.status not in ("confirmed", "candidate"):
            continue
        if sp.y_px < 0.18 * h or sp.y_px > 0.84 * h:      # handwriting band and origin zone
            continue
        prof = dens.get(sp.lane_index)
        if prof is None:
            continue
        fw = _fwhm_around(np.asarray(prof, float), sp.y_px, span=int(max(20, 3 * nominal_fwhm)))
        if fw is None or fw <= 1:
            continue
        rows.append({"lane": sp.lane_index, "status": sp.status, "y": round(float(sp.y_px), 1),
                     "fwhm_px": round(fw, 1), "nominal_fwhm_px": round(nominal_fwhm, 1),
                     "ratio": round(fw / nominal_fwhm, 3), "snr": round(float(sp.snr), 1),
                     "agreement": round(float(sp.ensemble.agreement), 3)})
    return {"file": p.name, "plate_w": w, "plate_h": h, "pitch": round(pitch, 1), "bands": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="dataset")
    ap.add_argument("--out", default="reports/band_widths.json")
    a = ap.parse_args()
    files = sorted(str(p) for p in Path(a.images).rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    with ProcessPoolExecutor() as ex:
        plates = list(ex.map(one, files, chunksize=1))
    ratios = np.array([b["ratio"] for pl in plates for b in pl["bands"]], float)
    strong = np.array([b["ratio"] for pl in plates for b in pl["bands"] if b["snr"] >= 5.0], float)
    if ratios.size == 0:
        print("no bands measured")
        return 1
    qs = [5, 10, 25, 50, 75, 90, 95]
    summary = {
        "n_plates": len(plates), "n_bands": int(ratios.size), "n_bands_snr5": int(strong.size),
        "ratio_quantiles": {str(q): round(float(np.percentile(ratios, q)), 3) for q in qs},
        "ratio_quantiles_snr5": ({str(q): round(float(np.percentile(strong, q)), 3) for q in qs}
                                 if strong.size else None),
        "mean": round(float(ratios.mean()), 3), "sd": round(float(ratios.std()), 3),
        "frac_above_2x": round(float((ratios > 2).mean()), 3),
        "frac_above_3x": round(float((ratios > 3).mean()), 3),
        "frac_above_4x": round(float((ratios > 4).mean()), 3),
        "nominal_definition": "2.355 * 0.18 * lane_pitch — the width the pipeline assumes for a spot",
    }
    Path(a.out).write_text(json.dumps({"summary": summary, "plates": plates}, indent=1, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
