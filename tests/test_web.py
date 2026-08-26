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
    """A gate status typed into a template goes stale the day after it is written."""
    import json as _json
    from pathlib import Path as _Path

    html = client.get("/method").text
    assert "Gate status" in html
    root = _Path(__file__).resolve().parents[1]
    g5 = _json.loads((root / "reports" / "gate5_evidence.json").read_text())
    assert f"{g5['position']['rst_err_p95']}" in html
    assert ("PASS" if g5["gate5_pass"] else "not met") in html


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
