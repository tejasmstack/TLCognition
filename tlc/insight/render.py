"""Plain-language templates and the forbidden-word linter (spec 02 §7.1, §9)."""

import re

# "proves"/"proved"/"proof" are forbidden as claims; the infinitive in §8.3's standing recommendation
# ("the fastest path to prove a trend") is the spec's own text and stays legal.
FORBIDDEN = [r"\bsignificant\b", r"\bprove[sd]\b", r"\bproof\b", r"\bconfirms that\b", r"\bpure\b", r"\bcomplete conversion\b",
             r"\bRf\b(?![ *]*\(|\*)", r"\d+(\.\d+)?%\s*(confidence|confident|sure)"]
_ALLOW_RF = re.compile(r"never Rf|Rf is not reported|not Rf|Rf, never|Rf\b.*convention", re.I)


def lint(text: str) -> list[str]:
    hits = []
    for pat in FORBIDDEN:
        for m in re.finditer(pat, text, flags=re.I):
            if pat.startswith(r"\bRf") and _ALLOW_RF.search(text):
                continue  # Rf may be named only inside the multi-convention / "never Rf" disclosure
            hits.append(m.group(0))
    return hits


def insufficient_data_text(h: dict, have: str, n_units: int, floor_p: float | None, descriptive: str, unlock: str) -> str:
    """§8.1 fixed wording template."""
    lines = ["Not enough plates to test this yet.", f"Hypothesis: {h['plain_language']}",
             f"Needs: {h.get('needs', f'{h['min_units']} independent units')}. You have: {have}."]
    if floor_p is not None and n_units >= 3:
        side = "one-sided" if h.get("sidedness") == "one_sided" else "two-sided"
        lines.append(f"At {n_units} units the strongest possible result — a perfect rank correlation — carries an exact "
                     f"{side} p of {floor_p:.3f}. There is no arrangement of {n_units} points that constitutes evidence.")
    elif n_units < 3:
        lines.append(f"With {n_units} unit(s) no correlation is defined.")
    if descriptive:
        lines.append(f"What we can tell you now, descriptively and with no statistics attached: {descriptive}")
    lines.append(f"To unlock this: {unlock}")
    return "\n".join(lines)


STANDING_RECOMMENDATION = (
    "The fastest path from \"we can see your plates\" to \"we can prove a trend\" is six plates in one campaign, "
    "captured identically. Fix the camera distance and exposure (background ~230, never 255), draw the solvent front, "
    "keep the loading constant, and include an sd lane and a blank lane on every plate.")
