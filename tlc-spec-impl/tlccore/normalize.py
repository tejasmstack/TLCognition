"""Phase B - NORMALIZE (spec section 5).

B1 find_plate : HSV green mask -> largest contour -> minAreaRect -> perspective warp
                -> STANDARDISE to height 700 px (spec 5.B1 / trap 12.3: every
                downstream threshold assumes this scale).
B2 orientation: the single VLM call (trap 12.5 - classical orientation does not work).
                Falls back to the origin-dot test, then to asking the user.
B3 OD map     : -log10(I/bg) against the plate's own glow, plus a MAD noise floor.
                Uses cv2.blur, never cv2.medianBlur (trap 12.2: asserts on float32).
"""
from __future__ import annotations
import numpy as np, cv2
from dataclasses import dataclass, field
from typing import Optional, List

STD_HEIGHT   = 700          # spec 5.B1
HUE_LO, HUE_HI = 30, 100    # broad band around UV-254 green (OpenCV hue units)
SAT_FLOOR    = 55
VAL_FLOOR    = 40
BG_KERNEL_FRAC = 0.35       # background box-blur kernel, as fraction of plate height
BG_ITERS     = 3            # spec: 2-3 iterations

def _order_corners(pts: np.ndarray) -> np.ndarray:
    pts = pts.astype(np.float32)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)  # TL,TR,BR,BL

@dataclass
class Plate:
    warped: np.ndarray                # BGR uint8, height == STD_HEIGHT
    quad: np.ndarray                  # 4x2 source corners
    src_height: int                   # plate height in ORIGINAL pixels (for the quality cap)
    scale: float                      # STD_HEIGHT / src_height
    mask_frac: float                  # fraction of frame the plate occupied
    method: str

def _trim_border(warp: np.ndarray, max_frac: float = 0.12, ratio: float = 3.0) -> np.ndarray:
    """Crop the plate's own dark edge / crop-shadow frame.

    Scans inward from each side while that line's median green differs from the
    interior median by more than `ratio` x the interior line-to-line spread. Without
    this a single dark border column reads as OD ~0.36 and its inflated MAD hides
    every real lane peak (see trap 12.7 - the 4% margin alone is not always enough).
    """
    g = warp[:, :, 1].astype(np.float32)
    h, w = g.shape
    iy0, iy1 = int(0.25 * h), int(0.75 * h)
    ix0, ix1 = int(0.25 * w), int(0.75 * w)
    interior = g[iy0:iy1, ix0:ix1]
    med = float(np.median(interior))
    spread = max(float(np.median(np.abs(np.median(g[iy0:iy1], axis=1) - med))), 1.0)
    tol = ratio * spread

    def scan(lines, limit):
        n = 0
        for ln in lines:
            if n >= limit: break
            if abs(float(np.median(ln)) - med) > tol: n += 1
            else: break
        return n
    top    = scan([g[i, :] for i in range(h)],            int(max_frac * h))
    bottom = scan([g[h - 1 - i, :] for i in range(h)],    int(max_frac * h))
    left   = scan([g[:, i] for i in range(w)],            int(max_frac * w))
    right  = scan([g[:, w - 1 - i] for i in range(w)],    int(max_frac * w))
    y0, y1 = top, h - bottom
    x0, x1 = left, w - right
    if y1 - y0 < 0.5 * h or x1 - x0 < 0.5 * w:
        return warp
    return warp[y0:y1, x0:x1]

