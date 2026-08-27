"""Refusals and flags: the abstention decision table (spec 01 §6.2; spec 03 §7.3.4; NN3).

Every stage has an abstention path; every abstention is a value with a reason code, a
one-sentence message for a chemist, a remedy they can act on, and the measured evidence.
No sentinels, ever: a refused quantity is `value: null` + `refusal`, never 0 / -1 / omitted.
Pure module: dataclasses in, dataclasses out.
"""

from dataclasses import dataclass, field

# Thresholds (chosen; echoed into the result as `chosen` gate_thresholds)
CLIP_UNUSABLE = 0.40         # spec 01 §6.2 row 4 (plate-level abstain)  [spec 03 says 0.60 -> D-014]
CLIP_PHOTOMETRY = 0.15       # spec 03 §7.3.4 E_CLIP_PHOTOMETRY (plate: positions only)
CLIP_LANE_ABSTAIN = 0.20     # spec 01 row 5: lane abstained
CLIP_LANE_AREA = 0.02        # spec 01 row 6: areas suppressed for the lane
OVERRUN_MAX = 0.02           # spec 03 §7.3.4 E_FRAME_OVERRUN
RECT_RESIDUAL_MAX_FRAC = 0.015  # spec 01 row 3: reprojection residual / plate width
PX_PER_LANE_MIN = 10.0       # spec 01 row 13
VIF_ABSTAIN = 6.0            # spec 01 row 14
VIF_WARN = 4.0               # spec 01 §2.1
ORIGIN_CI_MAX_FRAC = 0.08    # spec 01 row 10: ci90 width of origin > 8% plate height


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str
    remedy: str
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Flag:
    code: str
    severity: str        # info | warn | block
    message: str
    remedy: str
    evidence: dict = field(default_factory=dict)


def gate_thresholds() -> dict[str, float]:
    return {
        "green_clip_max": CLIP_PHOTOMETRY,
        "green_clip_unusable": CLIP_UNUSABLE,
        "lane_clip_abstain": CLIP_LANE_ABSTAIN,
        "lane_clip_area_max": CLIP_LANE_AREA,
        "frame_overrun_max": OVERRUN_MAX,
        "px_per_lane_min": PX_PER_LANE_MIN,
        "vif_abstain": VIF_ABSTAIN,
        "origin_ci90_max_frac": ORIGIN_CI_MAX_FRAC,
    }


def e_no_plate(score: float) -> Refusal:
    return Refusal("E_NO_PLATE", "No TLC plate was found in this image.",
                   "Photograph the plate on a dark background with the plate filling 60-80% of the frame.",
                   {"plate_area_frac": round(score, 4)})


def e_clip_unusable(frac: float) -> Refusal:
    return Refusal("E_CLIP_UNUSABLE", f"{frac:.0%} of the plate is saturated in the green channel; nothing on this image can be measured.",
                   "Re-shoot at 1/3 exposure (EV -1.5) or move the plate further from the lamp; keep the framing.",
                   {"green_clip_frac_in_plate": round(frac, 4), "gate": CLIP_UNUSABLE})


def e_clip_photometry(frac: float) -> Refusal:
    return Refusal("E_CLIP_PHOTOMETRY", f"Areas suppressed: {frac:.1%} of in-plate green pixels are clipped at 255.",
                   "Re-shoot 1-2 stops darker. Positions below are still valid; areas are not.",
                   {"green_clip_frac_in_plate": round(frac, 4), "gate": CLIP_PHOTOMETRY})


def e_lane_clip(lane: int, frac: float) -> Refusal:
    # M-021: lanes are 0-indexed in the data and 1-indexed on every screen; prose uses the screen's
    # numbering, evidence keeps the index, and `lane_display` is what copy interpolates.
    return Refusal("EXPOSURE_CLIPPED", f"Lane {lane + 1} is {frac:.0%} saturated; nothing in it can be measured.",
                   "Re-shoot darker; this lane sits under the brightest part of the illumination.",
                   {"lane": lane, "lane_display": lane + 1, "lane_clip_frac": round(frac, 4), "gate": CLIP_LANE_ABSTAIN})


def e_area_clip(lane: int, frac: float) -> Refusal:
    return Refusal("AREA_UNRELIABLE_CLIP", f"Lane {lane + 1}: {frac:.1%} of the lane band is clipped; areas withheld, positions kept.",
                   "Re-shoot darker to recover areas.",
                   {"lane": lane, "lane_display": lane + 1, "lane_clip_frac": round(frac, 4), "gate": CLIP_LANE_AREA})


def e_box_clip(spot_id: str) -> Refusal:
    return Refusal("CLIPPED", "The green channel saturates inside this spot's box; its area cannot be measured.",
                   "Re-shoot at EV -1.5.", {"spot": spot_id})


def e_no_front() -> Refusal:
    return Refusal("E_NO_FRONT", "Rf cannot be computed without a drawn solvent front. Rst is reported instead.",
                   "Draw the front in pencil immediately after development, before imaging.", {})


