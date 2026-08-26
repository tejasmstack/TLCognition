"""Phase 0 dataset capture audit — Gate 0 evidence.

Deterministic: no RNG, no wall-clock in the measurements. Writes dataset/audit.json
(canonical JSON) and dataset/AUDIT.md. Per image: pixel dimensions, in-plate green-clipping
fraction, estimated tilt, per-edge frame-overrun fraction, usability verdict, duplicate
identification by sha256.

Verdict thresholds are provisional Phase 0 choices (ASSUMPTIONS.md A-006); the shipped input
gate is set in Phase 4 against spec 01.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tlc.core.determinism  # noqa: F401, E402  (sets BLAS env; MUST import before numpy)

# isort: split

import imageio.v3 as iio  # noqa: E402
import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402
from skimage import measure  # noqa: E402
from skimage.filters import threshold_otsu  # noqa: E402

from tlc.core.canonical_json import canonical_json  # noqa: E402
from tlc.core.hashing import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"

# Provisional usability thresholds (A-006)
CLIP_OK = 0.05          # <=5% in-plate hard clipping: photometry usable
CLIP_PARTIAL = 0.15     # <=15%: photometry partial, lane-dependent
MIN_PLATE_FRAC = 0.20   # plate must cover >=20% of frame to count as detected
LANES_ASSUMED = 4       # S / co / R / sd per brief F5 — 'inferred', not measured


def plate_mask(green: np.ndarray) -> np.ndarray:
    t = threshold_otsu(green)
    mask = green >= t
    lab, n = ndimage.label(mask)
    if n == 0:
        return np.zeros_like(mask)
    largest = 1 + np.argmax(ndimage.sum_labels(mask, lab, index=range(1, n + 1)))
    mask = lab == largest
    return ndimage.binary_fill_holes(mask)


def tilt_estimate_deg(mask: np.ndarray) -> float:
    props = measure.regionprops(mask.astype(np.uint8))
    if not props:
        return float("nan")
    deg = float(np.degrees(props[0].orientation))
    # distance to the nearest axis-aligned orientation (0 or +/-90)
    return float(min(abs(deg), abs(90.0 - abs(deg))))


def audit_image(path: Path) -> dict:
    im = iio.imread(path)
    h, w = im.shape[:2]
    alpha_opaque = bool(im.shape[2] == 3 or (im.shape[2] == 4 and np.all(im[:, :, 3] == 255)))
    green = im[:, :, 1].astype(np.uint8)
    mask = plate_mask(green)
    plate_frac = float(mask.mean())
    detected = plate_frac >= MIN_PLATE_FRAC

    if detected:
        in_plate = green[mask]
        clip_hard = float(np.mean(in_plate >= 255))
        clip_near = float(np.mean(in_plate >= 250))
        tilt = tilt_estimate_deg(mask)
        overrun = {
            "top": float(mask[0, :].mean()),
            "bottom": float(mask[-1, :].mean()),
            "left": float(mask[:, 0].mean()),
            "right": float(mask[:, -1].mean()),
        }
        cols = np.where(mask.any(axis=0))[0]
        plate_w = int(cols[-1] - cols[0] + 1)
        px_per_lane = round(plate_w / LANES_ASSUMED, 1)
        g_p = np.percentile(in_plate, [5, 50, 95])
    else:
        clip_hard = clip_near = tilt = float("nan")
        overrun = {"top": float("nan"), "bottom": float("nan"), "left": float("nan"), "right": float("nan")}
        px_per_lane = float("nan")
        g_p = [float("nan")] * 3

    if not detected:
        verdict = "unusable_no_plate"
    elif clip_hard <= CLIP_OK:
        verdict = "photometry_ok"
    elif clip_hard <= CLIP_PARTIAL:
        verdict = "photometry_partial"
    else:
        verdict = "positions_only"

    def r(x, n=4):
        return None if isinstance(x, float) and np.isnan(x) else round(float(x), n)

    return {
        "file": str(path.relative_to(DATASET)),
        "sha256": sha256_file(path),
        "width_px": w,
        "height_px": h,
        "channels": int(im.shape[2]),
        "alpha_fully_opaque": alpha_opaque,
        "plate_detected": detected,
        "plate_area_fraction": r(plate_frac),
        "in_plate_green_clip_fraction": r(clip_hard),
        "in_plate_green_near_clip_fraction": r(clip_near),
        "tilt_deg_estimate": r(tilt, 2),
        "frame_overrun_fraction": {k: r(v) for k, v in overrun.items()},
        "px_per_lane_estimate": r(px_per_lane, 1),
        "px_per_lane_provenance": "inferred",  # lane count assumed 4 (S/co/R/sd), not measured
        "green_p5_p50_p95_in_plate": [r(v, 1) for v in g_p],
        "usability_verdict": verdict,
    }


def main() -> None:
    files = sorted(DATASET.rglob("*.png"))
    records = [audit_image(p) for p in files]

    by_hash: dict[str, str] = {}
    for rec in records:
        if rec["sha256"] in by_hash:
            rec["duplicate_of"] = by_hash[rec["sha256"]]
        else:
            by_hash[rec["sha256"]] = rec["file"]
            rec["duplicate_of"] = None

    unique = [rec for rec in records if rec["duplicate_of"] is None]
    usable_photometry = [rec for rec in unique if rec["usability_verdict"] in ("photometry_ok", "photometry_partial")]

    def series(rec: dict) -> str:
        return rec["file"].split("-")[0].replace("Scale", "MEHQ") if rec["file"].startswith(("MEHQ", "PER", "Scale")) else "other"

    from tlc.core.hashing import sha256_canonical

    summary = {
        "images_total": len(records),
        "images_unique": len(unique),
        "duplicates": len(records) - len(unique),
        "unique_usable_for_photometry": len(usable_photometry),
        "forced_stop_lt15_usable": len(usable_photometry) < 15,
        "by_series": {
            s: sum(1 for rec in unique if series(rec) == s) for s in sorted({series(rec) for rec in unique})
        },
        "verdicts": {
            v: sum(1 for rec in unique if rec["usability_verdict"] == v)
            for v in sorted({rec["usability_verdict"] for rec in unique})
        },
        "corpus_inventory_sha256": sha256_canonical(sorted((rec["file"], rec["sha256"]) for rec in records)),
        "thresholds": {"clip_ok": CLIP_OK, "clip_partial": CLIP_PARTIAL, "min_plate_frac": MIN_PLATE_FRAC},
        "lanes_assumed": LANES_ASSUMED,
    }
    audit = {"summary": summary, "images": records}
    (DATASET / "audit.json").write_text(canonical_json(audit) + "\n")

    lines = [
        "# Dataset capture audit (Phase 0)",
        "",
        f"**{summary['images_total']} files, {summary['images_unique']} unique images** "
        f"({summary['duplicates']} byte-identical duplicates). "
        f"**{summary['unique_usable_for_photometry']} unique images usable for photometry** "
        f"under provisional thresholds (hard-clip fraction <= {CLIP_PARTIAL}).",
        "",
        "**Forced-stop condition §10 (fewer than 15 usable for photometry): "
        + ("**TRIGGERED**" if summary["forced_stop_lt15_usable"] else "not triggered") + ".**",
        "",
        "Method: green-channel Otsu threshold -> largest connected component -> hole fill = plate mask.",
        "Clipping = fraction of in-plate pixels with G >= 255 (near-clip: G >= 250).",
        "Tilt = region orientation distance from axis-aligned (estimate only; Phase 2 measures properly).",
        "Overrun = fraction of each image border covered by plate mask.",
        f"px/lane assumes {LANES_ASSUMED} lanes (S/co/R/sd) — inferred, not measured.",
        "",
        "| file | WxH px | plate | clip | near-clip | tilt(deg) | overrun T/B/L/R | px/lane | verdict | dup of |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rec in records:
        ov = rec["frame_overrun_fraction"]
        ovs = "/".join("-" if ov[k] is None else f"{ov[k]:.2f}" for k in ("top", "bottom", "left", "right"))
        lines.append(
            f"| {rec['file']} | {rec['width_px']}x{rec['height_px']} | "
            f"{'y' if rec['plate_detected'] else 'n'} | {rec['in_plate_green_clip_fraction']} | "
            f"{rec['in_plate_green_near_clip_fraction']} | {rec['tilt_deg_estimate']} | {ovs} | "
            f"{rec['px_per_lane_estimate']} | {rec['usability_verdict']} | {rec['duplicate_of'] or '-'} |"
        )
    lines += [
        "",
        "## Capture-protocol observations",
        "",
        "- Images are extremely low resolution (71-158 px wide). At ~15-40 px/lane this sits at or",
        "  below the 10 px/lane stability floor from F13 for faint-band sensitivity, and far below",
        "  any OCR floor (F8). These appear to be downsampled exports, not native photographs.",
        "- The remedy is a capture-protocol change (native-resolution export, controlled exposure to",
        "  keep green-channel clipping < 5%, full plate in frame with margin, pencil solvent front),",
        "  not more code. See reports/CAPTURE_PROTOCOL.md.",
    ]
    (DATASET / "AUDIT.md").write_text("\n".join(lines) + "\n")
    print(canonical_json(summary))


if __name__ == "__main__":
    main()
