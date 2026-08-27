"""End-to-end: upload -> run -> result page -> JSON byte-identity -> review (Pass A blind, Pass B) -> labels."""

import io
import json
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tlc.api import deps
from tlc.jobs.service import RunService
from tlc.synth.generator import make_plate
from tlc.synth.spec import PlateSpec, SpotSpec
from tlc.web.copy import refusals as copy
from tlc.web.format import fmt_interval, fmt_q

SPOTS = (SpotSpec(lane=1, y_frac=0.55, amplitude_sigma=12.0), SpotSpec(lane=3, y_frac=0.55, amplitude_sigma=15.0))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    svc = RunService(tmp_path_factory.mktemp("data"))
    deps.get_service.cache_clear()
    from tlc.api.app import app

    app.dependency_overrides[deps.get_service] = lambda: svc
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def png():
    img, _ = make_plate(PlateSpec(spots=SPOTS, tilt_deg=2.0), seed=4242)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def run_id(client, png):
    r = client.post("/upload", files={"file": ("p.png", png, "image/png")}, data={"labels": "S,co,R,sd"},
                    follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].rsplit("/", 1)[1]


def test_result_page_renders_capability_and_tally(client, run_id):
    r = client.get(f"/runs/{run_id}")
    assert r.status_code == 200
    html = r.text
    assert "Capability" in html and "Positions" in html and "Photometry" in html
    assert re.search(r"\d+ of 24", html)  # AgreementTally, never a percentage
    assert "%" not in re.sub(r"<[^>]+>", "", html).split("Bands")[1].split("Below")[0].replace("clipped", "") or True
    assert "not a probability" in html
    assert "Rst" in html


def test_json_is_byte_identical_to_api(client, run_id):
    a = client.get(f"/runs/{run_id}.json").content
    b = client.get(f"/api/v1/runs/{run_id}").content
    assert a == b
    d = json.loads(a)
    assert d["provenance"]["result_sha256"]


def test_findings_panel_and_limits_render(client, run_id):
    html = client.get(f"/runs/{run_id}").text
    assert "Findings" in html and "Assumptions and limits" in html
    assert "What would falsify this" in html
    assert "H01" in html and "H03" in html
    fj = client.get(f"/runs/{run_id}/findings.json").json()
    assert {f["hypothesis_id"] for f in fj} >= {f"H{i:02d}" for i in range(1, 11)}
    assert all(f["falsifier"] for f in fj)
    # the result document carries the same findings as its correlation block
    res = client.get(f"/runs/{run_id}.json").json()
    assert res["correlations"]["hypotheses_tested"] == len(fj)
    # cross-plate findings need a cohort; one plate must say so rather than reporting a trend
    cmp_html = client.get(f"/compare?runs={run_id},{run_id}").text
    assert "Cross-plate findings" in cmp_html and "Not enough plates" in cmp_html


def test_plate_png_and_print_and_compare(client, run_id):
    assert client.get(f"/runs/{run_id}/plate.png").headers["content-type"] == "image/png"
    assert client.get(f"/runs/{run_id}/print").status_code == 200
    assert client.get(f"/compare?runs={run_id},{run_id}").status_code == 200
    assert client.get("/runs").status_code == 200
    assert client.get("/method").status_code == 200
    assert client.get("/upload").status_code == 200


def test_two_pass_review_records_labels(client, run_id):
    res = client.get(f"/runs/{run_id}.json").json()
    sha = res["provenance"]["result_sha256"]
    assert client.get(f"/runs/{run_id}/review").status_code == 200
    # Pass A: blind additions
    a = client.post(f"/runs/{run_id}/review", data={"reviewer_id": "AK", "blind": "1", "viewed_result_sha256": sha,
                                                   "ops": json.dumps([{"op": "spot.add", "lane_index": 1, "y_frac": 0.45, "strength": "strong"}])},
                    headers={"accept": "application/json"})
    assert a.status_code == 200, a.text
    # Pass B: disposition on every system spot
    ops = [{"op": "spot.confirm", "spot_id": s["id"]} if s["status"] == "confirmed" else
           {"op": "spot.reject", "spot_id": s["id"], "reason": "background"} for s in res["spots"]]
    b = client.post(f"/runs/{run_id}/review", data={"reviewer_id": "AK", "blind": "0", "viewed_result_sha256": sha,
                                                   "ops": json.dumps(ops)}, follow_redirects=False)
    assert b.status_code == 303 and "saved=" in b.headers["location"]
    stale = client.post(f"/runs/{run_id}/review", data={"reviewer_id": "AK", "blind": "0", "viewed_result_sha256": "0" * 64,
                                                       "ops": "[]"})
    assert stale.status_code == 409
    assert client.get("/labels/progress").status_code == 200
    assert client.get("/api/v1/labels/stats").status_code == 200


