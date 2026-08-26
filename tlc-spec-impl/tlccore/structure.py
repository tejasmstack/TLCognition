"""Phase C - STRUCTURE (spec section 6): pencil lines, origin dots, lanes.

C1 first-pass lane band  : x-projection from 0.28h down (trap 12.6 - header
                           handwriting above that creates fake lanes and fake dots)
C2 pencil lines          : median ROW profile over lane GUTTER columns only. Spots
                           live in lanes and cannot lift a gutter median; a drawn
                           line crosses the whole plate and can. Peaks >= 4*MAD.
C3 origin dots           : constrained blob detector. Every constraint below was
                           needed to stop diffuse-blob false positives (trap 12.6).
C4 lanes                 : projection peaks UNION origin-dot x-positions, with
                           noise-relative prominence and trimmed margins (trap 12.7).
"""
from __future__ import annotations
import numpy as np, cv2
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from .signals import x_projection, noise_peaks, robust_sigma, mad, detrend

SPOT_BAND_TOP   = 0.28     # trap 12.6
DOT_NOISE_K     = 12.0     # spec C3
DOT_MIN_Y       = 0.70     # centroid must be in the bottom 30%
DOT_ROUNDNESS   = (0.4, 2.5)
DOT_SOLIDITY    = 0.4
DOT_ROW_SPREAD  = 0.25     # accepted dots must span >30% of plate width
LINE_MAD_K      = 4.0      # spec C2

@dataclass
class Dot:
    x: float; y: float; area: float; roundness: float; solidity: float; od_peak: float
    compactness: float = 1.0   # area / bounding-box area: the label-vs-dot discriminator

@dataclass
class Line:
    y: float; strength: float; source: str

@dataclass
class Lane:
    cx: float; half_width: float; quality: float; from_dot: bool; from_projection: bool

@dataclass
class Structure:
    lanes: List[Lane] = field(default_factory=list)
    dots: List[Dot] = field(default_factory=list)
    origin: Optional[Line] = None
    front: Optional[Line] = None
    origin_source: str = "none"      # drawn | drawn_low | virtual | none
    front_source: str = "none"       # drawn | none
    vy: float = 0.0                  # first-pass vertical extent estimate
    notes: List[str] = field(default_factory=list)
    spot_band_top: float = SPOT_BAND_TOP
    needs_user_confirmation: bool = False
    confirmation_reasons: List[str] = field(default_factory=list)


GLYPH_FILL   = 0.68    # same discriminator as the dot row: strokes fill < 0.68 of
                       # their bounding box, chromatographic spots fill more
HDR_GAP      = 0.06    # vertical gap that ends the top handwriting block, x h

def detect_header_band(od: np.ndarray, noise: float) -> Tuple[float, List[str]]:
    """Find the bottom of the handwritten header block.

    Trap 12.6 fixes the spot band at 0.28h, which is right for a two-line header but
    not for a three-line one (P32b's header reaches 0.38h and put a marker stroke into
    the Sd lane as a 'confirmed spot'). Here the block is measured instead: take the
    high-OD components whose fill ratio marks them as strokes rather than spots, and
    walk down from the top while they stay contiguous.
    """
    h, w = od.shape; notes=[]
    m = (od > DOT_NOISE_K*noise).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    gl=[]
    for i in range(1, n):
        x0,y0,bw,bh,area = stats[i]; cx,cy = cent[i]
        if area < max(6, 0.00004*h*w) or cy > 0.55*h: continue
        if area/max(bw*bh,1) < GLYPH_FILL: gl.append((y0, y0+bh))
    gl.sort()
    if not gl or gl[0][0] >= 0.15*h:
        notes.append("no handwriting block found at the top of the plate")
        return SPOT_BAND_TOP, notes
    bottom = gl[0][1]
    for a,b in gl[1:]:
        if a - bottom <= HDR_GAP*h: bottom = max(bottom, b)
        else: break
    frac = bottom/h + 0.02
    notes.append(f"handwritten header block ends at {bottom/h:.3f}h "
                 f"({len(gl)} stroke components); spot band starts at "
                 f"{max(SPOT_BAND_TOP, frac):.3f}h")
    return max(SPOT_BAND_TOP, frac), notes

