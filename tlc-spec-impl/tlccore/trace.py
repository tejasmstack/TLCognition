"""Spec section 10 - replayable trace. Every phase logs inputs, outputs, decision
and reasons, so a wrong result is diagnosable to the exact step it went off-road."""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

def _j(o):
    import numpy as np
    if isinstance(o,(np.integer,)): return int(o)
    if isinstance(o,(np.floating,)): return float(o)
    if isinstance(o,(np.bool_,)): return bool(o)
    if isinstance(o,np.ndarray): return o.tolist()
    if hasattr(o,"as_dict"): return o.as_dict()
    if hasattr(o,"__dict__"): return {k:v for k,v in vars(o).items() if not k.startswith("_")}
    return str(o)

@dataclass
class Section:
    phase: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    decision: str = ""
    reasons: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)

class Trace:
    def __init__(self, run_dir: str, pipeline_version: str, params_hash: str):
        self.run_dir = run_dir
        self.meta = dict(pipeline_version=pipeline_version, params_hash=params_hash)
        self.sections: List[Section] = []
        os.makedirs(run_dir, exist_ok=True)

    def section(self, phase: str, **kw) -> Section:
        s = Section(phase=phase, **kw); self.sections.append(s); return s

    def note(self, s: Section, msg: str):
        s.reasons.append(msg)

    def save(self):
        p = os.path.join(self.run_dir, "trace.json")
        with open(p,"w") as fh:
            json.dump(dict(meta=self.meta, sections=[asdict(s) for s in self.sections]),
                      fh, indent=2, default=_j)
        return p
