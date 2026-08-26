"""Phase F - EXTRACT (spec section 8): profiles, peaks, streaks, stacking, bleed.

F1 lane profiles        : mean OD across the lane window, origin -> top of spot band
F2 peaks + sub-pixel fit: find_peaks at >= 2 sigma, then multi-Gaussian curve_fit
                          SNR tiers - confirmed >= 3 sigma, candidate 2-3 sigma
F3 streak measurement   : fraction of the lane profile above noise; > 0.55 -> the lane
                          is a streak, not spots (suppresses % quantification, rule G6)
F4 cross-lane stacking  : a 1.5-2 sigma bump repeating at one position in >= 3 lanes is
                          re-tested on the stacked profile
F5 smudges / lane bleed : 2-D x-extent per spot; spots crossing a gutter are attributed
                          fractionally and the victim lane is annotated, never given a
                          phantom compound
F6 expert metrics       : Rs between neighbours, width-vs-position model
"""
from __future__ import annotations
import numpy as np, cv2
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
from scipy.signal import find_peaks, savgol_filter
from scipy.optimize import curve_fit
from .signals import robust_sigma
from .structure import Structure, Lane, glyph_mask

TIER_CONFIRMED = 3.0
TIER_CANDIDATE = 2.0
STREAK_LIMIT   = 0.55
STACK_MIN      = 1.5
STACK_LANES    = 3

@dataclass
class Spot:
    lane: int
    y: float; y_err: float
    amp: float; sigma: float; integral: float
    snr: float; tier: str
    rel_pos: float                 # 1 at the front / top of usable band, 0 at the origin
    rf: Optional[float] = None
    rf_err: Optional[float] = None
    x_extent: float = 0.0
    bleed: Optional[str] = None
    near_text_band: bool = False
    share_pct: float = 0.0

@dataclass
class LaneResult:
    index: int
    profile: np.ndarray
    sigma_noise: float
    streak_fraction: float
    is_streak: bool
    spots: List[Spot] = field(default_factory=list)
    vetoed: List[Dict] = field(default_factory=list)
    total_od: float = 0.0

@dataclass
class Extract:
    lanes: List[LaneResult] = field(default_factory=list)
    y0: int = 0                    # top row of the analysed band (absolute)
    y1: int = 0                    # bottom row (the origin)
    stacked: List[Dict] = field(default_factory=list)
    gutter_contamination: List[float] = field(default_factory=list)
    separation_hygiene: float = 1.0
    width_model: Optional[Dict] = None
    notes: List[str] = field(default_factory=list)

