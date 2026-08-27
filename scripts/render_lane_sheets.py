"""Render each plate's lanes as a contact sheet: the rectified lane strip beside its own densitogram.

Every streak threshold in this build so far was derived on the synthetic generator, and M-027 showed
the generator's streaks and this lab's are not the same population — so those thresholds are fitted to
an artefact. The way out is ground truth on REAL lanes, and the cheapest reliable source of it is
looking at them.

One PNG per plate: four lane strips (cropped to the analysable band, contrast-stretched identically
across the plate so lanes stay comparable), each with its optical-density trace drawn beside it, and a
caption saying what the pipeline currently decided. A human — or a model with eyes — labels each lane
band / broad / streak from the picture, and those labels become the ground truth the rule is derived
against.

    uv run python scripts/render_lane_sheets.py --out reports/lane_sheets --plates 16
"""

import argparse
import json
from pathlib import Path

import tlc.core.determinism  # noqa: F401  (sets BLAS env; MUST import before numpy)

# isort: split

import numpy as np
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
STRIP_W = 78          # px per lane strip in the sheet
TRACE_W = 96          # px per densitogram
SHEET_H = 420
PAD = 10
HEADER = 54


def _stretch(a: np.ndarray, lo_pct: float = 1.0, hi_pct: float = 99.5) -> np.ndarray:
    """Contrast stretch shared across the whole plate, so a faint lane stays faint relative to a
    strong one — a per-lane stretch would make every lane look equally dark and defeat the purpose."""
    lo, hi = np.percentile(a, [lo_pct, hi_pct])
    if hi - lo < 1e-9:
        return np.zeros_like(a, dtype=np.uint8)
    return np.clip(255.0 * (a - lo) / (hi - lo), 0, 255).astype(np.uint8)