def e_no_origin(n_dots: int, needed: int) -> Refusal:
    return Refusal("E_NO_ORIGIN", f"Origin dots were found in only {n_dots} lane(s); {needed} are required to place the origin.",
                   "Mark the origin line in pencil, or set it manually in the review screen.",
                   {"origin_dots_found": n_dots, "required": needed})


def e_origin_uncertain(ci_frac: float) -> Refusal:
    return Refusal("ORIGIN_UNCERTAIN", f"The origin row is uncertain to {ci_frac:.1%} of the plate height; Rst is withheld.",
                   "Draw the origin line in pencil before spotting.", {"ci90_frac": round(ci_frac, 4), "gate": ORIGIN_CI_MAX_FRAC})


def e_no_reference(labels: list[str]) -> Refusal:
    return Refusal("E_NO_REFERENCE_LANE", "No standard lane (sd/S) is available to anchor Rst; positions are reported as row fractions only.",
                   "Include a standard lane on every plate, or set the reference lane in the review screen.",
                   {"lane_labels": ",".join(labels)})


def e_streak(lane: int, reason: str) -> Refusal:
    return Refusal("E_STREAK", f"Lane {lane + 1} is streaking ({reason}); the position of a streak is not defined.",
                   "Reduce loading or change the solvent system.", {"lane": lane, "lane_display": lane + 1})


def e_uncalibrated(n_labelled: int | None = None, required: int = 30) -> Refusal:
    """The pure pipeline does not know how many plates are labelled — that lives in the label store —
    so it emits the requirement only. A caller that knows the count passes it, and the count then
    travels with the refusal instead of being invented at render time."""
    ev: dict = {"required": required}
    if n_labelled is not None:
        ev["labelled_plates"] = n_labelled
    return Refusal("E_UNCALIBRATED", "Confidence is not calibrated for this pipeline version; treat ensemble agreement as ordinal evidence, not a probability.",
                   f"Complete {required} labelled plates (Gate 6) and run calibration (Gate 7).", ev)


def e_frame_overrun(edge: str, frac: float) -> Refusal:
    return Refusal("E_FRAME_OVERRUN", f"The plate is cut off at the {edge} edge ({frac:.0%} of that border).",
                   "Re-shoot with the whole plate, including the origin line, inside the frame.",
                   {"edge": edge, "overrun": round(frac, 4), "gate": OVERRUN_MAX})


def e_resolution(px_per_lane: float) -> Refusal:
    return Refusal("SPOT_RESOLUTION", f"Lanes are only {px_per_lane:.0f} px wide; spots cannot be resolved below {PX_PER_LANE_MIN:.0f} px/lane.",
                   "Photograph closer or export at native resolution.", {"px_per_lane": round(px_per_lane, 1)})


def e_noise_structured(vif: float) -> Refusal:
    return Refusal("NOISE_STRUCTURED", f"The plate's residual is dominated by structured artefacts (VIF {vif:.1f}), not noise.",
                   "Check for glare, fingerprints or heavy compression; re-shoot in the cabinet with a clean plate.",
                   {"vif": round(vif, 3), "gate": VIF_ABSTAIN})


def e_null_not_constructible(clean_cols: int, needed: int, gutter_max_z: float) -> Refusal:
    """M-030: the S1 surrogate null is built from the gutters between the lanes, which assumes the
    gutters hold no chemistry. When bands are broad relative to the lane pitch they bleed across the
    gutters, and the null then contains the very signal it is meant to test against — every p
    saturates at 1 and nothing is ever accepted. Reporting the lane as empty in that state is a
    silent false negative; this refusal is what the system says instead."""
    return Refusal(
        "E_NULL_NOT_CONSTRUCTIBLE",
        f"There is no band-free strip on this plate to test against: the gaps between the lanes carry "
        f"signal up to {gutter_max_z:.0f} sigma, so a band here cannot be distinguished from the plate's "
        f"own background by this method.",
        "Spot the lanes further apart, or load less so the bands stay inside their lanes; a plate with "
        "clear gaps between the lanes can be tested properly.",
        {"clean_gutter_columns": clean_cols, "columns_needed": needed, "gutter_max_z": round(gutter_max_z, 1)},
    )


def e_lane_count_unknown() -> Refusal:
    return Refusal("E_LANE_COUNT_UNKNOWN", "The number of lanes could not be read from the plate.",
                   "Enter the number of lanes.", {})


def e_in_annotation_band(spot_id: str) -> Refusal:
    return Refusal("IN_ANNOTATION_BAND", "This feature lies in the handwriting band; ink is indistinguishable from chemistry by density (F7).",
                   "Keep writing out of the chromatography zone.", {"spot": spot_id})


def e_vlm_unconfirmed(y_frac: float, support: float, required: float) -> Refusal:
    return Refusal("E_VLM_UNCONFIRMED", "The model reported a band here; the pixels do not support one.",
                   f"Inspect this lane at {y_frac:.0%} of plate height in the review screen.",
                   {"proposed_y_frac": round(y_frac, 4), "pixel_support_sigma": round(support, 3), "required_sigma": required})
