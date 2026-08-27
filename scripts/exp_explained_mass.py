"""Is a lane a streak, or does it just look like one to a badly-chosen statistic?

The operational question a chemist asks about a lane is not "is it flat-topped" — it is **can the
bands I can see account for the material in this lane?** If the fitted peaks explain the lane's
density, the lane can be quantified whatever its shape. If most of the density lies between the
peaks, it cannot, and that is a streak.

`explained_mass` = 1 - (density above baseline that the fitted peaks do NOT account for) / (total
density above baseline), over the analysable band. It is amplitude-free, width-free and scale-free,
and it maps directly onto the consequence: a lane you cannot account for is a lane you cannot divide
into bands.

This measures it on three populations, so the question can be settled rather than argued:
    synthetic clean lanes      — should be high
    synthetic streak lanes     — should be low
    REAL lanes the shipped rule currently flags as streaking — which population do they resemble?

    uv run python scripts/exp_explained_mass.py --out reports/exp_explained_mass.json
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


def _widths(prof: np.ndarray, band: tuple[int, int], spots, fwhm_nom: float, migration: float) -> float | None:
    """The dominant fitted band's width as a fraction of the migration distance. A band occupying a
    fifth of the run is not a band with a position — it is a zone. This is the one definition that
    does not depend on amplitude, on the peak family's flexibility, or on a nominal width."""
    from tlc.pipeline.peaks import fit_emg

    best = None
    for s in spots:
        f = fit_emg(prof, s.row, fwhm_nom, s.amplitude_med)
        if f.ok and np.isfinite(f.fwhm) and f.fwhm > 0:
            if best is None or f.area > best.area:
                best = f
    if best is None or migration <= 0:
        return None
    return float(best.fwhm / migration)


def _explained(prof: np.ndarray, band: tuple[int, int], spots, fwhm_nom: float) -> tuple[float, int]:
    """Fraction of the lane's above-baseline density accounted for by the fitted peaks."""
    from tlc.pipeline.peaks import emg, fit_emg

    yy = np.arange(prof.size, dtype=float)
    model = np.zeros_like(prof)
    n_fit = 0
    for s in spots:
        f = fit_emg(prof, s.row, fwhm_nom, s.amplitude_med)
        if f.ok:
            model += emg(yy, f.amp, f.mu, f.sigma, f.tau, 0.0)
            n_fit += 1
    seg = prof[band[0]:band[1]]
    mod = model[band[0]:band[1]]
    if seg.size < 8:
        return float("nan"), n_fit
    base = float(np.median(seg))
    mass = float(np.clip(seg - base, 0, None).sum())
    resid = float(np.clip(seg - base - mod, 0, None).sum())
    if mass <= 0:
        return float("nan"), n_fit
    return float(max(0.0, 1.0 - resid / mass)), n_fit


def _lane_rows(green, valid, geom_band, excl, noise, cfg, centres, pitch, prof_of, lanes, seed, truth_streaks=None):
    from tlc.pipeline.ensemble import run_ensemble_lane

    rows = []
    fwhm_nom = 2.355 * 0.18 * pitch
    od_cache: dict = {}
    for lane in lanes:
        prof = prof_of(lane)
        spots, _ = run_ensemble_lane(list(cfg.grid), np.array(cfg.weights), green, valid, noise, excl, lane,
                                     centres[lane], pitch, list(centres), geom_band, seed=seed,
                                     n_surrogates=30, od_cache=od_cache)
        expl, n_fit = _explained(prof, geom_band, spots, fwhm_nom)
        wfrac = _widths(prof, geom_band, spots, fwhm_nom, float(geom_band[1] - geom_band[0]))
        rows.append({"lane": lane, "explained": None if np.isnan(expl) else round(expl, 4),
                     "width_frac_of_migration": None if wfrac is None else round(wfrac, 4),
                     "n_peaks": len(spots), "n_fitted": n_fit,
                     "true_streak": None if truth_streaks is None else bool(lane in truth_streaks)})
    return rows


def synthetic(seed: int) -> dict:
    from scripts.exp_position_rule import run_config, synth_spec
    from tlc.pipeline.geometry import analyse_geometry
    from tlc.pipeline.noise import estimate_noise, prepass_exclusion_mask
    from tlc.pipeline.photometry import compute_od, lane_densitogram
    from tlc.pipeline.prep import rectify_and_mask
    from tlc.synth.generator import make_plate

    spec = synth_spec(seed)
    img, gt = make_plate(spec, seed=seed)
    geo = analyse_geometry(img)
    if not geo.found:
        return {"seed": seed, "lanes": []}
    pp = rectify_and_mask(img, geo)
    h, _ = pp.green.shape
    pitch = gt.lane_pitch
    band = (int(gt.header_band[1] + 2), int(gt.origin_row - 4))
    excl = prepass_exclusion_mask(pp.green, pp.valid, 1.18 * 0.18 * pitch,
                                  ((0, gt.header_band[1]), (gt.label_band[0], h)))
    noise = estimate_noise(pp.green, pp.valid, excl)
    odr = compute_od(pp.green, pp.valid, "poly3", 32)

    def prof_of(lane: int) -> np.ndarray:
        return np.asarray(lane_densitogram(odr, lane, gt.lane_centres_x[lane], pitch).profile, float)

    rows = _lane_rows(pp.green, pp.valid, band, excl, noise, run_config(4, ("S", "co", "R", "sd")),
                      list(gt.lane_centres_x), pitch, prof_of, range(4), seed, set(gt.streak_lanes))
    return {"seed": seed, "lanes": rows}