def glyph_mask(od: np.ndarray, noise: float) -> np.ndarray:
    """Boolean map of pixels belonging to stroke-like (handwritten) components."""
    h, w = od.shape
    m = (od > DOT_NOISE_K*noise).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    out = np.zeros((h,w), bool)
    for i in range(1, n):
        x0,y0,bw,bh,area = stats[i]
        if area < max(6, 0.00004*h*w): continue
        if area/max(bw*bh,1) < GLYPH_FILL: out |= (lab==i)
    return out

# ---------------- C3 ----------------
EDGE_GUARD   = 0.03    # drop candidates hugging the plate edge (warp artefacts)
DOT_MERGE_X  = 0.08    # merge fragments of one haloed dot, as fraction of width
ROW_TOL      = 0.035   # y tolerance for grouping candidates into one row, x h
DOT_COMPACT  = 0.68   # mean fill ratio: spotting dots measure 0.70-0.81, handwritten
                      # lane-label glyphs 0.48-0.66 on this plate set

def find_origin_dots(od: np.ndarray, noise: float) -> Tuple[List[Dot], List[str]]:
    """Constrained blob detector (spec C3), plus two additions these plates forced:

    * an edge guard - warp-edge artefacts otherwise masquerade as dots;
    * ROW SELECTION - the spec's 'bottom 30%' constraint is not sufficient when the
      chemist writes the lane labels ON the plate below the origin. Both the spotting
      row and the label row land in the bottom 30% and both are colinear with a wide
      x-spread, so colinearity alone cannot separate them. Rule used: group candidates
      into rows and take the HIGHEST qualifying row, because the spotting line is
      always above the labels. Lower qualifying rows are recorded as label rows.
    """
    h, w = od.shape; notes = []
    m = (od > DOT_NOISE_K * noise).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    cands: List[Dot] = []
    rej = {"y":0, "round":0, "solid":0, "area":0, "edge":0}
    for i in range(1, n):
        x0,y0,bw,bh,area = stats[i]
        cx,cy = cent[i]
        if area < max(6, 0.00004*h*w): rej["area"]+=1; continue
        if cy <= DOT_MIN_Y*h:          rej["y"]+=1;    continue
        if cx < EDGE_GUARD*w or cx > (1-EDGE_GUARD)*w: rej["edge"]+=1; continue
        r = bw/max(bh,1)
        if not (DOT_ROUNDNESS[0] <= r <= DOT_ROUNDNESS[1]): rej["round"]+=1; continue
        comp = (lab==i).astype(np.uint8)
        cnts,_ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hull_a = cv2.contourArea(cv2.convexHull(max(cnts,key=cv2.contourArea))) if cnts else 0
        sol = area/max(hull_a,1e-6)
        if sol < DOT_SOLIDITY: rej["solid"]+=1; continue
        cands.append(Dot(float(cx), float(cy), float(area), float(r), float(sol),
                         float(od[lab==i].max()), float(area/max(bw*bh,1))))
    notes.append(f"dot candidates: {len(cands)} kept; rejected {rej['y']} above the bottom 30%, "
                 f"{rej['edge']} at the plate edge, {rej['round']} by roundness, "
                 f"{rej['solid']} by solidity, {rej['area']} by area")
    if len(cands) < 2:
        notes.append("fewer than 2 dot candidates -> no spotting row")
        return [], notes

    cands.sort(key=lambda d: d.y)
    rows: List[List[Dot]] = []
    for d in cands:
        if rows and abs(d.y - rows[-1][-1].y) <= ROW_TOL*h: rows[-1].append(d)
        else: rows.append([d])

    def spread(row):
        return (max(d.x for d in row) - min(d.x for d in row))/w
    def compact(row):
        return float(np.mean([d.compactness for d in row]))

    wide = [r for r in rows if len(r) >= 2 and spread(r) >= DOT_ROW_SPREAD]
    if not wide:
        notes.append(f"{len(rows)} row clusters, none with >=2 dots spanning >={DOT_ROW_SPREAD:.2f}w")
        return [], notes
    good = [r for r in wide if compact(r) >= DOT_COMPACT]
    for r in wide:
        if compact(r) < DOT_COMPACT:
            notes.append(f"row at y={np.median([d.y for d in r])/h:.3f}h has fill ratio "
                         f"{compact(r):.2f} < {DOT_COMPACT} -> handwritten lane labels, not dots")
    if not good:
        notes.append("every wide row looks like handwriting -> no spotting row identified")
        return [], notes
    # among compact rows prefer the one with the most dots; tie-break to the higher row
    good.sort(key=lambda r: (-len(r), np.median([d.y for d in r])))
    chosen = good[0]
    for r in good[1:]:
        notes.append(f"row at y={np.median([d.y for d in r])/h:.3f}h ({len(r)} dots) "
                     f"not chosen as the spotting row")
    # merge fragments of one haloed dot
    chosen.sort(key=lambda d: d.x)
    merged: List[Dot] = []
    for d in chosen:
        if merged and (d.x - merged[-1].x) < DOT_MERGE_X*w:
            a = merged[-1]
            tot = a.area + d.area
            merged[-1] = Dot((a.x*a.area + d.x*d.area)/tot, (a.y*a.area + d.y*d.area)/tot,
                             tot, a.roundness, a.solidity, max(a.od_peak, d.od_peak))
        else:
            merged.append(d)
    sp = (max(d.x for d in merged) - min(d.x for d in merged))/w
    notes.append(f"spotting row at y={np.median([d.y for d in merged])/h:.3f}h, "
                 f"{len(chosen)} blobs -> {len(merged)} dots after merging fragments, x-spread {sp:.2f}w")
    return merged, notes

