"""Entry points: one plate (Class A) and one cohort (Class B/C). Pure functions over Result JSON."""

from tlc.insight import variables as V
from tlc.insight.classa import analyse_plate
from tlc.insight.cohort import analyse_cohort
from tlc.insight.findings import Finding
from tlc.insight.registry import load_registry


def analyse_plate_findings(result: dict, meta: dict | None = None) -> list[Finding]:
    doc, rhash = load_registry()
    pv = V.extract(result, meta)
    return analyse_plate(pv, rhash, doc["registry_version"], doc["sigma_definition"])


def analyse_cohort_findings(results: list[dict], metas: list[dict] | None = None) -> list[Finding]:
    metas = metas or [{} for _ in results]
    plates = [V.extract(r, m) for r, m in zip(results, metas, strict=True)]
    return analyse_cohort(plates)
