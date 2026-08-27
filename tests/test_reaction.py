"""The reaction reading: does the plate get read the way a chemist reads it?

Each test builds a synthetic four-lane plate where the answer is known by construction — starting
material at one height, product at another, an impurity where neither runs — and asks whether the
reading says what the plate was built to say. The seven rules of the project's design document
(TLC_System_Design_Visual.pdf p7) each get a test that can fail.
"""

import io

import numpy as np
import pytest
from PIL import Image

from tlc.insight.reaction import (
    ANCHOR_MIN_SNR,
    ORIGIN_ZONE_RST,
    RST_MATCH_TOL,
)
from tlc.jobs.service import RunService
from tlc.synth.generator import make_plate
from tlc.synth.spec import PlateSpec, SpotShape, SpotSpec

LANES = ("S", "R", "co", "sd")     # S=0, R=1, co=2, sd=3
SM_Y, PROD_Y = 0.45, 0.80          # where the starting material and the product run


def _plate(spots, seed=6100, **kw) -> bytes:
    img, _ = make_plate(PlateSpec(plate_w=140, plate_h=260, tilt_deg=1.0, clip_fraction=0.0,
                                  spots=tuple(spots), **kw), seed=seed)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def _read(tmp_path, spots, seed=6100, **kw):
    svc = RunService(tmp_path)
    out = svc.run(_plate(spots, seed, **kw), "t.png", n_lanes=4, labels=LANES)
    rep = svc.load_reaction(out["run_id"])
    assert rep is not None, "every run must produce a reading, even a refusing one"
    return rep, svc.load_result(out["run_id"])


def _spot(lane, y, amp=16.0, **kw):
    return SpotSpec(lane=lane, y_frac=y, amplitude_sigma=amp, **kw)


# --- the four verdicts ---------------------------------------------------------------------------
def test_reaction_in_progress_is_read_as_both_present(tmp_path):
    """SM in R and product in R: the plate is part-way, and the reading says so in both voices."""
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(3, PROD_Y, 20),
        _spot(1, SM_Y, 14), _spot(1, PROD_Y, 14),
        _spot(2, SM_Y, 16), _spot(2, PROD_Y, 12),
    ])
    assert rep["verdict"] == "in_progress"
    ids = {a["identity"] for a in rep["assignments"]}
    assert {"starting_material", "product"} <= ids
    answer = " ".join(rep["plain_summary"])
    assert "not finished" in answer or "under way" in answer
    assert any("SM anchor" in line for line in rep["chemist_summary"])


def test_complete_when_no_starting_material_band_remains(tmp_path):
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(3, PROD_Y, 20),
        _spot(1, PROD_Y, 18),
        _spot(2, SM_Y, 16), _spot(2, PROD_Y, 14),
    ], seed=6101)
    assert rep["verdict"] == "complete"
    assert any(a["identity"] == "product" for a in rep["assignments"])
    assert not any(a["identity"] == "starting_material" for a in rep["assignments"])
    # a "complete" call must carry the way it could be wrong
    assert any("below" in f and "detection limit" in f for f in rep["what_would_change_this"])


def test_no_conversion_when_only_starting_material_is_present(tmp_path):
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(3, PROD_Y, 20),
        _spot(1, SM_Y, 18),
        _spot(2, SM_Y, 16),
    ], seed=6102)
    assert rep["verdict"] == "no_reaction_detected"
    assert "below what this photograph can show" in " ".join(rep["plain_summary"])


def test_missing_reference_lane_refuses_instead_of_guessing(tmp_path):
    """R2: without an anchor there is no comparison to make, and the reading says which one is missing."""
    rep, _ = _read(tmp_path, [_spot(1, SM_Y, 18), _spot(1, PROD_Y, 18)], seed=6103)
    assert rep["verdict"] == "cannot_conclude"
    codes = {r["code"] for r in rep["refusals"]}
    assert codes & {"E_NO_SM_ANCHOR", "E_NO_PRODUCT_ANCHOR"}
    assert rep["confidence"]["grade"] in ("low", "medium")
    assert rep["quantities"]["apparent_conversion"]["value"] is None


# --- the rules ------------------------------------------------------------------------------------
def test_r2_origin_zone_never_anchors(tmp_path):
    """A band at the application point is polar residue or overload, never the reference."""
    rep, res = _read(tmp_path, [
        _spot(0, 0.03, 25), _spot(0, SM_Y, 18),        # origin residue AND a real SM band
        _spot(3, PROD_Y, 20), _spot(1, SM_Y, 16),
    ], seed=6104)
    anchor = rep["anchors"]["starting_material"]
    assert anchor is not None
    assert anchor["rst"] is None or anchor["rst"] >= ORIGIN_ZONE_RST


