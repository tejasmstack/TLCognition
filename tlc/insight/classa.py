"""CLASS A — within-plate structural findings, H01–H10 (spec 02 §2). Unit = 1 plate.
No correlation statistics: uncertainty comes from the ensemble, not from a p-value."""

from tlc.insight import confounds as C
from tlc.insight import variables as V
from tlc.insight.findings import Effect, Finding, make
from tlc.insight.registry import hypothesis

COMIGRATION_TOL = 0.05     # §2 H01: ~2x the worst observed cross-method disagreement
BROADEN_FLAG = 1.30        # §2 H02 third branch
BROADEN_FLOOR = 1.15       # below this, loading-induced width variation
TAIL_STREAK = 1.5
ORIGIN_RESIDUE_FRAC = 0.15


def _ci(b: V.Band) -> tuple[float, float] | None:
    return b.rst_ci


def _principal(bands: list[V.Band]) -> tuple[V.Band | None, str | None]:
    """The principal band of a lane: unambiguous only when one band dominates the others >2x in OD."""
    if not bands:
        return None, "no gated band in this lane"
    if len(bands) == 1:
        return bands[0], None
    ranked = sorted(bands, key=lambda b: -(b.area_od or b.peak_od or 0))
    top, second = ranked[0], ranked[1]
    a, b2 = (top.area_od or top.peak_od or 0), (second.area_od or second.peak_od or 0)
    if b2 > 0 and a / b2 <= 2.0:
        return None, f"{len(bands)} bands with agreement >= {V.AGREE_GATE} and no 2x optical-density dominance"
    return top, None


def _prov(pv: V.PlateVars, registry_hash: str, registry_version: str, sigma_def: dict) -> dict:
    return {"pipeline_version": "0.5.0", "registry_version": registry_version, "registry_hash": registry_hash,
            "sigma_definition": {**sigma_def, "sigma_value": pv.capture.get("sigma_od")},
            "position_coordinate": "Rst", "run_id": pv.run_id, "computed_at": pv.created_at}


def _bands_payload(bands: list[V.Band]) -> list[dict]:
    return [{"id": b.id, "agree": b.agree, "clip_frac": b.clip_frac, "in_annotation_band": b.in_annotation_band} for b in bands]


