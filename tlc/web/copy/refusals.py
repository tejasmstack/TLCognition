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
        "{labelled_plates} plates are labelled; {required} are required before calibration can be measured.",
        "Review plates in the review screen; calibration is computed once the label set is complete.",
        ("labels",),
    ),
    "E_FRAME_OVERRUN": Copy(
        "The plate is cut off at the {edge} edge",
        "The visible part of the plate was located.",
        "Positions and Rst are not reported.",
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
        "Lane count not read",
        "The plate outline is measured.",
        "Lanes and bands are not reported.",
        "The number of lanes could not be read from the plate.",
        "Enter the number of lanes on the upload screen and re-run.",
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
    c = COPY.get(code)
    if c is None:
        return {"code": code, "title": refusal.get("message", code), "measured": "", "withheld": "",
                "why": refusal.get("message", ""), "remedy": refusal.get("remedy", ""), "actions": ()}
    fmt = {k: (_pct(v) if k in PERCENT_KEYS else v) for k, v in ev.items()}

    class _Safe(dict):
        def __missing__(self, k):
            return "—"

    s = _Safe(fmt)
    return {"code": code, "title": c.title.format_map(s), "measured": c.measured.format_map(s),
            "withheld": c.withheld.format_map(s), "why": c.why.format_map(s), "remedy": c.remedy.format_map(s),
            "actions": c.actions}