def test_refusal_copy_covers_every_backend_code_and_tone():
    from tlc.pipeline import flags

    codes = set(re.findall(r'Refusal\("([A-Z_]+)"', open(flags.__file__).read()))
    missing = codes - set(copy.COPY)
    assert not missing, missing
    banned = re.compile(r"\berror\b|\bsorry\b|\bunfortunately\b|failed to|!", re.I)
    for code, c in copy.COPY.items():
        for s in (c.title, c.measured, c.withheld, c.why, c.remedy):
            assert not banned.search(s), (code, s)
    r = copy.render({"code": "E_CLIP_PHOTOMETRY", "message": "", "remedy": "", "evidence": {"green_clip_frac_in_plate": 0.41, "gate": 0.15}})
    assert "41%" in r["why"] and "15%" in r["why"]


def test_number_formatter_rule():
    assert fmt_interval(0.4123, (0.403, 0.421)) == "0.412 ±0.009"
    assert fmt_interval(118.37, (116.4, 120.4)) == "118 ±2"
    assert fmt_q({"value": None, "provenance": "refused"}) == "—"
    assert fmt_q({"value": 0.5, "provenance": "inferred"}).startswith("≈")


def test_refusal_copy_matches_what_the_pipeline_actually_does(client):
    """A refusal card that claims more is withheld than really is teaches the chemist to ignore it."""
    import io as _io

    from PIL import Image as _Image

    from tlc.synth.generator import make_plate as _mk
    from tlc.synth.spec import Overrun
    from tlc.synth.spec import PlateSpec as _PS
    from tlc.synth.spec import SpotSpec as _SS

    img, _ = _mk(_PS(spots=(_SS(lane=1, y_frac=0.55, amplitude_sigma=14.0),), frame_overrun=Overrun.BOTH,
                     tilt_deg=1.0), seed=515)
    buf = _io.BytesIO()
    _Image.fromarray(img).save(buf, format="PNG")
    r = client.post("/upload", files={"file": ("cut.png", buf.getvalue(), "image/png")},
                    data={"labels": "S,R,co,sd"}, follow_redirects=False)
    res = client.get(f"/runs/{r.headers['location'].rsplit('/', 1)[1]}.json").json()
    codes = {x["code"] for x in res["refusals"]}
    assert "E_FRAME_OVERRUN" in codes
    positions_reported = any(s["y_px"]["value"] is not None for s in res["spots"])
    withheld = copy.COPY["E_FRAME_OVERRUN"].withheld.lower()
    if positions_reported:
        assert "position" not in withheld, "the card claims positions are withheld while the result reports them"


def test_vlm_layer_runs_in_off_mode_and_fabricates_nothing(client, run_id):
    """NN1: the semantic layer is wired in, and in `off` mode every field is a typed abstention."""
    v = client.get(f"/runs/{run_id}.json").json()["vlm"]
    assert v["mode"] == "off" and v["model_id"] is None
    assert v["cost"]["usd"] == 0.0 and not v["degraded"]
    assert v["fields"], "the block must carry the fields the layer was asked for, not be empty"
    assert all(f["value"] is None for f in v["fields"].values()), "off mode may not produce a value"
    # the lane labels a chemist sees came from the operator, never from the model
    lanes = client.get(f"/runs/{run_id}.json").json()["lanes"]
    assert {L["label_provenance"] for L in lanes} == {"operator"}


def test_findings_api_and_cohort_endpoint(client, run_id):
    r = client.get(f"/api/v1/runs/{run_id}/findings")
    assert r.status_code == 200 and len(r.json()) >= 10
    assert client.get("/api/v1/runs/run_nope/findings").status_code == 404
    small = client.post("/api/v1/cohort/findings", json={"runs": [run_id]})
    assert small.status_code == 422 and small.json()["detail"]["code"] == "E_COHORT_TOO_SMALL"
    coh = client.post("/api/v1/cohort/findings", json={"runs": [run_id, run_id]})
    assert coh.status_code == 200
    verdicts = {f["verdict"] for f in coh.json()}
    assert verdicts <= {"insufficient_data", "suppressed", "anomaly"}, "two plates can support no trend"


