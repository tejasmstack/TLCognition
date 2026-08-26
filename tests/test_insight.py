"""Spec 02: registry, estimators, multiplicity arithmetic, confound suppression, unlock ladder."""

import math

import numpy as np
import pytest

from tlc.insight import estimators as E
from tlc.insight import render
from tlc.insight import variables as V
from tlc.insight.cohort import analyse_cohort, independent_units
from tlc.insight.confounds import check_confound
from tlc.insight.multiplicity import bh_adjusted, holm_adjusted, max_family_size
from tlc.insight.registry import load_registry, registered


def _band(bid, lane, role, rst, agree=0.7, snr=8.0, area=1.0, peak=0.2):
    return V.Band(id=bid, lane_index=lane, lane_label=role, lane_role=role, rst=rst, rst_ci=(rst - 0.01, rst + 0.01),
                  y_frac=0.5, agree=agree, snr=snr, area_od=area, peak_od=peak, clip_frac=0.0, tail_factor=0.8,
                  fwhm_px=6.0, shape_class="gaussian", in_annotation_band=False, status="confirmed")


def _plate(i, n_bands, sigma, campaign=None, t=None):
    bands = [_band(f"sp_{i}{j}", 1, "R", 0.9 - 0.1 * j) for j in range(n_bands)]
    bands.append(_band(f"sd_{i}", 3, "sd", 1.0))
    return V.PlateVars(run_id=f"run_{i:024d}", image_sha="0" * 64, created_at="2026-08-26T00:00:00Z",
                       campaign_id=campaign or f"P{i}", reaction_time_h=t, solvent_system_id="A", operator="AK",
                       n_lanes=4, lane_roles=["S", "R", "co", "sd"], bands=bands, quantified_lanes=[0, 1, 2, 3],
                       capture={"sigma_od": sigma, "green_clip_frac": 0.0, "mpix": 0.02 + 0.001 * i, "lane_px": 20.0,
                                "tilt_deg": 2.0, "plate_area_frac": 0.9, "focus_metric": 1.0, "capture_order": float(i)},
                       photometry_mode="full")


# --------------------------------------------------------------------------- registry
def test_registry_frozen_and_every_hypothesis_falsifiable():
    doc, h = load_registry()
    assert len(h) == 64
    ids = [x["id"] for x in doc["hypotheses"]]
    assert ids == sorted(ids) and len(ids) == 22          # §0: 22 registered relationships
    assert len(registered("A")) == 10 and len(registered("B")) == 9 and len(registered("C")) == 3
    for x in doc["hypotheses"]:
        assert x["falsifier"] and x["plain_language"]


def test_no_open_search():
    """§4.8/§5: only registered hypothesis ids may appear in output."""
    ids = {x["id"] for x in registered()}
    out = analyse_cohort([_plate(i, 2, 0.01) for i in range(3)])
    assert {f.hypothesis_id for f in out} <= ids


# --------------------------------------------------------------------------- §4.3 exact reality table
@pytest.mark.parametrize("n,expected", [(4, 2 / 24), (5, 2 / 120), (6, 2 / 720), (7, 2 / 5040)])
def test_exact_floor_matches_reality_table(n, expected):
    assert E.exact_floor(n) == pytest.approx(expected)


def test_rho_required_matches_spec_table():
    assert E.rho_required(6, 0.05) == pytest.approx(0.886, abs=0.002)
    assert E.rho_required(7, 0.05) == pytest.approx(0.786, abs=0.002)
    assert E.rho_required(6, 0.05, "one_sided") == pytest.approx(0.771, abs=0.002)
    assert E.rho_required(4, 0.05) is None          # arithmetically unattainable


def test_permutation_p_is_exact_and_never_zero():
    x = list(range(7))
    r = E.permutation_p(x, x, "spearman", "two_sided")
    assert r["p"] == pytest.approx(2 / math.factorial(7)) and r["method"].endswith("exact_permutation")
    assert r["p"] > 0
    r1 = E.permutation_p(x, x, "spearman", "one_sided", "positive")
    assert r1["p"] == pytest.approx(1 / math.factorial(7))


def test_kendall_chosen_for_counts():
    assert E.choose_estimator([1, 1, 2, 2, 3, 3, 3, 4], is_count=True) == "kendall"
    assert E.choose_estimator(list(np.linspace(0, 1, 12)), is_count=False) == "spearman"


