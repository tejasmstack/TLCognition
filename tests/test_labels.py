"""Tests for tlc/labels (§7.7)."""

import hashlib

import pytest
from pydantic import ValidationError

from tlc.labels.agreement import (
    AgreementReport,
    compare_truths,
    krippendorff_alpha_nominal,
    match_spots,
)
from tlc.labels.corrections import CorrectionDoc, CorrectionOpAdapter, OpSpotAdd
from tlc.labels.partition import (
    batch_key_for,
    effective_partition,
    eligible_for_holdout,
    partition_for,
)
from tlc.labels.promote import LabelRecordDraft, promote, resolve_adjudication
from tlc.labels.stats import label_stats
from tlc.labels.truth import ReviewerTruth, TruthSpot, apply_ops

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def q(v, unit="frac"):
    return {"value": v, "unit": unit, "provenance": "measured"}


def machine_result(h=1000):
    return {
        "geometry": {"rectified_shape": [h, 800]},
        "lanes": [
            {"index": 0, "label": "SM", "is_streaking": q(False, "1")},
            {"index": 1, "label": "RXN", "is_streaking": q(False, "1")},
        ],
        "annotation_bands": [{"kind": "header", "y0_frac": q(0.0), "y1_frac": q(0.1)}],
        "reference": {
            "origin_row_px": q(900.0, "px"),
            "front_row_px": {
                "value": None,
                "unit": "px",
                "provenance": "refused",
                "refusal": {"code": "E", "message": "m", "remedy": "r"},
            },
            "front_provenance": "absent",
            "rst_anchor": None,
        },
        "spots": [
            {"id": "sp_00", "lane_index": 0, "status": "confirmed", "y_frac": q(0.50)},
            {"id": "sp_01", "lane_index": 1, "status": "confirmed", "y_frac": q(0.50)},
            {"id": "sp_02", "lane_index": 1, "status": "candidate", "y_frac": q(0.70)},
            {"id": "sp_03", "lane_index": 1, "status": "rejected", "y_frac": q(0.80)},
        ],
    }


def truth(spots, labels=("SM", "RXN"), origin=0.9, cid=None, rid=None):
    return ReviewerTruth(
        n_lanes=len(labels),
        lane_labels=list(labels),
        lane_streaking=[False] * len(labels),
        bands=[],
        origin_y_frac=origin,
        front_y_frac="absent",
        spots=[
            TruthSpot(lane_index=li, y_frac=y, strength=st, source="reviewer_added")
            for li, y, st in spots
        ],
        unreviewed_machine_spots=[],
        rejected=[],
        plate_rejected=False,
        sample_id="MEHQ-P32",
        aided=False,
        correction_id=cid,
        reviewer_id=rid,
    )


BASE = [(0, 0.50, "strong"), (1, 0.50, "strong"), (1, 0.70, "faint")]

# ---------------------------------------------------------------------------
# corrections
# ---------------------------------------------------------------------------


def test_op_union_parses_and_rejects_unknown():
    op = CorrectionOpAdapter.validate_python(
        {"op": "spot.add", "lane_index": 1, "y_frac": 0.4, "strength": "trace"}
    )
    assert isinstance(op, OpSpotAdd)
    with pytest.raises(ValidationError):
        CorrectionOpAdapter.validate_python({"op": "spot.teleport", "spot_id": "x"})
    with pytest.raises(ValidationError):  # extra="forbid"
        CorrectionOpAdapter.validate_python({"op": "spot.confirm", "spot_id": "x", "z": 1})
    with pytest.raises(ValidationError):  # bad reject reason
        CorrectionOpAdapter.validate_python({"op": "spot.reject", "spot_id": "x", "reason": "meh"})
    doc = CorrectionDoc(
        run_id="r",
        viewed_result_sha256="a" * 64,
        ops=[{"op": "front.set", "y_frac": None}, {"op": "plate.reject", "reason": "other"}],
    )
    assert doc.blind is False and len(doc.ops) == 2


# ---------------------------------------------------------------------------
# apply_ops
# ---------------------------------------------------------------------------


def test_apply_ops_silence_is_not_assent():
    t = apply_ops(machine_result(), [{"op": "spot.confirm", "spot_id": "sp_00"}])
    assert [s.spot_id for s in t.spots] == ["sp_00"]
    assert t.spots[0].source == "machine_confirmed"
    assert [u.spot_id for u in t.unreviewed_machine_spots] == ["sp_01", "sp_02"]
    assert t.rejected == []
    assert t.origin_y_frac == pytest.approx(0.9)
    assert t.front_y_frac == "absent"
    assert t.aided is True and t.n_lanes == 2 and t.lane_labels == ["SM", "RXN"]


