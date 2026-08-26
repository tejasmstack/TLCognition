"""Phase E - PIXEL-FAITHFUL GRAPH (spec section 8-preamble).

Every plate pixel becomes a graph pixel: no ellipse abstraction, no redraw
(trap 12.8). Nothing is cropped above the front or below the origin - impurities
live there - so origin and front are drawn INSIDE the axes instead.
"""
from __future__ import annotations
import numpy as np
from typing import Optional, Tuple
from .structure import Structure
from .extract import Extract

def vertical_span(od: np.ndarray, st: Structure, ex: Extract) -> Tuple[int,int]:
    """Spec: from max(0.10h, min(front_y, topmost_spot) - 0.04h) down to origin_y + 0.035h."""
    h = od.shape[0]
    tops = [s.y for l in ex.lanes for s in l.spots] or [0.30*h]
    topmost = min(tops)
    ref = min(st.front.y, topmost) if st.front is not None else topmost
    y_top = int(max(0.10*h, ref - 0.04*h))
    y_bot = int(min(h-1, (st.origin.y if st.origin else 0.86*h) + 0.035*h))
    if y_bot - y_top < 30: y_top = max(0, y_bot-30)
    return y_top, y_bot

def to_rf(y: float, st: Structure, y_top: int, y_bot: int) -> float:
    """True Rf when a front exists, otherwise relative position in [0,1]."""
    if st.origin is None: 
        return (y_bot - y)/max(y_bot - y_top, 1e-6)
    if st.front is not None and abs(st.origin.y - st.front.y) > 1e-6:
        return (st.origin.y - y)/(st.origin.y - st.front.y)
    return (st.origin.y - y)/max(st.origin.y - y_top, 1e-6)

def vmax_for(od: np.ndarray) -> float:
    """Per-plate contrast: 99.7th percentile of the plate's own OD (spec)."""
    return float(np.percentile(od, 99.7)) or 1.0
