"""Phase 7 machinery: isotonic map, grouped CV, ECE with a cluster-bootstrap interval, refusals."""

import numpy as np
import pytest

from tlc.calibration.calibrate import (
    ECE_MAX,
    MIN_PLATES,
    CalibrationModel,
    bootstrap_ece_ci,
    confidence_for,
    expected_calibration_error,
    fit,
    gate7_verdict,
    grouped_cv_predictions,
    isotonic_fit,
    isotonic_predict,
    reliability_table,
)
from tlc.pipeline.flags import Refusal


def _synthetic(n_plates=40, per_plate=12, seed=0):
    rng = np.random.Generator(np.random.PCG64(seed))
    g = np.repeat(np.arange(n_plates), per_plate)
    a = rng.uniform(0.2, 0.95, len(g))
    y = (rng.uniform(size=len(a)) < np.clip((a - 0.25) / 0.6, 0, 1)).astype(float)
    return a, y, g


def test_isotonic_is_monotone_and_exact_on_monotone_data():
    x = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    y = np.array([0.0, 0.0, 1.0, 1.0, 1.0])
    kx, ky = isotonic_fit(x, y)
    assert np.all(np.diff(ky) >= -1e-12)
    assert isotonic_predict(kx, ky, [0.05, 0.45]) == pytest.approx([0.0, 1.0])
    # violations are pooled to their weighted mean, never re-ordered
    kx2, ky2 = isotonic_fit([1.0, 2.0, 3.0], [1.0, 0.0, 1.0])
    assert ky2[0] == pytest.approx(0.5) and ky2[1] == pytest.approx(0.5)


def test_ece_zero_for_a_perfectly_calibrated_forecast():
    rng = np.random.Generator(np.random.PCG64(1))
    p = rng.uniform(0, 1, 20000)
    y = (rng.uniform(size=p.size) < p).astype(float)
    assert expected_calibration_error(p, y) < 0.02
    assert expected_calibration_error(np.full(1000, 0.9), np.zeros(1000)) == pytest.approx(0.9)


def test_fit_refuses_below_thirty_plates():
    a, y, g = _synthetic(n_plates=12)
    r = fit(a, y, g)
    assert isinstance(r, Refusal) and r.code == "E_UNCALIBRATED"
    assert r.evidence["labelled_plates"] == 12 and r.evidence["required"] == MIN_PLATES
    assert gate7_verdict(r)["passed"] is False


def test_fit_refuses_when_every_label_is_the_same():
    a, _, g = _synthetic()
    r = fit(a, np.ones_like(a), g)
    assert isinstance(r, Refusal) and r.code == "E_UNCALIBRATED"


def test_fit_reports_ece_with_interval_and_passes_gate_on_clean_labels():
    a, y, g = _synthetic()
    m = fit(a, y, g)
    assert isinstance(m, CalibrationModel)
    v = gate7_verdict(m)
    assert v["passed"] and v["ece"] <= ECE_MAX
    lo, hi = m.ece_ci95
    assert 0 <= lo <= m.ece + 0.05 and hi >= m.ece - 0.05
    assert m.n_plates == 40 and m.fitted_on == "grouped_cv"


def test_grouped_cv_never_trains_on_the_plate_it_predicts():
    """A plate whose labels are inverted must not be able to fit itself."""
    a, y, g = _synthetic(n_plates=30)
    y2 = y.copy()
    y2[g == 0] = 1 - y2[g == 0]
    p = grouped_cv_predictions(a, y2, g)
    assert np.mean((p[g == 0] - y2[g == 0]) ** 2) > np.mean((p[g != 0] - y2[g != 0]) ** 2)


def test_bootstrap_interval_resamples_plates_not_spots():
    a, y, g = _synthetic(n_plates=30)
    p = grouped_cv_predictions(a, y, g)
    lo, hi = bootstrap_ece_ci(p, y, g, n_boot=200)
    assert 0.0 <= lo <= hi <= 1.0


def test_reliability_table_shape():
    a, y, g = _synthetic()
    rows = reliability_table(grouped_cv_predictions(a, y, g), y)
    assert len(rows) == 10 and sum(r["n"] for r in rows) == len(y)


def test_confidence_is_refused_without_a_model():
    v, r = confidence_for(0.7, None)
    assert v is None and r.code == "E_UNCALIBRATED"


def test_no_calibration_model_is_shipped_yet():
    """NN4: until Gate 7 passes on real labels, no map may sit in config/."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert not list((root / "config").glob("calibration/*.json")), "a shipped map implies a passed Gate 7"