def test_apply_ops_reject_move_add():
    ops = [
        {"op": "spot.reject", "spot_id": "sp_02", "reason": "artefact", "note": "dust"},
        {"op": "spot.move", "spot_id": "sp_01", "y_frac": 0.52},
        {"op": "spot.add", "lane_index": 0, "y_frac": 0.3, "strength": "faint"},
        {"op": "lane.relabel", "lane_index": 1, "label": "CRUDE"},
        {"op": "origin.set", "y_frac": 0.91},
        {"op": "sample.set_id", "sample_id": "MEHQ-P32-4hr", "conditions": "rt"},
    ]
    t = apply_ops(machine_result(), ops, blind=True)
    assert [(s.lane_index, s.y_frac, s.source) for s in t.spots] == [
        (0, 0.3, "reviewer_added"),
        (1, 0.52, "machine_moved"),
    ]
    assert [(r.spot_id, r.reason) for r in t.rejected] == [("sp_02", "artefact")]
    assert [u.spot_id for u in t.unreviewed_machine_spots] == ["sp_00"]
    assert t.lane_labels == ["SM", "CRUDE"] and t.origin_y_frac == 0.91
    assert t.sample_id == "MEHQ-P32-4hr" and t.aided is False


def test_apply_ops_unknown_spot_and_set_count():
    with pytest.raises(ValueError):
        apply_ops(machine_result(), [{"op": "spot.confirm", "spot_id": "nope"}])
    t = apply_ops(
        machine_result(),
        [{"op": "spot.confirm", "spot_id": "sp_01"}, {"op": "lane.set_count", "n_lanes": 1}],
    )
    assert t.n_lanes == 1 and t.spots == [] and t.rejected[0].reason == "lane_removed"


# ---------------------------------------------------------------------------
# agreement
# ---------------------------------------------------------------------------


def test_identical_truths_agree():
    r = compare_truths(truth(BASE), truth(BASE))
    assert r.verdict == "agreed" and r.jaccard == 1.0 and r.matched == 3
    assert r.lane_label_agree == "2/2" and r.position_delta_rst.max == 0.0
    assert r.krippendorff_alpha_positions == pytest.approx(1.0)


def test_extra_strong_spot_disputed():
    r = compare_truths(truth(BASE), truth(BASE + [(0, 0.2, "strong")]))
    assert r.verdict == "disputed"
    assert r.strong_count_delta_per_lane == [-1, 0] and r.only_b == 1


def test_trace_only_dissent():
    r = compare_truths(truth(BASE), truth(BASE + [(0, 0.2, "trace")]))
    assert r.verdict == "agreed_with_trace_dissent"
    assert r.jaccard == pytest.approx(0.75)


def test_matching_tolerance_edge():
    a = [TruthSpot(lane_index=0, y_frac=0.500, strength="strong", source="reviewer_added")]
    b = [TruthSpot(lane_index=0, y_frac=0.515, strength="strong", source="reviewer_added")]
    c = [TruthSpot(lane_index=0, y_frac=0.516, strength="strong", source="reviewer_added")]
    assert len(match_spots(a, b)[0]) == 1
    assert len(match_spots(a, c)[0]) == 0
    d = [TruthSpot(lane_index=1, y_frac=0.500, strength="strong", source="reviewer_added")]
    assert len(match_spots(a, d)[0]) == 0  # never across lanes


def test_lane_label_mismatch_disputed():
    r = compare_truths(truth(BASE), truth(BASE, labels=("SM", "CO")))
    assert r.verdict == "disputed" and r.lane_label_agree == "1/2"


def test_rst_scale_used_when_anchor_known():
    a = truth(BASE).model_copy(update={"anchor_y_frac": 0.4})
    b = truth([(0, 0.51, "strong"), (1, 0.50, "strong"), (1, 0.70, "faint")]).model_copy(
        update={"anchor_y_frac": 0.4}
    )
    r = compare_truths(a, b)
    assert r.position_scale == "rst_anchor"
    assert r.position_delta_rst.max == pytest.approx(0.01 / 0.5)
    assert r.verdict == "agreed"  # 0.02 Rst is the inclusive limit


def test_krippendorff_alpha_hand_case():
    # coders A,B over 5 units: (1,1),(1,0),(0,0),(0,0),(0,0)
    # o_10=o_01=1, o_11=2, o_00=6; n_1=3, n_0=7, n=10
    # D_o = 2/10 = 0.2 ; D_e = 2*3*7/(10*9) = 0.4667 ; alpha = 1 - 0.2/0.4667 = 0.571428
    a = krippendorff_alpha_nominal([[1, 1, 0, 0, 0], [1, 0, 0, 0, 0]])
    assert a == pytest.approx(1 - 0.2 / (42 / 90), abs=1e-9)
    assert krippendorff_alpha_nominal([[0, 0], [0, 0]]) == 1.0
    assert krippendorff_alpha_nominal([[1, None], [None, 0]]) is None
    # missing values: unit 2 has a single coder and is dropped -> same as 1-unit perfect case
    assert krippendorff_alpha_nominal([[1, 1], [1, None]]) == 1.0


# ---------------------------------------------------------------------------
# promoter
# ---------------------------------------------------------------------------


