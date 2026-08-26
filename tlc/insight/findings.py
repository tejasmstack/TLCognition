"""The Finding record (spec 02 §7.2) and its constructors. Verdicts: reported | tentative |
suppressed | anomaly | insufficient_data. A failed finding is never deleted — silence looks like
"we didn't look"."""

from dataclasses import asdict, dataclass, field
from typing import Any

from tlc.insight import render

VERDICTS = ("reported", "tentative", "suppressed", "anomaly", "insufficient_data")


@dataclass(frozen=True)
class Effect:
    metric: str
    value: float | int | None
    interval: tuple[float, float] | list[float] | None
    interval_method: str | None
    units: str
    supporting: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    finding_id: str
    hypothesis_id: str
    cls: str
    family: str
    verdict: str
    headline: str
    plain_language: str
    effect: Effect | None
    evidence: dict
    test: dict
    confounds: list[dict]
    nulls: dict
    caveats: list[str]
    falsifier: str
    next_experiment: str | None
    provenance: dict
    suppression: dict | None = None
    what_would_make_this_reportable: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["class"] = d.pop("cls")
        if d["effect"] and d["effect"]["interval"] is not None:
            d["effect"]["interval"] = list(d["effect"]["interval"])
        return d

    def lint(self) -> list[str]:
        text = " ".join(str(x) for x in (self.headline, self.plain_language, self.falsifier,
                                         " ".join(self.caveats), self.next_experiment or ""))
        return render.lint(text)


def finding_id(run_or_cohort: str, hid: str, created_at: str) -> str:
    return f"F-{created_at[:10]}-{hid}-{run_or_cohort[-8:]}"


def make(hid: str, h: dict, verdict: str, headline: str, effect: Effect | None, evidence: dict, test: dict,
         confounds: list[dict], nulls: dict, caveats: list[str], provenance: dict, key: str, created_at: str,
         suppression: dict | None = None, unlock: list[str] | None = None, next_experiment: str | None = None,
         plain_language: str | None = None) -> Finding:
    f = Finding(finding_id=finding_id(key, hid, created_at), hypothesis_id=hid, cls=h["class"], family=h["family"],
                verdict=verdict, headline=headline, plain_language=plain_language or h["plain_language"], effect=effect,
                evidence=evidence, test=test, confounds=confounds, nulls=nulls, caveats=caveats,
                falsifier=h["falsifier"], next_experiment=next_experiment, provenance=provenance,
                suppression=suppression, what_would_make_this_reportable=unlock or [])
    bad = f.lint()
    if bad:
        raise ValueError(f"{hid}: forbidden words in user-facing text: {bad}")
    return f


def to_result_block(findings: list[Finding], fdr_target: float, adjustment: str) -> dict[str, Any]:
    """Map onto the frozen result schema's CorrelationBlock (spec 03 §7.3.5). Suppressed findings are
    carried in `suppressed`, never dropped — hiding them is how an FDR control becomes theatre."""
    def one(f: Finding) -> dict:
        t = f.test
        return {"hypothesis_id": f.hypothesis_id, "statement": f.headline,
                "n_plates": int(f.evidence.get("n_plates", 0)), "n_min_required": int(f.evidence.get("n_min_required", 1)),
                "verdict": "supported" if f.verdict in ("reported", "tentative") else
                           ("insufficient_data" if f.verdict == "insufficient_data" else "not_supported"),
                "effect": None if not f.effect or not isinstance(f.effect.value, int | float) else float(f.effect.value),
                "ci95": None if not f.effect or not f.effect.interval else tuple(float(x) for x in f.effect.interval),
                "p_raw": t.get("p_raw"), "p_adjusted": t.get("p_adjusted"), "adjustment": t.get("adjustment"),
                "confounds_checked": [c["id"] for c in f.confounds],
                "confounds_unresolved": [c["id"] for c in f.confounds if c.get("result") == "FIRED"],
                "suppressed_reason": None if f.verdict != "suppressed" else {
                    "code": (f.suppression or {}).get("reasons", [{}])[0].get("code", "SUPPRESSED"),
                    "message": (f.suppression or {}).get("reasons", [{}])[0].get("statement", f.headline),
                    "remedy": (f.what_would_make_this_reportable or ["Collect the plates listed in the finding."])[0],
                    "evidence": {}}}
    reported = [one(f) for f in findings if f.verdict in ("reported", "tentative", "insufficient_data", "anomaly")]
    suppressed = [one(f) for f in findings if f.verdict == "suppressed"]
    return {"hypotheses_tested": len(findings), "adjustment": adjustment, "fdr_target": fdr_target,
            "findings": reported, "suppressed": suppressed}
