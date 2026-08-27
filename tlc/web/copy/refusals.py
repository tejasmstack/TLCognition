"""Refusal microcopy (spec 04 §11.4.3): the backend emits a code and numbers; this file owns the
sentence. Four parts, always in this order: measured -> withheld -> why (number + threshold) ->
physical action. No apology, no exclamation, no blame, never the word "error".
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Copy:
    title: str
    measured: str
    withheld: str
    why: str          # may use {placeholders} from refusal.evidence (as percent-ready floats)
    remedy: str
    actions: tuple[str, ...] = ("retake",)


def _num(x):
    """The result schema stores evidence as floats, so counts arrive as 30.0. Print a whole number
    as a whole number: "30.0 labelled plates" reads like a measurement of something."""
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def _pct(x) -> str:
    try:
        return f"{100.0 * float(x):.0f}%"
    except (TypeError, ValueError):
        return "—"


COPY: dict[str, Copy] = {
    "E_CLIP_PHOTOMETRY": Copy(
        "Photometry refused — the photograph is over-exposed",
        "Band positions and Rst are measured and reported.",
        "Areas, amplitudes and the fraction of each lane are not reported.",
        "{green_clip_frac_in_plate} of the plate is at the sensor's maximum in the green channel; the threshold is {gate}. "
        "Where the background is clipped, a spot's depth cannot be measured against it.",
        "Re-shoot 1–2 stops darker, keeping the framing, and compare.",
    ),
    "E_CLIP_UNUSABLE": Copy(
        "Nothing on this photograph can be measured",
        "The plate outline and its tilt were measured.",
        "Positions, areas and Rst are not reported.",
        "{green_clip_frac_in_plate} of the plate is saturated in the green channel; above {gate} the spots themselves are clipped.",
        "Re-shoot at about a third of the exposure (EV −1.5) or move the plate further from the lamp.",
    ),
    "EXPOSURE_CLIPPED": Copy(
        "Lane {lane} is saturated",
        "Other lanes on this plate are measured normally.",
        "Nothing in this lane is reported.",
        "{lane_clip_frac} of the lane is at the sensor's maximum; the threshold is {gate}.",
        "Re-shoot darker; this lane sits under the brightest part of the illumination.",
    ),
    "AREA_UNRELIABLE_CLIP": Copy(
        "Lane {lane}: positions kept, areas withheld",
        "Band positions in this lane are measured.",
        "Areas in this lane are not reported.",
        "{lane_clip_frac} of the lane band is clipped; areas are withheld above {gate}.",
        "Re-shoot darker to recover areas.",
    ),
    "CLIPPED": Copy(
        "This band's area is not measured",
        "The band's position is measured.",
        "Its area and amplitude are not reported.",
        "The green channel saturates inside the band's box.",
        "Re-shoot at EV −1.5.",
    ),
    "E_NO_FRONT": Copy(
        "No solvent front on this plate — Rst is reported, Rf is not",
        "Positions relative to the standard lane (Rst) are measured.",
        "Rf is not reported.",
        "No drawn front line is present; any Rf would depend on an assumed front position.",
        "Draw the front in pencil immediately after development, before imaging.",
        ("method",),
    ),
    "E_NO_ORIGIN": Copy(
        "Origin not located — Rst withheld",
        "Band positions in pixels are measured.",
        "Rst is not reported.",
        "Origin dots were found in {origin_dots_found} lane(s); {required} are required.",
        "Mark the origin line in pencil, or set it in the review screen.",
        ("review",),
    ),
    "ORIGIN_UNCERTAIN": Copy(
        "Origin uncertain — Rst withheld",
        "Band positions in pixels are measured.",
        "Rst is not reported.",
        "The origin row is uncertain to {ci90_frac} of the plate height; the limit is {gate}.",
        "Draw the origin line in pencil before spotting.",
        ("review",),
    ),
    "E_NO_REFERENCE_LANE": Copy(
        "No standard lane — positions are plate-relative only",
        "Band positions as a fraction of the plate are measured.",
        "Rst is not reported.",
        "No lane is labelled as a standard (sd/S), so there is nothing to anchor Rst to.",
        "Include a standard lane on every plate, or set the reference lane in the review screen.",
        ("review",),
    ),
    "E_STREAK": Copy(
        "Lane {lane} is streaking — no discrete band position",
        "The lane's densitogram is shown for inspection.",
        "Band positions and areas in this lane are not reported.",
        "Density is spread along the lane rather than concentrated in peaks; a streak has no single position.",
        "Reduce loading or change the solvent system, then re-run.",
    ),
    "E_UNCALIBRATED": Copy(
        "Confidence is not yet calibrated",
        "Ensemble agreement (n of K variants) is reported for every band.",
        "No probability is reported.",
        "No calibration map is fitted for this pipeline version. {labelled_plates_clause}"
        "{required} labelled plates are required before one can be measured.",
        "Review plates in the review screen; calibration is computed once the label set is complete.",
        ("labels",),
    ),
    "E_FRAME_OVERRUN": Copy(
        "The plate is cut off at the {edge} edge",
        "Positions and Rst are measured on the part of the plate that is inside the frame.",
        "Nothing is reported about the part outside the frame: bands there are missing from this result, "
        "and the band count is a lower bound.",
        "{overrun} of the {edge} border touches the frame; the limit is {gate}.",
        "Re-shoot with the whole plate, including the origin line, inside the frame.",
    ),
    "E_NO_PLATE": Copy(
        "No plate found in this image",
        "The image was decoded and its size recorded.",
        "Nothing else is reported.",
        "The largest bright green region covers {plate_area_frac} of the frame.",
        "Photograph the plate on a dark background with the plate filling 60–80% of the frame.",
    ),
    "SPOT_RESOLUTION": Copy(
        "Lanes are too narrow to resolve bands",
        "The plate outline and lane positions are measured.",
        "Band positions are not reported.",
        "Lanes are {px_per_lane} px wide; at least 10 px per lane is required.",
        "Photograph closer, or export at native resolution.",
    ),
    "NOISE_STRUCTURED": Copy(
        "The residual is structured, not noise",
        "The plate outline and lane positions are measured.",
        "Band detection is not reported.",
        "The variance inflation factor is {vif}; above 6 the null model does not hold.",
        "Check for glare, fingerprints or heavy compression; re-shoot in the cabinet with a clean plate.",
    ),
    "E_LANE_COUNT_UNKNOWN": Copy(
        "Lane count not read — lanes are assumed, not measured",
        "Band positions are measured, and densitograms are drawn over an assumed four-lane grid.",
        "Lane labels, the standard-lane anchor and therefore Rst are not reported; which lane a band "
        "belongs to is provisional.",
        "The number of lanes could not be read from the plate, and it is never inferred from the signal.",
        "Enter the number of lanes and their labels on the upload screen and re-run.",
        ("upload",),
    ),
    "IN_ANNOTATION_BAND": Copy(
        "Feature in the handwriting band",
        "Its position is recorded.",
        "It is not counted as a band.",
        "It lies in the annotation band, where ink is indistinguishable from chemistry by density.",
        "Keep writing out of the chromatography zone.",
    ),
    "E_VLM_UNCONFIRMED": Copy(
        "Model-proposed band not supported by the pixels",
        "The pixels at the proposed position were measured.",
        "No band is reported there.",
        "Pixel support is {pixel_support_sigma}σ; {required_sigma}σ is required.",
        "Inspect this lane in the review screen.",
        ("review",),
    ),
}

PERCENT_KEYS = {"green_clip_frac_in_plate", "gate", "lane_clip_frac", "ci90_frac", "overrun", "plate_area_frac"}


def render(refusal: dict) -> dict:
    """Interpolate a refusal object into its copy. Unknown codes get a generic honest card."""
    code = refusal.get("code", "")
    ev = dict(refusal.get("evidence") or {})
    if "lane_display" in ev:
        ev = {**ev, "lane": ev["lane_display"]}   # M-021: prose counts lanes the way the screen does
    c = COPY.get(code)
    if c is None:
        return {"code": code, "title": refusal.get("message", code), "measured": "", "withheld": "",
                "why": refusal.get("message", ""), "remedy": refusal.get("remedy", ""), "actions": ()}
    fmt = {k: (_pct(v) if k in PERCENT_KEYS else _num(v)) for k, v in ev.items()}
    if code == "E_UNCALIBRATED":
        n = ev.get("labelled_plates")
        fmt["labelled_plates_clause"] = "" if n is None else f"{int(n)} plate(s) are labelled so far; "

    missing: list[str] = []

    class _Safe(dict):
        def __missing__(self, k):
            # a placeholder with nothing behind it is a broken sentence; record it so CI can see it
            missing.append(k)
            return "—"

    s = _Safe(fmt)
    out = {"code": code, "title": c.title.format_map(s), "measured": c.measured.format_map(s),
           "withheld": c.withheld.format_map(s), "why": c.why.format_map(s), "remedy": c.remedy.format_map(s),
           "actions": c.actions}
    out["missing_placeholders"] = sorted(set(missing))
    return out