def test_r3_impurity_present_in_the_starting_material_is_marked_inherited(tmp_path):
    """The action for an inherited impurity is to purify the SM, not to change the route."""
    imp_y = 0.62
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(0, imp_y, 12),        # the impurity rides in on the SM
        _spot(3, PROD_Y, 20),
        _spot(1, SM_Y, 14), _spot(1, imp_y, 12), _spot(1, PROD_Y, 14),
        _spot(2, SM_Y, 16), _spot(2, imp_y, 10),
    ], seed=6105)
    inherited = [i for i in rep["impurities"] if i["inherited_from_starting_material"]]
    assert inherited, f"expected an inherited impurity, got {rep['impurities']}"
    assert "purify the starting material" in inherited[0]["reading"]


def test_r4_cospot_decomposition_runs_and_scores_the_plate(tmp_path):
    """co should be explainable as a mixture of S and R; R^2 is the plate's self-consistency."""
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(3, PROD_Y, 20),
        _spot(1, SM_Y, 14), _spot(1, PROD_Y, 14),
        _spot(2, SM_Y, 18), _spot(2, PROD_Y, 12),
    ], seed=6106)
    co = rep["cospot"]
    assert co["available"], co.get("reason")
    assert 0.0 <= co["r_squared"] <= 1.0
    assert co["alpha_S"] >= 0 and co["beta_R"] >= 0, "a negative amount of a lane is not a mixture"
    assert "mixture" in co["reading"]


def test_r6_a_streaking_lane_gets_zones_not_percentages(tmp_path):
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(3, PROD_Y, 20),
        _spot(1, 0.35, 25, shape=SpotShape.STREAK),
        _spot(2, SM_Y, 16),
    ], seed=6107)
    conv = rep["quantities"]["apparent_conversion"]
    if conv["value"] is None:
        assert conv["refusal"]["code"] in ("E_STREAK_NO_CONVERSION", "E_CONVERSION_UNQUANTIFIED",
                                           "E_NO_SM_OR_PRODUCT", "E_NO_SIGNAL")
    for a in rep["assignments"]:
        if a["share_of_lane"]["value"] is None:
            assert a["share_of_lane"]["refusal"]["code"].startswith("E_")


def test_r7_conversion_is_within_one_lane_and_never_a_mole_claim(tmp_path):
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(3, PROD_Y, 20),
        _spot(1, SM_Y, 8), _spot(1, PROD_Y, 20),        # mostly converted
        _spot(2, SM_Y, 16), _spot(2, PROD_Y, 12),
    ], seed=6108)
    conv = rep["quantities"]["apparent_conversion"]
    if conv["value"] is not None:
        assert 0.0 <= conv["value"] <= 1.0
        assert "within this lane only" in conv["basis"]
        assert conv["provenance"] == "inferred"
    assert any("not of moles" in c or "not a yield" in c for c in rep["caveats"])


# --- the honesty contract -------------------------------------------------------------------------
def test_no_probability_appears_anywhere_in_the_reading(tmp_path):
    """NN1/NN4: confidence is an ordinal grade with named factors, never a number."""
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(3, PROD_Y, 20), _spot(1, SM_Y, 14), _spot(1, PROD_Y, 14),
    ], seed=6109)
    assert rep["confidence"]["grade"] in ("high", "medium", "low")
    assert isinstance(rep["confidence"]["factors"], list) and rep["confidence"]["factors"]
    text = " ".join(rep["plain_summary"] + rep["chemist_summary"] + rep["caveats"])
    for banned in ("probability", "% confident", "% sure", "p-value"):
        assert banned not in text.lower()
    for a in rep["assignments"]:
        assert a["confidence"] in ("high", "medium", "low")


def test_every_number_in_the_reading_is_enveloped(tmp_path):
    """Every quantity is a value with a provenance and a basis, or a refusal with a remedy."""
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(3, PROD_Y, 20), _spot(1, SM_Y, 14), _spot(1, PROD_Y, 14),
    ], seed=6110)
    envelopes = [a["share_of_lane"] for a in rep["assignments"]]
    envelopes += [v for k, v in rep["quantities"].items() if isinstance(v, dict) and "provenance" in v]
    envelopes.append(rep["matrix_shift"]["applied"])
    assert envelopes
    for e in envelopes:
        assert e["provenance"] in ("measured", "inferred", "chosen", "refused")
        if e["provenance"] == "refused":
            assert e["value"] is None and e["refusal"]["message"] and e["refusal"]["remedy"]
        else:
            assert e["value"] is not None
            assert e["basis"], "a number without a stated basis is a number nobody can check"


