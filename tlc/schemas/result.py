"""Frozen result JSON schema (v1) for the TLC plate readout system.

Normative source: ``TLC_build_brief/specs/03_backend.md`` §7.3 (structure, field
reference, worked example) plus §7.2.2 item 6 (float canonicalisation). This module
is THE contract of the build: everything else produces or consumes instances of
:class:`Result`. Models are frozen (immutable) and reject unknown keys.

Float canonicalisation (§7.2.2 item 6), as implemented
------------------------------------------------------
The spec's rule is: "positions and fractions to 6 dp, OD values to 6 dp,
probabilities to 4 dp, costs to 6 dp". A "probability" cannot be identified
structurally: unit ``"1"`` covers probabilities (rst, ensemble_agreement,
confidence) as well as other dimensionless quantities (snr, focus_metric,
origin_support_sigma). The implemented interpretation — chosen because the rule's
purpose is byte-stable canonicalisation, which any fixed per-unit rule satisfies:

- every float ``Q.value`` (and each ``Q.ci95`` bound) with unit ``"1"`` rounds to
  4 dp; with any other unit it rounds to 6 dp (this covers px, frac, OD, OD*px,
  deg, USD, s, B);
- bare (un-enveloped) agreement/probability floats — ``AnnotationBand.vlm_agreement``,
  ``Lane.label_agreement``, ``VLMField.agreement``, ``CorrelationBlock.fdr_target``,
  ``CorrelationFinding.p_raw`` / ``p_adjusted`` — round to 4 dp;
- every other bare float in the JSON output (corners, homography, densitogram
  preview, evidence values, effects, costs, ...) rounds to 6 dp.

Rounding is applied only in JSON serialisation (``model_dump(mode="json")`` and
``model_dump_json``); python-mode dumps keep full precision. Floats hidden inside
``Any``-typed VLM payloads (``VLMField.value`` / ``samples`` / ``disagreements``)
and inside the verbatim ``config_document`` cannot be reached structurally; their
canonical form is the producer's responsibility.

NaN/inf are rejected on every typed float field (``allow_inf_nan=False`` /
explicit validators): per §7.2.2 item 5 a NaN in the output is a bug — the schema
requires ``null`` plus a refusal instead.

This module lives outside ``tlc/pipeline/`` and may import pydantic; it must not
import anything under ``tlc.pipeline``.
"""

import math
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_serializer,
    model_validator,
)

# ---------------------------------------------------------------------------
# Canonicalisation primitives
# ---------------------------------------------------------------------------


def _round6(v: float) -> float:
    return round(v, 6)


def _round4(v: float) -> float:
    return round(v, 4)


#: Finite float, no serialisation rounding of its own (used where a parent
#: serializer applies the unit-dependent rule, e.g. Q.ci95).
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]

#: Finite float rounded to 6 dp in JSON output (positions, fractions, OD, costs).
F6 = Annotated[
    float,
    Field(allow_inf_nan=False),
    PlainSerializer(_round6, return_type=float, when_used="json"),
]

#: Finite float rounded to 4 dp in JSON output (bare probabilities/agreements).
F4 = Annotated[
    float,
    Field(allow_inf_nan=False),
    PlainSerializer(_round4, return_type=float, when_used="json"),
]

Point2 = tuple[F6, F6]
Corners = tuple[Point2, Point2, Point2, Point2]
Row3 = tuple[F6, F6, F6]
Homography = tuple[Row3, Row3, Row3]

# ---------------------------------------------------------------------------
# §7.3.1 The provenance envelope
# ---------------------------------------------------------------------------

Provenance = Literal[
    "measured",  # derived from this image's pixels by deterministic code
    "chosen",  # a configured constant or an operator input; a decision, not an observation
    "inferred",  # derived from other measured values under a stated model/assumption
    "refused",  # the system declined to produce this value; `refusal` is populated
]

#: Closed unit enum. There is no unitless float in the scientific part of the result.
Unit = Literal["px", "frac", "OD", "OD*px", "deg", "1", "USD", "s", "B"]


