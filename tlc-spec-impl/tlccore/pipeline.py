"""Orchestrator: analyze(image_path) -> Result.  A -> B -> C -> D -> E -> F.

Design invariants enforced here: determinism (no randomness, no time-dependence),
confidence on everything, replayable trace, graceful degradation, only two hard stops.
"""
from __future__ import annotations
import hashlib, json, os
import numpy as np, cv2
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .normalize import find_plate, to_od, resolve_orientation, orientation_crops, STD_HEIGHT
from .gate import run_gate
from .structure import analyse_structure, DOT_NOISE_K
from .lanes import resolve_roles, structural_crosscheck, VLM_LABEL_CACHE
from .extract import extract, resolution_pairs
from .graph import vertical_span, to_rf, vmax_for
from .confidence import Factor, combine, weakest_link
from .trace import Trace

PIPELINE_VERSION = "0.1.0"

def params_hash() -> str:
    from . import structure as S, extract as E, normalize as N, gate as G
    blob = json.dumps(dict(
        std_height=N.STD_HEIGHT, hue=(N.HUE_LO,N.HUE_HI), sat=N.SAT_FLOOR,
        bg_kernel=N.BG_KERNEL_FRAC, bg_iters=N.BG_ITERS,
        dot_k=S.DOT_NOISE_K, dot_min_y=S.DOT_MIN_Y, dot_round=S.DOT_ROUNDNESS,
        dot_sol=S.DOT_SOLIDITY, dot_spread=S.DOT_ROW_SPREAD, dot_compact=S.DOT_COMPACT,
        line_k=S.LINE_MAD_K, spot_band=S.SPOT_BAND_TOP,
        tier=(E.TIER_CONFIRMED,E.TIER_CANDIDATE), streak=E.STREAK_LIMIT,
        gate=(G.GREEN_DOMINANCE_MIN,G.EDGE_DENSITY_MAX,G.DARK_CONTENT_MIN,G.RESOLUTION_FLOOR),
    ), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]

@dataclass
class Result:
    status: str                       # OK | NOT_A_PLATE | NOT_WORKABLE | NEEDS_LANE_MAP
    image_sha256: str = ""
    pipeline_version: str = PIPELINE_VERSION
    params_hash: str = ""
    gate: Dict[str, Any] = field(default_factory=dict)
    plate: Dict[str, Any] = field(default_factory=dict)
    structure: Dict[str, Any] = field(default_factory=dict)
    roles: List[Dict[str, Any]] = field(default_factory=list)
    lanes: List[Dict[str, Any]] = field(default_factory=list)
    plate_metrics: Dict[str, Any] = field(default_factory=dict)
    cannot_conclude: List[Dict[str, str]] = field(default_factory=list)
    confidence: Dict[str, Any] = field(default_factory=dict)

def _sha(path: str) -> str:
    return hashlib.sha256(open(path,"rb").read()).hexdigest()