# ---------------- C2 ----------------
def find_lines(od: np.ndarray, lanes: List[Lane], vy: float,
               noise: float) -> Tuple[Optional[Line], Optional[Line], List[str]]:
    h, w = od.shape; notes=[]
    gutter = np.ones(w, bool)
    for L in lanes:
        a,b = int(max(0,L.cx-L.half_width)), int(min(w,L.cx+L.half_width))
        gutter[a:b] = False
    m = int(0.04*w); gutter[:m]=False; gutter[w-m:]=False
    if gutter.sum() < max(6, 0.05*w):
        notes.append("no usable gutter columns -> pencil lines cannot be separated from spots")
        return None, None, notes
    prof = np.median(od[:, gutter], axis=1)          # spots cannot lift a gutter median
    prof = detrend(prof, 0.30)
    sig = robust_sigma(prof)
    from scipy.signal import find_peaks
    pk, props = find_peaks(prof, prominence=LINE_MAD_K*sig, distance=max(4,int(0.02*h)))
    # Edge guard: the top/bottom few percent of a warped crop carry a dark border
    # artefact that reads as a strong full-width "drawn line" and would otherwise be
    # taken as the origin (measured 0.974h on P32 vs a true origin of 0.861h).
    drawn = [Line(float(p), float(props["prominences"][i]/sig), "drawn")
             for i,p in enumerate(pk) if 0.04*h < p < 0.96*h]
    notes.append(f"gutter columns: {int(gutter.sum())}; drawn-line candidates: "
                 f"{[round(d.y/h,3) for d in drawn]}")
    # origin: a drawn line near the first-pass vertical extent
    origin=None
    for d in drawn:
        if vy-0.10*h <= d.y <= vy+0.03*h: 
            origin = Line(d.y, d.strength, "drawn"); break
    if origin is None:
        low=[d for d in drawn if d.y > 0.62*h]
        if low: origin = Line(max(low,key=lambda d:d.y).y, 0.0, "drawn_low")
        if origin is not None and vy > 0 and abs(origin.y - vy) > 0.06*h:
            notes.append(f"drawn_low candidate at {origin.y/h:.3f}h disagrees with the dot row at "
                         f"{vy/h:.3f}h by more than 0.06h -> discarded in favour of the dot row")
            origin = None
    # front: lowest drawn line in (0.04h, origin-0.35h)
    front=None
    if origin is not None:
        band=[d for d in drawn if 0.04*h < d.y < origin.y-0.35*h]
        if band: front = Line(max(band,key=lambda d:d.y).y, 0.0, "drawn")
    return origin, front, notes