def _multi_gauss(x, *p):
    out = np.zeros_like(x, dtype=float)
    for i in range(len(p)//3):
        A, mu, sg = p[3*i:3*i+3]
        out += A*np.exp(-0.5*((x-mu)/max(sg,0.5))**2)
    return out

def lane_profile(od: np.ndarray, lane: Lane, y0: int, y1: int) -> np.ndarray:
    w = od.shape[1]
    a = int(max(0, lane.cx - lane.half_width)); b = int(min(w, lane.cx + lane.half_width + 1))
    if b - a < 2: a, b = max(0, int(lane.cx)-1), min(w, int(lane.cx)+2)
    seg = od[y0:y1, a:b]
    prof = seg.mean(1)
    if len(prof) >= 11: prof = savgol_filter(prof, 11, 2)
    return np.clip(prof, 0, None)

def extract(od: np.ndarray, noise: float, st: Structure) -> Extract:
    h, w = od.shape
    ex = Extract()
    y1 = int(st.origin.y) if st.origin else int(0.86*h)
    y0 = int(max(0.10*h, (st.front.y if st.front else st.spot_band_top*h)))
    gl = glyph_mask(od, noise)      # handwriting map, used to veto stroke "spots"
    if y1 - y0 < 20: y0 = max(0, y1-20)
    ex.y0, ex.y1 = y0, y1

    for i, L in enumerate(st.lanes):
        prof = lane_profile(od, L, y0, y1)
        # Spec F2 puts the tier threshold at multiples of sigma taken from the profile
        # baseline. On a cleanly separated lane that sigma collapses towards zero and
        # ordinary noise bumps then clear 3 sigma, producing phantom spots with ~0%
        # share (observed in the Sd lane of P33). Floor it at the THEORETICAL noise of
        # the lane mean, plate_noise / sqrt(columns averaged), which is the smallest
        # sigma the measurement can physically have.
        ncols = max(1, int(2*L.half_width) + 1)
        sig_floor = noise / np.sqrt(ncols)
        sig = max(robust_sigma(prof), float(sig_floor))
        above = float((prof > 2.0*noise).mean())
        lr = LaneResult(index=i, profile=prof, sigma_noise=sig,
                        streak_fraction=above, is_streak=above > STREAK_LIMIT,
                        total_od=float(prof.sum()))
        pk, props = find_peaks(prof, height=TIER_CANDIDATE*sig,
                               distance=max(3, int(0.02*(y1-y0))), prominence=0.5*sig)
        # sub-pixel multi-Gaussian refit around the detected cluster
        fitted = {}
        if len(pk):
            x = np.arange(len(prof), dtype=float)
            p0, lo, hi = [], [], []
            for m in pk[:10]:
                p0 += [float(prof[m]), float(m), 6.0]
                lo += [0.0, m-8.0, 1.5]; hi += [float(prof.max()*3+1e-6), m+8.0, 40.0]
            try:
                popt, pcov = curve_fit(_multi_gauss, x, prof, p0=p0, bounds=(lo,hi), maxfev=20000)
                perr = np.sqrt(np.clip(np.diag(pcov), 0, None))
                for j in range(len(popt)//3):
                    fitted[j] = (popt[3*j], popt[3*j+1], popt[3*j+2], perr[3*j+1])
            except Exception:
                for j, m in enumerate(pk[:10]):
                    fitted[j] = (float(prof[m]), float(m), 6.0, 2.0)
        for j,(A, mu, sg, mu_err) in fitted.items():
            snr = A/max(sig,1e-9)
            tier = "confirmed" if snr >= TIER_CONFIRMED else ("candidate" if snr >= TIER_CANDIDATE else "trace")
            if tier == "trace": continue
            y_abs = y0 + mu
            rel = (y1 - y_abs)/max(y1 - y0, 1e-6)
            sp = Spot(lane=i, y=float(y_abs), y_err=float(mu_err), amp=float(A), sigma=float(sg),
                      integral=float(abs(A*sg*np.sqrt(2*np.pi))), snr=float(snr), tier=tier,
                      rel_pos=float(rel))
            if st.front is not None and st.origin is not None:
                denom = st.origin.y - st.front.y
                if abs(denom) > 1e-6:
                    sp.rf = float((st.origin.y - y_abs)/denom)
                    sp.rf_err = float(mu_err/abs(denom))
            # F5: x-extent at half maximum, for bleed detection
            row = int(np.clip(y_abs, 0, h-1))
            band = od[max(0,row-2):row+3].mean(0)
            half = 0.5*band[int(np.clip(L.cx,0,w-1))]
            xa = int(L.cx)
            while xa > 0 and band[xa] > half: xa -= 1
            xb = int(L.cx)
            while xb < w-1 and band[xb] > half: xb += 1
            sp.x_extent = float(xb-xa)
            if sp.x_extent > 2.2*L.half_width:
                sp.bleed = "wide - crosses the lane gutter"
            # A compactness veto was tried here and REMOVED: streaks and diffuse smears
            # are also stroke-like, so it deleted real spots on the streaky plates
            # (P29 lost 3 of 4). Handwriting is separated by POSITION (the measured
            # header band) instead. Anything still landing in the top tenth of the
            # analysis band is flagged for the chemist rather than silently trusted.
            # Flag by ABSOLUTE distance to the measured header band, not by rel_pos:
            # rel_pos depends on where the origin is, so a fixed rel_pos cut misses the
            # glyph on plates with a tall header (P32b's ")" landed at exactly 0.90).
            if (y_abs - st.spot_band_top*h) < 0.08*h:
                sp.near_text_band = True
            lr.spots.append(sp)
        tot = sum(s.integral for s in lr.spots) or 1.0
        for s in lr.spots: s.share_pct = round(100*s.integral/tot, 1)
        lr.spots.sort(key=lambda s: s.y)
        ex.lanes.append(lr)

    # F5 gutter hygiene
    for a, b in zip(st.lanes[:-1], st.lanes[1:]):
        g0 = int(a.cx + a.half_width); g1 = int(b.cx - b.half_width)
        if g1 - g0 >= 2:
            ex.gutter_contamination.append(float(od[y0:y1, g0:g1].mean()/max(noise,1e-9)))
    if ex.gutter_contamination:
        worst = max(ex.gutter_contamination)
        ex.separation_hygiene = float(np.clip(1.0 - (worst-1.0)/8.0, 0.0, 1.0))
        ex.notes.append(f"gutter contamination {min(ex.gutter_contamination):.1f}-{worst:.1f}x noise "
                        f"-> separation hygiene {ex.separation_hygiene:.2f}")

    # F4 cross-lane stacking of sub-threshold bumps
    if len(ex.lanes) >= STACK_LANES:
        n = min(len(l.profile) for l in ex.lanes)
        M = np.stack([l.profile[:n] for l in ex.lanes])
        stack = M.mean(0); ssig = robust_sigma(stack)
        spk,_ = find_peaks(stack, height=TIER_CONFIRMED*ssig, distance=max(3,int(0.02*n)))
        for m in spk:
            hits = sum(1 for l in ex.lanes if l.profile[m] > STACK_MIN*l.sigma_noise)
            already = any(abs(s.y-(y0+m)) < 8 for l in ex.lanes for s in l.spots if s.tier=="confirmed")
            if hits >= STACK_LANES and not already:
                ex.stacked.append(dict(y=float(y0+m), rel_pos=float((y1-(y0+m))/max(y1-y0,1e-6)),
                                       lanes_supporting=hits, stack_snr=float(stack[m]/ssig)))
    if ex.stacked:
        ex.notes.append(f"cross-lane stacking confirmed {len(ex.stacked)} feature(s) too faint "
                        f"to call in any single lane")

    # F6 width model
    conf = [s for l in ex.lanes for s in l.spots if s.tier == "confirmed"]
    if len(conf) >= 4:
        yy = np.array([s.rel_pos for s in conf]); ss = np.array([s.sigma for s in conf])
        A = np.vstack([yy, np.ones_like(yy)]).T
        coef, *_ = np.linalg.lstsq(A, ss, rcond=None)
        pred = A@coef
        wide = [dict(lane=s.lane, rel_pos=round(s.rel_pos,3), sigma=round(s.sigma,1),
                     expected=round(float(p),1))
                for s,p in zip(conf,pred) if p > 0 and s.sigma > 2.0*p]
        ex.width_model = dict(slope=float(coef[0]), intercept=float(coef[1]),
                              n=len(conf), overwide=wide)
        if wide:
            ex.notes.append(f"{len(wide)} spot(s) at least 2x the width trend -> likely two "
                            f"co-running compounds or a smear; a weaker eluent would separate it")
    return ex

def resolution_pairs(lr: LaneResult) -> List[Dict]:
    """F6: Rs = dy / (2 (sigma1 + sigma2)) between adjacent spots in one lane."""
    out=[]
    sp=[s for s in lr.spots if s.tier=="confirmed"]
    for a,b in zip(sp[:-1], sp[1:]):
        rs = abs(b.y-a.y)/max(2*(a.sigma+b.sigma), 1e-9)
        out.append(dict(a=round(a.rel_pos,3), b=round(b.rel_pos,3), rs=round(float(rs),2),
                        verdict="baseline-resolved" if rs>=1.5 else ("partial" if rs>=1.0 else "overlapping")))
    return out