# --------------------------------------------------------------------------- §4.7 multiplicity
def test_bh_and_holm():
    assert bh_adjusted([0.001, 0.5, 0.9])[0] == pytest.approx(0.003)
    assert holm_adjusted([0.01, 0.02, 0.03])[0] == pytest.approx(0.03)
    assert bh_adjusted([]) == []


def test_unlock_arithmetic():
    """m ≤ q·n!/2 — the arithmetic that governs everything (§4.7)."""
    assert max_family_size(4) == 1
    assert max_family_size(5) == 6
    assert max_family_size(6) == 36
    assert max_family_size(7) == 252


# --------------------------------------------------------------------------- §3 confound panel
def test_partial_correlation_and_c01_fires_on_sigma():
    rng = np.random.Generator(np.random.PCG64(0))
    sigma = np.linspace(0.004, 0.016, 8)
    count = 9 - 400 * sigma + rng.normal(0, 0.05, 8)     # counts driven purely by the noise floor
    order = np.arange(8, dtype=float)                     # "time" happens to track capture order
    c = check_confound("C01", "sigma_od", order, count, sigma)
    assert c["result"] == "FIRED"
    assert "explained by sigma_od, not by the chemistry" in c["statement"]
    assert abs(c["rho_response_vs_confound"]) >= 0.70


def test_count_trend_explained_by_noise_is_suppressed_not_reported():
    """§3.3 flagship: fewer bands on noisier plates is the detector, not the chemistry."""
    sig = np.linspace(0.004, 0.016, 8)
    plates = [_plate(i, max(0, 8 - i), float(sig[i]), campaign=f"P{i}", t=float(i)) for i in range(8)]
    out = analyse_cohort(plates)
    h14 = next(f for f in out if f.hypothesis_id == "H14")
    assert h14.verdict in ("suppressed", "insufficient_data")
    if h14.verdict == "suppressed":
        codes = {r["code"] for r in h14.suppression["reasons"]}
        assert "CONFOUND_C01" in codes or "FAILS_MULTIPLICITY" in codes or "UNDERPOWERED_DESIGN" in codes
    assert not [f for f in out if f.verdict == "reported"]


# --------------------------------------------------------------------------- §8 insufficient-data ladder
def test_five_campaigns_cannot_report_any_cross_plate_correlation():
    """§4.7 applied: Class B family of 9 at 5 independent units → m ≤ 6 < 9."""
    plates = [_plate(i, 2, 0.01, campaign="P32" if i in (1, 2, 4) else f"P{i}", t=float(i)) for i in range(7)]
    assert independent_units(plates, "plate") == 5
    assert independent_units(plates, "campaign") == 5
    out = analyse_cohort(plates)
    assert not [f for f in out if f.verdict in ("reported", "tentative")]
    ins = [f for f in out if f.verdict == "insufficient_data"]
    assert ins, "every untestable hypothesis must say so"
    f = ins[0]
    assert f.evidence["n_campaigns"] == 5 and f.evidence["n_plates"] == 7
    assert "Not enough plates" in f.caveats[0]
    assert f.what_would_make_this_reportable


def test_three_plates_states_the_arithmetic():
    out = analyse_cohort([_plate(i, 2, 0.01, t=float(i)) for i in range(3)])
    f11 = next(f for f in out if f.hypothesis_id == "H11")     # pre-registered sign -> one-sided
    assert f11.verdict == "insufficient_data"
    assert "one-sided p of 0.167" in f11.caveats[0]
    f14 = next(f for f in out if f.hypothesis_id == "H14")     # two-sided: the spec's own 0.333 at n=3
    assert "two-sided p of 0.333" in f14.caveats[0]


# --------------------------------------------------------------------------- §7.1 / §9 linter
def test_forbidden_words_linter():
    assert render.lint("This result is significant") == ["significant"]
    assert render.lint("It proves the structure") == ["proves"]
    assert render.lint("Rf 0.43") == ["Rf"]
    assert render.lint("Rst is reported; Rf is not reported on this corpus") == []
    assert render.lint("Positions are measured; agreement is 25 of 32.") == []


def test_every_emitted_finding_passes_the_linter():
    for f in analyse_cohort([_plate(i, 2, 0.01, t=float(i)) for i in range(4)]):
        assert f.lint() == []