# ---------------- C1 + C4 ----------------
def find_lanes(od: np.ndarray, noise: float, dots: List[Dot]) -> Tuple[List[Lane], float, List[str]]:
    h, w = od.shape; notes=[]
    idx, prof = x_projection(od, SPOT_BAND_TOP, 0.96, frac=0.50)
    pk, props, sig = noise_peaks(prof, w)
    proj_x = [float(idx[i]) for i in pk]
    notes.append(f"projection peaks ({len(proj_x)}): {[round(x/w,3) for x in proj_x]}")
    dot_x = [d.x for d in dots]
    notes.append(f"origin-dot x ({len(dot_x)}): {[round(x/w,3) for x in dot_x]}")

    # union, merging positions closer than 6% of width (same lane seen twice)
    merged: List[Tuple[float,bool,bool]] = []
    for x in sorted([(x,False,True) for x in proj_x] + [(x,True,False) for x in dot_x]):
        if merged and abs(x[0]-merged[-1][0]) < 0.06*w:
            px,pd,pp = merged[-1]
            merged[-1] = ((px+x[0])/2, pd or x[1], pp or x[2])
        else:
            merged.append(x)
    # half-width from valley-to-valley on the projection
    lanes=[]
    full = np.zeros(w); full[idx] = prof
    for cx,fd,fp in merged:
        i = int(round(cx))
        L=i
        while L>1 and full[L-1] <= full[L]: L-=1
        R=i
        while R<w-2 and full[R+1] <= full[R]: R+=1
        hw = max(0.02*w, min(0.5*(R-L), 0.10*w))
        q = 1.0 if (fd and fp) else 0.7
        lanes.append(Lane(float(cx), float(hw), q, fd, fp))
    lanes, cnotes = complete_lane_pitch(lanes, w, od, noise)
    notes += cnotes
    vy = float(np.median([d.y for d in dots])) if dots else 0.86*h
    notes.append(f"lanes after union/merge/completion: {[round(l.cx/w,3) for l in lanes]}")
    return lanes, vy, notes

