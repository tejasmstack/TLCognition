"""CLASS B / C — cross-plate trends and the insufficient-data ladder (spec 02 §4, §8).

The unit of analysis is the plate for Class B and the campaign for Class C; plates from the same
campaign do not count separately (§4.6). Before any test runs, §4.7's unlock arithmetic decides
whether a claim is even arithmetically possible; when it is not, the system says exactly that
rather than reporting the largest rho it found.
"""

import numpy as np

from tlc.assemble import PIPELINE_VERSION
from tlc.insight import confounds as C
from tlc.insight import estimators as E
from tlc.insight import render
from tlc.insight import variables as V
from tlc.insight.findings import Effect, Finding, make
from tlc.insight.multiplicity import adjust, max_family_size
from tlc.insight.registry import load_registry, registered

Q_F1 = 0.10


def independent_units(plates: list[V.PlateVars], unit: str) -> int:
    """§4.6 rule 5: 7 plates with 5 campaigns is n = 5, and both numbers are displayed."""
    if unit == "campaign":
        return len({p.campaign_id for p in plates if p.campaign_id})
    keys = {p.campaign_id or p.run_id for p in plates}
    return len(keys)


def _series(plates: list[V.PlateVars], hid: str) -> tuple[list[float], list[float], str, bool] | None:
    """(x, y, y_name, is_count) for a Class B hypothesis, or None when the data does not exist."""
    t = [p.reaction_time_h for p in plates]
    if hid in ("H11", "H12", "H13", "H15", "H17") and any(v is None for v in t):
        return None
    if hid == "H11":
        y = [_band_od(p, "S") for p in plates]
        return (t, y, "sm_band_od", False) if all(v is not None for v in y) else None
    if hid == "H12":
        y = [_band_od(p, "product") for p in plates]
        return (t, y, "product_band_od", False) if all(v is not None for v in y) else None
    if hid == "H13":
        ys, yp = [_band_od(p, "S") for p in plates], [_band_od(p, "product") for p in plates]
        return (ys, yp, "sm_vs_product_od", False) if all(v is not None for v in ys + yp) else None
    if hid == "H14":
        return ([float(i) for i in range(len(plates))], [float(len(p.bands_in("R"))) for p in plates], "band_count", True)
    if hid == "H15":
        y = [_area_ratio(p) for p in plates]
        return (t, y, "area_ratio", False) if all(v is not None for v in y) else None
    if hid == "H17":
        y = [_principal_rst(p, "R") for p in plates]
        return (t, y, "rst_R", False) if all(v is not None for v in y) else None
    if hid == "H16":
        y = [_principal_rst(p, "R") for p in plates]
        return ([float(i) for i in range(len(plates))], y, "rst_R", False) if all(v is not None for v in y) else None
    return None


def _principal_rst(p: V.PlateVars, role: str) -> float | None:
    bands = p.bands_in(role)
    return bands[0].rst if bands and bands[0].rst is not None else None


def _band_od(p: V.PlateVars, which: str) -> float | None:
    """Peak OD of the SM band (at the S-lane position) or of the product band, inside the R lane."""
    if p.photometry_mode != "full":
        return None
    R = p.bands_in("R")
    if not R:
        return None
    if which == "S":
        ref = p.bands_in("S") or p.bands_in("sd")
        if not ref or ref[0].rst is None:
            return None
        hit = [b for b in R if b.rst is not None and abs(b.rst - ref[0].rst) <= V.RST_MDE]
        return hit[0].peak_od if hit and hit[0].peak_od is not None else 0.0
    ref = p.bands_in("sd")
    if not ref or ref[0].rst is None:
        return None
    hit = [b for b in R if b.rst is not None and abs(b.rst - ref[0].rst) <= V.RST_MDE]
    return hit[0].peak_od if hit and hit[0].peak_od is not None else 0.0


def _area_ratio(p: V.PlateVars) -> float | None:
    """H15: area(P)/[area(P)+area(S)] within one lane, only under its hard validity gate."""
    if p.photometry_mode != "full":
        return None
    R = p.bands_in("R")
    sm = _band_od(p, "S")
    if not R or sm is None:
        return None
    areas = [b.area_od for b in R if b.area_od]
    if len(areas) < 2:
        return None
    total = sum(areas)
    return max(areas) / total if total > 0 else None