def find_plate(bgr: np.ndarray) -> Plate:
    h0, w0 = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (HUE_LO, SAT_FLOOR, VAL_FLOOR), (HUE_HI, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5,5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN,  np.ones((3,3), np.uint8))
    cnts,_ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    method = "hsv_green_contour"
    if cnts:
        c = max(cnts, key=cv2.contourArea)
        if cv2.contourArea(c) < 0.10 * h0 * w0:
            c = None; method = "full_frame_fallback"
    else:
        c = None; method = "full_frame_fallback"
    if c is None:
        quad = np.array([[0,0],[w0-1,0],[w0-1,h0-1],[0,h0-1]], np.float32)
    else:
        quad = _order_corners(cv2.boxPoints(cv2.minAreaRect(c)))
    tl,tr,br,bl = quad
    wq = int(round(max(np.linalg.norm(tr-tl), np.linalg.norm(br-bl))))
    hq = int(round(max(np.linalg.norm(bl-tl), np.linalg.norm(br-tr))))
    wq, hq = max(wq,8), max(hq,8)
    dst = np.array([[0,0],[wq-1,0],[wq-1,hq-1],[0,hq-1]], np.float32)
    warp = cv2.warpPerspective(bgr, cv2.getPerspectiveTransform(quad,dst), (wq,hq),
                               flags=cv2.INTER_LANCZOS4)
    warp = _trim_border(warp)
    hq, wq = warp.shape[:2]
    scale = STD_HEIGHT / hq
    std = cv2.resize(warp, (max(8,int(round(wq*scale))), STD_HEIGHT),
                     interpolation=cv2.INTER_LANCZOS4)
    return Plate(warped=std, quad=quad, src_height=hq, scale=scale,
                 mask_frac=float((m>0).mean()), method=method)

# ---------------- B2 orientation ----------------
@dataclass
class Orientation:
    flipped: bool
    source: str          # vlm | origin_dots | user | assumed
    reason: str

def orientation_crops(warped: np.ndarray, frac: float = 0.18):
    h = warped.shape[0]
    return warped[:int(frac*h)].copy(), warped[int((1-frac)*h):].copy()

def resolve_orientation(warped: np.ndarray, vlm_answer: Optional[str],
                        dot_test=None) -> Orientation:
    """vlm_answer in {'TOP','BOTTOM','NEITHER',None}. dot_test(img)->bool tells whether
    origin dots sit in the lower third for that orientation."""
    if vlm_answer == "TOP":
        return Orientation(False, "vlm", "upright handwriting found in the top crop")
    if vlm_answer == "BOTTOM":
        return Orientation(True, "vlm", "upright handwriting found in the bottom crop -> rotate 180")
    if dot_test is not None:
        up   = dot_test(warped)
        down = dot_test(cv2.rotate(warped, cv2.ROTATE_180))
        if up and not down:
            return Orientation(False, "origin_dots", "origin dots sit in the lower third as-is")
        if down and not up:
            return Orientation(True, "origin_dots", "origin dots only sit low after a 180 rotation")
    return Orientation(False, "assumed", "orientation unresolved - ask the user (recorded, not guessed)")

# ---------------- B3 OD ----------------
@dataclass
class ODMap:
    od: np.ndarray            # float32, >=0, spots POSITIVE (analysis signal)
    od_signed: np.ndarray     # float32, unclipped - noise is estimated on THIS
    bg: np.ndarray            # background glow field
    noise: float              # 1.4826 * MAD over background pixels
    green: np.ndarray
    sat_frac: float           # fraction of plate pixels clipped at 255 (quality factor)

def to_od(warped: np.ndarray) -> ODMap:
    g = warped[:,:,1].astype(np.float32)
    h, w = g.shape
    k = max(3, int(BG_KERNEL_FRAC * h) | 1)
    bg = cv2.blur(g, (k,k))                       # trap 12.2: box blur, not medianBlur
    for _ in range(BG_ITERS - 1):
        # re-blur excluding pixels far below the current background (i.e. the spots)
        keep = g > bg - 0.5 * np.abs(bg - g).mean()
        filled = np.where(keep, g, bg)
        bg = cv2.blur(filled, (k,k))
    bg = np.maximum(bg, 1.0)
    od_signed = (-np.log10(np.clip(g, 1e-3, None) / bg)).astype(np.float32)
    od = np.clip(od_signed, 0, None).astype(np.float32)
    # Noise MUST come from the SIGNED map: clipping at 0 removes the negative half of
    # the background distribution, which drives MAD (and therefore every threshold
    # downstream) to exactly zero. Background population = below the 70th percentile.
    thr = np.percentile(od_signed, 70)
    bgpix = od_signed[od_signed <= thr]
    mad = float(np.median(np.abs(bgpix - np.median(bgpix))))
    noise = max(1.4826 * mad, 1e-4)
    sat = float((g >= 254).mean())
    return ODMap(od=od, od_signed=od_signed, bg=bg, noise=noise, green=g, sat_frac=sat)