class Refusal(BaseModel):
    """NN3: refusal is a value, not a null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str  # stable machine code, e.g. "E_CLIP_PHOTOMETRY"
    message: str  # one sentence for a chemist
    remedy: str  # what the human should DO
    evidence: dict[str, F6 | str] = {}


class Q[T](BaseModel):
    """Quantity envelope (NN2 made structural): every scientific scalar is wrapped."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: T | None  # null iff provenance == "refused"
    unit: Unit
    provenance: Provenance
    method: str | None = None  # e.g. "emg_fit", "poly3_background", "vlm_majority_vote"
    ci95: tuple[FiniteFloat, FiniteFloat] | None = None
    n: int | None = None  # sample size behind ci95, where applicable
    refusal: Refusal | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _enforce_envelope_rules(self) -> "Q[T]":
        if self.provenance == "refused":
            if self.value is not None:
                raise ValueError("provenance == 'refused' requires value to be None")
            if self.refusal is None:
                raise ValueError("provenance == 'refused' requires a populated refusal")
        elif self.value is None:
            raise ValueError("value may be None only when provenance == 'refused'")
        if self.provenance == "inferred" and self.method is None:
            raise ValueError("provenance == 'inferred' requires method to be stated")
        if self.ci95 is not None and self.provenance not in ("measured", "inferred"):
            raise ValueError("ci95 is only allowed when provenance is 'measured' or 'inferred'")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("value must be finite; NaN/inf are bugs, refuse instead")
        return self

    def _decimals(self) -> int:
        return 4 if self.unit == "1" else 6

    @field_serializer("value", when_used="json-unless-none")
    def _serialize_value(self, v: Any) -> Any:
        # bool is not a float subclass check: isinstance(True, float) is False.
        if isinstance(v, float):
            return round(v, self._decimals())
        return v

    @field_serializer("ci95", when_used="json-unless-none")
    def _serialize_ci95(self, v: tuple[float, float]) -> list[float]:
        nd = self._decimals()
        return [round(x, nd) for x in v]


# ---------------------------------------------------------------------------
# §7.3.3 image
# ---------------------------------------------------------------------------


class ImageBlock(BaseModel):
    """All measured from the bytes; no envelope needed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str  # of original bytes, pre-decode
    bytes: int  # unit: B
    mime: str  # image/jpeg | image/png | image/heic
    width_px: int  # after EXIF orientation
    height_px: int  # after EXIF orientation
    exif_orientation: int | None  # value found; applied explicitly, never by the decoder
    decoder: str  # e.g. "imageio.v3/pillow-11.0.0"
    original_filename: str | None  # never trusted for anything


# ---------------------------------------------------------------------------
# §7.3.3 capture_qc — the input gate (F1)
# ---------------------------------------------------------------------------


class FrameOverrun(BaseModel):
    """Per-edge overrun fractions; >0.02 on any edge means the plate is cropped in-camera."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    top: Q[float]
    bottom: Q[float]
    left: Q[float]
    right: Q[float]


class CaptureQCBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    green_clip_frac_in_plate: Q[float]  # the F1 gate: fraction of in-plate px with G>=254
    green_clip_frac_frame: Q[float]  # whole frame, for comparison
    black_clip_frac_in_plate: Q[float]  # G<=1
    channel_sat_frac: Q[float]  # any channel >=254
    plate_area_frac: Q[float]  # plate mask / frame
    frame_overrun: FrameOverrun
    tilt_deg: Q[float]  # from the min-area rect
    focus_metric: Q[float]  # var(Laplacian)/mean^2; relative only, note says so
    mean_green_in_plate: Q[float]  # 0-1
    verdict: Literal["ok", "positions_only", "unusable"]
    gates_fired: list[str]  # codes of every gate that changed the verdict
    gate_thresholds: dict[str, Q[float]]  # config thresholds echoed as `chosen` values


# ---------------------------------------------------------------------------
# §7.3.3 geometry
# ---------------------------------------------------------------------------


class GeometryBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corners_src_px: Corners  # measured; order TL,TR,BR,BL after canonical sort
    homography: Homography  # measured — maps rectified -> source
    rectified_shape: tuple[int, int]  # inferred (H,W from corner distances)
    tilt_deg: Q[float]
    idempotency_residual_px: Q[float]  # Gate 2's re-warp check, run every time
    valid_erosion_px: Q[int]  # inferred — derived from tilt_deg, not a constant (M-007)
    valid_frac: Q[float]
    detection_method: str  # e.g. "hsv_bright_green_largest_cc"


# ---------------------------------------------------------------------------
# §7.3.3 annotation_bands — F7: never deterministic
# ---------------------------------------------------------------------------


class AnnotationBand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["header", "label_row", "footer"]
    y0_frac: Q[float]
    y1_frac: Q[float]
    provenance: Literal["vlm", "operator", "convention", "refused"]
    vlm_agreement: F4 | None = None  # 0..1, samples agreeing on the modal band
    vlm_iqr_frac: F6 | None = None  # spread across samples; >0.05 => abstain
    source_detail: str  # e.g. "gemini-2.5-flash-lite-001@prompt:bands/v3"


# ---------------------------------------------------------------------------
# §7.3.3 lanes — F10: count never comes from the signal
# ---------------------------------------------------------------------------


