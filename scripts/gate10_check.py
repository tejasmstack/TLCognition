"""Gate 10 — schema validation on 100% of outputs, byte-identical re-run, API contract.

Runs a small corpus through the real service (synthetic plates by default, or --images DIR for real
photographs), then for every stored run:

  1. validates the stored JSON against `schemas/result_v1.schema.json` AND the pydantic model;
  2. replays it at its recorded pipeline version and requires the same `result_sha256`;
  3. checks the API contract: GET /api/v1/runs/{id} returns the stored bytes exactly, an unknown id
     is a typed 404, and a stale correction is a typed 409.

Run: uv run python scripts/gate10_check.py [--images DIR] [--n 4]
Writes reports/gate10.json and prints PASS/FAIL.
"""

import argparse
import io
import json
import tempfile
from pathlib import Path

import tlc.core.determinism  # noqa: F401, E402  (sets BLAS env; MUST import before numpy)

# isort: split

from fastapi.testclient import TestClient
from PIL import Image

from tlc.api import deps
from tlc.jobs.service import RunService
from tlc.schemas.result import Result
from tlc.synth.generator import make_plate
from tlc.synth.spec import PlateSpec, SpotSpec

ROOT = Path(__file__).resolve().parents[1]


def synth_png(seed: int) -> bytes:
    spots = (SpotSpec(lane=0, y_frac=0.55, amplitude_sigma=14.0),
             SpotSpec(lane=1, y_frac=0.55, amplitude_sigma=12.0),
             SpotSpec(lane=1, y_frac=0.32, amplitude_sigma=8.0),
             SpotSpec(lane=3, y_frac=0.55, amplitude_sigma=15.0))
    img, _ = make_plate(PlateSpec(spots=spots, tilt_deg=1.0 + (seed % 5)), seed=seed)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def json_schema_check(doc: dict, schema: dict) -> list[str]:
    """Minimal structural validation without a jsonschema dependency: required top-level keys,
    no unknown top-level keys, and the pydantic model (which is the normative contract) round-trips."""
    errs = []
    req = set(schema.get("required", [])) or set(schema.get("properties", {}))
    missing = req - set(doc)
    extra = set(doc) - set(schema.get("properties", {}))
    if missing:
        errs.append(f"missing top-level keys: {sorted(missing)}")
    if extra:
        errs.append(f"unknown top-level keys: {sorted(extra)}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default=None, help="directory of real plate photographs")
    ap.add_argument("--n", type=int, default=4)
    a = ap.parse_args()

    schema = json.loads((ROOT / "schemas" / "result_v1.schema.json").read_text())
    tmp = Path(tempfile.mkdtemp(prefix="gate10_"))
    svc = RunService(tmp)
    deps.get_service.cache_clear()
    from tlc.api.app import app

    app.dependency_overrides[deps.get_service] = lambda: svc

    if a.images:
        files = sorted(p for p in Path(a.images).rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg"))[: a.n]
        payloads = [(p.name, p.read_bytes()) for p in files]
    else:
        payloads = [(f"synth_{i}.png", synth_png(9000 + i)) for i in range(a.n)]

    runs, schema_errors, replay = [], [], []
    for name, data in payloads:
        out = svc.run(data, name, n_lanes=4, labels=("S", "R", "co", "sd"))
        runs.append(out["run_id"])

    with TestClient(app) as client:
        api = {"stored_bytes_match": True, "unknown_run_404": None, "stale_correction_409": None}
        for rid in runs:
            row = svc.repo.get_run(rid)
            doc = json.loads(Path(row["result_path"]).read_text())
            errs = json_schema_check(doc, schema)
            try:
                Result.model_validate(doc)      # the normative contract: frozen, extra="forbid"
            except Exception as e:              # noqa: BLE001 - any validation failure is a gate failure
                errs.append(f"pydantic: {type(e).__name__}: {str(e)[:200]}")
            if errs:
                schema_errors.append({"run_id": rid, "errors": errs})

            r = svc.replay(rid)
            replay.append({"run_id": rid, "replay_run_id": r["run_id"], "drift": bool(r["replay_drift"]),
                           "result_sha256": r["result_sha256"], "expected": r["expected_result_sha256"],
                           "superseded": svc.repo.get_run(rid)["superseded_by"] == r["run_id"]})

            got = client.get(f"/api/v1/runs/{rid}").content
            if got != Path(row["result_path"]).read_bytes():
                api["stored_bytes_match"] = False

        api["unknown_run_404"] = client.get("/api/v1/runs/run_does_not_exist").status_code
        rid = runs[0]
        stale = client.post(f"/api/v1/runs/{rid}/corrections",
                            json={"run_id": rid, "viewed_result_sha256": "0" * 64, "ops": []})
        api["stale_correction_409"] = stale.status_code
    app.dependency_overrides.clear()

    n = len(runs)
    verdict = {
        "gate": 10, "n_runs": n, "source": a.images or "synthetic",
        "schema_valid_frac": 0.0 if n == 0 else (n - len(schema_errors)) / n,
        "schema_errors": schema_errors,
        "replay": replay,
        "replay_identical_frac": 0.0 if n == 0 else sum(not r["drift"] for r in replay) / n,
        "replay_supersede_ok": all(r["superseded"] for r in replay) if replay else False,
        "api": api,
        "passed": bool(n > 0 and not schema_errors and all(not r["drift"] for r in replay)
                       and all(r["superseded"] for r in replay) and api["stored_bytes_match"]
                       and api["unknown_run_404"] == 404 and api["stale_correction_409"] == 409),
    }
    out_path = ROOT / "reports" / "gate10.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in verdict.items() if k != "replay"}, indent=2, sort_keys=True))
    print("GATE 10:", "PASS" if verdict["passed"] else "FAIL")
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
