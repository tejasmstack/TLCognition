"""Tests for the frozen result JSON schema (spec 03 §7.3, §7.2.2 item 6).

Covers: envelope validator rules (a/b/c), the Spot rst-anchor rule, frozen models,
extra="forbid", NaN/inf rejection, the densitogram preview cap, unit-dependent float
canonicalisation in JSON dumps, a full worked-example Result (positions-only plate,
§7.3.6) with a round-trip, and deterministic schema emission.
"""

import importlib.util
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from tlc.schemas.result import (
    AnnotationBand,
    CaptureQCBlock,
    CorrelationBlock,
    CorrelationFinding,
    Densitogram,
    DensitogramYAxis,
    Flag,
    FrameOverrun,
    GeometryBlock,
    ImageBlock,
    Lane,
    PhotometryBlock,
    ProvenanceBlock,
    Q,
    ReferenceBlock,
    Refusal,
    Result,
    RstAnchor,
    Spot,
    StorageBlock,
    VLMBlock,
    VLMConfirmation,
    VLMField,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _refusal(code="E_TEST", message="msg.", remedy="fix.", evidence=None) -> Refusal:
    return Refusal(code=code, message=message, remedy=remedy, evidence=evidence or {})


def qf(value, unit, provenance="measured", **kw) -> Q[float]:
    return Q[float](value=value, unit=unit, provenance=provenance, **kw)


def qi(value, unit, provenance="measured", **kw) -> Q[int]:
    return Q[int](value=value, unit=unit, provenance=provenance, **kw)


def qb(value, provenance="measured", **kw) -> Q[bool]:
    return Q[bool](value=value, unit="1", provenance=provenance, **kw)


def qref(unit, code, message="msg.", remedy="fix.", evidence=None) -> Q[float]:
    return Q[float](
        value=None,
        unit=unit,
        provenance="refused",
        refusal=_refusal(code=code, message=message, remedy=remedy, evidence=evidence),
    )


def make_spot(**over) -> Spot:
    base = dict(
        id="sp_01",
        lane_index=0,
        status="confirmed",
        y_px=qf(615.2, "px", method="emg_fit", ci95=(613.9, 616.5)),
        y_frac=qf(0.557, "frac", provenance="inferred", method="y_px_over_H"),
        rst=qf(
            0.991,
            "1",
            provenance="inferred",
            method="(y_origin-y_spot)/(y_origin-y_anchor)",
            ci95=(0.983, 0.999),
            n=32,
        ),
        rst_reference_spot_id="sp_07",
        rf=qref("1", "E_NO_FRONT", "No solvent front.", "Draw the front."),
        peak_model="emg",
        emg_sigma_px=qf(8.4, "px", ci95=(7.6, 9.3)),
        emg_tau_px=qf(4.1, "px", ci95=(2.9, 5.6)),
        fwhm_px=qf(22.7, "px", provenance="inferred", method="numeric_fwhm_of_fitted_emg"),
        amplitude_od=qref(
            "OD",
            "E_CLIP_PHOTOMETRY",
            "Photometry suppressed: 34.1% green clipping.",
            "Re-shoot darker.",
            {"green_clip_frac_in_plate": 0.341},
        ),
        area_od_px=qref("OD*px", "E_CLIP_PHOTOMETRY", "Photometry suppressed.", "Re-shoot darker."),
        area_frac_of_lane=qref(
            "frac", "E_CLIP_PHOTOMETRY", "Photometry suppressed.", "Re-shoot darker."
        ),
        snr=qf(7.9, "1", note="detection statistic only"),
        ensemble_agreement=qf(0.78, "1"),
        ensemble_n_total=32,
        ensemble_n_hit=25,
        ensemble_y_spread_px=qf(0.9, "px"),
        confidence=qref(
            "1",
            "E_UNCALIBRATED",
            "Confidence is not calibrated on this instrument yet.",
            "Complete 30 labelled plates (Gate 6) and run calibration (Gate 7).",
            {"labelled_plates": 7, "required": 30},
        ),
        calibration_version=None,
        fit_residual_rms_od=qf(0.0041, "OD"),
        vlm_proposed=False,
        vlm_confirmation=None,
        flags=["clipped_neighbourhood"],
    )
    base.update(over)
    return Spot(**base)


def make_lane(index=0, label="S", **over) -> Lane:
    base = dict(
        index=index,
        label=label,
        label_provenance="vlm",
        label_agreement=1.0,
        x_center_px=qf(151.3, "px", method="colprofile_argmax_in_vlm_window"),
        x_center_frac=qf(0.186, "frac", provenance="inferred", method="x_center_px_over_W"),
        half_width_px=qi(56, "px", provenance="chosen"),
        x_seed_provenance="vlm",
        is_empty=qb(False),
        is_streaking=qb(False),
        quantified=False,
        suppression=_refusal(
            "E_CLIP_PHOTOMETRY",
            "Areas suppressed: 34.1% of in-plate green pixels are clipped at 255.",
            "Re-shoot 1-2 stops darker. Positions remain valid.",
            {"green_clip_frac_in_plate": 0.341, "gate": 0.15},
        ),
        at_plate_edge=False,
    )
    base.update(over)
    return Lane(**base)


def build_worked_example() -> Result:
    """A positions-only plate modeled on the §7.3.6 worked example (plate1, MEHQ-P29)."""
    e_clip = _refusal(
        "E_CLIP_PHOTOMETRY",
        "All photometry suppressed on this plate.",
        "Re-shoot with correct exposure.",
        {"green_clip_frac_in_plate": 0.341},
    )
    e_front = _refusal("E_NO_FRONT", "Rf not reported.", "Draw the front.")
    e_uncal = _refusal(
        "E_UNCALIBRATED",
        "Confidence withheld.",
        "Complete Gate 6 and 7.",
        {"labelled_plates": 7, "required": 30},
    )
    return Result(
        schema_version="1",
        run_id="run_01JB8Q2M7K4X0ZP3RCN9VH6TAD",
        image_id="img_9f2c41ab7de05c83b1a4",
        created_at="2026-08-26T09:14:22.104Z",
        status="degraded",
        image=ImageBlock(
            sha256="9f2c41ab7de05c83b1a4e6072d1f88c5a3b90e14cc7f2a6d55b8e0913f4c72aa",
            bytes=3184922,
            mime="image/jpeg",
            width_px=3024,
            height_px=4032,
            exif_orientation=6,
            decoder="imageio.v3/pillow-11.0.0",
            original_filename="IMG_4471.jpg",
        ),
        capture_qc=CaptureQCBlock(
            green_clip_frac_in_plate=qf(0.341, "frac"),
            green_clip_frac_frame=qf(0.118, "frac"),
            black_clip_frac_in_plate=qf(0.0, "frac"),
            channel_sat_frac=qf(0.352, "frac"),
            plate_area_frac=qf(0.287, "frac"),
            frame_overrun=FrameOverrun(
                top=qf(0.0, "frac"),
                bottom=qf(0.041, "frac"),
                left=qf(0.0, "frac"),
                right=qf(0.0, "frac"),
            ),
            tilt_deg=qf(3.72, "deg"),
            focus_metric=qf(0.0184, "1", note="relative only; not comparable across cameras"),
            mean_green_in_plate=qf(0.897, "1"),
            verdict="positions_only",
            gates_fired=["E_CLIP_PHOTOMETRY", "E_FRAME_OVERRUN"],
            gate_thresholds={
                "green_clip_max": qf(0.15, "frac", provenance="chosen"),
                "green_clip_unusable": qf(0.60, "frac", provenance="chosen"),
                "frame_overrun_max": qf(0.02, "frac", provenance="chosen"),
            },
        ),
        geometry=GeometryBlock(
            corners_src_px=((402.1, 988.7), (2611.4, 845.3), (2698.0, 3719.6), (489.2, 3862.9)),
            homography=(
                (0.7212, -0.0468, 402.1),
                (0.0455, 0.7189, 988.7),
                (0.0, 0.0, 1.0),
            ),
            rectified_shape=(1104, 812),
            tilt_deg=qf(3.72, "deg"),
            idempotency_residual_px=qf(0.21, "px"),
            valid_erosion_px=qi(4, "px", provenance="inferred", method="ceil(2 + tilt_deg*0.55)"),
            valid_frac=qf(0.981, "frac"),
            detection_method="hsv_bright_green_largest_cc",
        ),
        annotation_bands=[
            AnnotationBand(
                kind="header",
                y0_frac=qf(0.0, "frac", provenance="chosen"),
                y1_frac=qf(0.262, "frac", provenance="inferred", method="vlm_median_of_5"),
                provenance="vlm",
                vlm_agreement=0.8,
                vlm_iqr_frac=0.011,
                source_detail="gemini-2.5-flash-lite-001@prompt:bands/v3",
            ),
            AnnotationBand(
                kind="label_row",
                y0_frac=qf(0.901, "frac", provenance="inferred", method="vlm_median_of_5"),
                y1_frac=qf(1.0, "frac", provenance="chosen"),
                provenance="vlm",
                vlm_agreement=1.0,
                vlm_iqr_frac=0.004,
                source_detail="gemini-2.5-flash-lite-001@prompt:bands/v3",
            ),
        ],
        lanes=[
            make_lane(0, "S"),
            make_lane(1, "co", label_agreement=0.8),
            make_lane(2, "R"),
            make_lane(
                3,
                "sd",
                label_agreement=0.8,
                x_center_px=qf(631.8, "px", method="colprofile_argmax_in_vlm_window"),
                x_center_frac=qf(0.778, "frac", provenance="inferred", method="x_center_px_over_W"),
            ),
        ],
        reference=ReferenceBlock(
            origin_row_px=qf(
                958.4, "px", method="origin_dot_blobs_median", ci95=(956.9, 959.8), n=4
            ),
            origin_provenance="detected_dots",
            origin_support_sigma=qf(6.9, "1"),
            origin_dots_found=qi(4, "1"),
            front_row_px=qref(
                "px",
                "E_NO_FRONT",
                "No pencil solvent front is drawn on this plate.",
                "Draw the solvent front in pencil immediately after development, before imaging.",
                {"best_line_candidate_sigma": 1.1, "required_sigma": 4.0, "row_coverage": 0.31},
            ),
            front_provenance="absent",
            front_absent_reason=None,
            rst_anchor=RstAnchor(
                spot_id="sp_07",
                lane_index=3,
                lane_label="sd",
                y_px=qf(611.9, "px"),
                selection_rule="highest_confirmed_spot_in_standard_lane",
                provenance="measured",
            ),
            rf_available=False,
            rf_unavailable_reason=_refusal(
                "E_NO_FRONT",
                "Rf is undefined without a front.",
                "Use the reported Rst, or draw a front and re-image.",
            ),
        ),
        photometry=PhotometryBlock(
            signal_channel="green",
            background_model="poly3",
            background_radius_px=qi(
                97, "px", provenance="chosen", note="0.12 x max(H,W); primary member only"
            ),
            od_transform="log10(I0/I)",
            sigma_od=qf(
                0.011213,
                "OD",
                method="mad_1.4826_prespot",
                note="measured once on the raw analysable band before any spot masking (F4)",
            ),
            sigma_method="mad_1.4826_prespot",
            sigma_stability_across_radii=qf(0.081, "frac"),
            clipped_px_frac_in_analysable=qf(0.229, "frac"),
            photometry_mode="positions_only",
        ),
        densitograms=[
            Densitogram(
                lane_index=i,
                y_px=DensitogramYAxis(start=0, stop=1104, step=1),
                unit="OD",
                sampling="mean over valid px in [x_center-56, x_center+56]",
                n_valid_columns=112,
                ref=f"h5://runs/run_01JB8Q2M7K4X0ZP3RCN9VH6TAD.h5#/densitograms/lane_{i:02d}",
                sha256="4c1e" + "0" * 60,
                preview=[0.0, 0.0, 0.00031, 0.0012, 0.0009],
            )
            for i in range(4)
        ],
        spots=[
            make_spot(),
            make_spot(
                id="sp_06",
                lane_index=2,
                status="proposed_unconfirmed",
                y_px=qref(
                    "px",
                    "E_VLM_UNCONFIRMED",
                    "The model reported a band here; the pixels do not support one.",
                    "Inspect lane R at 42% of plate height in the review screen.",
                    {"proposed_y_frac": 0.420, "pixel_support_sigma": 1.8, "required_sigma": 3.0},
                ),
                y_frac=qref("frac", "E_VLM_UNCONFIRMED", "Unconfirmed hypothesis.", "Inspect."),
                rst=qref("1", "E_VLM_UNCONFIRMED", "Unconfirmed hypothesis.", "Inspect visually."),
                rst_reference_spot_id=None,
                rf=None,
                peak_model="none",
                emg_sigma_px=None,
                emg_tau_px=None,
                fwhm_px=None,
                snr=qref("1", "E_VLM_UNCONFIRMED", "Unconfirmed hypothesis.", "Inspect visually."),
                ensemble_agreement=qf(0.09, "1"),
                ensemble_n_hit=3,
                ensemble_y_spread_px=None,
                fit_residual_rms_od=None,
                vlm_proposed=True,
                vlm_confirmation=VLMConfirmation(
                    proposed_y_frac=0.420, pixel_support_sigma=1.8, confirmed=False
                ),
                flags=["vlm_hallucination_candidate"],
            ),
            make_spot(
                id="sp_07",
                lane_index=3,
                y_px=qf(611.9, "px", method="emg_fit", ci95=(610.8, 613.0)),
                y_frac=qf(0.554, "frac", provenance="inferred", method="y_px_over_H"),
                rst=qf(
                    1.0,
                    "1",
                    provenance="inferred",
                    method="anchor_by_definition",
                    ci95=(1.0, 1.0),
                    note="this spot defines the Rst scale; its interval is degenerate",
                ),
                snr=qf(8.4, "1"),
                flags=[],
            ),
        ],
        flags=[
            Flag(
                code="green_clipping_high",
                severity="block",
                message="34.1% of in-plate green pixels read 255. OD is undefined there.",
                remedy="Re-shoot 1-2 stops darker or with a shorter shutter.",
                evidence={"green_clip_frac_in_plate": 0.341, "gate": 0.15},
            ),
            Flag(
                code="no_solvent_front",
                severity="warn",
                message="No solvent front is drawn, so Rf is not reported.",
                remedy="Draw the front in pencil before imaging.",
                evidence={},
            ),
            Flag(
                code="low_ensemble_agreement",
                severity="warn",
                message="No feature reaches 80% ensemble agreement.",
                remedy="Treat single-pipeline spot counts from this plate as unreliable.",
                evidence={"max_agreement": 0.78, "per_lane_max": [0.78, 0.75, 0.66, 0.56]},
            ),
            Flag(
                code="uncalibrated_confidence",
                severity="info",
                message="Confidence values are withheld until the calibration set exists.",
                remedy="Label 30 plates.",
                evidence={"labelled_plates": 7, "required": 30},
            ),
        ],
        correlations=CorrelationBlock(
            hypotheses_tested=3,
            adjustment="benjamini_hochberg",
            fdr_target=0.10,
            findings=[],
            suppressed=[
                CorrelationFinding(
                    hypothesis_id="H1_conversion_vs_time",
                    n_plates=7,
                    n_min_required=20,
                    verdict="insufficient_data",
                    p_raw=None,
                    confounds_checked=["operator", "plate_batch", "capture_session", "exposure"],
                    confounds_unresolved=["capture_session"],
                    suppressed_reason=_refusal(
                        "E_INSUFFICIENT_DATA",
                        "7 plates; 20 required, from at least 3 batches.",
                        "Run and label 13 more plates.",
                        {"batches_available": 1},
                    ),
                ),
                CorrelationFinding(
                    hypothesis_id="H2_rst_shift_vs_solvent",
                    n_plates=7,
                    n_min_required=20,
                    verdict="insufficient_data",
                    p_raw=None,
                    confounds_checked=["operator", "plate_batch"],
                    confounds_unresolved=[],
                    suppressed_reason=_refusal(
                        "E_INSUFFICIENT_DATA",
                        "Only two solvent systems are represented; confounded with date.",
                        "Randomise solvent system within a session.",
                    ),
                ),
                CorrelationFinding(
                    hypothesis_id="H3_area_vs_loading",
                    n_plates=0,
                    n_min_required=20,
                    verdict="insufficient_data",
                    p_raw=None,
                    confounds_checked=[],
                    confounds_unresolved=[],
                    suppressed_reason=_refusal(
                        "E_CLIP_PHOTOMETRY",
                        "No plate in the corpus has usable photometry.",
                        "Re-shoot the corpus with correct exposure.",
                        {"plates_with_full_photometry": 0},
                    ),
                ),
            ],
        ),
        vlm=VLMBlock(
            mode="live",
            model_id="gemini-2.5-flash-lite-001",
            prompt_bundle={"bands": "v3", "lanes": "v4", "front": "v2", "header": "v3"},
            n_samples=5,
            temperature=1.0,
            fields={
                "lane_count": VLMField(value=4, agreement=1.0, samples=[4, 4, 4, 4, 4]),
                "lane_labels": VLMField(
                    value=["S", "co", "R", "sd"],
                    agreement=0.8,
                    disagreements=[
                        {"index": 1, "votes": {"co": 4, "Co": 1}},
                        {"index": 3, "votes": {"sd": 4, "Sd": 1}},
                    ],
                ),
                "bands": VLMField(value=[0.262, 0.901], agreement=0.6, iqr_frac=0.011),
                "front_present": VLMField(value=False, agreement=1.0),
                "header_text": VLMField(
                    value="MEHQ-P29  4hr (30+ GA:MeOH)", agreement=0.4, flagged_for_review=True
                ),
            },
            cache={"hits": 0, "misses": 5, "bundle_hash": "7b2f19c4ad38"},
            cost={"input_tokens": 4820, "output_tokens": 610, "cached_tokens": 0, "usd": 0.000112},
            attempts=5,
            retries=0,
            degraded=False,
        ),
        refusals=[e_clip, e_front, e_uncal],
        storage=StorageBlock(
            od_h5="blobs/runs/run_01JB8Q2M7K4X0ZP3RCN9VH6TAD.h5",
            image="blobs/sha256/9f/2c/9f2c41ab",
            preview_png="cache/previews/img_9f2c41ab7de05c83b1a4_1024.png",
        ),
        provenance=ProvenanceBlock(
            pipeline_version="1.3.0",
            schema_version="1",
            config_hash="a71c0e5f9b2d",
            config_ref="config/pipeline/v1.3.0.toml",
            config_document={"seed_salt": 0, "gates": {"green_clip_max": 0.15}},
            code_fingerprint="3d8ba0",
            git_commit="c41f9a2",
            git_dirty=False,
            env_fingerprint="e2907b",
            lock_hash="5fa31c",
            libraries={
                "python": "3.12.13",
                "numpy": "2.2.6",
                "scipy": "1.16.1",
                "scikit-image": "0.25.2",
                "h5py": "3.14.0",
                "pillow": "11.3.0",
            },
            platform_tag="darwin-arm64-openblas",
            seed=11497285633021448106,
            seed_derivation="int(image_sha256[:16],16) ^ config.seed_salt",
            run_key="c0d4e1",
            vlm_bundle_hash=None,
            result_sha256=None,
            od_sha256="f309cc",
            replay_of=None,
            superseded_by=None,
            determinism_tier="tier1",
        ),
    )


# ---------------------------------------------------------------------------
# Rule (a): refused <=> value None + refusal populated
# ---------------------------------------------------------------------------


def test_refused_with_null_value_and_refusal_accepted():
    q = qref("px", "E_NO_FRONT")
    assert q.value is None
    assert q.refusal is not None and q.refusal.code == "E_NO_FRONT"


def test_refused_with_value_rejected():
    with pytest.raises(ValidationError, match="value to be None"):
        Q[float](value=1.0, unit="px", provenance="refused", refusal=_refusal())


def test_refused_without_refusal_rejected():
    with pytest.raises(ValidationError, match="populated refusal"):
        Q[float](value=None, unit="px", provenance="refused")


@pytest.mark.parametrize("provenance", ["measured", "chosen", "inferred"])
def test_null_value_without_refused_provenance_rejected(provenance):
    with pytest.raises(ValidationError, match="only when provenance == 'refused'"):
        Q[float](value=None, unit="px", provenance=provenance, method="m")


# ---------------------------------------------------------------------------
# Rule (b): inferred => method
# ---------------------------------------------------------------------------


def test_inferred_with_method_accepted():
    q = qf(0.5, "frac", provenance="inferred", method="y_px_over_H")
    assert q.method == "y_px_over_H"


def test_inferred_without_method_rejected():
    with pytest.raises(ValidationError, match="requires method"):
        Q[float](value=0.5, unit="frac", provenance="inferred")


# ---------------------------------------------------------------------------
# Rule (c): ci95 only for measured/inferred
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provenance", ["measured", "inferred"])
def test_ci95_allowed_for_measured_and_inferred(provenance):
    q = qf(0.5, "px", provenance=provenance, method="m", ci95=(0.4, 0.6))
    assert q.ci95 == (0.4, 0.6)


def test_ci95_rejected_for_chosen():
    with pytest.raises(ValidationError, match="ci95"):
        Q[float](value=0.15, unit="frac", provenance="chosen", ci95=(0.1, 0.2))


def test_ci95_rejected_for_refused():
    with pytest.raises(ValidationError, match="ci95"):
        Q[float](
            value=None, unit="px", provenance="refused", refusal=_refusal(), ci95=(0.1, 0.2)
        )


# ---------------------------------------------------------------------------
# Spot rule: rst.value non-null => named reference spot (§7.2.6 point 3)
# ---------------------------------------------------------------------------


def test_spot_rst_with_reference_accepted():
    spot = make_spot()
    assert spot.rst_reference_spot_id == "sp_07"


def test_spot_rst_without_reference_rejected():
    with pytest.raises(ValidationError, match="rst_reference_spot_id"):
        make_spot(rst_reference_spot_id=None)


def test_spot_refused_rst_without_reference_accepted():
    spot = make_spot(
        rst=qref("1", "E_VLM_UNCONFIRMED", "Unconfirmed.", "Inspect."),
        rst_reference_spot_id=None,
    )
    assert spot.rst.value is None


# ---------------------------------------------------------------------------
# Lane rule: suppression populated iff quantified == false
# ---------------------------------------------------------------------------


def test_lane_unquantified_without_suppression_rejected():
    with pytest.raises(ValidationError, match="suppression is mandatory"):
        make_lane(quantified=False, suppression=None)


def test_lane_quantified_with_suppression_rejected():
    with pytest.raises(ValidationError, match="suppression must be None"):
        make_lane(quantified=True, suppression=_refusal())


def test_lane_quantified_without_suppression_accepted():
    lane = make_lane(quantified=True, suppression=None)
    assert lane.quantified is True


# ---------------------------------------------------------------------------
# Frozen + extra="forbid"
# ---------------------------------------------------------------------------


def test_frozen_mutation_raises():
    q = qf(1.0, "px")
    with pytest.raises(ValidationError):
        q.value = 2.0
    r = _refusal()
    with pytest.raises(ValidationError):
        r.code = "E_OTHER"
    spot = make_spot()
    with pytest.raises(ValidationError):
        spot.status = "rejected"


def test_extra_key_rejected():
    with pytest.raises(ValidationError, match="bogus"):
        Q[float](value=1.0, unit="px", provenance="measured", bogus=1)
    with pytest.raises(ValidationError, match="surprise"):
        Refusal(code="E", message="m", remedy="r", surprise="no")
    with pytest.raises(ValidationError, match="extra_field"):
        make_spot().model_copy()  # sanity: copy works
        Spot(**{**make_spot().model_dump(), "extra_field": 1})


# ---------------------------------------------------------------------------
# NaN / inf rejection
# ---------------------------------------------------------------------------


def test_nan_value_rejected():
    with pytest.raises(ValidationError, match="finite"):
        Q[float](value=float("nan"), unit="px", provenance="measured")


def test_inf_value_rejected():
    with pytest.raises(ValidationError, match="finite"):
        Q[float](value=float("inf"), unit="OD", provenance="measured")


def test_nan_in_ci95_rejected():
    with pytest.raises(ValidationError):
        Q[float](value=1.0, unit="px", provenance="measured", ci95=(float("nan"), 2.0))


def test_nan_in_bare_float_fields_rejected():
    with pytest.raises(ValidationError):
        VLMConfirmation(proposed_y_frac=float("nan"), pixel_support_sigma=1.0, confirmed=True)
    with pytest.raises(ValidationError):
        make_lane(label_agreement=float("inf"))


# ---------------------------------------------------------------------------
# Densitogram preview cap
# ---------------------------------------------------------------------------


def _make_densitogram(n_preview: int) -> Densitogram:
    return Densitogram(
        lane_index=0,
        y_px=DensitogramYAxis(start=0, stop=1104, step=1),
        unit="OD",
        sampling="mean over valid px",
        n_valid_columns=89,
        ref="h5://runs/run_x.h5#/densitograms/lane_00",
        sha256="ab" * 32,
        preview=[0.0001 * i for i in range(n_preview)],
    )


def test_preview_at_cap_accepted():
    d = _make_densitogram(512)
    assert len(d.preview) == 512


def test_preview_over_cap_rejected():
    with pytest.raises(ValidationError, match="512"):
        _make_densitogram(513)


# ---------------------------------------------------------------------------
# Float canonicalisation (§7.2.2 item 6)
# ---------------------------------------------------------------------------


def test_value_rounded_to_6dp_in_json_dump():
    q = qf(0.1234567, "px")
    assert q.model_dump(mode="json")["value"] == 0.123457
    # python-mode dump keeps full precision
    assert q.model_dump()["value"] == 0.1234567
    # model_dump_json agrees with model_dump(mode="json")
    assert json.loads(q.model_dump_json())["value"] == 0.123457


def test_unit_one_rounded_to_4dp_in_json_dump():
    q = qf(0.65432109, "1")
    assert q.model_dump(mode="json")["value"] == 0.6543
    assert q.model_dump()["value"] == 0.65432109


def test_ci95_rounded_by_unit():
    q = qf(1.0, "px", ci95=(0.12345678, 2.98765432))
    assert q.model_dump(mode="json")["ci95"] == [0.123457, 2.987654]
    p = qf(0.5, "1", ci95=(0.12345678, 0.98765432))
    assert p.model_dump(mode="json")["ci95"] == [0.1235, 0.9877]


def test_bool_and_int_values_untouched_by_rounding():
    assert qb(True).model_dump(mode="json")["value"] is True
    assert qi(4, "1").model_dump(mode="json")["value"] == 4


def test_bare_floats_rounded_in_json_dump():
    band = AnnotationBand(
        kind="header",
        y0_frac=qf(0.0, "frac", provenance="chosen"),
        y1_frac=qf(0.2621239876, "frac"),
        provenance="vlm",
        vlm_agreement=0.83334567,
        vlm_iqr_frac=0.0111119876,
        source_detail="x",
    )
    dumped = band.model_dump(mode="json")
    assert dumped["vlm_agreement"] == 0.8333  # probability-like: 4dp
    assert dumped["vlm_iqr_frac"] == 0.011112  # fraction: 6dp
    assert dumped["y1_frac"]["value"] == 0.262124


def test_geometry_corner_floats_rounded():
    g = build_worked_example().geometry
    g2 = GeometryBlock(**{**g.model_dump(), "corners_src_px": (
        (402.12345678, 988.7), (2611.4, 845.3), (2698.0, 3719.6), (489.2, 3862.9)
    )})
    assert g2.model_dump(mode="json")["corners_src_px"][0][0] == 402.123457


# ---------------------------------------------------------------------------
# Unit enum is closed
# ---------------------------------------------------------------------------


def test_unknown_unit_rejected():
    with pytest.raises(ValidationError, match="unit"):
        Q[float](value=1.0, unit="furlong", provenance="measured")


# ---------------------------------------------------------------------------
# Full worked example (§7.3.6): positions-only plate
# ---------------------------------------------------------------------------


def test_worked_example_builds_and_encodes_the_right_refusals():
    result = build_worked_example()
    assert result.status == "degraded"
    assert result.capture_qc.verdict == "positions_only"
    assert result.photometry.photometry_mode == "positions_only"

    sp_01 = result.spots[0]
    assert sp_01.amplitude_od.provenance == "refused"
    assert sp_01.amplitude_od.refusal.code == "E_CLIP_PHOTOMETRY"
    assert sp_01.rf.refusal.code == "E_NO_FRONT"
    assert sp_01.confidence.refusal.code == "E_UNCALIBRATED"
    assert sp_01.y_px.value == 615.2  # positions remain valid

    sp_06 = result.spots[1]
    assert sp_06.status == "proposed_unconfirmed"
    assert sp_06.vlm_proposed is True
    assert sp_06.vlm_confirmation.confirmed is False
    assert sp_06.vlm_confirmation.pixel_support_sigma == 1.8

    assert result.reference.front_provenance == "absent"
    assert result.reference.rf_available is False
    assert result.reference.rst_anchor.spot_id == "sp_07"

    assert result.correlations.findings == []
    assert all(f.verdict == "insufficient_data" for f in result.correlations.suppressed)
    assert {r.code for r in result.refusals} == {
        "E_CLIP_PHOTOMETRY",
        "E_NO_FRONT",
        "E_UNCALIBRATED",
    }


def test_worked_example_json_roundtrip_and_canonical_dump():
    result = build_worked_example()
    dumped = result.model_dump(mode="json")
    # re-parses, and re-dumping is a fixed point (rounding is idempotent)
    again = Result.model_validate(dumped)
    assert again.model_dump(mode="json") == dumped
    # canonical JSON per §7.2.2 item 5 must succeed (no NaN anywhere)
    canonical = json.dumps(
        dumped, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    assert canonical == json.dumps(
        json.loads(canonical),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    # spot check the rounding applied inside the tree
    assert dumped["photometry"]["sigma_od"]["value"] == 0.011213
    assert dumped["geometry"]["corners_src_px"][0] == [402.1, 988.7]
    assert dumped["vlm"]["cost"]["usd"] == 0.000112
    assert dumped["schema_version"] == "1"


def test_worked_example_roundtrip_via_json_string():
    result = build_worked_example()
    text = result.model_dump_json()
    again = Result.model_validate(json.loads(text))
    assert again.model_dump(mode="json") == result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Schema emission determinism
# ---------------------------------------------------------------------------


def _load_emit_module():
    path = REPO_ROOT / "scripts" / "emit_schema.py"
    spec = importlib.util.spec_from_file_location("emit_schema", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_schema_emission_is_deterministic_and_committed_file_matches():
    mod = _load_emit_module()
    first = mod.render()
    second = mod.render()
    assert first == second
    assert first.endswith("\n")
    json.loads(first)  # valid JSON
    committed = (REPO_ROOT / "schemas" / "result_v1.schema.json").read_text(encoding="ascii")
    assert committed == first


def test_emitted_schema_names_the_top_level_blocks():
    mod = _load_emit_module()
    schema = json.loads(mod.render())
    props = schema["properties"]
    expected = {
        "schema_version", "run_id", "image_id", "created_at", "status",
        "image", "capture_qc", "geometry", "annotation_bands", "lanes",
        "reference", "photometry", "densitograms", "spots", "flags",
        "correlations", "vlm", "refusals", "storage", "provenance",
    }
    assert set(props) == expected
    assert schema["additionalProperties"] is False


# ---------------------------------------------------------------------------
# misc: math sanity for the rounding helpers used in assertions above
# ---------------------------------------------------------------------------


def test_rounding_is_stable_under_reround():
    for v in (0.1234567, 0.65432109, 958.4, 0.011213, 1e-7, -0.0000004):
        assert round(round(v, 6), 6) == round(v, 6)
        assert math.isfinite(round(v, 6))
