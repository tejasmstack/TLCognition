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
from tlc.insight.render import STANDING_RECOMMENDATION as INSIGHT_RECOMMENDATION
from tlc.insight.service import analyse_cohort_findings
from tlc.jobs.service import RunService, decode_image
from tlc.labels.corrections import CorrectionDoc
from tlc.labels.stats import draft_from_row, label_stats
from tlc.web import format as nf
from tlc.web.copy import refusals as copy

router = APIRouter(include_in_schema=False)
_here = Path(__file__).resolve().parent
ROOT = _here.parents[1]
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
    findings = svc.load_findings(run_id)
    cfg = {**svc.config_doc, "_reported_agreement_min": svc.op_doc["tiers"]["reported"]["agreement_min"]}
    vm = view_model(res, cfg)
    order = {"reported": 0, "tentative": 1, "anomaly": 2, "insufficient_data": 3, "suppressed": 4}
    findings.sort(key=lambda f: (order.get(f["verdict"], 9), f["hypothesis_id"]))
    return {"request": request, "row": row, "findings": findings,
            "limits": _limits(res, svc), **vm}


def _limits(res: dict, svc: RunService) -> list[str]:
    """§7.4: a per-run assumptions-and-limits panel, mandatory and non-dismissible."""
    qc, phot = res["capture_qc"], res["photometry"]
    sig = phot["sigma_od"]["value"]
    return [
        f"Clipping on this plate: {nf.fmt_pct(qc['green_clip_frac_in_plate']['value'], 1)} of in-plate green pixels at the sensor maximum.",
        f"Noise convention (frozen): {phot['sigma_method']}, background {phot['background_model']}, "
        f"sigma = {nf.fmt_plain(sig, 5)} optical density. A finding computed under a different convention is not comparable.",
        "No solvent front exists on this corpus, so positions are Rst against the standard lane; Rf is not reported.",
        "Phantom baseline at the reporting gate (5 sigma and ensemble agreement 0.60): about 0.19 bands per blank plate, "
        "measured on synthetic-noise blanks only — the rate on genuine solvent-only plates is not yet measured.",
        "Independent units: 1 plate, 1 campaign. Cross-plate trends need at least 6 plates in one campaign.",
        f"Confidence is not calibrated: {svc.op_doc['id']} reports agreement tallies, not probabilities.",
    ]


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


@router.get("/runs/{run_id}/findings.json")
def run_findings(run_id: str, svc: RunService = Depends(get_service)):  # noqa: B008
    _load(svc, run_id)
    return JSONResponse(svc.load_findings(run_id))


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
    cohort = []
    if len(cols) >= 2:
        metas = [{"campaign_id": c["res"]["image"]["original_filename"], "capture_order": i}
                 for i, c in enumerate(cols)]
        cohort = [f.to_dict() for f in analyse_cohort_findings([c["res"] for c in cols], metas)]
    return templates.TemplateResponse(request, "compare.html",
                                      {"request": request, "cols": cols, "cohort": cohort,
                                       "standing": INSIGHT_RECOMMENDATION})


def gate_status() -> list[dict]:
    """Read the committed gate evidence rather than restating it in prose that can go stale."""
    rep = ROOT / "reports"

    def load(name: str) -> dict | None:
        try:
            return json.loads((rep / name).read_text())
        except (OSError, ValueError):
            return None

    g4, g5, g9, g10 = (load(f"gate{i}{s}.json") for i, s in ((4, "_evidence"), (5, "_evidence"), (9, ""), (10, "")))
    out = []
    if g4:
        ev = g4.get("eval", {})
        out.append({"gate": "4 · detection", "passed": bool(g4.get("gate4_pass")),
                    "detail": f"recall {ev.get('recall_5sigma')} at >=5 sigma on {ev.get('n_true_spots_5s')} spots, "
                              f"{ev.get('fp_per_blank')} false bands per blank (bound 0.95 / 0.2), evaluation split"})
    if g5:
        p, st = g5["position"], g5["streak"]
        out.append({"gate": "5 · position and streaks", "passed": bool(g5.get("gate5_pass")),
                    "detail": f"Rst error p95 {p['rst_err_p95']} on {p['n_matched_resolved']} resolved spots (bound 0.01); "
                              f"false streak flags {st['false_streak_rate']:.1%} (bound 2%); "
                              f"{st['flagged_and_unquantified']}/{st['n_streak_lanes']} true streaks caught"})
    out.append({"gate": "6 · labelled set", "passed": False, "detail": "not started — needs a chemist through 30 plates"})
    out.append({"gate": "7 · calibration", "passed": False,
                "detail": "blocked by Gate 6; no confidence probability is shown anywhere until it passes"})
    out.append({"gate": "8 · semantic layer accuracy", "passed": False,
                "detail": "blocked by Gate 6 and by credentials; the layer runs in off/replay only"})
    if g9:
        out.append({"gate": "9 · null battery", "passed": bool(g9.get("passed")),
                    "detail": f"{g9['surfaced_finding_rate']:.1%} of {g9['shuffles']} label-shuffled cohorts surfaced a "
                              f"finding (nominal q = {g9['nominal_q']})"})
    if g10:
        out.append({"gate": "10 · API and replay", "passed": bool(g10.get("passed")),
                    "detail": f"schema {g10['schema_valid_frac']:.0%}, replay reproduced the result hash on "
                              f"{g10['replay_identical_frac']:.0%} of {g10['n_runs']} runs"})
    out.append({"gate": "11 · a chemist, unaided", "passed": False, "detail": "not tested — needs a person at a bench"})
    return out


@router.get("/method", response_class=HTMLResponse)
def method(request: Request, svc: RunService = Depends(get_service)):  # noqa: B008
    return templates.TemplateResponse(request, "method.html", {
        "request": request, "config_hash": svc.config_hash, "config_ref": svc.config_ref, "version": svc.pipeline_version,
        "grid": svc.grid_doc, "op": svc.op_doc, "z_limit": Z_LIMIT, "gates": gate_status()})


@router.get("/labels/progress", response_class=HTMLResponse)
def labels_progress(request: Request, svc: RunService = Depends(get_service)):  # noqa: B008
    recs = svc.repo.label_records()
    stats = label_stats([draft_from_row(r) for r in recs])
    return templates.TemplateResponse(request, "labels.html", {"request": request, "stats": stats, "recs": recs})
