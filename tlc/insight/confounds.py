"""Confound panel (spec 02 §3): uncorrected, α = 0.10, deliberately trigger-happy. Hard vetoes encode
mechanism (C03 clipping, C06 annotation ink, C07 pipeline agreement, C10 front convention)."""

import numpy as np
from scipy import stats

from tlc.insight.estimators import permutation_p, spearman

AGREE_SUPPRESS = 0.60
AGREE_CONFIRMED = 0.80

CAPTURE_VARIABLES = {
    "C01": ["sigma_od"], "C02": ["lane_px", "mpix"], "C03": ["green_clip_frac"], "C04": ["tilt_deg", "plate_area_frac"],
    "C08": ["capture_order"], "C11": ["focus_metric"],
}


def partial_spearman(x, y, z) -> float:
    rx, ry, rz = (stats.rankdata(v) for v in (x, y, z))
    rxy, rxz, ryz = spearman(rx, ry), spearman(rx, rz), spearman(ry, rz)
    den = np.sqrt((1 - rxz**2) * (1 - ryz**2))
    return float((rxy - rxz * ryz) / den) if den > 1e-12 else float("nan")


def stratified_permutation_p(x, y, z, n_perm: int = 5000, seed: int = 0) -> float:
    """Permute y within Z-strata (median split at n<10, tertiles at n≥12), vectorised over
    permutations: partial Spearman is Pearson on ranks, so one matrix product does the whole battery."""
    z = np.asarray(z, float)
    n = len(z)
    qs = [0.5] if n < 12 else [1 / 3, 2 / 3]
    strata = np.digitize(z, np.quantile(z, qs))
    rx, ry, rz = (stats.rankdata(v) for v in (x, y, z))
    obs = abs(partial_spearman(x, y, z))
    if not np.isfinite(obs):
        return 1.0
    rng = np.random.Generator(np.random.PCG64(seed))
    Y = np.tile(ry, (n_perm, 1))
    for s in np.unique(strata):
        idx = np.where(strata == s)[0]
        if len(idx) > 1:
            Y[:, idx] = Y[:, idx][np.arange(n_perm)[:, None], np.argsort(rng.random((n_perm, len(idx))), axis=1)]
    xc, zc = rx - rx.mean(), rz - rz.mean()
    Yc = Y - Y.mean(axis=1, keepdims=True)
    nx, nz, ny = np.linalg.norm(xc), np.linalg.norm(zc), np.linalg.norm(Yc, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        rxy = (Yc @ xc) / np.where(ny > 0, ny * nx, np.nan)
        ryz = (Yc @ zc) / np.where(ny > 0, ny * nz, np.nan)
        rxz = float(np.dot(xc, zc) / (nx * nz)) if nx > 0 and nz > 0 else np.nan
        part = (rxy - rxz * ryz) / np.sqrt((1 - rxz**2) * (1 - ryz**2))
    hits = int(np.sum(np.isfinite(part) & (np.abs(part) >= obs - 1e-12)))
    return (1 + hits) / (1 + n_perm)


def check_confound(cid: str, variable: str, x, y, z) -> dict:
    """§3.2 suppression rule for one capture variable Z against candidate (X, Y)."""
    rho_raw = spearman(x, y)
    rho_yz = spearman(y, z)
    rho_part = partial_spearman(x, y, z)
    if not (np.isfinite(rho_raw) and np.isfinite(rho_yz)):
        return {"id": cid, "variable": variable, "result": "not_evaluable"}
    p_yz = permutation_p(y, z)["p"]
    fired = []
    if np.isfinite(rho_part) and abs(rho_part) < 0.5 * abs(rho_raw):
        fired.append("|rho_partial| < 0.5 * |rho_raw|")
    if not np.isfinite(rho_part) and abs(rho_raw) > 0:
        # Z is collinear with X (rank-identical): the partial correlation is undefined, so the design
        # cannot separate the capture variable from the predictor. Suppress rather than guess.
        fired.append("capture variable is rank-collinear with the predictor")
    if abs(rho_yz) >= 0.70:
        fired.append("|Spearman(Y, Z)| >= 0.70")
    p_part = stratified_permutation_p(x, y, z) if fired or abs(rho_yz) >= 0.5 else None
    if p_part is not None and p_part > 0.10:
        fired.append("p_partial > 0.10")
    result = "FIRED" if fired else ("borderline" if abs(rho_yz) >= 0.5 else "clear")
    return {"id": cid, "variable": variable, "result": result, "rho_raw": round(rho_raw, 3),
            "rho_response_vs_confound": round(rho_yz, 3), "p_exact": None if p_yz is None else round(p_yz, 4),
            "rho_partial": None if not np.isfinite(rho_part) else round(rho_part, 3),
            "p_partial": None if p_part is None else round(p_part, 4), "rules_triggered": fired,
            "statement": f"This apparent trend is explained by {variable}, not by the chemistry." if fired else None}


def agreement_grade(agree: float | None) -> str:
    """C07: pipeline-parameter confound. <0.60 suppress; 0.60–0.79 tentative; ≥0.80 confirmed."""
    if agree is None or agree < AGREE_SUPPRESS:
        return "suppressed"
    return "confirmed" if agree >= AGREE_CONFIRMED else "tentative"


def hard_vetoes(bands, od_claim: bool) -> list[dict]:
    """C03 (OD with clipping), C06 (annotation band), C07 (agreement) as deterministic checks on the
    contributing bands. Returns the confound entries; any with result FIRED vetoes the claim."""
    bands = [b if isinstance(b, dict) else b.to_dict() for b in bands if b is not None]
    out = []
    clipped = [b["id"] for b in bands if od_claim and (b.get("clip_frac") or 0) > 0]
    out.append({"id": "C03", "variable": "clip_frac", "result": "FIRED" if clipped else ("pass" if od_claim else "not_applicable"),
                "detail": f"clipped support on {clipped}" if clipped else "clip_frac = 0 on all contributing bands"})
    ink = [b["id"] for b in bands if b.get("in_annotation_band")]
    out.append({"id": "C06", "variable": "in_annotation_band", "result": "FIRED" if ink else "clear",
                "detail": f"{ink} lie in the annotation band" if ink else "no contributing band in the annotation band"})
    low = [b["id"] for b in bands if (b.get("agree") or 0) < AGREE_SUPPRESS]
    amin = min((b.get("agree") or 0) for b in bands) if bands else None
    out.append({"id": "C07", "variable": "pipeline_parameters", "result": "FIRED" if low else "pass",
                "detail": f"minimum ensemble agreement {amin:.2f}" if amin is not None else "no bands"})
    out.append({"id": "C10", "variable": "front_convention", "result": "immune", "detail": "Rst is convention-independent by construction"})
    return out
