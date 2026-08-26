"""Confidence calibration: map ensemble agreement -> P(band is real), with grouped CV by plate,
expected calibration error and a bootstrap interval (Gate 7).

Until a real labelled set exists this module produces nothing user-visible: `confidence_for` refuses
with E_UNCALIBRATED, and `fit` refuses below `MIN_PLATES` labelled plates. The machinery is tested on
synthetic labels so that the day the labels arrive, the only new thing is the data.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from tlc.pipeline.flags import Refusal, e_uncalibrated


def isotonic_fit(x, y, w=None) -> tuple[np.ndarray, np.ndarray]:
    """Pool-adjacent-violators isotonic regression (no sklearn dependency; PAVA is 20 lines and
    deterministic). Returns (knot_x, knot_y) suitable for np.interp."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    w = np.ones_like(x) if w is None else np.asarray(w, float)
    order = np.argsort(x, kind="stable")
    x, y, w = x[order], y[order], w[order]
    # average duplicate x first so the fit is a function of x
    ux, inv = np.unique(x, return_inverse=True)
    sw = np.bincount(inv, weights=w)
    sy = np.bincount(inv, weights=w * y) / np.maximum(sw, 1e-12)
    vals, wts, sizes = [], [], []
    for v, ww in zip(sy, sw, strict=True):
        vals.append(v)
        wts.append(ww)
        sizes.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:      # violation: pool
            v2, w2 = vals.pop(), wts.pop()
            s2 = sizes.pop()
            v1, w1 = vals.pop(), wts.pop()
            s1 = sizes.pop()
            tw = w1 + w2
            vals.append((v1 * w1 + v2 * w2) / max(tw, 1e-12))
            wts.append(tw)
            sizes.append(s1 + s2)
    out = np.concatenate([np.full(s, v) for v, s in zip(vals, sizes, strict=True)])
    return ux, np.clip(out, 0.0, 1.0)


def isotonic_predict(knot_x, knot_y, x) -> np.ndarray:
    return np.interp(np.asarray(x, float), knot_x, knot_y)


MIN_PLATES = 30            # Gate 6 requirement; calibration refuses below it
MIN_DOUBLE = 10
ECE_MAX = 0.10             # Gate 7
N_BINS = 10
N_BOOTSTRAP = 2000


@dataclass(frozen=True)
class CalibrationModel:
    version: str
    method: str                      # "isotonic" (monotone, no shape assumption at small n)
    x: list[float]                   # knots: ensemble agreement
    y: list[float]                   # calibrated probabilities
    n_samples: int
    n_plates: int
    fitted_on: str                   # partition name
    ece: float
    ece_ci95: tuple[float, float]
    brier: float
    holdout_n: int

    def predict(self, agreement: float) -> float:
        return float(np.interp(agreement, self.x, self.y))

    def to_dict(self) -> dict:
        return asdict(self)