def _capture_matrix(plates: list[V.PlateVars]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for cid, names in C.CAPTURE_VARIABLES.items():
        for nm in names:
            vals = [p.capture.get(nm) for p in plates]
            if all(v is not None for v in vals) and len(set(vals)) > 1:
                out[f"{cid}:{nm}"] = [float(v) for v in vals]
    return out


def confound_panel(plates: list[V.PlateVars], x, y) -> list[dict]:
    """§3: every candidate re-tested against each capture variable, uncorrected at α = 0.10."""
    out = []
    for key, z in _capture_matrix(plates).items():
        cid, nm = key.split(":", 1)
        out.append(C.check_confound(cid, nm, x, y, z))
    bands = [b.to_dict() for p in plates for b in p.bands_in("R")]
    out.extend(C.hard_vetoes([{"id": b["id"], "clip_frac": b["clip_frac"], "agree": b["agree"],
                               "in_annotation_band": b["in_annotation_band"]} for b in bands] or [{"id": "-", "agree": 0}],
                             od_claim=True))
    return out


def analyse_cohort(plates: list[V.PlateVars], sigma_def: dict | None = None) -> list[Finding]:
    """Every registered Class B/C hypothesis, in the order H16 (veto) first (§2 H16)."""
    doc, rhash = load_registry()
    sigma_def = sigma_def or doc["sigma_definition"]
    hyps = [h for h in registered() if h["class"] in ("B", "C")]
    hyps.sort(key=lambda h: (h["id"] != "H16", h["id"]))
    key = plates[0].run_id if plates else "cohort"
    ts = max((p.created_at for p in plates), default="1970-01-01T00:00:00Z")
    n_plates = len(plates)
    n_campaigns = independent_units(plates, "campaign")
    prov = {"pipeline_version": PIPELINE_VERSION, "registry_version": doc["registry_version"], "registry_hash": rhash,
            "sigma_definition": sigma_def, "position_coordinate": "Rst", "computed_at": ts,
            "run_ids": [p.run_id for p in plates]}

    # First pass: compute every testable statistic, so BH sees the true family size.
    candidates: list[tuple[dict, dict]] = []
    blocked: list[tuple[dict, str, list[str]]] = []
    for h in hyps:
        unit = h.get("unit", "plate")
        n_units = independent_units(plates, unit)
        need = int(h["min_units"])
        s = _series(plates, h["id"])
        if n_units < need or s is None:
            have = (f"{n_plates} plate(s) in {n_campaigns or 'no declared'} campaign(s)"
                    + ("" if s is not None else "; the required measurements are not all available on these plates"))
            blocked.append((h, have, []))
            continue
        x, y, yname, is_count = s
        est = h.get("estimator") or E.choose_estimator(y, is_count)
        est = "kendall" if est == "kendall" else ("spearman" if est in ("spearman", "tost_slope") else est)
        r = E.permutation_p(x, y, est, h.get("sidedness", "two_sided"), h.get("expected_sign"))
        if r["p"] is None:      # a constant response (or predictor) has no rank correlation to test
            blocked.append((h, f"{n_plates} plate(s) in {n_campaigns or 'no declared'} campaign(s); "
                               f"{yname} does not vary across them", []))
            continue
        candidates.append((h, {"x": x, "y": y, "y_name": yname, "estimator": est, "n_units": n_units, **r}))

    out: list[Finding] = []
    for h, have, _ in blocked:
        unit = h.get("unit", "plate")
        n_units = independent_units(plates, unit)
        floor = E.exact_floor(n_units, h.get("sidedness", "two_sided")) if 3 <= n_units <= E.EXACT_MAX_N else None
        desc = _descriptive(plates)
        text = render.insufficient_data_text(
            h, have, n_units, floor, desc,
            f"{max(0, int(h['min_units']) - n_units)} more {unit}(s) meeting: {h.get('needs', 'the hypothesis conditions')}. "
            + render.STANDING_RECOMMENDATION)
        out.append(make(h["id"], h, "insufficient_data", text.splitlines()[0], None,
                        {"unit_of_analysis": unit, "n_units": n_units, "n_plates": n_plates, "n_campaigns": n_campaigns,
                         "n_min_required": int(h["min_units"]), "plate_ids": [p.run_id for p in plates],
                         "note": f"n_campaigns ({n_campaigns}) is the correct unit for a cross-plate claim; "
                                 f"{n_plates} plates are not {n_plates} independent units."},
                        {"method": None, "p_raw": None, "p_adjusted": None, "adjustment": None,
                         "family_size_m": None, "exact_p_floor": floor},
                        [], {}, [text], prov, key, ts,
                        unlock=[ln for ln in text.splitlines()[1:] if ln.startswith("To unlock")] or [render.STANDING_RECOMMENDATION]))

    if not candidates:
        return out

    m = len(candidates)
    ps = [c[1]["p"] for c in candidates]
    adj, proc = adjust(ps, "F1_chemistry")
    n_units_min = min(c[1]["n_units"] for c in candidates)
    m_max = max_family_size(n_units_min, Q_F1)
    for (h, st), p_adj in zip(candidates, adj, strict=True):
        x, y, est = st["x"], st["y"], st["estimator"]
        rho = st["statistic"]
        n_units = st["n_units"]
        ci = E.permutation_inverted_interval(x, y, est) if n_units <= 10 else None
        panel = confound_panel(plates, x, y)
        fired = [c for c in panel if c.get("result") == "FIRED"]
        mde = float((h.get("mde") or {}).get("min_abs", 0.70)) if (h.get("mde") or {}).get("metric") == "rho" else 0.70
        sign_ok = (h.get("expected_sign") is None
                   or (rho < 0 if h["expected_sign"] == "negative" else rho > 0))
        test = {"method": st["method"], "permutations": st["permutations"], "p_raw": round(st["p"], 6),
                "p_adjusted": round(p_adj, 6), "adjustment": proc, "family_size_m": m,
                "bh_threshold_for_top_test": round(Q_F1 / m, 6), "exact_p_floor": E.exact_floor(n_units, h.get("sidedness", "two_sided")),
                "max_family_size_at_n": m_max, "null_percentile": None}
        evidence = {"unit_of_analysis": h.get("unit", "plate"), "n_units": n_units, "n_plates": n_plates,
                    "n_campaigns": n_campaigns, "n_min_required": int(h["min_units"]),
                    "plate_ids": [p.run_id for p in plates], "y": st["y_name"],
                    "note": f"n_campaigns ({n_campaigns}) is the unit for this claim; plates within a campaign are not independent."}
        effect = Effect("spearman_rho" if est == "spearman" else "kendall_tau", None if rho is None else round(rho, 4),
                        None if ci is None else [round(ci[0], 3), round(ci[1], 3)],
                        "permutation_inverted_exact" if ci else None, "rank correlation",
                        {"x": h.get("x", "index"), "y": st["y_name"]})
        reasons = []
        if m > m_max:
            reasons.append({"code": "UNDERPOWERED_DESIGN", "statement":
                            f"This hypothesis family has {m} members and your data has {n_units} independent units. "
                            f"A family of at most {m_max} tests is arithmetically reportable at {n_units} units, so no "
                            f"cross-plate correlation can clear the bar with this dataset — regardless of how strong the "
                            f"underlying effect is."})
        if p_adj > Q_F1:
            reasons.append({"code": "FAILS_MULTIPLICITY", "statement":
                            f"Across the {m} checks run on this cohort, nothing reaches the false-discovery threshold. "
                            f"The best adjusted p is {min(adj):.4f} against a q of {Q_F1}."})
        if abs(rho or 0) < mde:
            reasons.append({"code": "BELOW_MDE", "statement":
                            f"The rank correlation is {rho:.3f}; the pre-registered minimum effect of chemical interest is {mde}."})
        for c in fired:
            reasons.append({"code": f"CONFOUND_{c['id']}", "explained_by": c["variable"], "statement": c["statement"],
                            "rho_raw": c.get("rho_raw"), "rho_partial": c.get("rho_partial"),
                            "rho_response_vs_confound": c.get("rho_response_vs_confound"),
                            "rule_triggered": "; ".join(c.get("rules_triggered", []))})
        unlock = ["Standardise the capture so residual sigma varies by less than 1.5x across the set (fixed camera distance, fixed exposure, no auto-mode).",
                  "Eliminate clipping: expose so the brightest plate background reads about 230, not 255.",
                  f"Collect {max(int(h['min_units']), 8)} plates within a single campaign at a single solvent system."]
        if not sign_ok:
            verdict, head = "anomaly", (f"ANOMALY — the trend runs opposite to the pre-registered sign "
                                        f"(rho {rho:.3f}, expected {h['expected_sign']}). At this sample size a sign flip "
                                        f"is more likely to be noise or a mislabelled plate than a chemical surprise.")
        elif reasons:
            verdict = "suppressed"
            head = ("SUPPRESSED — " + (reasons[0].get("statement", "") if reasons[0]["code"] != "CONFOUND_C01"
                    else f"apparent trend in {st['y_name']} is explained by {reasons[0].get('explained_by')}, not by the chemistry."))
        else:
            agree = np.nanmin([b.agree or np.nan for p in plates for b in p.bands_in("R")]) if any(p.bands_in("R") for p in plates) else None
            grade = C.agreement_grade(None if agree is None or np.isnan(agree) else float(agree))
            verdict = "reported" if grade == "confirmed" else "tentative"
            head = f"{h['title']}: rank correlation {rho:.3f} over {n_units} independent units."
        out.append(make(h["id"], h, verdict, head[:400], effect, evidence, test, panel,
                        {"N3_blank_cohort_fire_rate": None, "N4_replicate_stability": None}, [], prov, key, ts,
                        suppression={"reasons": reasons} if reasons and verdict == "suppressed" else None,
                        unlock=unlock))
    return out


def _descriptive(plates: list[V.PlateVars]) -> str:
    bits = []
    for p in plates[:6]:
        b = p.bands_in("R") or p.bands_in("sd")
        rst = ", ".join(f"{x.rst:.3f}" for x in b if x.rst is not None)
        bits.append(f"{p.run_id[4:12]}: {len(b)} band(s) at the reporting gate" + (f" (Rst {rst})" if rst else ""))
    return "; ".join(bits)
