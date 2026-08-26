"""Server-rendered screens (spec 04 §11.1–11.4). Numbers pass through tlc.web.format only;
refusal sentences come from tlc.web.copy.refusals only. D-018: one inline vanilla-JS island."""

import io
import json
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from PIL import Image

from tlc.api.deps import get_service
from tlc.api.routers.runs import record_correction
from tlc.jobs.service import RunService, decode_image
from tlc.labels.corrections import CorrectionDoc
from tlc.labels.stats import draft_from_row, label_stats
from tlc.web import format as nf
from tlc.web.copy import refusals as copy

router = APIRouter(include_in_schema=False)
_here = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_here / "templates"))
templates.env.filters.update({"q": nf.fmt_q, "pct": nf.fmt_pct, "plain": nf.fmt_plain, "interval": nf.fmt_interval})
templates.env.globals.update({"refusal_copy": copy.render})

# Detection-limit statement (§11.3.H): the faintest band that would have registered ≈ Z_LIMIT × σ_od.
# `chosen`, stated on /method; not derived from the ensemble threshold, which is an agreement fraction.
Z_LIMIT = 5.0


# ----------------------------------------------------------------------------- view model
def capability(res: dict) -> list[dict]:
    """Four independent segments (§11.3.F): state ∈ measured | refused | partial | none."""
    status = res["status"]
    pm = res["photometry"]["photometry_mode"]
    lanes = res["lanes"]
    ref = res["reference"]
    pos_state = "refused" if status == "refused" or not lanes else "measured"
    pos_word = "measured" if pos_state == "measured" else "refused"
    if pm == "full":
        pho = ("measured", "measured")
    elif pm == "positions_only":
        clip = res["capture_qc"]["green_clip_frac_in_plate"]["value"]
        pho = ("refused", f"refused — {nf.fmt_pct(clip)} of the plate is clipped")
    else:
        pho = ("refused", "refused")
    provs = {L["label_provenance"] for L in lanes}
    if lanes and provs == {"operator"}:
        ide = ("measured", "confirmed by operator")
    elif lanes and "vlm" in provs:
        ide = ("partial", "unverified — model read, not confirmed by a person")
    else:
        ide = ("none", "not read — lane labels not provided")
    if ref.get("rst_anchor"):
        sca = ("measured", "Rst — no solvent front, so Rf is not reported" if not ref["rf_available"] else "Rst")
    elif status == "refused":
        sca = ("none", "not attempted")
    else:
        why = (ref.get("rf_unavailable_reason") or {}).get("code", "")
        sca = ("refused", f"refused — {why.replace('_', ' ').lower()}" if why else "refused — no reference band")
    return [{"key": "Positions", "state": pos_state, "word": pos_word},
            {"key": "Photometry", "state": pho[0], "word": pho[1]},
            {"key": "Identity", "state": ide[0], "word": ide[1]},
            {"key": "Scale", "state": sca[0], "word": sca[1]}]