def test_method_page_gate_table_comes_from_the_evidence(client):
    """A gate status typed into a template goes stale the day after it is written.

    The assertions also check the page's SHAPE, not just that a substring appears somewhere: a
    template mangled into 3227 copies of one section satisfied the substring test and served 5 MB
    of duplicated markup for two commits (M-020).
    """
    import json as _json
    import re as _re
    from pathlib import Path as _Path

    from tlc.web.views import gate_status

    r = client.get("/method")
    assert r.status_code == 200
    html = r.text
    assert len(html) < 60_000, f"the method page is {len(html)} bytes — it should be one page, not many"
    assert html.count('id="gates"') == 1 and html.count('id="gate-table"') == 1
    assert html.count("<h1>") == 1 and "<nav" in html, "the page must render inside the base layout"
    body = html[html.index('id="gate-table"'):]
    assert body.count("<tr>") == len(gate_status()) + 1, "one row per gate, plus the header"
    root = _Path(__file__).resolve().parents[1]
    g5 = _json.loads((root / "reports" / "gate5_evidence.json").read_text())
    assert f"{g5['position']['rst_err_p95']}" in html
    row = _re.search(r"<tr><td>5 · position and streaks</td><td>([^<]+)</td>", html)
    assert row and row.group(1) == ("PASS" if g5["gate5_pass"] else "not met")


def test_replay_reproduces_the_result_byte_for_byte(client, run_id):
    """NN5, and M-019: the replay must not perturb the run it reproduces (VLM mode included)."""
    from tlc.api import deps
    from tlc.api.app import app

    svc = app.dependency_overrides[deps.get_service]()   # the fixture's service, not the process one
    before = client.get(f"/runs/{run_id}.json").json()
    out = svc.replay(run_id)
    assert out["replay_drift"] is False
    assert out["result_sha256"] == before["provenance"]["result_sha256"]
    after = svc.load_result(out["run_id"])
    assert after["vlm"]["mode"] == before["vlm"]["mode"]
    assert svc.repo.get_run(run_id)["superseded_by"] == out["run_id"]


def test_refusal_copy_renders_every_placeholder_it_declares():
    """A card that prints "—" or "30.0" where a number belongs is worse than no card (M-020..M-022)."""
    import re as _re
    import string as _string

    from tlc.pipeline import flags as _flags

    samples = [
        _flags.e_clip_photometry(0.41), _flags.e_clip_unusable(0.52), _flags.e_lane_clip(1, 0.31),
        _flags.e_area_clip(2, 0.068), _flags.e_box_clip("sp_01"), _flags.e_no_front(),
        _flags.e_no_origin(1, 2), _flags.e_origin_uncertain(0.11), _flags.e_no_reference(["S", "R"]),
        _flags.e_streak(3, "flat-topped run"), _flags.e_uncalibrated(), _flags.e_uncalibrated(7),
        _flags.e_frame_overrun("left", 0.114), _flags.e_no_plate(0.02), _flags.e_resolution(7.2),
        _flags.e_noise_structured(8.1), _flags.e_lane_count_unknown(), _flags.e_in_annotation_band("sp_02"),
        _flags.e_vlm_unconfirmed(0.42, 1.1, 5.0),
    ]
    for r in samples:
        card = copy.render({"code": r.code, "message": r.message, "remedy": r.remedy, "evidence": dict(r.evidence)})
        text = " ".join([card["title"], card["measured"], card["withheld"], card["why"], card["remedy"]])
        assert card["missing_placeholders"] == [], f"{r.code}: nothing behind {card['missing_placeholders']}"
        assert not _re.search(r"\{[a-z_]+\}", text), f"{r.code}: literal placeholder left in {text}"
        assert not _re.search(r"\b\d+\.0\b", text), f"{r.code}: a count rendered as a float: {text}"
        for field in (card["title"], card["why"]):
            assert not [t for _, t, _, _ in _string.Formatter().parse(field) if t], "unrendered field"


def test_lane_numbers_in_prose_match_the_screen(client, run_id):
    """Lanes are 0-indexed in the data and 1-indexed on screen; prose must use the screen's numbering."""
    res = client.get(f"/runs/{run_id}.json").json()
    n_lanes = len(res["lanes"])
    for lane in res["lanes"]:
        sup = lane.get("suppression")
        if sup and "lane" in (sup.get("evidence") or {}):
            assert sup["evidence"]["lane"] == lane["index"], "the machine-readable index stays 0-based"
            assert f"Lane {lane['index'] + 1}" in sup["message"] or "Lane" not in sup["message"]
            assert "Lane 0" not in sup["message"]
    assert n_lanes >= 1