def test_promote_single_is_provisional():
    d = promote([truth(BASE, cid="c1")])
    assert d.status == "provisional" and d.agreement is None and d.derived_from == ["c1"]
    assert d.payload is not None and d.n_reviewers == 1


def test_promote_two_agreeing_median_merge():
    a = truth(BASE, cid="c1")
    b = truth([(0, 0.51, "strong"), (1, 0.50, "strong"), (1, 0.69, "faint")], cid="c2")
    d = promote([b, a])  # order independent
    assert d.status == "agreed" and d.derived_from == ["c1", "c2"]
    ys = [s.y_frac for s in d.payload.spots]
    assert ys == pytest.approx([0.505, 0.50, 0.695])
    assert d.agreement is not None and d.agreement.verdict == "agreed"
    assert d.payload.lane_labels == ["SM", "RXN"]


def test_promote_dispute_and_resolve():
    a = truth(BASE, cid="c1")
    b = truth(BASE + [(0, 0.2, "strong")], cid="c2")
    d = promote([a, b])
    assert d.status == "disputed" and d.payload is None and d.adjudication is not None
    assert d.adjudication.agreement.verdict == "disputed"
    adj = truth(BASE, cid="adj1")
    r = resolve_adjudication(d, adj)
    assert r.status == "adjudicated" and r.payload == adj
    assert r.agreement.verdict == "disputed" and r.agreement.only_b == 1  # retained
    assert r.derived_from == ["c1", "c2", "adj1"] and r.adjudication is None
    with pytest.raises(ValueError):
        resolve_adjudication(r, adj)


# ---------------------------------------------------------------------------
# partition
# ---------------------------------------------------------------------------


def _ref(batch_key, salt):
    h = int(hashlib.sha256(f"{salt}:{batch_key}".encode()).hexdigest()[:8], 16) % 100
    return "tune" if h < 60 else ("calibrate" if h < 80 else "holdout")


def test_partition_deterministic_and_fixed():
    assert partition_for("MEHQ-P32", "s1") == partition_for("MEHQ-P32", "s1")
    for k in ("MEHQ-P32", "2026-08-26/tejas", "X"):
        assert partition_for(k, "salt-v1") == _ref(k, "salt-v1")
    # pinned values (recomputed independently above; guards the algorithm text)
    assert partition_for("MEHQ-P32", "salt-v1") == _ref("MEHQ-P32", "salt-v1")


def test_partition_distribution_and_growth_stability():
    keys = [f"key-{i}" for i in range(10_000)]
    parts = [partition_for(k, "salt-v1") for k in keys]
    for name, target in (("tune", 60), ("calibrate", 20), ("holdout", 20)):
        pct = 100 * parts.count(name) / len(parts)
        assert abs(pct - target) <= 2, (name, pct)
    more = [partition_for(k, "salt-v1") for k in keys + [f"new-{i}" for i in range(500)]]
    assert more[: len(keys)] == parts


def test_batch_key_and_holdout_eligibility():
    assert batch_key_for("MEHQ-P32-4hr-plate2", "sess") == "MEHQ-P32"
    assert batch_key_for("MEHQ-P32", "sess") == "MEHQ-P32"
    assert batch_key_for("UNREADABLE", "2026-08-26/tg") == "2026-08-26/tg"
    assert batch_key_for(None, "sess") == "sess"
    with pytest.raises(ValueError):
        batch_key_for(None, None)
    assert eligible_for_holdout("agreed") and eligible_for_holdout("adjudicated")
    assert not eligible_for_holdout("provisional") and not eligible_for_holdout("disputed")
    hk = next(k for k in (f"k{i}" for i in range(1000)) if partition_for(k, "s") == "holdout")
    assert effective_partition(hk, "s", "provisional") == "tune"
    assert effective_partition(hk, "s", "agreed") == "holdout"


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_label_stats_bootstrap():
    recs: list[LabelRecordDraft] = []
    for i in range(12):
        a = truth(BASE, cid=f"a{i}")
        extra = [(0, 0.2, "strong")] if i % 3 == 0 else []
        b = truth(BASE + extra, cid=f"b{i}")
        recs.append(promote([a, b]).model_copy(update={"partition": "tune"}))
    recs.append(promote([truth(BASE, cid="solo")]))
    s1 = label_stats(recs)
    s2 = label_stats(recs)
    assert s1 == s2
    assert s1["n_labelled"] == 13 and s1["n_double_labelled"] == 12
    agr = s1["inter_reviewer_agreement"]
    assert agr["n"] == 12 and agr["rate"] == pytest.approx(8 / 12)
    lo, hi = agr["ci95"]
    assert lo <= agr["rate"] <= hi and lo < hi
    assert s1["status_counts"] == {"provisional": 1, "agreed": 8, "disputed": 4, "adjudicated": 0}
    assert s1["partition_counts"] == {"tune": 12, "calibrate": 0, "holdout": 0, "unassigned": 1}
    assert s1["krippendorff_alpha_positions"]["n"] == 12
    assert isinstance(recs[0].agreement, AgreementReport)