def view_model(res: dict, cfg: dict) -> dict:
    K = res["spots"][0]["ensemble_n_total"] if res["spots"] else int(cfg.get("ensemble", {}).get("k", 24))
    a_rep = float(cfg.get("_reported_agreement_min", 0.55))
    thr = int(np.ceil(a_rep * K))
    ceiling = int(round(0.78 * K))
    H = res["geometry"]["rectified_shape"][0]
    sig = res["photometry"]["sigma_od"]["value"]
    lanes = []
    for L in res["lanes"]:
        spots = [s for s in res["spots"] if s["lane_index"] == L["index"]]
        main = [s for s in spots if s["status"] == "confirmed"]
        below = [s for s in spots if s["status"] in ("candidate", "rejected", "proposed_unconfirmed")]
        streak = [s for s in spots if s["status"] == "suppressed_streak"]
        limit = None if sig is None else Z_LIMIT * sig
        lanes.append({"lane": L, "main": main, "below": below, "streak": streak, "spots": spots,
                      "limit_od": limit, "dens": next((d for d in res["densitograms"] if d["lane_index"] == L["index"]), None)})
    cards = [copy.render(r) for r in res["refusals"]]
    for L in res["lanes"]:
        if L.get("suppression"):
            cards.append(copy.render(L["suppression"]))
    seen, uniq = set(), []
    for c in cards:
        k = (c["code"], c["title"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    origin = res["reference"]["origin_row_px"]["value"]
    anchor = res["reference"].get("rst_anchor")
    return {"res": res, "cap": capability(res), "K": K, "thr": thr, "ceiling": ceiling, "H": H, "lanes": lanes,
            "cards": uniq, "origin_px": origin, "anchor": anchor, "n_confirmed": sum(len(x["main"]) for x in lanes),
            "n_below": sum(len(x["below"]) for x in lanes), "sha8": res["provenance"]["result_sha256"][:8],
            "flags": res["flags"], "uncalibrated": copy.render(_uncalibrated(res))}


def _uncalibrated(res: dict) -> dict:
    for s in res["spots"]:
        if s["confidence"]["provenance"] == "refused" and s["confidence"].get("refusal"):
            return s["confidence"]["refusal"]
    return {"code": "E_UNCALIBRATED", "message": "", "remedy": "", "evidence": {"labelled_plates": 0, "required": 30}}


def _load(svc: RunService, run_id: str) -> tuple[dict, dict]:
    row = svc.repo.get_run(run_id)
    if not row or not row["result_path"]:
        raise HTTPException(404, "No such run")
    return row, json.loads(Path(row["result_path"]).read_text())


def _ctx(request: Request, svc: RunService, run_id: str) -> dict:
    row, res = _load(svc, run_id)
    cfg = {**svc.config_doc, "_reported_agreement_min": svc.op_doc["tiers"]["reported"]["agreement_min"]}
    vm = view_model(res, cfg)
    return {"request": request, "row": row, **vm}


# ----------------------------------------------------------------------------- routes
@router.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/upload", status_code=303)


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request):
    return templates.TemplateResponse(request, "upload.html", {"request": request})


@router.post("/upload")
def upload_submit(file: UploadFile = File(...), n_lanes: int | None = Form(None), labels: str = Form(""),  # noqa: B008
                  svc: RunService = Depends(get_service)):  # noqa: B008
    data = file.file.read()
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    lab = tuple(x.strip() for x in labels.split(",") if x.strip()) or None
    if lab and n_lanes is None:
        n_lanes = len(lab)
    out = svc.run(data, file.filename, n_lanes=n_lanes, labels=lab)
    return RedirectResponse(f"/runs/{out['run_id']}", status_code=303)


@router.get("/runs", response_class=HTMLResponse)
def runs_list(request: Request, status: str | None = None, svc: RunService = Depends(get_service)):  # noqa: B008
    rows = svc.repo.list_runs(limit=200, status=status)
    items = []
    for r in rows:
        res = json.loads(Path(r["result_path"]).read_text()) if r["result_path"] else None
        items.append({"row": r, "cap": capability(res) if res else None,
                      "n_below": sum(1 for s in res["spots"] if s["status"] == "candidate") if res else 0,
                      "labels": [L["label"] for L in res["lanes"]] if res else []})
    labs = svc.repo.label_records()
    return templates.TemplateResponse(request, "runs.html", {"request": request, "items": items, "n_labelled": len(labs),
                                                             "status": status})


@router.get("/runs/{run_id}.json")
def run_json(run_id: str, svc: RunService = Depends(get_service)):  # noqa: B008
    row, _ = _load(svc, run_id)
    return Response(Path(row["result_path"]).read_text(), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'})


# NOTE: declared before /runs/{run_id} — Starlette matches routes in order and `{run_id}` would swallow ".json".
@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_view(request: Request, run_id: str, saved: str | None = None, svc: RunService = Depends(get_service)):  # noqa: B008
    ctx = _ctx(request, svc, run_id)
    ctx["saved"] = saved
    return templates.TemplateResponse(request, "result.html", ctx)


@router.get("/runs/{run_id}/print", response_class=HTMLResponse)
def run_print(request: Request, run_id: str, svc: RunService = Depends(get_service)):  # noqa: B008
    return templates.TemplateResponse(request, "print.html", _ctx(request, svc, run_id))


@router.get("/runs/{run_id}/plate.png")
def plate_png(run_id: str, svc: RunService = Depends(get_service)):  # noqa: B008
    """Rectified plate, rendered server-side from the stored bytes + the run's own homography. The only
    transform is the recorded rectification (spec 04 §11.7 item 2: no client-side pixel edits)."""
    row, res = _load(svc, run_id)
    cache = svc.data_dir / "previews" / f"{run_id}.png"
    if cache.exists():
        return Response(cache.read_bytes(), media_type="image/png")
    data = svc.blobs.get(res["image"]["sha256"])
    rgb = decode_image(data).rgb
    if res["status"] == "refused" and res["geometry"]["rectified_shape"] == [0, 0]:
        img = Image.fromarray(rgb)
    else:
        from tlc.pipeline.geometry import warp_rectify

        Hm = np.array(res["geometry"]["homography"], dtype=float)
        shape = tuple(int(x) for x in res["geometry"]["rectified_shape"])
        rect, _ = warp_rectify(rgb.astype(np.float64), Hm, shape)
        img = Image.fromarray(np.clip(np.round(rect), 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(buf.getvalue())
    return Response(buf.getvalue(), media_type="image/png")


@router.get("/runs/{run_id}/review", response_class=HTMLResponse)
def review_view(request: Request, run_id: str, svc: RunService = Depends(get_service)):  # noqa: B008
    ctx = _ctx(request, svc, run_id)
    ctx["existing"] = svc.repo.corrections_for_image(ctx["row"]["image_id"])
    return templates.TemplateResponse(request, "review.html", ctx)


@router.post("/runs/{run_id}/review")
def review_submit(request: Request, run_id: str, reviewer_id: str = Form(...), blind: str = Form("0"),
                  ops: str = Form("[]"), viewed_result_sha256: str = Form(...), review_seconds: int | None = Form(None),
                  svc: RunService = Depends(get_service)):  # noqa: B008
    try:
        doc = CorrectionDoc(run_id=run_id, viewed_result_sha256=viewed_result_sha256, blind=blind == "1",
                            ops=json.loads(ops), review_seconds=review_seconds)
    except Exception as e:  # malformed op document from the island
        raise HTTPException(422, f"Correction document rejected: {type(e).__name__}") from e
    out = record_correction(svc, run_id, doc, reviewer_id.strip() or "anonymous", None, "web-review")
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(out)
    return RedirectResponse(f"/runs/{run_id}?saved={out['label_status']}", status_code=303)


@router.get("/compare", response_class=HTMLResponse)
def compare(request: Request, runs: str = "", svc: RunService = Depends(get_service)):  # noqa: B008
    ids = [x for x in runs.split(",") if x]
    cols = []
    for rid in ids:
        row = svc.repo.get_run(rid)
        if not row or not row["result_path"]:
            continue
        res = json.loads(Path(row["result_path"]).read_text())
        cols.append({"row": row, "res": res, "cap": capability(res),
                     "spots": [s for s in res["spots"] if s["status"] == "confirmed"]})
    return templates.TemplateResponse(request, "compare.html", {"request": request, "cols": cols})


@router.get("/method", response_class=HTMLResponse)
def method(request: Request, svc: RunService = Depends(get_service)):  # noqa: B008
    return templates.TemplateResponse(request, "method.html", {
        "request": request, "config_hash": svc.config_hash, "config_ref": svc.config_ref, "version": svc.pipeline_version,
        "grid": svc.grid_doc, "op": svc.op_doc, "z_limit": Z_LIMIT})


@router.get("/labels/progress", response_class=HTMLResponse)
def labels_progress(request: Request, svc: RunService = Depends(get_service)):  # noqa: B008
    recs = svc.repo.label_records()
    stats = label_stats([draft_from_row(r) for r in recs])
    return templates.TemplateResponse(request, "labels.html", {"request": request, "stats": stats, "recs": recs})
