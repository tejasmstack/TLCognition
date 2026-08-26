"""The agreement ensemble: run the config grid, cluster detections, weight, report (spec 01 §2.3-2.4).

Outputs per candidate position: weighted Jeffreys-shrunk agreement `a`, z_med, z_min, position
spread, per-config support. Raw agreement NEVER reaches a UI as a percentage (anti-pattern 2);
it is evidence for the calibrator (Phase 7) and, pre-calibration, for the dev-chosen operating
point whose curve is Gate 4 evidence.
"""

from dataclasses import dataclass

import numpy as np

from tlc.pipeline.configs import Config, LanePeak, detect_lane
from tlc.pipeline.noise import NoiseModel

JEFFREYS = 0.5
MATCH_TOL_FWHM = 0.4   # tau = 0.4 x nominal spot FWHM (spec 01 §2.3)


@dataclass(frozen=True)
class EnsembleSpot:
    lane: int
    row: float                 # weight-averaged position across detecting configs
    agreement: float           # weighted, Jeffreys-shrunk
    n_hit: int
    n_total: int
    z_med: float
    z_min: float
    row_spread: float          # sd of matched positions across configs (px)
    p_med: float               # median MC p across detecting configs
    amplitude_med: float


def k_eff(detection_vectors: np.ndarray) -> float:
    """Effective independent config count from mean pairwise phi correlation (spec 01 §2.3)."""
    k = detection_vectors.shape[0]
    if k < 2 or detection_vectors.shape[1] == 0:
        return float(k)
    rows_std = detection_vectors.std(axis=1)
    usable = rows_std > 0
    if usable.sum() < 2:
        return 1.0
    c = np.corrcoef(detection_vectors[usable])
    off = c[~np.eye(c.shape[0], dtype=bool)]
    rho = float(np.clip(np.nanmean(off), 0.0, 1.0))
    return float(k / (1.0 + (k - 1) * rho))


def config_weights(detection_vectors: np.ndarray) -> np.ndarray:
    """w_c ∝ 1 / (1 + Σ_{c'≠c} ρ_{cc'}) — a cluster of near-identical configs counts ~once."""
    k = detection_vectors.shape[0]
    if k < 2 or detection_vectors.shape[1] == 0:
        return np.ones(k)
    rows_std = detection_vectors.std(axis=1)
    c = np.zeros((k, k))
    usable = np.nonzero(rows_std > 0)[0]
    if usable.size >= 2:
        cc = np.corrcoef(detection_vectors[usable])
        for a, ia in enumerate(usable):
            for b, ib in enumerate(usable):
                c[ia, ib] = cc[a, b]
    w = 1.0 / (1.0 + np.clip(c, 0, 1).sum(axis=1) - 1.0)
    return w * (k / w.sum())  # D-015: sum to K so Jeffreys shrinkage is mild, a reads as a hit fraction


def run_ensemble_lane(
    grid: list[Config],
    weights: np.ndarray | None,
    green: np.ndarray,
    valid: np.ndarray,
    noise: NoiseModel,
    exclusion: np.ndarray,
    lane: int,
    x_center: float,
    pitch: float,
    lane_centres: list[float],
    analysable_rows: tuple[int, int],
    seed: int,
    n_surrogates: int = 60,
    od_cache: dict | None = None,
) -> tuple[list[EnsembleSpot], list[list[LanePeak]]]:
    """Run every config on one lane and cluster accepted peaks into ensemble spots.
    Pass one `od_cache` dict per plate so the 16 distinct background fits run once (M-008)."""
    per_config: list[list[LanePeak]] = []
    for idx, cfg in enumerate(grid):
        rng = np.random.Generator(np.random.PCG64(seed ^ (idx * 0x9E3779B9 + lane)))
        per_config.append(
            detect_lane(
                cfg, green, valid, noise, exclusion, x_center, pitch, lane_centres,
                analysable_rows, rng, n_surrogates=n_surrogates, od_cache=od_cache,
            )
        )
    w = np.ones(len(grid)) if weights is None else np.asarray(weights, dtype=float)
    if w.sum() > 0 and abs(w.sum() - len(grid)) > 1e-6:
        w = w * (len(grid) / w.sum())  # D-015: agreement on the sum-to-K scale

    sigma_nom = 0.18 * pitch
    tol = MATCH_TOL_FWHM * 2.355 * max(1.0, sigma_nom)

    # Greedy clustering of accepted peaks by position, strongest evidence first.
    entries = [
        (ci, pk)
        for ci, peaks in enumerate(per_config)
        for pk in peaks
        if pk.accepted
    ]
    entries.sort(key=lambda e: -e[1].z)
    clusters: list[dict] = []
    for ci, pk in entries:
        placed = False
        for cl in clusters:
            if abs(pk.row - cl["row"]) <= tol and ci not in cl["configs"]:
                n = len(cl["configs"])
                cl["row"] = (cl["row"] * n + pk.row) / (n + 1)
                cl["configs"][ci] = pk
                placed = True
                break
        if not placed:
            clusters.append({"row": pk.row, "configs": {ci: pk}})

    out: list[EnsembleSpot] = []
    w_sum = float(w.sum())
    for cl in clusters:
        hit_w = sum(w[ci] for ci in cl["configs"])
        a = (hit_w + JEFFREYS) / (w_sum + 2 * JEFFREYS)
        zs = [pk.z for pk in cl["configs"].values()]
        rows = [pk.row for pk in cl["configs"].values()]
        ps = [pk.p_mc for pk in cl["configs"].values()]
        amps = [pk.amplitude for pk in cl["configs"].values()]
        out.append(
            EnsembleSpot(
                lane=lane,
                row=float(np.average(rows, weights=[w[ci] for ci in cl["configs"]])),
                agreement=float(a),
                n_hit=len(cl["configs"]),
                n_total=len(grid),
                z_med=float(np.median(zs)),
                z_min=float(np.min(zs)),
                row_spread=float(np.std(rows)),
                p_med=float(np.median(ps)),
                amplitude_med=float(np.median(amps)),
            )
        )
    out.sort(key=lambda s: s.row)
    return out, per_config