def test_a_non_chemist_gets_told_what_a_plate_even_is(tmp_path):
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(3, PROD_Y, 20), _spot(1, SM_Y, 14),
    ], seed=6111)
    plain = " ".join(rep["plain_summary"]).lower()
    assert "race" in plain or "climbs" in plain, "the plain summary must explain the plate itself"
    assert "**the answer" in plain, "the plain summary must contain the answer, marked"
    assert "not proof" in plain, "the plain summary must state the limit of the evidence"
    for term in ("band", "Rst", "co-spot"):
        assert term in rep["glossary"]


def test_the_reading_is_deterministic(tmp_path):
    """NN5: the same plate must read the same way twice."""
    spots = [_spot(0, SM_Y, 20), _spot(3, PROD_Y, 20), _spot(1, SM_Y, 14), _spot(1, PROD_Y, 14)]
    a, _ = _read(tmp_path / "a", spots, seed=6112)
    b, _ = _read(tmp_path / "b", spots, seed=6112)
    assert a == b


def test_matrix_shift_needs_two_agreeing_estimates(tmp_path):
    """R1: one estimate is used with a widened tolerance; none means no shift at all."""
    rep, _ = _read(tmp_path, [
        _spot(0, SM_Y, 20), _spot(3, PROD_Y, 20), _spot(1, SM_Y, 14),
        _spot(2, SM_Y, 16),
    ], seed=6113)
    ms = rep["matrix_shift"]
    assert ms["applied"]["provenance"] in ("measured", "chosen")
    assert ms["tolerance"] >= RST_MATCH_TOL
    if ms["agree"] is False:
        assert ms["applied"]["value"] == 0.0
        assert ms["tolerance"] > RST_MATCH_TOL


def test_anchor_snr_floor_is_enforced(tmp_path):
    """A reference band too weak to trust cannot anchor the whole reading."""
    rep, res = _read(tmp_path, [
        _spot(0, SM_Y, 5.0), _spot(3, PROD_Y, 20), _spot(1, SM_Y, 16),
    ], seed=6114)
    a = rep["anchors"]["starting_material"]
    if a is not None:
        assert a["snr"] >= ANCHOR_MIN_SNR


@pytest.mark.parametrize("dataset_plate", ["MEHQ-P20_2nd Step 1hr_21st July26.png"])
def test_reads_a_real_plate_without_crashing(tmp_path, dataset_plate):
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "dataset" / dataset_plate
    if not p.exists():
        pytest.skip("corpus not present")
    svc = RunService(tmp_path)
    out = svc.run(p.read_bytes(), p.name, n_lanes=4, labels=LANES)
    rep = svc.load_reaction(out["run_id"])
    assert rep["verdict"] in ("complete", "in_progress", "no_reaction_detected", "cannot_conclude")
    assert rep["plain_summary"] and rep["chemist_summary"]
    assert rep["what_would_change_this"]


def test_xcorr_shift_recovers_a_known_lag():
    from tlc.insight.reaction import _xcorr_shift

    y = np.zeros(300)
    y[100:110] = 1.0
    y[180:190] = 0.6
    shifted = np.roll(y, 7)
    lag, quality = _xcorr_shift(y, shifted, 30)
    assert lag == pytest.approx(7, abs=1)
    assert quality > 0.05


def test_nnls2_is_non_negative_and_scores_a_true_mixture():
    from tlc.insight.reaction import _nnls2

    rng = np.random.Generator(np.random.PCG64(3))
    s = np.abs(rng.normal(0, 1, 200))
    r = np.abs(rng.normal(0, 1, 200))
    co = 0.4 * s + 0.9 * r
    a, b, r2 = _nnls2(np.column_stack([s, r]), co)
    assert a == pytest.approx(0.4, abs=0.05) and b == pytest.approx(0.9, abs=0.05)
    assert r2 > 0.95
    a2, b2, _ = _nnls2(np.column_stack([s, r]), -s)      # a negative mixture is not a mixture
    assert a2 >= 0 and b2 >= 0