def analyze(image_path: str, run_dir: str, plate_id: Optional[str] = None,
            vlm_orientation: Optional[str] = None) -> Dict[str, Any]:
    os.makedirs(run_dir, exist_ok=True)
    ph = params_hash()
    tr = Trace(run_dir, PIPELINE_VERSION, ph)
    res = Result(status="OK", image_sha256=_sha(image_path), params_hash=ph)

    bgr = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if bgr is None:
        res.status = "NOT_WORKABLE"; res.gate = dict(verdict="NOT_WORKABLE", because="unreadable_file")
        tr.save(); return asdict(res)

    # ---- B1
    sB = tr.section("B_normalize", inputs=dict(path=os.path.basename(image_path)))
    pl = find_plate(bgr)
    od = to_od(pl.warped)
    sB.outputs = dict(warp=[pl.warped.shape[1], pl.warped.shape[0]], src_height=pl.src_height,
                      scale=round(pl.scale,3), noise=round(od.noise,6),
                      sat_frac=round(od.sat_frac,4), method=pl.method)
    sB.params = dict(std_height=STD_HEIGHT)
    sB.decision = f"plate warped and standardised to {STD_HEIGHT}px height via {pl.method}"
    tr.note(sB, f"OD noise floor {od.noise:.5f}; every threshold below is a multiple of this")
    if od.sat_frac > 0.10:
        tr.note(sB, f"{100*od.sat_frac:.1f}% of plate pixels are clipped at 255 - the unquenched "
                    f"reference level I0 is partly unknowable, quantification confidence capped")
    cv2.imwrite(os.path.join(run_dir,"b_warped.png"), pl.warped)
    sB.artifacts.append("b_warped.png")
    res.plate = sB.outputs

    # ---- B2 orientation
    sO = tr.section("B2_orientation")
    ori = resolve_orientation(pl.warped, vlm_orientation)
    sO.outputs = dict(flipped=ori.flipped, source=ori.source); sO.decision = ori.reason
    if ori.flipped:
        pl.warped = cv2.rotate(pl.warped, cv2.ROTATE_180); od = to_od(pl.warped)

    # ---- A gate
    sA = tr.section("A_gate")
    g = run_gate(pl.warped, od.od, od.noise, pl.src_height)
    sA.outputs = g.as_dict(); sA.decision = g.verdict
    for c in g.checks: tr.note(sA, f"{c.name}={c.measured:.4f} (needed {c.needed}) -> {'ok' if c.passed else 'FAIL'}")
    res.gate = g.as_dict()
    if g.verdict != "PASS":
        res.status = g.verdict
        res.cannot_conclude.append(dict(reason=g.because or "gate", detail="; ".join(g.quality_reasons)))
        tr.save(); return asdict(res)

    # ---- C structure
    sC = tr.section("C_structure")
    st = analyse_structure(od.od, od.noise)
    sC.outputs = dict(lanes=[round(l.cx,1) for l in st.lanes], n_dots=len(st.dots),
                      origin=None if st.origin is None else round(st.origin.y,1),
                      origin_source=st.origin_source, front_source=st.front_source,
                      needs_user_confirmation=st.needs_user_confirmation)
    sC.params = dict(dot_noise_k=DOT_NOISE_K)
    for n in st.notes: tr.note(sC, n)
    sC.decision = (f"{len(st.lanes)} lanes, origin={st.origin_source}, front={st.front_source}")
    res.structure = sC.outputs | dict(confirmation_reasons=st.confirmation_reasons)

    # ---- F extract
    sF = tr.section("F_extract")
    ex = extract(od.od, od.noise, st)
    for n in ex.notes: tr.note(sF, n)
    sF.outputs = dict(n_confirmed=sum(1 for l in ex.lanes for s in l.spots if s.tier=="confirmed"),
                      n_candidate=sum(1 for l in ex.lanes for s in l.spots if s.tier=="candidate"),
                      streak_lanes=[l.index for l in ex.lanes if l.is_streak],
                      separation_hygiene=round(ex.separation_hygiene,2),
                      stacked=ex.stacked)

    # ---- D roles
    sD = tr.section("D_lane_roles")
    cache = VLM_LABEL_CACHE.get(plate_id or "", None)
    if cache is None:
        res.status = "NEEDS_LANE_MAP"
        tr.note(sD, "no cached label read and no user map -> NEEDS_LANE_MAP (never guessed)")
        roles = resolve_roles(["?"]*len(st.lanes), [0.0]*len(st.lanes))
    else:
        labels = (cache["labels"] + ["?"]*len(st.lanes))[:len(st.lanes)]
        confs  = (cache["conf"]   + [0.0]*len(st.lanes))[:len(st.lanes)]
        roles = resolve_roles(labels, confs)
        tr.note(sD, f"labels from {cache['model']} ({cache['prompt_version']}, cached): {labels}")
    roles = structural_crosscheck(roles, [sum(1 for s in l.spots if s.tier=="confirmed") for l in ex.lanes])
    for r in roles:
        for n in r.notes: tr.note(sD, f"lane {r.index}: {n}")
    sD.outputs = dict(roles=[(r.index, r.name_raw, r.role, r.role_source, r.role_conf) for r in roles])
    if any(r.role == "UNKNOWN" for r in roles):
        res.status = "NEEDS_LANE_MAP"
    res.roles = [asdict(r) for r in roles]

    # ---- confidence
    fac_plate = [
        Factor("image_quality", 1.0 if pl.src_height >= 200 else 0.8,
               f"plate {pl.src_height} source px tall"),
        Factor("saturation", float(np.clip(1.0 - od.sat_frac, 0.3, 1.0)),
               f"{100*od.sat_frac:.1f}% of pixels clipped at 255"),
        Factor("origin_source", {"drawn":1.0,"drawn_low":0.85,"virtual":0.7,"none":0.4}[st.origin_source],
               f"origin from {st.origin_source}"),
        Factor("lane_structure", 0.75 if st.needs_user_confirmation else 1.0,
               "lane set needs user confirmation" if st.needs_user_confirmation else "lane set self-consistent"),
        Factor("separation_hygiene", float(np.clip(ex.separation_hygiene,0.4,1.0)),
               f"gutter contamination -> hygiene {ex.separation_hygiene:.2f}"),
    ]
    plate_conf = combine(fac_plate)
    res.confidence = plate_conf.as_dict()

    # ---- assemble per-lane output with Rf / relative position
    y_top, y_bot = vertical_span(od.od, st, ex)
    for l, r in zip(ex.lanes, roles):
        spots=[]
        for s in l.spots:
            sc = combine(fac_plate + [
                Factor("snr", float(np.clip(s.snr/6.0, 0.3, 1.0)), f"SNR {s.snr:.1f} sigma"),
                Factor("role_source", {"lexicon":1.0,"pattern":0.85,"structure":0.7,"user":1.0,"none":0.5}[r.role_source],
                       f"lane role via {r.role_source}"),
            ] + ([Factor("lane_bleed", 0.7, s.bleed)] if s.bleed else [])
              + ([Factor("near_text_band", 0.5,
                         "sits in the top tenth of the band, next to the handwritten header - "
                         "may be marker ink rather than analyte")] if s.near_text_band else []))
            spots.append(dict(rel_pos=round(s.rel_pos,3),
                              rf=None if s.rf is None else round(s.rf,3),
                              rf_err=None if s.rf_err is None else round(s.rf_err,4),
                              y=round(s.y,1), y_err=round(s.y_err,2),
                              sigma=round(s.sigma,1), integral=round(s.integral,3),
                              share_pct=s.share_pct, snr=round(s.snr,1), tier=s.tier,
                              bleed=s.bleed, near_text_band=s.near_text_band,
                              confidence=sc.as_dict()))
        res.lanes.append(dict(index=l.index, name=r.name_raw, role=r.role,
                              streak_fraction=round(l.streak_fraction,3), is_streak=l.is_streak,
                              quantification_suppressed=l.is_streak,
                              total_od=round(l.total_od,2), spots=spots,
                              resolution_pairs=resolution_pairs(l)))

    res.plate_metrics = dict(noise=round(od.noise,6), sat_frac=round(od.sat_frac,4),
                             separation_hygiene=round(ex.separation_hygiene,2),
                             width_model=ex.width_model, stacked=ex.stacked,
                             graph_span=[y_top,y_bot], vmax=round(vmax_for(od.od),4))

    # ---- cannot-conclude catalogue
    if st.front_source == "none":
        res.cannot_conclude.append(dict(reason="no_front_line",
            detail="no solvent front drawn -> true Rf unavailable; positions are relative height. "
                   "Fix: pencil the front before photographing."))
    if st.origin_source == "virtual":
        res.cannot_conclude.append(dict(reason="virtual_origin",
            detail="origin inferred from the spotting-dot row, not a drawn line -> related confidences capped."))
    if all(l.is_streak for l in ex.lanes):
        res.cannot_conclude.append(dict(reason="all_streaks",
            detail="every lane is a streak -> no percentage quantification possible."))
    if od.sat_frac > 0.10:
        res.cannot_conclude.append(dict(reason="clipped_background",
            detail=f"{100*od.sat_frac:.1f}% of pixels clipped at 255 -> the unquenched reference is "
                   f"partly unknowable; area percentages are indicative only. Fix: lower the exposure."))
    ntb = [(l['name'], s['rel_pos']) for l in res.lanes for s in l['spots'] if s.get('near_text_band')]
    if ntb:
        res.cannot_conclude.append(dict(reason="feature_adjacent_to_handwriting",
            detail=f"{len(ntb)} feature(s) sit beside the handwritten header "
                   f"({', '.join(f'{n} at {r}' for n,r in ntb)}) and may be marker ink. "
                   f"Fix: write the header on a tab outside the plate area."))
    if st.needs_user_confirmation:
        res.cannot_conclude.append(dict(reason="lane_map_unresolved",
            detail="; ".join(st.confirmation_reasons)))

    tr.save()
    np.savez_compressed(os.path.join(run_dir,"od.npz"), od=od.od, bg=od.bg)
    with open(os.path.join(run_dir,"result.json"),"w") as fh:
        json.dump(asdict(res), fh, indent=2, default=float)
    return asdict(res)
