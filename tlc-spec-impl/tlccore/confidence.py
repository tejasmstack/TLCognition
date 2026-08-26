"""Spec section 10 - the one confidence implementation used by every phase.

Quantised to 0.05 steps WITH HYSTERESIS so a re-run or a boundary value can never
flip a verdict: if a previously stored score sits within one step of the new raw
value, the stored score is kept. That is part of the determinism invariant.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from math import prod
from typing import List, Optional

STEP = 0.05

@dataclass(frozen=True)
class Factor:
    name: str          # image_quality, origin_source, snr, delta_agreement, lane_bleed, ...
    value: float       # in [0,1]
    reason: str        # human sentence, shown on hover / in the expert layer

    def __post_init__(self):
        if not (0.0 <= self.value <= 1.0):
            raise ValueError(f"factor {self.name} out of range: {self.value}")

def quantize(raw: float, previous: Optional[float] = None) -> float:
    q = round(raw / STEP) * STEP
    q = min(1.0, max(0.0, round(q, 4)))
    if previous is not None and abs(previous - raw) <= STEP:
        return round(previous, 4)          # hysteresis: keep the stored value
    return q

@dataclass
class Conf:
    score: float
    factors: List[Factor] = field(default_factory=list)

    @property
    def band(self) -> str:
        if self.score >= 0.75: return "stated"      # say it plainly
        if self.score >= 0.50: return "caveated"    # say it with the caveat inline
        return "watchlist"                          # phrase as a question, never assert

    def as_dict(self):
        return dict(score=self.score, band=self.band, factors=[asdict(f) for f in self.factors])

def combine(factors: List[Factor], previous: Optional[float] = None) -> Conf:
    if not factors:
        return Conf(score=quantize(1.0, previous), factors=[])
    raw = prod(f.value for f in factors)
    return Conf(score=quantize(raw, previous), factors=list(factors))

def weakest_link(confs: List[Conf], own: Optional[List[Factor]] = None) -> Conf:
    """Spec: a verdict's confidence = MIN over the findings it depends on
    (weakest link), then its own factors applied on top."""
    if not confs:
        base, fac = 1.0, []
    else:
        w = min(confs, key=lambda c: c.score)
        base, fac = w.score, list(w.factors)
    if own:
        base *= prod(f.value for f in own); fac = fac + list(own)
    return Conf(score=quantize(base), factors=fac)
