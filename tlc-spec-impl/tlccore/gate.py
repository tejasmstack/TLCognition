"""Phase A - GATE (spec section 4). The ONLY place a hard 'no' happens.

Two rejection classes: NOT_A_PLATE, NOT_WORKABLE. Everything else passes, possibly
with a quality cap. Checks run on the WARPED STANDARDISED plate, never on the raw
image (trap 12.3: scale-dependent metrics on raw images caused false rejections).
Do not add more hard checks - borderline images must pass and be caveated instead.
"""
from __future__ import annotations
import numpy as np, cv2
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

GREEN_DOMINANCE_MIN = 5      # median(G) - max(median(R), median(B))
EDGE_DENSITY_MAX    = 0.06   # Canny edge fraction
DARK_CONTENT_MIN    = 0.005  # fraction of pixels with OD > 3*noise
RESOLUTION_FLOOR    = 120    # source px; BELOW this -> quality cap, NOT rejection

@dataclass
class Check:
    name: str
    measured: float
    needed: str
    passed: bool

@dataclass
class GateResult:
    verdict: str                     # PASS | NOT_A_PLATE | NOT_WORKABLE
    because: Optional[str] = None
    checks: List[Check] = field(default_factory=list)
    quality: str = "OK"              # OK | LOW
    quality_reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        d = dict(verdict=self.verdict, quality=self.quality,
                 quality_reasons=self.quality_reasons,
                 checks=[vars(c) for c in self.checks])
        if self.because:
            f = next((c for c in self.checks if c.name == self.because), None)
            d.update(because=self.because,
                     measured=None if f is None else f.measured,
                     needed=None if f is None else f.needed)
        return d

def run_gate(warped: np.ndarray, od: np.ndarray, noise: float, src_height: int) -> GateResult:
    checks: List[Check] = []
    b, g, r = [np.median(warped[:,:,i]) for i in range(3)]

    gd = float(g - max(r, b))
    checks.append(Check("green_dominance", gd, f">= {GREEN_DOMINANCE_MIN}", gd >= GREEN_DOMINANCE_MIN))

    grey = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    ed = float((cv2.Canny(cv2.GaussianBlur(grey,(3,3),0), 60, 160) > 0).mean())
    checks.append(Check("edge_density", ed, f"<= {EDGE_DENSITY_MAX}", ed <= EDGE_DENSITY_MAX))

    dark = float((od > 3.0 * noise).mean())
    checks.append(Check("dark_content", dark, f">= {DARK_CONTENT_MIN}", dark >= DARK_CONTENT_MIN))

    from .signals import x_projection, noise_peaks
    from .structure import find_origin_dots
    h, w = od.shape
    _, prof = x_projection(od, 0.28, 0.95)      # spot band starts at 0.28h (trap 12.6)
    pk, _, _ = noise_peaks(prof, w)
    # Lane evidence must accept EITHER source of lane structure. A plate whose
    # reference lanes are very lightly loaded can show zero projection peaks while its
    # spotting-dot row is perfectly clear (measured on P31), and rejecting that plate as
    # NOT_WORKABLE would contradict the spec's rule that borderline images pass.
    dots, _ = find_origin_dots(od, noise)
    ev = max(len(pk), len(dots))
    checks.append(Check("lane_evidence", float(ev),
                        ">= 1 (projection peaks or spotting dots)", ev >= 1))

    res_ok = src_height >= RESOLUTION_FLOOR
    checks.append(Check("resolution_floor", float(src_height),
                        f">= {RESOLUTION_FLOOR} (cap only, never a rejection)", True))

    # ordering matters: NOT_A_PLATE checks first
    for name, cls in (("green_dominance","NOT_A_PLATE"), ("edge_density","NOT_A_PLATE"),
                      ("dark_content","NOT_WORKABLE"), ("lane_evidence","NOT_WORKABLE")):
        c = next(x for x in checks if x.name == name)
        if not c.passed:
            reason = {"dark_content":"blank plate - no spots detected anywhere",
                      "lane_evidence":"no lane structure found"}.get(name, name)
            gr = GateResult(verdict=cls, because=name, checks=checks)
            gr.quality_reasons.append(reason)
            return gr

    gr = GateResult(verdict="PASS", checks=checks)
    if not res_ok:
        gr.quality = "LOW"
        gr.quality_reasons.append(f"plate only {src_height} source px tall (< {RESOLUTION_FLOOR}) - confidence capped")
    return gr
