"""JSON API — spec 03 §7.5 (sync `def` endpoints: the pipeline is CPU-bound numpy)."""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from tlc.api.deps import get_service
from tlc.insight.service import analyse_cohort_findings
from tlc.jobs.service import RunService
from tlc.labels.corrections import CorrectionDoc
from tlc.labels.partition import batch_key_for, effective_partition
from tlc.labels.promote import promote
from tlc.labels.stats import draft_from_row, label_stats
from tlc.labels.truth import apply_ops

router = APIRouter(prefix="/api/v1")


def _error(status: int, code: str, message: str, remedy: str = "") -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, "remedy": remedy})


@router.post("/images")
def upload_image(file: UploadFile = File(...), svc: RunService = Depends(get_service)):  # noqa: B008
    data = file.file.read()
    if not data:
        raise _error(400, "E_EMPTY_UPLOAD", "The uploaded file is empty.", "Choose an image file.")
    return svc.ingest(data, file.filename)


@router.post("/runs", status_code=200)
def create_run(file: UploadFile = File(...), n_lanes: int | None = Form(None), labels: str | None = Form(None),  # noqa: B008
               svc: RunService = Depends(get_service)):  # noqa: B008
    data = file.file.read()
    lab = tuple(x.strip() for x in labels.split(",")) if labels else None
    if lab and n_lanes is None:
        n_lanes = len(lab)
    try:
        return svc.run(data, file.filename, n_lanes=n_lanes, labels=lab)
    except Exception as e:  # the pipeline itself never raises on plate content; this is decode/IO
        raise _error(422, "E_UNPROCESSABLE", f"The image could not be processed: {type(e).__name__}.",
                     "Upload a PNG or JPEG photograph of a TLC plate.") from e


@router.get("/runs")
def list_runs(limit: int = 50, offset: int = 0, status: str | None = None, svc: RunService = Depends(get_service)):  # noqa: B008
    return {"runs": svc.repo.list_runs(limit=limit, offset=offset, status=status)}


@router.get("/runs/{run_id}")
def get_run(run_id: str, svc: RunService = Depends(get_service)):  # noqa: B008
    row = svc.repo.get_run(run_id)
    if not row:
        raise _error(404, "E_NOT_FOUND", "No such run.", "Check the run id.")
    # byte-identical to the stored pipeline output
    return Response(content=Path(row["result_path"]).read_text(), media_type="application/json")


@router.get("/runs/{run_id}/findings")
def get_findings(run_id: str, svc: RunService = Depends(get_service)):  # noqa: B008
    """Spec 02 §7.2 findings for this plate (Class A). Cross-plate findings need a cohort: POST /cohort/findings."""
    if not svc.repo.get_run(run_id):
        raise _error(404, "E_NOT_FOUND", "No such run.", "Check the run id.")
    return svc.load_findings(run_id)


@router.post("/cohort/findings")
def cohort_findings(body: dict, svc: RunService = Depends(get_service)):  # noqa: B008
    """body: {"runs": [run_id, ...], "meta": {run_id: {campaign_id, reaction_time_h, ...}}}"""
    ids = list(body.get("runs") or [])
    meta = body.get("meta") or {}
    results, metas = [], []
    for rid in ids:
        r = svc.load_result(rid)
        if r is None:
            raise _error(404, "E_NOT_FOUND", f"No such run: {rid}.", "Check the run ids.")
        results.append(r)
        metas.append(meta.get(rid, {}))
    if len(results) < 2:
        raise _error(422, "E_COHORT_TOO_SMALL", "A cohort needs at least two runs.",
                     "Select more plates; a cross-plate trend needs at least six in one campaign.")
    return [f.to_dict() for f in analyse_cohort_findings(results, metas)]


@router.post("/runs/{run_id}/replay", status_code=202)
def replay_run(run_id: str, svc: RunService = Depends(get_service)):  # noqa: B008
    try:
        return svc.replay(run_id)
    except KeyError as e:
        raise _error(404, "E_NOT_FOUND", "No such run.", "Check the run id.") from e


def record_correction(svc: RunService, run_id: str, doc: CorrectionDoc, reviewer_id: str, display_name: str | None,
                      source: str) -> dict:
    """Persist a correction and run the promoter (spec 03 §7.7.3). Shared by the JSON API and the review screen."""
    row = svc.repo.get_run(run_id)
    if not row:
        raise _error(404, "E_NOT_FOUND", "No such run.", "Check the run id.")
    result = json.loads(Path(row["result_path"]).read_text())
    if doc.viewed_result_sha256 != result["provenance"]["result_sha256"]:
        raise _error(409, "E_STALE_VIEW", "The correction was made against a different result version.",
                     "Reload the run and review again.")
    svc.repo.ensure_reviewer(reviewer_id, display_name or reviewer_id)
    cid = f"cor_{uuid.uuid4().hex[:20]}"
    ops = [op.model_dump(mode="json") for op in doc.ops]
    svc.repo.insert_correction(cid, run_id, row["image_id"], reviewer_id, doc.viewed_result_sha256, doc.blind, ops,
                               doc.review_seconds, source)
    truths = []
    for c in svc.repo.corrections_for_image(row["image_id"]):
        res_c = json.loads(Path(svc.repo.get_run(c["run_id"])["result_path"]).read_text())
        truths.append(apply_ops(res_c, json.loads(c["ops_json"]), blind=bool(c["blind"]), correction_id=c["correction_id"],
                                reviewer_id=c["reviewer_id"], review_seconds=c["review_seconds"]))
    draft = promote(truths)
    sample_id = getattr(draft.payload, "sample_id", None) if draft.payload else None
    key = batch_key_for(sample_id, capture_session=f"{result['created_at'][:10]}:{reviewer_id}")
    part = effective_partition(key, str(svc.config_doc.get("seed_salt", "salt")), draft.status)
    lid = f"lab_{uuid.uuid4().hex[:20]}"
    svc.repo.upsert_label_record(lid, row["image_id"], draft.status, draft.n_reviewers,
                                 draft.agreement.model_dump(mode="json") if draft.agreement else None,
                                 draft.payload.model_dump(mode="json") if draft.payload else {}, part, draft.derived_from)
    return {"correction_id": cid, "label_status": draft.status,
            "adjudication_id": None if draft.adjudication is None else f"adj_{lid[4:]}", "partition": part}


@router.post("/runs/{run_id}/corrections", status_code=201)
def submit_correction(run_id: str, doc: CorrectionDoc, reviewer_id: str = "anonymous", display_name: str | None = None,
                      svc: RunService = Depends(get_service)):  # noqa: B008
    return record_correction(svc, run_id, doc, reviewer_id, display_name, "api-v1")


@router.get("/labels/stats")
def labels_stats(svc: RunService = Depends(get_service)):  # noqa: B008
    recs = svc.repo.label_records()
    return label_stats([draft_from_row(r) for r in recs])


@router.get("/health")
def health():
    return {"ok": True}