def one(path_str: str, out_dir: Path) -> dict:
    from scripts.corpus_scan import _config
    from tlc.core.hashing import sha256_bytes
    from tlc.pipeline.geometry import analyse_geometry
    from tlc.pipeline.photometry import compute_od, lane_densitogram
    from tlc.pipeline.prep import rectify_and_mask
    from tlc.pipeline.runner import run_plate

    p = Path(path_str)
    rgb = np.asarray(ImageOps.exif_transpose(Image.open(p)).convert("RGB"))
    geo = analyse_geometry(rgb)
    if not geo.found:
        return {"file": p.name, "rendered": False, "reason": "no plate found"}
    pp = rectify_and_mask(rgb, geo)
    h, w = pp.green.shape
    pitch = w / 4.0
    band = (int(0.16 * h) + 2, int(0.86 * h) - 4)
    seed = int(sha256_bytes(p.read_bytes())[:16], 16) ^ 20260826
    out = run_plate(rgb, _config(), seed=seed)
    odr = compute_od(pp.green, pp.valid, "poly3", 32)

    # the plate's green channel, stretched once, is what a chemist would see under the lamp
    plate = _stretch(pp.green[band[0]:band[1], :])
    n_lanes = 4
    sheet_w = PAD + n_lanes * (STRIP_W + TRACE_W + PAD)
    sheet = Image.new("RGB", (sheet_w, SHEET_H + HEADER + 16), "white")
    d = ImageDraw.Draw(sheet)
    d.text((PAD, 6), f"{p.name[:64]}", fill="black")
    d.text((PAD, 20), f"plate {w}x{h}  analysable rows {band[0]}-{band[1]}  status {out.status}", fill="#555555")
    d.text((PAD, 34), "lane: label | pipeline verdict | bands found", fill="#555555")

    lanes_meta = []
    for lane in range(n_lanes):
        x0 = PAD + lane * (STRIP_W + TRACE_W + PAD)
        L0 = next((x for x in out.lanes if x.index == lane), None)
        # the strip spans a FULL lane pitch centred on the pipeline's refined centre, with the window
        # it actually integrates drawn on top — so a misaligned lane is visible as a band outside the
        # window rather than hidden by cropping to the window itself
        centre = float(L0.x_center_px) if L0 is not None else (lane + 0.5) * pitch
        hw_used = float(L0.half_width_px) if L0 is not None else 0.275 * pitch
        lo = max(0, int(round(centre - 0.5 * pitch)))
        hi = min(plate.shape[1], int(round(centre + 0.5 * pitch)))
        if hi - lo < 4:
            continue
        strip = Image.fromarray(plate[:, lo:hi]).resize((STRIP_W, SHEET_H), Image.NEAREST)
        sheet.paste(strip, (x0, HEADER))
        sx = STRIP_W / max(1, hi - lo)
        for edge in (centre - hw_used, centre + hw_used):
            ex = x0 + int(round((edge - lo) * sx))
            if x0 <= ex <= x0 + STRIP_W:
                d.line([ex, HEADER, ex, HEADER + SHEET_H], fill="#22aa55", width=1)

        prof = np.asarray(lane_densitogram(odr, lane, centre, pitch).profile, float)
        seg = prof[band[0]:band[1]]
        base = float(np.median(seg))
        peak = float(np.max(seg) - base)
        scale = TRACE_W - 12 if peak <= 0 else (TRACE_W - 12) / peak
        tx = x0 + STRIP_W + 4
        d.rectangle([tx, HEADER, tx + TRACE_W - 6, HEADER + SHEET_H], outline="#dddddd")
        pts = []
        for i, v in enumerate(seg):
            y = HEADER + int(round(i * (SHEET_H - 1) / max(1, seg.size - 1)))
            xx = tx + 2 + int(round(max(0.0, (v - base)) * scale))
            pts.append((min(xx, tx + TRACE_W - 8), y))
        d.line(pts, fill="#1f4e8c", width=1)

        L = L0
        nb = sum(1 for s in out.spots if s.lane_index == lane)
        verdict = "streak" if (L and L.is_streaking) else ("empty" if (L and L.is_empty) else "bands")
        for s in out.spots:
            if s.lane_index != lane:
                continue
            yy = HEADER + int(round((s.y_px - band[0]) * (SHEET_H - 1) / max(1, band[1] - band[0] - 1)))
            if 0 <= yy - HEADER < SHEET_H:
                d.line([x0, yy, x0 + 6, yy], fill="#a8172f", width=1)
        d.text((x0, HEADER - 14), f"L{lane + 1} {L.label if L else '?'} | {verdict} | {nb}", fill="black")
        d.text((x0, HEADER + SHEET_H + 2), f"x={centre:.0f} hw={hw_used:.0f} {L0.x_center_method if L0 else ''}"[:34],
               fill="#22aa55")
        lanes_meta.append({
            "lane": lane, "label": L.label if L else None,
            "flagged_streaking": bool(L and L.is_streaking), "n_bands": nb,
            "width_frac": (None if not L or L.streak is None or L.streak.width_frac_of_migration is None
                           else round(L.streak.width_frac_of_migration, 3)),
            "streak_frac": None if not L or L.streak is None else round(L.streak.streak_fraction, 3),
            "reason": None if not L or L.streak is None else L.streak.reason,
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    name = "".join(c if c.isalnum() or c in "-_." else "_" for c in p.stem)[:60] + ".png"
    sheet.save(out_dir / name)
    return {"file": p.name, "sheet": name, "rendered": True, "status": out.status, "lanes": lanes_meta}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="dataset")
    ap.add_argument("--out", default="reports/lane_sheets")
    ap.add_argument("--plates", type=int, default=0, help="0 = all")
    ap.add_argument("--only-flagged", action="store_true", help="only plates with a lane flagged streaking")
    a = ap.parse_args()

    files = sorted(str(p) for p in Path(a.images).rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if a.plates:
        files = files[: a.plates]
    out_dir = ROOT / a.out
    rows = []
    for f in files:
        try:
            rows.append(one(f, out_dir))
        except Exception as e:  # noqa: BLE001 - a sheet that fails must not stop the rest
            rows.append({"file": Path(f).name, "rendered": False, "reason": f"{type(e).__name__}: {e}"})
    if a.only_flagged:
        rows = [r for r in rows if any(L.get("flagged_streaking") for L in r.get("lanes", []))]
    (out_dir / "index.json").write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n")
    n_flag = sum(1 for r in rows for L in r.get("lanes", []) if L.get("flagged_streaking"))
    print(f"{sum(1 for r in rows if r.get('rendered'))} sheets in {out_dir}, {n_flag} lanes currently flagged streaking")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