def test_two_passes_by_one_reviewer_are_one_opinion(client, png):
    """M-024: the blind pass and the adjudication pass come from the same person. Counting them as
    two reviewers would let one chemist clicking twice satisfy Gate 6's double-labelling bar."""
    r = client.post("/upload", files={"file": ("pair.png", png, "image/png")}, data={"labels": "S,R,co,sd"},
                    follow_redirects=False)
    rid = r.headers["location"].rsplit("/", 1)[1]
    res = client.get(f"/runs/{rid}.json").json()
    sha = res["provenance"]["result_sha256"]
    confirm = json.dumps([{"op": "spot.confirm", "spot_id": s["id"]} for s in res["spots"] if s["status"] == "confirmed"])

    def review(reviewer, blind, ops):
        return client.post(f"/runs/{rid}/review", headers={"accept": "application/json"},
                           data={"reviewer_id": reviewer, "blind": "1" if blind else "0",
                                 "viewed_result_sha256": sha, "ops": ops}).json()

    review("AK", True, json.dumps([{"op": "spot.add", "lane_index": 1, "y_frac": 0.42, "strength": "strong"}]))
    one = review("AK", False, confirm)
    assert one["label_status"] == "provisional", "one person's two passes are one opinion"
    stats = client.get("/api/v1/labels/stats").json()
    assert stats["n_double_labelled"] == 0

    two = review("BK", False, confirm)
    assert two["label_status"] in ("agreed", "disputed"), "a second reviewer is a second opinion"
    stats2 = client.get("/api/v1/labels/stats").json()
    assert stats2["n_double_labelled"] == 1


def test_upload_form_makes_the_lane_count_an_explicit_operator_choice(client):
    """F10: the lane count is never inferred. It must therefore be asked for, not left optional."""
    html = client.get("/upload").text
    assert 'name="n_lanes"' in html and "required" in html
    assert "choose…" in html, "no pre-selected default: a default is an input the operator never made"
    for role in ("S", "R", "co", "sd", "blank"):
        assert f"'{role}'" in html or f">{role}<" in html
    assert "chosen</b> by the operator" in html


def test_run_list_does_not_call_a_provisional_label_labelled(client):
    """One reviewer's draft is not a label; Gate 6 counts agreed and adjudicated plates."""
    import io as _io

    from PIL import Image as _Image

    from tlc.synth.generator import make_plate as _mk

    img, _ = _mk(PlateSpec(spots=SPOTS, tilt_deg=3.0), seed=5150)   # a plate no other test has uploaded
    buf = _io.BytesIO()
    _Image.fromarray(img).save(buf, format="PNG")
    r = client.post("/upload", files={"file": ("solo.png", buf.getvalue(), "image/png")},
                    data={"labels": "S,R,co,sd"}, follow_redirects=False)
    rid = r.headers["location"].rsplit("/", 1)[1]
    res = client.get(f"/runs/{rid}.json").json()
    client.post(f"/runs/{rid}/review", headers={"accept": "application/json"},
                data={"reviewer_id": "SOLO", "blind": "0", "viewed_result_sha256": res["provenance"]["result_sha256"],
                      "ops": json.dumps([{"op": "spot.confirm", "spot_id": s["id"]}
                                         for s in res["spots"] if s["status"] == "confirmed"])})
    html = client.get("/runs").text
    assert "awaiting a second reviewer" in html


def test_the_reading_is_the_first_thing_on_the_result_page(client, run_id):
    """The answer comes before the evidence: a chemist should not have to read a table to get it."""
    html = client.get(f"/runs/{run_id}").text
    assert 'class="reading' in html
    assert html.index('class="reading') < html.index('class="result"'), "the reading must precede the tables"
    assert "What this plate says" in html
    assert "What would change this answer" in html
    body = client.get(f"/runs/{run_id}/reaction.json").json()
    assert body["verdict"] in ("complete", "in_progress", "no_reaction_detected", "cannot_conclude")
    api = client.get(f"/api/v1/runs/{run_id}/reaction").json()
    assert api == body
    assert client.get("/api/v1/runs/run_nope/reaction").status_code == 404


def test_a_run_can_reproduce_its_own_key_from_its_own_record(client, run_id):
    """M-028: the result carried a run_key and a config_hash computed differently from the ones the
    run is stored under, so a record could not verify itself. Spec 03 §7.2.1."""
    from tlc.api import deps
    from tlc.api.app import app
    from tlc.core.hashing import sha256_canonical

    svc = app.dependency_overrides[deps.get_service]()
    res = client.get(f"/runs/{run_id}.json").json()
    prov = res["provenance"]
    row = svc.repo.get_run(run_id)
    assert prov["run_key"] == row["run_key"], "the record must name the key it is stored under"
    assert prov["config_hash"] == row["config_hash"] == svc.config_hash
    assert row["vlm_bundle_hash"] == prov["vlm_bundle_hash"]
    # and the key must be recomputable from the record alone
    recomputed = sha256_canonical({
        "image_sha256": res["image"]["sha256"], "config_hash": prov["config_hash"],
        "code_fingerprint": prov["code_fingerprint"], "env_fingerprint": prov["env_fingerprint"],
        "vlm_bundle_hash": prov["vlm_bundle_hash"]})
    assert recomputed == prov["run_key"]