def real(path_str: str) -> dict:
    from scripts.corpus_scan import _config
    from tlc.core.hashing import sha256_bytes
    from tlc.pipeline.geometry import analyse_geometry
    from tlc.pipeline.noise import estimate_noise, prepass_exclusion_mask
    from tlc.pipeline.photometry import compute_od, lane_densitogram
    from tlc.pipeline.prep import rectify_and_mask
    from tlc.pipeline.runner import run_plate

    p = Path(path_str)
    rgb = np.asarray(ImageOps.exif_transpose(Image.open(p)).convert("RGB"))
    geo = analyse_geometry(rgb)
    if not geo.found:
        return {"file": p.name, "lanes": []}
    seed = int(sha256_bytes(p.read_bytes())[:16], 16) ^ 20260826
    out = run_plate(rgb, _config(), seed=seed)
    flagged = {L.index: bool(L.is_streaking) for L in out.lanes}
    pp = rectify_and_mask(rgb, geo)
    h, w = pp.green.shape
    pitch = w / 4.0
    band = (int(0.16 * h) + 2, int(0.86 * h) - 4)
    excl = prepass_exclusion_mask(pp.green, pp.valid, 1.18 * 0.18 * pitch,
                                  ((0, int(0.16 * h)), (int(0.86 * h), h)))
    noise = estimate_noise(pp.green, pp.valid, excl)
    odr = compute_od(pp.green, pp.valid, "poly3", 32)
    centres = [(i + 0.5) * pitch for i in range(4)]

    def prof_of(lane: int) -> np.ndarray:
        return np.asarray(lane_densitogram(odr, lane, centres[lane], pitch).profile, float)

    rows = _lane_rows(pp.green, pp.valid, band, excl, noise, _config(), centres, pitch, prof_of, range(4), seed)
    for r in rows:
        r["flagged_streaking"] = flagged.get(r["lane"], False)
    return {"file": p.name, "lanes": rows}


def _stats(vals: list[float]) -> dict:
    v = np.array([x for x in vals if x is not None], float)
    if v.size == 0:
        return {"n": 0}
    return {"n": int(v.size), **{f"p{q}": round(float(np.percentile(v, q)), 3) for q in (5, 25, 50, 75, 95)},
            "mean": round(float(v.mean()), 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--images", default="dataset")
    ap.add_argument("--out", default="reports/exp_explained_mass.json")
    a = ap.parse_args()

    seeds = [11000 + i for i in range(a.seeds)]
    files = sorted(str(p) for p in Path(a.images).rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    with ProcessPoolExecutor(max_workers=6) as ex:
        syn = list(ex.map(synthetic, seeds, chunksize=1))
        rea = list(ex.map(real, files, chunksize=1))

    def pick(rows, key):
        return [r[key] for r in rows]

    syn_clean = [r for pl in syn for r in pl["lanes"] if r["true_streak"] is False]
    syn_streak = [r for pl in syn for r in pl["lanes"] if r["true_streak"] is True]
    rl_flag = [r for pl in rea for r in pl["lanes"] if r.get("flagged_streaking")]
    rl_ok = [r for pl in rea for r in pl["lanes"] if not r.get("flagged_streaking")]

    res = {"populations": {
        "synthetic_clean": _stats(pick(syn_clean, "explained")),
        "synthetic_true_streak": _stats(pick(syn_streak, "explained")),
        "real_flagged_streaking": _stats(pick(rl_flag, "explained")),
        "real_not_flagged": _stats(pick(rl_ok, "explained"))},
        "width_frac_of_migration": {
            "synthetic_clean": _stats(pick(syn_clean, "width_frac_of_migration")),
            "synthetic_true_streak": _stats(pick(syn_streak, "width_frac_of_migration")),
            "real_flagged_streaking": _stats(pick(rl_flag, "width_frac_of_migration")),
            "real_not_flagged": _stats(pick(rl_ok, "width_frac_of_migration"))},
        "n_synthetic_plates": len(syn), "n_real_plates": len(rea),
        "definition": "explained_mass = 1 - unexplained above-baseline density / total above-baseline density, "
                      "over the analysable band, after fitting every peak the ensemble proposes",
        "synthetic": syn, "real": rea}
    Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print("explained mass:")
    for name, st in res["populations"].items():
        print(f"  {name:26s} {st}")
    print("fitted width as a fraction of migration:")
    for name, st in res["width_frac_of_migration"].items():
        print(f"  {name:26s} {st}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
