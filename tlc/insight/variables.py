"""chem[] / capture[] extraction from a Result document (spec 02 §1). The separation is the design:
a capture variable may only ever appear as a confound, never as a chemical cause."""

from dataclasses import dataclass, field

# §1.3 algebraic-dependence blacklist is enforced through derives_from tokens.
CHEM_DERIVES = {
    "rst": {"y_band", "y_origin", "y_anchor"}, "area_od": {"od", "fit"}, "snr": {"od", "sigma"},
    "band_count": {"detection"}, "tail_factor": {"fit"}, "peak_od": {"od", "fit"},
    "area_ratio": {"od", "fit"},
}
CAPTURE_DERIVES = {
    "sigma_od": {"od", "sigma"}, "green_clip_frac": {"exposure"}, "lane_px": {"resolution"}, "mpix": {"resolution"},
    "tilt_deg": {"geometry"}, "plate_area_frac": {"geometry"}, "focus_metric": {"optics"}, "capture_order": {"index"},
}
STANDARD_LABELS = ("sd", "s")
REACTION_LABELS = ("r",)
COSPOT_LABELS = ("co",)
SM_LABELS = ("s",)

SNR_GATE = 5.0          # §2 H03: counts are never reported at 3σ
AGREE_GATE = 0.60       # §4.4 MDE gate on any input band
RST_MDE = 0.05          # §4.4 Rst difference gate
ORIGIN_RST_MAX = 0.08   # §2 H04


@dataclass(frozen=True)
class Band:
    id: str
    lane_index: int
    lane_label: str
    lane_role: str            # S | co | R | sd | blank | unknown — never inferred from signal (F10)
    rst: float | None
    rst_ci: tuple[float, float] | None
    y_frac: float | None
    agree: float | None
    snr: float | None
    area_od: float | None
    peak_od: float | None
    clip_frac: float
    tail_factor: float | None
    fwhm_px: float | None
    shape_class: str
    in_annotation_band: bool
    status: str

    def to_dict(self) -> dict:
        return {"id": self.id, "lane": self.lane_index, "role": self.lane_role, "rst": self.rst, "rst_ci": self.rst_ci,
                "agree": self.agree, "snr": self.snr, "area_od": self.area_od, "peak_od": self.peak_od,
                "clip_frac": self.clip_frac, "tail_factor": self.tail_factor, "shape_class": self.shape_class,
                "in_annotation_band": self.in_annotation_band}


@dataclass(frozen=True)
class PlateVars:
    run_id: str
    image_sha: str
    created_at: str
    campaign_id: str | None
    reaction_time_h: float | None
    solvent_system_id: str | None
    operator: str | None
    n_lanes: int
    lane_roles: list[str]
    bands: list[Band]
    quantified_lanes: list[int]
    capture: dict = field(default_factory=dict)
    photometry_mode: str = "full"
    anchor_spot_id: str | None = None
    quarantined: str | None = None       # N5: a positive declared-blank lane quarantines the plate

    def bands_in(self, role: str, gated: bool = True) -> list[Band]:
        out = [b for b in self.bands if b.lane_role == role]
        if gated:
            out = [b for b in out if (b.agree or 0) >= AGREE_GATE and (b.snr or 0) >= SNR_GATE
                   and not b.in_annotation_band and b.status == "confirmed"]
        return sorted(out, key=lambda b: -(b.rst if b.rst is not None else b.y_frac or 0))


def lane_role(label: str) -> str:
    lab = (label or "").strip().lower()
    if lab in STANDARD_LABELS and lab == "sd":
        return "sd"
    if lab in ("s",):
        return "S"
    if lab in COSPOT_LABELS:
        return "co"
    if lab in REACTION_LABELS:
        return "R"
    if lab in ("blank", "-", "empty"):
        return "blank"
    return "unknown"


def _shape_class(spot: dict, streaking: bool) -> str:
    if streaking or spot["status"] == "suppressed_streak":
        return "streak"
    tau, sig = (spot.get("emg_tau_px") or {}).get("value"), (spot.get("emg_sigma_px") or {}).get("value")
    if tau and sig and sig > 0 and tau / sig > 1.5:
        return "comet"
    if spot["peak_model"] == "emg":
        return "emg"
    return "gaussian"


def extract(result: dict, meta: dict | None = None) -> PlateVars:
    """Build the two disjoint variable vectors from one Result document. `meta` carries the
    operator-declared, never-measured fields (campaign_id, reaction_time_h, solvent_system_id)."""
    meta = meta or {}
    lanes = {L["index"]: L for L in result["lanes"]}
    roles = [lane_role(lanes[i]["label"]) if i in lanes else "unknown" for i in range(len(lanes))]
    bands_ann = result.get("annotation_bands") or []
    ann = [(b["y0_frac"]["value"], b["y1_frac"]["value"]) for b in bands_ann
           if b["y0_frac"]["value"] is not None and b["y1_frac"]["value"] is not None]
    qc, geo, phot = result["capture_qc"], result["geometry"], result["photometry"]
    bands = []
    for s in result["spots"]:
        li = s["lane_index"]
        L = lanes.get(li, {})
        yf = s["y_frac"]["value"]
        in_ann = any(y0 <= (yf or -1) <= y1 for y0, y1 in ann) or "overlaps_annotation_band" in s["flags"]
        tau, sig = (s.get("emg_tau_px") or {}).get("value"), (s.get("emg_sigma_px") or {}).get("value")
        streaking = bool((L.get("is_streaking") or {}).get("value"))
        amp = s["amplitude_od"]["value"]
        bands.append(Band(
            id=s["id"], lane_index=li, lane_label=L.get("label", "UNREADABLE"), lane_role=lane_role(L.get("label", "")),
            rst=s["rst"]["value"], rst_ci=tuple(s["rst"]["ci95"]) if s["rst"].get("ci95") else None, y_frac=yf,
            agree=s["ensemble_agreement"]["value"], snr=s["snr"]["value"], area_od=s["area_od_px"]["value"],
            peak_od=amp, clip_frac=1.0 if "clipped_neighbourhood" in s["flags"] else 0.0,
            tail_factor=(tau / sig) if (tau and sig and sig > 0) else None,
            fwhm_px=(s.get("fwhm_px") or {}).get("value"), shape_class=_shape_class(s, streaking),
            in_annotation_band=in_ann, status=s["status"]))
    capture = {
        "sigma_od": phot["sigma_od"]["value"], "green_clip_frac": qc["green_clip_frac_in_plate"]["value"],
        "mpix": result["image"]["width_px"] * result["image"]["height_px"] / 1e6,
        "lane_px": (2 * lanes[0]["half_width_px"]["value"]) if lanes else None,
        "tilt_deg": qc["tilt_deg"]["value"], "plate_area_frac": qc["plate_area_frac"]["value"],
        "focus_metric": qc["focus_metric"]["value"], "rectified_h": geo["rectified_shape"][0],
        "capture_order": meta.get("capture_order"),
    }
    return PlateVars(
        run_id=result["run_id"], image_sha=result["image"]["sha256"], created_at=result["created_at"],
        campaign_id=meta.get("campaign_id"), reaction_time_h=meta.get("reaction_time_h"),
        solvent_system_id=meta.get("solvent_system_id"), operator=meta.get("operator"),
        n_lanes=len(lanes), lane_roles=roles, bands=bands,
        quantified_lanes=[i for i, L in lanes.items() if L["quantified"]], capture=capture,
        photometry_mode=phot["photometry_mode"],
        anchor_spot_id=(result["reference"].get("rst_anchor") or {}).get("spot_id"))
