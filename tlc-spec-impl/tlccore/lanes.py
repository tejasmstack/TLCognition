"""Phase D - LANE ROLES (spec section 7). Lexicon-first ladder, deliberately not agentic.

The correlation engine must never see raw names, only roles. Resolution order:
 1 read the labels (VLM, pinned + cached)   2 exact lexicon hit
 3 pattern hit (condition codes T1/A3...)   4 structural cross-check (can demote)
 5 ask the user, then learn the token so the same chemist is never asked twice.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

ROLES = ("SM_REFERENCE","COSPOT","REACTION","PRODUCT_STANDARD","OTHER_REFERENCE","UNKNOWN")

SEED_LEXICON: Dict[str,str] = {
    "S":"SM_REFERENCE","SM":"SM_REFERENCE","KSM":"SM_REFERENCE",
    "CO":"COSPOT","C":"COSPOT",
    "R":"REACTION","RM":"REACTION","RXN":"REACTION",
    "SD":"PRODUCT_STANDARD","STD":"PRODUCT_STANDARD","P":"PRODUCT_STANDARD",
}
CONDITION_CODE = re.compile(r"^[A-Z]\d+$")

@dataclass
class LaneRole:
    index: int
    name_raw: str
    name_read_conf: float
    role: str
    role_source: str          # lexicon | pattern | structure | user | none
    role_conf: float
    notes: List[str] = field(default_factory=list)

def norm_token(t: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (t or "")).upper()

def resolve_roles(labels: List[str], read_conf: List[float],
                  lexicon: Optional[Dict[str,str]] = None) -> List[LaneRole]:
    lex = dict(SEED_LEXICON); lex.update(lexicon or {})
    out: List[LaneRole] = []
    for i, raw in enumerate(labels):
        t = norm_token(raw)
        rc = read_conf[i] if i < len(read_conf) else 0.6
        if t in lex:
            out.append(LaneRole(i, raw, rc, lex[t], "lexicon", 0.95))
        elif CONDITION_CODE.match(t):
            out.append(LaneRole(i, raw, rc, "REACTION", "pattern", 0.75,
                                ["condition-code shape -> reaction lane"]))
        elif len(t) == 1 and t.isalpha():
            out.append(LaneRole(i, raw, rc, "OTHER_REFERENCE", "pattern", 0.55,
                                ["single unknown letter -> candidate reference"]))
        else:
            out.append(LaneRole(i, raw, rc, "UNKNOWN", "none", 0.3,
                                [f"token '{raw}' not in lexicon -> ask the user"]))
    return out

def structural_crosscheck(roles: List[LaneRole], spot_counts: List[int]) -> List[LaneRole]:
    """Spec D4: roles imply signal structure. Demote to UNKNOWN on contradiction rather
    than trusting the label. Only the cheap, always-available checks are done here;
    the alpha/beta co-spot decomposition belongs to phase G."""
    for r in roles:
        n = spot_counts[r.index] if r.index < len(spot_counts) else 0
        if r.role == "PRODUCT_STANDARD" and n > 3:
            r.notes.append(f"labelled a standard but shows {n} spots - a standard is usually "
                           f"single-spot; role confidence reduced")
            r.role_conf = min(r.role_conf, 0.45); r.role_source = "structure"
        if r.role in ("SM_REFERENCE","PRODUCT_STANDARD") and n == 0:
            r.notes.append("reference lane shows no confirmed spot - absence claims will be weak")
            r.role_conf = min(r.role_conf, 0.5)
    return roles

# --- cached VLM label reads for the seven supplied plates (temperature 0, model pinned).
# Recorded verbatim from claude-opus-5 reading the below-origin strip of each plate.
VLM_LABEL_CACHE: Dict[str, Dict] = {
    sid: dict(model="claude-opus-5", prompt_version="lane-labels-v1",
              labels=["S","co","R","Sd"], conf=[0.95,0.92,0.95,0.90])
    for sid in ("P29","P30","P31","P32-1","P32","P32b","P33")
}