class Lane(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int  # 0-based, left -> right in the rectified frame
    label: str  # closed enum from config + "UNREADABLE"
    label_provenance: Literal["vlm", "operator", "refused"]
    label_agreement: F4 | None = None  # VLM self-consistency, 0..1
    x_center_px: Q[float]  # measured — refined within a +/- window seeded by the VLM
    x_center_frac: Q[float]  # inferred
    half_width_px: Q[int]  # chosen (config lane_halfwidth_frac x lane pitch)
    x_seed_provenance: Literal["vlm", "operator", "uniform_grid"]
    is_empty: Q[bool]  # measured — no feature >= empty_lane_sigma anywhere
    is_streaking: Q[bool]  # measured — F11 streak statistic
    quantified: bool  # false => areas suppressed for this lane
    suppression: Refusal | None = None  # populated iff quantified == false
    at_plate_edge: bool  # the M-007 guard

    @model_validator(mode="after")
    def _suppression_iff_not_quantified(self) -> "Lane":
        if self.quantified and self.suppression is not None:
            raise ValueError("suppression must be None when quantified is true")
        if not self.quantified and self.suppression is None:
            raise ValueError("suppression is mandatory when quantified is false")
        return self


# ---------------------------------------------------------------------------
# §7.3.3 reference — where the scale comes from
# ---------------------------------------------------------------------------


class RstAnchor(BaseModel):
    """NN: an Rst without a named anchor is not a measurement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spot_id: str
    lane_index: int
    lane_label: str
    y_px: Q[float]
    selection_rule: str  # e.g. "highest_confirmed_spot_in_standard_lane" (chosen)
    provenance: Provenance


class ReferenceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin_row_px: Q[float]
    origin_provenance: Literal[
        "detected_dots", "operator", "vlm_proposed_confirmed", "assumed_plate_bottom", "refused"
    ]
    origin_support_sigma: Q[float]  # how many sigma the dots stand out
    origin_dots_found: Q[int]  # per-lane count, out of n_lanes
    front_row_px: Q[float]  # F2: on our corpus this is ALWAYS refused
    front_provenance: Literal["detected_line", "vlm_confirmed", "operator", "absent"]
    front_absent_reason: Refusal | None = None
    rst_anchor: RstAnchor | None = None
    rf_available: bool
    rf_unavailable_reason: Refusal | None = None


# ---------------------------------------------------------------------------
# §7.3.3 photometry
# ---------------------------------------------------------------------------


class PhotometryBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_channel: str  # chosen: "green" (F: green carries the signal)
    background_model: str  # chosen: "poly3" primary (D-014)
    background_radius_px: Q[int]  # chosen; primary member only
    od_transform: str  # chosen: "log10(I0/I)"; kubelka_munk not permitted (F5)
    sigma_od: Q[float]  # measured once, raw analysable band, before spot masking (F4)
    sigma_method: str  # chosen: "mad_1.4826_prespot"
    sigma_stability_across_radii: Q[float]  # Gate 3 requires <= 0.15
    clipped_px_frac_in_analysable: Q[float]
    photometry_mode: Literal["full", "positions_only", "refused"]  # inferred


# ---------------------------------------------------------------------------
# §7.3.3 densitograms — one per lane
# ---------------------------------------------------------------------------


class DensitogramYAxis(BaseModel):
    """Implicit axis, not an array."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int
    stop: int
    step: int


class Densitogram(BaseModel):
    """The preview is non-authoritative (decimated block-mean, rounded by the
    producer to 5 dp, for the frontend only). Nothing may be computed from it;
    the full float32 trace behind ``ref`` is authoritative."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lane_index: int
    y_px: DensitogramYAxis
    unit: Unit
    sampling: str  # e.g. "mean over valid px in [x_center-hw, x_center+hw]"
    n_valid_columns: int
    ref: str  # h5 reference; full float32, authoritative
    sha256: str
    preview: list[F6] = Field(max_length=512)


# ---------------------------------------------------------------------------
# §7.3.3 spots — the table
# ---------------------------------------------------------------------------


class VLMConfirmation(BaseModel):
    """F9: a VLM-proposed position and whether the pixels supported it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposed_y_frac: F6
    pixel_support_sigma: F6
    confirmed: bool


class Spot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str  # sp_NN, stable within a run, assigned in (lane, y) order
    lane_index: int
    status: Literal["confirmed", "candidate", "rejected", "proposed_unconfirmed", "suppressed_streak"]
    y_px: Q[float]  # rectified-frame row; ci95 from the EMG fit covariance
    y_frac: Q[float]  # inferred: y_px / H
    rst: Q[float]  # inferred: (y_origin - y_spot)/(y_origin - y_anchor)
    rst_reference_spot_id: str | None = None  # mandatory when rst.value is not null
    rf: Q[float] | None = None  # refused on this corpus (F2)
    peak_model: Literal["emg", "gaussian", "none"]
    emg_sigma_px: Q[float] | None = None  # Gaussian width component
    emg_tau_px: Q[float] | None = None  # tailing constant; tau/sigma > tail_ratio_max => streak
    fwhm_px: Q[float] | None = None  # numerically from the fitted EMG, not a closed form
    amplitude_od: Q[float]  # refused when photometry_mode == "positions_only"
    area_od_px: Q[float]  # integral of the fitted EMG
    area_frac_of_lane: Q[float]  # the number a chemist wants; refused with the area
    snr: Q[float]  # amplitude / sigma_od; computed even in positions_only but flagged
    ensemble_agreement: Q[float]  # fraction of the pipelines placing a peak within match_tol_px
    ensemble_n_total: int
    ensemble_n_hit: int
    ensemble_y_spread_px: Q[float] | None = None  # sd of matched positions across pipelines
    confidence: Q[float]  # calibrated probability; provenance="refused" before Gate 7
    calibration_version: str | None = None
    fit_residual_rms_od: Q[float] | None = None
    vlm_proposed: bool  # true if this position originated as a VLM hypothesis
    vlm_confirmation: VLMConfirmation | None = None
    flags: list[str]  # e.g. near_origin, overlaps_annotation_band, clipped_neighbourhood

    @model_validator(mode="after")
    def _rst_requires_named_reference(self) -> "Spot":
        # §7.2.6 point 3: an Rst without a named reference is not a measurement.
        if self.rst.value is not None and self.rst_reference_spot_id is None:
            raise ValueError("rst_reference_spot_id is mandatory when rst.value is not null")
        return self


# ---------------------------------------------------------------------------
# §7.3.3 flags (plate-level)
# ---------------------------------------------------------------------------


class Flag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str  # reserved codes listed in §7.3.3
    severity: Literal["info", "warn", "block"]
    message: str
    remedy: str
    evidence: dict[str, F6 | str | list[F6]] = {}


# ---------------------------------------------------------------------------
# §7.3.5 correlations
# ---------------------------------------------------------------------------


class CorrelationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str
    statement: str | None = None
    n_plates: int
    n_min_required: int
    verdict: Literal["supported", "not_supported", "insufficient_data"]
    effect: F6 | None = None
    ci95: tuple[F6, F6] | None = None
    p_raw: F4 | None = None
    p_adjusted: F4 | None = None
    adjustment: str | None = None
    confounds_checked: list[str]
    confounds_unresolved: list[str]
    suppressed_reason: Refusal | None = None


class CorrelationBlock(BaseModel):
    """Fixed, pre-registered hypothesis list; hiding the suppressed list is how an
    FDR control becomes theatre."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hypotheses_tested: int
    adjustment: str  # e.g. "benjamini_hochberg"
    fdr_target: F4
    findings: list[CorrelationFinding]
    suppressed: list[CorrelationFinding]


# ---------------------------------------------------------------------------
# §7.3.3 vlm
# ---------------------------------------------------------------------------


class VLMField(BaseModel):
    """One structured field read by the VLM, with its self-consistency evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: Any
    agreement: F4 | None = None
    samples: list[Any] | None = None
    disagreements: list[Any] | None = None
    iqr_frac: F6 | None = None
    flagged_for_review: bool | None = None


class VLMBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    mode: Literal["live", "replay", "off"]
    model_id: str | None
    prompt_bundle: dict[str, str]  # e.g. {"bands": "v3", "lanes": "v4", "front": "v2"}
    n_samples: int
    temperature: F6
    fields: dict[str, VLMField]
    cache: dict[str, int | str]  # {"hits": ..., "misses": ..., "bundle_hash": ...}
    cost: dict[str, int | F6]  # token counts and usd
    attempts: int
    retries: int
    degraded: bool


# ---------------------------------------------------------------------------
# §7.3.3 storage — excluded from result_sha256
# ---------------------------------------------------------------------------


class StorageBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    od_h5: str | None
    image: str | None
    preview_png: str | None


# ---------------------------------------------------------------------------
# §7.3.3 provenance
# ---------------------------------------------------------------------------


class ProvenanceBlock(BaseModel):
    """`config_document` is embedded verbatim, not by reference: a config file can
    go missing; a run must remain reproducible from its own record alone."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pipeline_version: str
    schema_version: str
    config_hash: str
    config_ref: str
    config_document: dict[str, Any]
    code_fingerprint: str
    git_commit: str
    git_dirty: bool
    env_fingerprint: str
    lock_hash: str
    libraries: dict[str, str]
    platform_tag: str
    seed: int
    seed_derivation: str  # "int(image_sha256[:16],16) ^ config.seed_salt"
    run_key: str
    vlm_bundle_hash: str | None   # §7.2.1/§7.9.3: null when vlm_mode == "off"; an INPUT to run_key,
    #: so it lives in the record — without it a run cannot recompute its own key (M-028)
    result_sha256: str | None
    od_sha256: str | None
    replay_of: str | None
    superseded_by: str | None
    determinism_tier: Literal["tier1", "tier2"]


# ---------------------------------------------------------------------------
# §7.3.2 Top-level structure
# ---------------------------------------------------------------------------


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"  # fixed per MAJOR
    run_id: str
    image_id: str
    created_at: str  # ISO 8601 string; kept as str for canonical JSON stability
    status: Literal["succeeded", "refused", "degraded"]
    image: ImageBlock
    capture_qc: CaptureQCBlock
    geometry: GeometryBlock
    annotation_bands: list[AnnotationBand]
    lanes: list[Lane]
    reference: ReferenceBlock
    photometry: PhotometryBlock
    densitograms: list[Densitogram]
    spots: list[Spot]
    flags: list[Flag]
    correlations: CorrelationBlock
    vlm: VLMBlock
    refusals: list[Refusal]  # plate-level; NN3 — refusals are values, not nulls
    storage: StorageBlock  # excluded from result_sha256
    provenance: ProvenanceBlock