def expected_calibration_error(p: np.ndarray, y: np.ndarray, n_bins: int = N_BINS) -> float:
    """Equal-width binned |confidence - accuracy|, weighted by bin population."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    if len(p) == 0:
        return float("nan")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(ece)


def bootstrap_ece_ci(p: np.ndarray, y: np.ndarray, groups: np.ndarray, n_boot: int = N_BOOTSTRAP,
                     seed: int = 0) -> tuple[float, float]:
    """Cluster bootstrap over plates: resample plates, not spots — spots within a plate share σ."""
    rng = np.random.Generator(np.random.PCG64(seed))
    uniq = np.unique(groups)
    vals = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        m = np.concatenate([np.where(groups == g)[0] for g in pick])
        v = expected_calibration_error(p[m], y[m])
        if np.isfinite(v):
            vals.append(v)
    if not vals:
        return (float("nan"), float("nan"))
    return tuple(float(x) for x in np.quantile(vals, [0.025, 0.975]))


def grouped_cv_predictions(x: np.ndarray, y: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """Leave-one-plate-out isotonic predictions — grouped by plate throughout (Gate 7)."""
    out = np.zeros_like(x, dtype=float)
    for g in np.unique(groups):
        te = groups == g
        tr = ~te
        if tr.sum() < 2 or len(np.unique(y[tr])) < 2:
            out[te] = y[tr].mean() if tr.any() else 0.5
            continue
        kx, ky = isotonic_fit(x[tr], y[tr])
        out[te] = isotonic_predict(kx, ky, x[te])
    return out


def fit(agreements, labels, plate_ids, version: str = "cal_v1", holdout_mask=None) -> CalibrationModel | Refusal:
    """Fit the map on the calibrate partition; report ECE on the held-out partition when given.
    Refuses (never guesses) below MIN_PLATES labelled plates."""
    x = np.asarray(agreements, float)
    y = np.asarray(labels, float)
    g = np.asarray(plate_ids)
    n_plates = len(np.unique(g))
    if n_plates < MIN_PLATES:
        return e_uncalibrated(n_plates, MIN_PLATES)
    if len(np.unique(y)) < 2:
        return Refusal("E_UNCALIBRATED", "Every labelled band has the same outcome; a calibration map cannot be fitted.",
                       "Label plates that include both confirmed and rejected bands.",
                       {"labelled_plates": n_plates, "positives": float(y.sum())})
    kx, ky = isotonic_fit(x, y)
    if holdout_mask is not None:
        hm = np.asarray(holdout_mask, bool)
        p_eval, y_eval, g_eval = isotonic_predict(kx, ky, x[hm]), y[hm], g[hm]
    else:
        p_eval, y_eval, g_eval = grouped_cv_predictions(x, y, g), y, g
    ece = expected_calibration_error(p_eval, y_eval)
    lo, hi = bootstrap_ece_ci(p_eval, y_eval, g_eval)
    brier = float(np.mean((p_eval - y_eval) ** 2))
    return CalibrationModel(version=version, method="isotonic_pava", x=[float(v) for v in kx],
                            y=[float(v) for v in ky], n_samples=int(len(x)), n_plates=int(n_plates),
                            fitted_on="calibrate" if holdout_mask is not None else "grouped_cv",
                            ece=ece, ece_ci95=(lo, hi), brier=brier, holdout_n=int(len(p_eval)))


def gate7_verdict(model: CalibrationModel | Refusal) -> dict:
    if isinstance(model, Refusal):
        return {"passed": False, "reason": model.code, "message": model.message, "ece": None}
    return {"passed": bool(model.ece <= ECE_MAX), "reason": None if model.ece <= ECE_MAX else "ECE_ABOVE_GATE",
            "ece": model.ece, "ece_ci95": list(model.ece_ci95), "gate": ECE_MAX, "n_plates": model.n_plates,
            "n_samples": model.n_samples, "holdout_n": model.holdout_n}


def reliability_table(p, y, n_bins: int = N_BINS) -> list[dict]:
    """The data behind the committed reliability diagram."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        m = idx == b
        rows.append({"bin": [round(edges[b], 2), round(edges[b + 1], 2)], "n": int(m.sum()),
                     "mean_confidence": None if not m.any() else round(float(p[m].mean()), 4),
                     "observed_rate": None if not m.any() else round(float(y[m].mean()), 4)})
    return rows


def load_model(path: Path) -> CalibrationModel | None:
    if not Path(path).exists():
        return None
    d = json.loads(Path(path).read_text())
    d["ece_ci95"] = tuple(d["ece_ci95"])
    return CalibrationModel(**d)


def confidence_for(agreement: float, model: CalibrationModel | None) -> tuple[float | None, Refusal | None]:
    """NN2/NN4: before Gate 7 this returns (None, E_UNCALIBRATED) — the pipeline's own behaviour."""
    if model is None:
        return None, e_uncalibrated(0, MIN_PLATES)
    return model.predict(agreement), None
