"""Self-consistency aggregation (spec 03 §7.9.3, spec 01 §7.3).

Categorical: modal vote, Jeffreys-smoothed agreement, normalized entropy; abstain when the raw
plurality share < 0.6 or the mode is UNREADABLE.  Quasi-continuous: median + IQR, abstain if
IQR > 0.05.  Free text: normalize, cluster (normalized Levenshtein <= 0.20), per-position
majority consensus with Jeffreys char_confidence, ALWAYS flagged_for_review.

Nothing here is a measurement.  Fractions are proposals for where the pipeline should look.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from tlc.vlm.errors import E_VLM_DISAGREEMENT, E_VLM_INVALID_OUTPUT, E_VLM_UNREADABLE

UNREADABLE = "UNREADABLE"
MIN_VALID = 3
AGREE_THRESHOLD = 0.6
IQR_THRESHOLD = 0.05
TEXT_CLUSTER_D = 0.20
CONFUSION_CLASSES = ({"0", "O", "Q"}, {"1", "L", "I", "|"}, {"5", "S"}, {"2", "Z"}, {"8", "B"},
                     {"6", "G"})  # fmt: skip  (upper-cased: o->O, l->L)
CONFUSION_VERSION = "v1"
_CANON = {c: sorted(cls)[0] for cls in CONFUSION_CLASSES for c in cls}


@dataclass
class FieldRead:
    """VLMField-compatible; ``asdict`` feeds ``VLMBlock.fields[name]`` after dropping ``reason``."""

    value: Any
    agreement: float | None = None
    samples: list[Any] | None = None
    disagreements: list[Any] | None = None
    iqr_frac: float | None = None
    flagged_for_review: bool | None = None
    reason: str | None = None  # abstention code, or None when a value is reported
    entropy: float | None = None

    def to_vlm_field(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("reason")
        d.pop("entropy")
        return d


def jeffreys(k_top: int, k: int, v: int) -> float:
    return (k_top + 0.5) / (k + 0.5 * v)


def _entropy(counts: Counter, v: int) -> float:
    k = sum(counts.values())
    if k == 0 or v <= 1:
        return 0.0
    h = -sum((c / k) * math.log(c / k) for c in counts.values())
    return h / math.log(v)


def _key(x: Any) -> Any:
    return x if isinstance(x, str | int | float | bool) or x is None else repr(x)


def invalid_field(n_valid: int, n_total: int) -> FieldRead:
    return FieldRead(value=None, samples=[], reason=E_VLM_INVALID_OUTPUT,
                     disagreements=[f"{n_valid}/{n_total} samples validated"])  # fmt: skip


def categorical(samples: list[Any], vocab_size: int) -> FieldRead:
    """Modal vote.  Reported agreement is Jeffreys-smoothed (spec 01 §7.3); the abstention
    threshold applies to the raw plurality share mode_count/n_valid (spec 03 §7.9.3)."""
    if not samples:
        return FieldRead(value=None, samples=[], reason=E_VLM_INVALID_OUTPUT)
    counts = Counter(_key(s) for s in samples)
    mode, k_top = max(counts.items(), key=lambda kv: (kv[1], str(kv[0])))
    k = len(samples)
    a = jeffreys(k_top, k, vocab_size)
    h = _entropy(counts, vocab_size)
    dis = [s for s in samples if _key(s) != mode] or None
    if mode == UNREADABLE:
        return FieldRead(None, a, list(samples), dis, reason=E_VLM_UNREADABLE, entropy=h)
    if k_top / k < AGREE_THRESHOLD:
        return FieldRead(None, a, list(samples), dis, reason=E_VLM_DISAGREEMENT, entropy=h)
    return FieldRead(mode, a, list(samples), dis, entropy=h)


def quasi_continuous(samples: list[float | None]) -> FieldRead:
    """Median + IQR over the non-null samples.  Nulls (absent/UNREADABLE) count against the
    plurality: if fewer than 60% of samples give a number the field abstains as UNREADABLE."""
    vals = [float(s) for s in samples if s is not None]
    if not samples:
        return FieldRead(value=None, samples=[], reason=E_VLM_INVALID_OUTPUT)
    if len(vals) / len(samples) < AGREE_THRESHOLD or len(vals) < 2:
        return FieldRead(None, None, list(samples), reason=E_VLM_UNREADABLE)
    med = statistics.median(vals)
    q = statistics.quantiles(vals, n=4, method="inclusive") if len(vals) >= 2 else [med, med, med]
    iqr = q[2] - q[0]
    if iqr > IQR_THRESHOLD:
        return FieldRead(None, None, list(samples), iqr_frac=iqr, reason=E_VLM_DISAGREEMENT)
    return FieldRead(med, None, list(samples), iqr_frac=iqr)


# --- free text ------------------------------------------------------------------------------


def normalize_text(s: str) -> str:
    s = re.sub(r"\s+", "", s.upper())
    return "".join(_CANON.get(c, c) for c in s)


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def norm_dist(a: str, b: str) -> float:
    m = max(len(a), len(b))
    return 0.0 if m == 0 else levenshtein(a, b) / m


def cluster_texts(norm: list[str]) -> list[list[int]]:
    """Agglomerative, average linkage, cut at d = TEXT_CLUSTER_D.  Deterministic in input order."""
    clusters = [[i] for i in range(len(norm))]
    while len(clusters) > 1:
        best, pair = None, None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = statistics.fmean(norm_dist(norm[a], norm[b])
                                     for a in clusters[i] for b in clusters[j])  # fmt: skip
                if best is None or d < best:
                    best, pair = d, (i, j)
        if best is None or best > TEXT_CLUSTER_D:
            break
        i, j = pair
        clusters[i] += clusters.pop(j)
    return clusters


def consensus(strings: list[str]) -> tuple[str, list[float]]:
    """Per-position majority after padding to the max length (simple alignment), with
    Jeffreys-smoothed char_confidence over the distinct symbols seen at that position."""
    if not strings:
        return "", []
    n = max(len(s) for s in strings)
    out, conf = [], []
    for j in range(n):
        col = Counter(s[j] if j < len(s) else "" for s in strings)
        ch, k_top = max(col.items(), key=lambda kv: (kv[1], kv[0]))
        out.append(ch)
        conf.append(jeffreys(k_top, len(strings), len(col)))
    return "".join(out), conf


def free_text(samples: list[str | None]) -> FieldRead:
    if not samples:
        return FieldRead(value=None, samples=[], reason=E_VLM_INVALID_OUTPUT, flagged_for_review=True)
    texts = [s for s in samples if s]
    if len(texts) / len(samples) < AGREE_THRESHOLD:
        return FieldRead(None, None, list(samples), reason=E_VLM_UNREADABLE, flagged_for_review=True)
    norm = [normalize_text(t) for t in texts]
    clusters = cluster_texts(norm)
    top = max(clusters, key=lambda c: (len(c), -min(c)))
    a = jeffreys(len(top), len(samples), len(clusters) + 1)  # +1: UNREADABLE is always in |V|
    share = len(top) / len(samples)
    # Consensus is voted on the ORIGINAL (whitespace-stripped, upper-cased) strings so that the
    # confusion classes only drive clustering, never rewrite the chemist's characters.
    raw_top = [re.sub(r"\s+", "", texts[i].upper()) for i in top]
    text, char_conf = consensus(raw_top)
    dis = [texts[i] for i in range(len(texts)) if i not in top] or None
    fr = FieldRead(None, a, list(samples), dis, flagged_for_review=True)
    if share < AGREE_THRESHOLD:
        fr.reason = E_VLM_DISAGREEMENT
        return fr
    fr.value = {"text": text, "char_confidence": char_conf,
                "needs_confirmation": any(c < 0.6 for c in char_conf)}
    return fr


# --- the semantic read ----------------------------------------------------------------------


@dataclass
class SemanticRead:
    lane_count: int | None = None
    lane_labels: list[str] | None = None
    lane_x_frac: list[float] | None = None  # proposals only -- seeds for the pixel refiner
    bands: dict[str, float | None] = field(
        default_factory=lambda: {"header_y1_frac": None, "label_row_y0_frac": None}
    )
    front_present: bool | None = None
    front_y_frac: float | None = None  # proposal only
    header_text: str | None = None  # always flagged for review
    fields: dict[str, FieldRead] = field(default_factory=dict)
    abstentions: dict[str, str] = field(default_factory=dict)  # field -> E_VLM_* code
    mode: str = "off"
    model_id: str | None = None
    prompt_bundle: dict[str, str] = field(default_factory=dict)
    n_samples: int = 0
    temperature: float = 1.0
    cache: dict[str, int | str] = field(default_factory=dict)
    cost: dict[str, int | float] = field(default_factory=dict)
    attempts: int = 0
    retries: int = 0
    invalid_samples: int = 0
    degraded: bool = False

    def vlm_fields(self) -> dict[str, dict[str, Any]]:
        return {k: v.to_vlm_field() for k, v in self.fields.items()}

    def _set(self, name: str, fr: FieldRead) -> FieldRead:
        self.fields[name] = fr
        if fr.reason is not None:
            self.abstentions[name] = fr.reason
        return fr


def aggregate_lanes(valid: list[dict], n_total: int, read: SemanticRead) -> None:
    if len(valid) < MIN_VALID:
        read._set("lane_count", invalid_field(len(valid), n_total))
        read._set("lane_labels", invalid_field(len(valid), n_total))
        return
    lc = read._set("lane_count", categorical([s["lane_count"] for s in valid], 9))
    if lc.value is None:
        read._set("lane_labels", FieldRead(None, None, reason=lc.reason))
        return
    read.lane_count = int(lc.value)
    agreeing = [s for s in valid if s["lane_count"] == lc.value and len(s["lanes"]) == lc.value]
    labels, xs, agreements, any_abstain = [], [], [], False
    for i in range(read.lane_count):
        lab = categorical([s["lanes"][i]["label"] for s in agreeing], 7)
        pos = quasi_continuous([s["lanes"][i]["x_frac"] for s in agreeing])
        labels.append(lab.value if lab.value is not None else UNREADABLE)
        xs.append(pos.value)
        agreements.append(lab.agreement)
        any_abstain |= lab.value is None
        read._set(f"lane_{i}_label", lab)
        read._set(f"lane_{i}_x_frac", pos)
    read.lane_labels = labels
    read.lane_x_frac = xs if all(x is not None for x in xs) else None
    read._set("lane_labels", FieldRead(labels, min(a for a in agreements if a is not None)
                                       if any(a is not None for a in agreements) else None,
                                       reason=E_VLM_UNREADABLE if any_abstain else None))  # fmt: skip


def aggregate_bands(valid: list[dict], n_total: int, read: SemanticRead) -> None:
    for key, present in (("header_y1_frac", "header_present"),
                         ("label_row_y0_frac", "label_row_present")):  # fmt: skip
        if len(valid) < MIN_VALID:
            read._set(key, invalid_field(len(valid), n_total))
            continue
        pres = categorical([s[present] for s in valid], 3)
        if pres.value == "no":
            read._set(key, FieldRead(None, pres.agreement, [s[key] for s in valid],
                                     reason=None))  # fmt: skip  absent band is a value, not abstain
            continue
        if pres.value is None:
            read._set(key, FieldRead(None, pres.agreement, pres.samples, reason=pres.reason))
            continue
        fr = read._set(key, quasi_continuous([s[key] for s in valid]))
        read.bands[key] = fr.value


def aggregate_front(valid: list[dict], n_total: int, read: SemanticRead) -> None:
    if len(valid) < MIN_VALID:
        read._set("front_present", invalid_field(len(valid), n_total))
        return
    fr = read._set("front_present", categorical([s["front_drawn"] for s in valid], 3))
    if fr.value is not None:
        read.front_present = fr.value == "yes"
        if read.front_present:
            pos = read._set("front_y_frac", quasi_continuous([s["front_y_frac"] for s in valid]))
            read.front_y_frac = pos.value


def aggregate_header(valid: list[dict], n_total: int, read: SemanticRead) -> None:
    if len(valid) < MIN_VALID:
        fr = invalid_field(len(valid), n_total)
        fr.flagged_for_review = True
        read._set("header_text", fr)
        return
    fr = read._set("header_text", free_text(
        [s["text"] if s["legible"] != UNREADABLE else None for s in valid]))  # fmt: skip
    if fr.value is not None:
        read.header_text = fr.value["text"]


AGGREGATORS = {"lanes": aggregate_lanes, "bands": aggregate_bands, "front": aggregate_front,
               "header": aggregate_header}  # fmt: skip