def analyse_plate(pv: V.PlateVars, registry_hash: str, registry_version: str, sigma_def: dict) -> list[Finding]:
    """Every Class A hypothesis that has the data it needs; the rest emit insufficient_data."""
    prov = _prov(pv, registry_hash, registry_version, sigma_def)
    key, ts = pv.run_id, pv.created_at
    out: list[Finding] = []
    sd = pv.bands_in("sd")
    R = pv.bands_in("R")
    S = pv.bands_in("S")
    co = pv.bands_in("co")
    sd_principal, sd_why = _principal(sd)
    od_ok = pv.photometry_mode == "full"

    def emit(hid, verdict, headline, effect, evidence, caveats, confs, nulls=None, suppression=None, unlock=None,
             next_experiment=None, plain=None):
        out.append(make(hid, hypothesis(hid), verdict, headline, effect, evidence,
                        {"method": "structural_comparison", "p_raw": None, "p_adjusted": None, "adjustment": None,
                         "family_size_m": None, "null_percentile": None},
                        confs, nulls or {}, caveats, prov, key, ts, suppression=suppression, unlock=unlock,
                        next_experiment=next_experiment, plain_language=plain))

    def ev(n_bands: int, lanes: list[str], agree: float | None, **kw) -> dict:
        return {"unit_of_analysis": "plate", "n_units": 1, "n_plates": 1, "n_campaigns": 1, "n_min_required": 1,
                "plate_ids": [pv.run_id], "lanes_used": lanes, "ensemble_agreement": agree,
                "confidence_grade": C.agreement_grade(agree), "n_bands": n_bands, **kw}

    # ---- H01 comigration of an R-lane band with the standard --------------------------------
    if sd_principal is None or not R:
        emit("H01", "insufficient_data",
             "Comigration cannot be assessed: " + (sd_why or "no gated band in the reaction lane") + ".",
             None, ev(0, ["R", "sd"], None), [], [],
             unlock=["Include a standard (sd) lane with one dominant band and a reaction (R) lane on the plate."])
    else:
        pairs = [b for b in R if b.rst is not None and abs(b.rst - 1.0) <= COMIGRATION_TOL]
        overlapping = [b for b in pairs if not (_ci(b) and _ci(sd_principal))
                       or (_ci(b)[0] <= _ci(sd_principal)[1] and _ci(sd_principal)[0] <= _ci(b)[1])]
        blocked = [b for b in (R + [sd_principal]) if b.shape_class in ("streak", "comet") or b.in_annotation_band
                   or b.clip_frac > 0.02]
        confs = C.hard_vetoes([sd_principal] + (overlapping or R[:1]), od_claim=False)
        if blocked:
            emit("H01", "suppressed", "Comigration not assessed — a contributing band is streaking, clipped or in the annotation band.",
                 None, ev(len(R), ["R", "sd"], min((b.agree or 0) for b in blocked)), [], confs,
                 suppression={"reasons": [{"code": "BAND_INVALID", "statement":
                              f"Bands {[b.id for b in blocked]} are streaking, clipped above 2%, or inside the annotation band."}]},
                 unlock=["Re-run the plate with lower loading and the writing outside the chromatography zone."])
        elif overlapping:
            b = overlapping[0]
            emit("H01", "reported" if C.agreement_grade(min(b.agree or 0, sd_principal.agree or 0)) != "suppressed" else "suppressed",
                 f"In the R lane, the band at Rst {b.rst:.3f} comigrates with the sd principal band.",
                 Effect("rst_difference", round(abs(b.rst - 1.0), 4), list(_ci(b)) if _ci(b) else None,
                        "ensemble_range" if _ci(b) else None, "Rst",
                        {"rst_R": {"value": b.rst, "interval": list(_ci(b)) if _ci(b) else None},
                         "rst_sd": {"value": sd_principal.rst, "interval": list(_ci(sd_principal)) if _ci(sd_principal) else None},
                         "tolerance": COMIGRATION_TOL}),
                 ev(len(R), ["R", "sd"], min(b.agree or 0, sd_principal.agree or 0), matched_spot=b.id, sd_spot=sd_principal.id),
                 ["Comigration is consistent with identity. It is never identity — two different compounds comigrating in one solvent system is the normal case."],
                 confs, next_experiment="Run R and sd in a second, orthogonal solvent system, or as a co-spot (H02).")
        else:
            emit("H01", "reported", "No R-lane band comigrates with the sd principal band within 0.05 Rst.",
                 Effect("n_comigrating_bands", 0, [0, 0], "ensemble_range", "bands",
                        {"nearest_rst": min((abs((b.rst or 0) - 1.0) for b in R), default=None)}),
                 ev(len(R), ["R", "sd"], min((b.agree or 0) for b in R) if R else None),
                 ["Absence of comigration at this tolerance does not establish that no shared component exists below the detection gate."],
                 confs, next_experiment="Run a co-spot lane (H02) to test the pair directly.")

    # ---- H02 the co-spot test ----------------------------------------------------------------
    if not co:
        emit("H02", "insufficient_data", "No co-spot lane on this plate — the most informative single TLC operation was not run.",
             None, ev(0, ["co"], None), [], [],
             unlock=["Spot the reaction mixture and the standard on top of each other in one lane, labelled `co`, at comparable loads."])
    else:
        overloaded = any((b.tail_factor or 0) > TAIL_STREAK for b in co)
        co_total = sum(b.area_od or 0 for b in co)
        comp_total = [sum(b.area_od or 0 for b in R), sum(b.area_od or 0 for b in sd)]
        overloaded = overloaded or (od_ok and comp_total and min(comp_total) > 0
                                    and co_total > 1.8 * (sum(comp_total) / 2))
        confs = C.hard_vetoes(co, od_claim=False)
        if overloaded:
            emit("H02", "suppressed", "The co-spot lane is overloaded — a merged band cannot be read as identity.",
                 None, ev(len(co), ["co", "R", "sd"], min((b.agree or 0) for b in co)),
                 ["An overloaded co-spot merges genuinely resolvable bands; only the two-band outcome is robust to overload."],
                 confs, suppression={"reasons": [{"code": "OVERLOADED_CO_LANE", "statement":
                          "Tailing above 1.5 or a co-lane total optical density above 1.8x the component mean."}]},
                 unlock=["Re-spot the co lane at half the load."])
        elif len(co) >= 2:
            rs = [b.rst for b in co if b.rst is not None][:2]
            sep = abs(rs[0] - rs[1]) if len(rs) == 2 else None
            emit("H02", "reported",
                 "The co-spot lane resolves into two bands — the reaction-mixture material is NOT identical to the standard.",
                 Effect("n_resolved_bands_in_co_lane", len(co), [len(co), len(co)], "ensemble_range", "bands",
                        {"rst_bands": rs, "separation": {"value": sep, "mde_gate": V.RST_MDE,
                                                         "passed": bool(sep is not None and sep >= V.RST_MDE)}}),
                 ev(len(co), ["co", "R", "sd"], min((b.agree or 0) for b in co)),
                 ["Overloading the co-spot lane can merge two bands into one, but cannot split one band into two. A two-band result is robust to overload in the direction that matters.",
                  "This says the materials differ. It does not say what the second component is."],
                 confs, next_experiment="Run R and sd in a second, orthogonal solvent system to estimate how much of the mixture is the non-standard component.")
        else:
            b = co[0]
            widths = [x.fwhm_px for x in (R + sd) if x.fwhm_px]
            ratio = (b.fwhm_px / (sum(widths) / len(widths))) if (b.fwhm_px and widths) else None
            if ratio is not None and ratio > BROADEN_FLAG:
                emit("H02", "tentative",
                     f"The co-spot lane shows one band, broadened {ratio:.2f}x against its components — partial resolution is suspected.",
                     Effect("co_width_ratio", round(ratio, 3), None, None, "1", {"flag_at": BROADEN_FLAG, "floor": BROADEN_FLOOR}),
                     ev(1, ["co", "R", "sd"], b.agree),
                     ["A single broadened band is a flag, not a result: it is consistent with two unresolved components and with a heavier load."],
                     confs, next_experiment="Develop longer, or run an orthogonal solvent system.")
            else:
                emit("H02", "reported", "The co-spot lane shows one band — the materials are not distinguishable in this system.",
                     Effect("n_resolved_bands_in_co_lane", 1, [1, 1], "ensemble_range", "bands",
                            {"co_width_ratio": None if ratio is None else round(ratio, 3)}),
                     ev(1, ["co", "R", "sd"], b.agree),
                     ["This is absence of evidence, never identity. A single solvent system routinely fails to separate different compounds."],
                     confs, next_experiment="Repeat the co-spot in an orthogonal solvent system before treating the materials as the same.")

    # ---- H03 band inventory at the reporting gate ---------------------------------------------
    R_all = [b for b in pv.bands if b.lane_role == "R"]
    gated = pv.bands_in("R")
    ann_excluded = [b.id for b in R_all if b.in_annotation_band]
    clip_frac = pv.capture.get("green_clip_frac") or 0.0
    confs = C.hard_vetoes(gated or R_all[:1], od_claim=False)
    caveats = [f"Blank plates built from this plate's own noise yield about 0.2 bands per plate at the reporting gate "
               f"(5 sigma and ensemble agreement {V.AGREE_GATE:.2f}); that baseline is a caveat, not a number to subtract."]
    if ann_excluded:
        caveats.append(f"{len(ann_excluded)} feature(s) in the handwriting band were excluded: ink density overlaps and often exceeds analyte density.")
    if clip_frac > 0.40:
        caveats.append(f"{clip_frac:.0%} of this plate is saturated; the count is reported but flagged low_confidence_clipping.")
    if R_all or pv.lane_roles.count("R"):
        emit("H03", "reported" if gated or R_all else "insufficient_data",
             f"The R lane shows {len(gated)} band(s) at 5 sigma with ensemble agreement at or above {V.AGREE_GATE:.2f}.",
             Effect("band_count", len(gated), [len(gated), len(gated)], "ensemble_range", "bands",
                    {"ungated_count": len(R_all), "gate": {"snr": V.SNR_GATE, "agree": V.AGREE_GATE},
                     "blank_baseline_bands_per_plate": 0.19}),
             ev(len(gated), ["R"], max((b.agree or 0) for b in gated) if gated else None,
                excluded_annotation=ann_excluded, clip_frac=clip_frac),
             caveats, confs, next_experiment="Include a solvent-only blank lane on the next plate; it must show zero bands at this gate.")
    else:
        emit("H03", "insufficient_data", "No lane is labelled R, so there is no reaction lane to inventory.",
             None, ev(0, [], None), [], [], unlock=["Label the reaction lane `R` at upload."])

    # ---- H04 origin residue --------------------------------------------------------------------
    origin_bands = [b for b in pv.bands if b.rst is not None and b.rst < V.ORIGIN_RST_MAX
                    and (b.agree or 0) >= V.AGREE_GATE and b.status == "confirmed"]
    if origin_bands:
        b = origin_bands[0]
        above = [x for x in pv.bands if x.lane_index == b.lane_index and (x.rst or 0) > b.rst]
        tail_above = max(((x.tail_factor or 0) for x in above), default=0.0)
        conf = C.hard_vetoes([b], od_claim=od_ok)
        if tail_above > TAIL_STREAK:
            emit("H04", "suppressed", "Material near the origin cannot be separated from the tail of an overloaded band above it.",
                 None, ev(1, [b.lane_label], b.agree), [], conf,
                 suppression={"reasons": [{"code": "TAIL_CONTAMINATION", "statement":
                              f"The next band up has tailing {tail_above:.2f}, above the 1.5 limit."}]},
                 unlock=["Re-spot at half the load so the band above the origin stops tailing."])
        else:
            emit("H04", "reported", f"Material remains at the application point in lane {b.lane_index + 1} (Rst {b.rst:.3f}).",
                 Effect("origin_band_rst", round(b.rst, 4), list(_ci(b)) if _ci(b) else None,
                        "ensemble_range" if _ci(b) else None, "Rst", {"spot": b.id}),
                 ev(1, [b.lane_label], b.agree),
                 ["Two readings are equally consistent on one plate: highly polar material (demethylation product, oxidised phenolics, salts) or simple overload. These are not separable here."],
                 conf, next_experiment="Spot the same sample at two different loads on one plate; residue that scales with load was overload.")
    else:
        emit("H04", "reported", "No material is detected at the application point above the reporting gate.",
             Effect("origin_band_rst", None, None, None, "Rst", {}), ev(0, [], None),
             ["A bounded absence: material below the gate would not appear."], [])

    # ---- H05 streak / tailing ------------------------------------------------------------------
    streaks = sorted({b.lane_index for b in pv.bands if b.shape_class in ("streak", "comet")}
                     | {i for i in range(pv.n_lanes) if i not in pv.quantified_lanes})
    if streaks:
        emit("H05", "reported", f"Lane(s) {[i + 1 for i in streaks]} are streaking — every area-derived quantity there is withheld.",
             Effect("n_streaking_lanes", len(streaks), [len(streaks), len(streaks)], "ensemble_range", "lanes",
                    {"lanes": [i + 1 for i in streaks]}),
             ev(0, [f"lane{i + 1}" for i in streaks], None),
             ["Chemical readings, all consistent: overload; an acidic or basic compound streaking on unbuffered silica (plausible for a phenol — try 1% acetic acid in the eluent); decomposition on the plate.",
              "Position may still be read with a widened interval; areas may not."],
             [], next_experiment="Re-run at half the load with 1% acetic acid in the eluent.")
    else:
        emit("H05", "reported", "No lane is streaking; band shapes support quantification where photometry is available.",
             Effect("n_streaking_lanes", 0, [0, 0], "ensemble_range", "lanes", {}), ev(0, [], None), [], [])

    # ---- H06 lane-role consistency --------------------------------------------------------------
    declared = [r for r in pv.lane_roles if r != "unknown"]
    emit("H06", "reported",
         f"{pv.n_lanes} lanes, roles {pv.lane_roles} — taken from the operator or the label row, never from the signal."
         if declared else f"{pv.n_lanes} lanes with no declared roles; the grid was fitted at the declared count.",
         Effect("n_lanes", pv.n_lanes, [pv.n_lanes, pv.n_lanes], "declared", "lanes", {"roles": pv.lane_roles}),
         ev(0, pv.lane_roles, None),
         ["Lane detection from the signal fails when a lane is empty: the grid slides toward the loaded lanes. The declared count wins."], [])

    # ---- H07 bounded absence of the starting material in R ---------------------------------------
    sm_ref = None
    if S:
        sm_ref, _ = _principal(S)
    elif sd_principal is not None:
        sm_ref = sd_principal
    sigma = pv.capture.get("sigma_od")
    if sm_ref is None or sm_ref.rst is None:
        emit("H07", "insufficient_data", "No starting-material reference band, so the reaction lane cannot be tested for its absence.",
             None, ev(0, ["S", "R"], None), [], [],
             unlock=["Run the starting material in its own lane, labelled `S`, on the same plate."])
    else:
        hit = [b for b in R if b.rst is not None and abs(b.rst - sm_ref.rst) <= V.RST_MDE]
        limit = None if sigma is None else V.SNR_GATE * sigma
        if hit:
            b = hit[0]
            emit("H07", "reported", f"Starting material is still detected in the R lane at Rst {b.rst:.3f}.",
                 Effect("sm_band_rst", round(b.rst, 4), list(_ci(b)) if _ci(b) else None,
                        "ensemble_range" if _ci(b) else None, "Rst", {"reference_spot": sm_ref.id}),
                 ev(1, ["S", "R"], min(b.agree or 0, sm_ref.agree or 0)),
                 ["Presence at the S-lane position is consistent with unconsumed starting material and with any co-migrating component."],
                 C.hard_vetoes([b, sm_ref], od_claim=False))
        else:
            emit("H07", "reported",
                 "No band is detected in the R lane at the starting-material position above the 5 sigma limit"
                 + (f"; the detection limit corresponds to about {limit:.3f} optical density." if limit else "."),
                 Effect("detection_limit_od", None if limit is None else round(limit, 4), None, None, "OD",
                        {"reference_rst": sm_ref.rst, "k_sigma": V.SNR_GATE, "sigma_od": sigma}),
                 ev(0, ["S", "R"], sm_ref.agree),
                 ["This is a bounded absence, not a completed reaction: starting material below the stated limit would not appear.",
                  "The limit is in optical-density units, not moles; converting it needs a loading series on the same plate."],
                 C.hard_vetoes([sm_ref], od_claim=False),
                 next_experiment="Run a loading series of the standard to convert the limit into an amount.")

    # ---- H08 a new band above the product Rst -----------------------------------------------------
    ref_rst = sd_principal.rst if sd_principal and sd_principal.rst is not None else None
    if ref_rst is None:
        emit("H08", "insufficient_data", "Without a standard-lane reference band there is no product position to compare against.",
             None, ev(0, ["R", "sd"], None), [], [], unlock=["Include an sd lane with one dominant band."])
    else:
        elsewhere = [b.rst for b in (S + sd) if b.rst is not None]
        new = [b for b in R if b.rst is not None and b.rst > ref_rst + V.RST_MDE
               and b.shape_class != "halo"
               and not any(abs(b.rst - r) <= V.RST_MDE for r in elsewhere)]
        if new:
            emit("H08", "tentative" if len(new) else "reported",
                 f"{len(new)} band(s) sit above the standard's position in the R lane and appear in no other lane.",
                 Effect("n_new_high_bands", len(new), [len(new), len(new)], "ensemble_range", "bands",
                        {"rst": [round(b.rst, 4) for b in new]}),
                 ev(len(new), ["R", "S", "sd"], min((b.agree or 0) for b in new)),
                 ["Consistent readings: over-reaction (bis-alkylation), oxidation to the quinone, or an eluent-borne artefact. One plate does not separate them.",
                  "Three plates of the same campaign are needed before calling this a reproducible impurity."],
                 C.hard_vetoes(new, od_claim=False),
                 next_experiment="Repeat with fresh eluent from a new bottle; an eluent artefact disappears.")
        else:
            emit("H08", "reported", "No band above the standard's position is unique to the R lane.",
                 Effect("n_new_high_bands", 0, [0, 0], "ensemble_range", "bands", {}), ev(0, ["R", "S", "sd"], None),
                 ["A bounded absence at the reporting gate."], [])

    # ---- H09 the identity coordinate ---------------------------------------------------------------
    R_principal, R_why = _principal(R)
    if R_principal is None or R_principal.rst is None:
        emit("H09", "insufficient_data", "No principal R-lane band with an Rst: " + (R_why or "no reference anchor") + ".",
             None, ev(len(R), ["R"], None), [], [],
             unlock=["Include an sd lane so positions can be anchored, and load the reaction lane so one band dominates."])
    else:
        emit("H09", "reported", f"R-lane principal band at Rst {R_principal.rst:.3f}"
             + (f" (interval {_ci(R_principal)[0]:.3f}–{_ci(R_principal)[1]:.3f})." if _ci(R_principal) else "."),
             Effect("rst", round(R_principal.rst, 4), list(_ci(R_principal)) if _ci(R_principal) else None,
                    "ensemble_range" if _ci(R_principal) else None, "Rst",
                    {"anchor_spot_id": pv.anchor_spot_id, "spot": R_principal.id}),
             ev(1, ["R", "sd"], R_principal.agree),
             ["Rst is relative to this plate's standard lane. It is transportable between plates only while the same standard and solvent system are used."],
             C.hard_vetoes([R_principal], od_claim=False),
             next_experiment="Record the solvent system with the plate; Rst is only comparable within one system.")

    # ---- H10 co-spot elongation ratio ---------------------------------------------------------------
    if co and (R or sd):
        widths = [x.fwhm_px for x in (R + sd) if x.fwhm_px]
        cw = co[0].fwhm_px
        if cw and widths:
            ratio = cw / (sum(widths) / len(widths))
            emit("H10", "reported" if ratio >= BROADEN_FLOOR else "reported",
                 f"Co-spot width ratio {ratio:.2f} against the mean of its component lanes.",
                 Effect("co_width_ratio", round(ratio, 3), None, None, "1",
                        {"flag_at": BROADEN_FLAG, "no_report_below": BROADEN_FLOOR, "co_fwhm_px": cw}),
                 ev(1, ["co", "R", "sd"], co[0].agree),
                 ["Below 1.15 this is within loading-induced width variation and carries no information."],
                 C.hard_vetoes(co, od_claim=False))
        else:
            emit("H10", "insufficient_data", "Band widths were not fitted, so the co-spot elongation ratio is not computed.",
                 None, ev(len(co), ["co"], None), [], [], unlock=["A resolvable band with an EMG fit in the co lane and in both component lanes."])
    else:
        emit("H10", "insufficient_data", "No co-spot lane, so there is no elongation ratio.",
             None, ev(0, [], None), [], [], unlock=["Include a co-spot lane."])

    return out