def complete_lane_pitch(lanes: List[Lane], w: int, od: np.ndarray,
                        noise: float) -> Tuple[List[Lane], List[str]]:
    """Spec C4 adaptive fallback, non-interactive half.

    Lightly loaded lanes (the S and Sd references) often sit below the dot threshold
    AND below the projection prominence, so the detected set has holes. Lanes are
    spotted at a regular pitch, so: take the smallest gap as the pitch, subdivide any
    gap that is a near-integer multiple of it, then extrapolate outwards while a slot
    still fits on the plate. Inferred lanes are marked quality=0.4 and
    from_dot=from_projection=False, so the dashboard shows them as needing confirmation
    (the spec forbids silently proceeding with a lane set that contradicts the dots).
    """
    notes: List[str] = []
    if len(lanes) < 2:
        notes.append("fewer than 2 lanes -> pitch completion not possible")
        return lanes, notes
    xs = sorted(l.cx for l in lanes)
    gaps = np.diff(xs)
    pitch = float(np.min(gaps))
    if pitch < 0.08*w:
        notes.append("lane pitch implausibly small -> completion skipped")
        return lanes, notes
    h = od.shape[0]
    band = od[int(SPOT_BAND_TOP*h):int(0.96*h)]
    SUPPORT_K = 2.0          # a slot is only filled where there IS material, just below
                             # the dot/projection thresholds - never on geometry alone
    def support(x: float) -> float:
        a = int(max(0, x - 0.35*pitch)); b = int(min(w, x + 0.35*pitch))
        if b - a < 2: return 0.0
        return float(band[:, a:b].mean())

    out = {round(x,3): l for x,l in zip(xs, sorted(lanes, key=lambda l: l.cx))}
    added, refused = [], []
    def try_add(nx: float):
        if not (0.015*w < nx < 0.985*w): return False
        sup = support(nx)
        if sup < SUPPORT_K*noise:
            refused.append((nx, sup)); return False
        out[round(nx,3)] = Lane(float(nx), float(0.4*pitch), 0.4, False, False)
        added.append(nx); return True

    for a, b in zip(xs[:-1], xs[1:]):
        k = int(round((b-a)/pitch))
        if k >= 2 and abs((b-a)/k - pitch) < 0.30*pitch:
            step = (b-a)/k
            for j in range(1, k): try_add(a + j*step)
    lo, hi = min(xs), max(xs)
    j = 1
    while lo - j*pitch > 0.015*w:
        if not try_add(lo - j*pitch): break
        j += 1
    j = 1
    while hi + j*pitch < 0.985*w:
        if not try_add(hi + j*pitch): break
        j += 1
    if added:
        notes.append(f"pitch completion: pitch={pitch/w:.3f}w, inferred {len(added)} lane(s) at "
                     f"{[round(x/w,3) for x in sorted(added)]} (OD support confirmed) "
                     f"-> flagged for user confirmation")
    for nx, sup in refused:
        notes.append(f"slot at {nx/w:.3f}w refused: OD support {sup:.4f} < {SUPPORT_K}x noise "
                     f"({SUPPORT_K*noise:.4f}) - no material there")
    return [out[k] for k in sorted(out)], notes

def analyse_structure(od: np.ndarray, noise: float) -> Structure:
    h,w = od.shape
    band_top, n0 = detect_header_band(od, noise)
    dots, n1 = find_origin_dots(od, noise)
    lanes, vy, n2 = find_lanes(od, noise, dots)
    origin, front, n3 = find_lines(od, lanes, vy, noise)
    st = Structure(lanes=lanes, dots=dots, vy=vy, notes=n0+n1+n2+n3, spot_band_top=band_top)
    if origin is not None:
        st.origin, st.origin_source = origin, origin.source
    elif dots:
        st.origin = Line(vy, 0.0, "virtual"); st.origin_source = "virtual"
        st.notes.append("no drawn origin line -> VIRTUAL origin from the dot row (confidence capped)")
    else:
        st.origin_source = "none"
        st.notes.append("no origin line and no dots -> positions are frame-relative only")
    # Spec C4: never silently proceed with a lane set that contradicts the dots.
    inferred = [l for l in st.lanes if not l.from_dot and not l.from_projection]
    if inferred:
        st.needs_user_confirmation = True
        st.confirmation_reasons.append(
            f"{len(inferred)} lane(s) inferred from the spotting pitch rather than detected directly")
    if st.dots and len(st.lanes) != len(st.dots):
        st.needs_user_confirmation = True
        st.confirmation_reasons.append(
            f"lane count ({len(st.lanes)}) differs from the number of spotting dots ({len(st.dots)})")
    near = [n for n in st.notes if n.startswith("slot at")]
    if near:
        st.needs_user_confirmation = True
        st.confirmation_reasons.append(
            "a lane slot was refused for insufficient signal - a very lightly loaded lane may be missing")
    if front is not None:
        st.front, st.front_source = front, "drawn"
    else:
        st.front_source = "none"
        st.notes.append("no solvent front line found -> true Rf disabled, relative height reported "
                        "(SOP: mark the front before photographing to unlock Rf)")
    return st
