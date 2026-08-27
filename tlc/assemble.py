"""Assemble a RunOutput (pure numbers, tlc.pipeline) into the frozen Result schema
(tlc.schemas.result). This is where provenance tags become structural (NN2), refusals become
values (NN3), and the run envelope (ids, hashes, environment) is attached. Lives OUTSIDE
tlc/pipeline so the pipeline stays free of pydantic and I/O.
"""

import hashlib
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np

from tlc.core.canonical_json import canonical_json
from tlc.core.hashing import sha256_bytes, sha256_canonical, tree_fingerprint
from tlc.pipeline import flags as F
from tlc.pipeline.runner import RunOutput
from tlc.schemas import result as S

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "0.6.0"


def _q(value, unit, provenance="measured", method=None, ci95=None, n=None, note=None):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return S.Q(value=None, unit=unit, provenance="refused", method=method,
                   refusal=S.Refusal(code="E_NOT_COMPUTED", message="This value could not be computed on this plate.",
                                     remedy="See the plate-level refusals.", evidence={}))
    return S.Q(value=value, unit=unit, provenance=provenance, method=method, ci95=ci95, n=n, note=note)


def _refused(unit, ref) -> S.Q:
    return S.Q(value=None, unit=unit, provenance="refused",
               refusal=S.Refusal(code=ref.code, message=ref.message, remedy=ref.remedy,
                                 evidence={k: (float(v) if isinstance(v, int | float | np.floating | np.integer) else str(v)) for k, v in ref.evidence.items()}))


def _refusal(ref) -> S.Refusal:
    return S.Refusal(code=ref.code, message=ref.message, remedy=ref.remedy,
                     evidence={k: (float(v) if isinstance(v, int | float | np.floating | np.integer) else str(v)) for k, v in ref.evidence.items()})


def _flag(fl) -> S.Flag:
    ev = {}
    for k, v in fl.evidence.items():
        if isinstance(v, list | tuple):
            ev[k] = [float(x) for x in v]
        elif isinstance(v, int | float | np.floating | np.integer):
            ev[k] = float(v)
        else:
            ev[k] = str(v)
    return S.Flag(code=fl.code, severity=fl.severity, message=fl.message, remedy=fl.remedy, evidence=ev)


def environment_fingerprint(lock_hash: str) -> tuple[str, dict[str, str], str]:
    import h5py
    import numpy
    import PIL
    import scipy
    import skimage

    libs = {"python": platform.python_version(), "numpy": numpy.__version__, "scipy": scipy.__version__,
            "scikit-image": skimage.__version__, "h5py": h5py.__version__, "pillow": PIL.__version__}
    tag = f"{sys.platform}-{platform.machine()}"
    return sha256_canonical({**libs, "lock_hash": lock_hash, "platform_tag": tag}), libs, tag


def git_state() -> tuple[str, bool]:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip())
        return commit, dirty
    except Exception:
        return "unknown", True


def assemble(out: RunOutput, image_bytes: bytes, image_meta: dict, config_document: dict, run_id: str,
             created_at: str, vlm_block: S.VLMBlock | None = None, storage: S.StorageBlock | None = None,
             correlations: S.CorrelationBlock | None = None, config_hash: str | None = None,
             vlm_bundle_hash: str | None = None) -> S.Result:
    """`config_hash` and `vlm_bundle_hash` are passed in by the caller that KEYS the run, so the
    result records the identifiers the run is stored under. Recomputing them here produced a record
    that could not reproduce its own run_key: the caller hashes the config document before enriching
    it, and knows the VLM bundle, and this function knows neither (M-028)."""
    image_sha = sha256_bytes(image_bytes)
    config_hash = config_hash or sha256_canonical(config_document)
    lock_hash = sha256_bytes((ROOT / "uv.lock").read_bytes()) if (ROOT / "uv.lock").exists() else "unavailable"
    env_hash, libs, platform_tag = environment_fingerprint(lock_hash)
    code_fp = tree_fingerprint(ROOT / "tlc" / "pipeline", ROOT / "tlc" / "core")
    commit, dirty = git_state()
    seed_salt = int(config_document.get("seed_salt", 0))
    seed = int(image_sha[:16], 16) ^ seed_salt

    thresholds = {k: _q(v, "frac" if "clip" in k or "overrun" in k or "ci90" in k else "1", "chosen") for k, v in config_document.get("gate_thresholds", {}).items()}
    cap = out.capture
    geo = out.geometry
    fo = geo.frame_overrun if geo is not None else {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}
    capture_qc = S.CaptureQCBlock(
        green_clip_frac_in_plate=_q(cap.get("green_clip_frac_in_plate"), "frac"),
        green_clip_frac_frame=_q(cap.get("green_clip_frac_frame"), "frac"),
        black_clip_frac_in_plate=_q(cap.get("black_clip_frac_in_plate"), "frac"),
        channel_sat_frac=_q(cap.get("channel_sat_frac"), "frac"),
        plate_area_frac=_q(cap.get("plate_area_frac"), "frac"),
        frame_overrun=S.FrameOverrun(**{k: _q(float(v), "frac") for k, v in fo.items()}),
        tilt_deg=_q(cap.get("tilt_deg"), "deg"),
        focus_metric=_q(cap.get("focus_metric"), "1", note="relative only; not comparable across cameras"),
        mean_green_in_plate=_q(cap.get("mean_green_in_plate"), "1"),
        verdict=out.verdict,
        gates_fired=sorted(set(out.gates_fired)),
        gate_thresholds=thresholds,
    )

    if geo is not None and geo.found:
        corners = tuple((float(x), float(y)) for x, y in geo.corners_src)
        hmat = tuple(tuple(float(v) for v in row) for row in geo.homography)
        geometry = S.GeometryBlock(
            corners_src_px=corners, homography=hmat, rectified_shape=tuple(int(v) for v in geo.rectified_shape),
            tilt_deg=_q(geo.tilt_deg, "deg"),
            idempotency_residual_px=_q(out.idempotency_px, "px") if out.idempotency_px is not None else S.Q(value=None, unit="px", provenance="refused", refusal=S.Refusal(code="E_NOT_COMPUTED", message="Re-warp check not run in this mode.", remedy="Run the full replay.", evidence={})),
            valid_erosion_px=_q(int(np.ceil(2.0 + 0.55 * (geo.tilt_deg or 0.0))), "px", "inferred", method="ceil(2 + tilt_deg*0.55)"),
            valid_frac=_q(out.valid_frac, "frac"),
            detection_method=geo.detection_method,
        )
    else:
        zero = ((0.0, 0.0),) * 4
        geometry = S.GeometryBlock(corners_src_px=zero, homography=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), rectified_shape=(0, 0),
                                   tilt_deg=_refused("deg", out.refusals[0]), idempotency_residual_px=_refused("px", out.refusals[0]),
                                   valid_erosion_px=_refused("px", out.refusals[0]), valid_frac=_refused("frac", out.refusals[0]),
                                   detection_method=geo.detection_method if geo else "hsv_bright_green_largest_cc")

    bands = [S.AnnotationBand(kind=b["kind"], y0_frac=_q(b["y0_frac"], "frac", "chosen"), y1_frac=_q(b["y1_frac"], "frac", "chosen"),
                              provenance=b["provenance"], source_detail=b["source_detail"]) for b in out.bands]

    H = out.rectified_shape[0] if out.rectified_shape else 1
    W = out.rectified_shape[1] if out.rectified_shape else 1
    lanes = []
    for L in out.lanes:
        lanes.append(S.Lane(
            index=L.index, label=L.label, label_provenance=L.label_provenance,
            x_center_px=_q(L.x_center_px, "px", method=L.x_center_method),
            x_center_frac=_q(L.x_center_px / max(W, 1), "frac", "inferred", method="x_center_px_over_W"),
            half_width_px=_q(L.half_width_px, "px", "chosen"),
            x_seed_provenance=L.x_seed_provenance,
            is_empty=_q(L.is_empty, "1"), is_streaking=_q(L.is_streaking, "1"),
            quantified=L.quantified, suppression=_refusal(L.suppression) if L.suppression else None,
            at_plate_edge=L.at_plate_edge,
        ))

    org = out.origin
    if org is not None and org.found and out.origin_refusal is None:
        origin_q = _q(org.row, "px", method="origin_dot_blobs_median", ci95=(org.row - 1.96 * (org.row_sd or 0.0), org.row + 1.96 * (org.row_sd or 0.0)), n=org.n_dots)
        origin_prov = "detected_dots"
        support = _q(org.support_sigma, "1")
    else:
        ref = out.origin_refusal or out.refusals[0]
        origin_q = _refused("px", ref)
        origin_prov = "refused"
        support = _refused("1", ref)
    no_front = next((r for r in out.refusals if r.code == "E_NO_FRONT"), None)
    anchor = None
    if out.anchor is not None and out.anchor.get("spot_id"):
        anchor = S.RstAnchor(spot_id=out.anchor["spot_id"], lane_index=out.anchor["lane_index"], lane_label=out.anchor["lane_label"],
                             y_px=_q(out.anchor["y_px"], "px", method="emg_fit_mode"),
                             selection_rule="highest_agreement_confirmed_spot_in_standard_lane", provenance="measured")
    reference = S.ReferenceBlock(
        origin_row_px=origin_q, origin_provenance=origin_prov, origin_support_sigma=support,
        origin_dots_found=_q(org.n_dots if org else 0, "1"),
        front_row_px=_refused("px", no_front) if no_front else S.Q(value=None, unit="px", provenance="refused", refusal=S.Refusal(code="E_NO_FRONT", message="No solvent front.", remedy="Draw the front.", evidence={})),
        front_provenance="absent", front_absent_reason=_refusal(no_front) if no_front else None,
        rst_anchor=anchor, rf_available=False,
        rf_unavailable_reason=_refusal(no_front) if no_front else None,
    )

    photometry = S.PhotometryBlock(
        signal_channel="green", background_model="poly3",
        background_radius_px=_q(0, "px", "chosen", note="poly3 has no radius; primary member only"),
        od_transform="log10(I0/I)",
        sigma_od=_q(out.sigma_od, "OD", method="mad_diff_1.4826_prespot", note="measured once on the raw analysable band before any spot masking (F4)"),
        sigma_method="mad_diff_1.4826_prespot",
        sigma_stability_across_radii=_q(out.sigma_stability, "frac"),
        clipped_px_frac_in_analysable=_q(out.clip_frac_analysable, "frac"),
        photometry_mode=out.photometry_mode,
    )

    od_sha = hashlib.sha256(out.od.astype(np.float32).tobytes()).hexdigest() if out.od is not None else "none"
    densitograms = []
    for L in out.lanes:
        prof32 = L.profile.astype(np.float32)
        step = max(1, int(np.ceil(prof32.size / 512)))
        preview = [round(float(v), 5) for v in prof32[: (prof32.size // step) * step].reshape(-1, step).mean(axis=1)] if prof32.size >= step else [round(float(v), 5) for v in prof32]
        densitograms.append(S.Densitogram(
            lane_index=L.index, y_px=S.DensitogramYAxis(start=0, stop=int(prof32.size), step=1), unit="OD",
            sampling=f"mean over valid px in [x_center-{L.half_width_px}, x_center+{L.half_width_px}]",
            n_valid_columns=int(np.median(L.n_valid_columns)) if L.n_valid_columns.size else 0,
            # content-addressed (A-018): the reference denotes the OD record, not a deployment path
            ref=f"h5://od/{od_sha}#/densitograms/lane_{L.index:02d}", sha256=hashlib.sha256(prof32.tobytes()).hexdigest(),
            preview=preview[:512],
        ))

    uncal = next((r for r in out.refusals if r.code == "E_UNCALIBRATED"), None)
    spots = []
    for sp in out.spots:
        f = sp.fit
        if sp.rst is not None:
            rst_q = _q(sp.rst.value, "1", "inferred", method=sp.rst.method, ci95=(sp.rst.value - 1.96 * sp.rst.sd, sp.rst.value + 1.96 * sp.rst.sd),
                       note="variance budget spot/origin/reference = " + "/".join(f"{sp.rst.budget[k]:.2f}" for k in ("spot", "origin", "reference")))
            ref_id = out.anchor["spot_id"] if out.anchor else None
        else:
            rst_q = _refused("1", sp.rst_refusal) if sp.rst_refusal else S.Q(value=None, unit="1", provenance="refused", refusal=S.Refusal(code="E_NOT_COMPUTED", message="Rst not computed.", remedy="See refusals.", evidence={}))
            ref_id = None
        se = float(np.sqrt(sp.y_var)) if np.isfinite(sp.y_var) else None
        spots.append(S.Spot(
            id=sp.id, lane_index=sp.lane_index, status=sp.status,
            y_px=_q(sp.y_px, "px", method=("emg_fit_mode" if f.ok else "matched_filter_seed"), ci95=(sp.y_px - 1.96 * se, sp.y_px + 1.96 * se) if se is not None else None),
            y_frac=_q(sp.y_px / max(H, 1), "frac", "inferred", method="y_px_over_H"),
            rst=rst_q, rst_reference_spot_id=ref_id if sp.rst is not None else None,
            rf=_refused("1", no_front) if no_front else None,
            peak_model=("emg" if f.ok and f.method == "emg_fit" else ("gaussian" if f.ok else "none")),
            emg_sigma_px=_q(f.sigma, "px") if f.ok else None, emg_tau_px=_q(f.tau, "px") if f.ok else None,
            fwhm_px=_q(f.fwhm, "px", "inferred", method="numeric_fwhm_of_fitted_curve") if f.ok and np.isfinite(f.fwhm) else None,
            amplitude_od=_q(sp.amplitude_od, "OD") if sp.amplitude_od is not None else _refused("OD", sp.area_refusal),
            area_od_px=_q(sp.area_od_px, "OD*px") if sp.area_od_px is not None else _refused("OD*px", sp.area_refusal),
            area_frac_of_lane=_q(sp.area_frac_of_lane, "frac", "inferred", method="area_over_lane_total_area") if sp.area_frac_of_lane is not None else _refused("frac", sp.area_refusal),
            snr=_q(sp.snr, "1", note="detection statistic in profile-noise units"),
            ensemble_agreement=_q(sp.ensemble.agreement, "1", method="weighted_jeffreys_agreement"),
            ensemble_n_total=sp.ensemble.n_total, ensemble_n_hit=sp.ensemble.n_hit,
            ensemble_y_spread_px=_q(sp.ensemble.row_spread, "px"),
            confidence=_refused("1", uncal or F.e_uncalibrated()),
            calibration_version=None,
            fit_residual_rms_od=_q(f.residual_rms, "OD") if f.ok and np.isfinite(f.residual_rms) else None,
            vlm_proposed=False, vlm_confirmation=None, flags=list(sp.flags),
        ))

    correlations = correlations or S.CorrelationBlock(hypotheses_tested=0, adjustment="benjamini_hochberg", fdr_target=0.10, findings=[], suppressed=[])
    vlm = vlm_block or S.VLMBlock(mode="off", model_id=None, prompt_bundle={}, n_samples=0, temperature=0.0, fields={},
                                  cache={"hits": 0, "misses": 0, "bundle_hash": ""}, cost={"input_tokens": 0, "output_tokens": 0, "usd": 0.0},
                                  attempts=0, retries=0, degraded=False)
    storage = storage or S.StorageBlock(od_h5=None, image=None, preview_png=None)
    provenance = S.ProvenanceBlock(
        pipeline_version=PIPELINE_VERSION, schema_version="1", config_hash=config_hash,
        config_ref=str(config_document.get("config_ref", "config/pipeline/v0.6.0.toml")), config_document=config_document,
        code_fingerprint=code_fp, git_commit=commit, git_dirty=dirty, env_fingerprint=env_hash, lock_hash=lock_hash,
        libraries=libs, platform_tag=platform_tag, seed=seed, seed_derivation="int(image_sha256[:16],16) ^ config.seed_salt",
        vlm_bundle_hash=vlm_bundle_hash,
        run_key=sha256_canonical({"image_sha256": image_sha, "config_hash": config_hash, "code_fingerprint": code_fp, "env_fingerprint": env_hash, "vlm_bundle_hash": vlm_bundle_hash}),
        result_sha256=None, od_sha256=hashlib.sha256(out.od.astype(np.float32).tobytes()).hexdigest() if out.od is not None else None,
        replay_of=None, superseded_by=None, determinism_tier="tier1",
    )
    image_block = S.ImageBlock(sha256=image_sha, bytes=len(image_bytes), mime=image_meta.get("mime", "image/png"),
                               width_px=int(image_meta["width_px"]), height_px=int(image_meta["height_px"]),
                               exif_orientation=image_meta.get("exif_orientation"), decoder=image_meta.get("decoder", "imageio.v3/pillow"),
                               original_filename=image_meta.get("original_filename"))
    result = S.Result(run_id=run_id, image_id=f"img_{image_sha[:20]}", created_at=created_at, status=out.status, image=image_block,
                      capture_qc=capture_qc, geometry=geometry, annotation_bands=bands, lanes=lanes, reference=reference,
                      photometry=photometry, densitograms=densitograms, spots=spots, flags=[_flag(f) for f in out.flags],
                      correlations=correlations, vlm=vlm, refusals=[_refusal(r) for r in out.refusals], storage=storage, provenance=provenance)
    return result


# spec 03 §7.2.4 plus the run-envelope fields that necessarily differ between an original and its
# byte-identical replay (A-018): replay_of/superseded_by and the git bookkeeping (code_fingerprint
# is the hashed identity; git_commit is not).
DETERMINISM_EXCLUDED_TOP = {"run_id", "created_at", "storage"}
DETERMINISM_EXCLUDED_PROVENANCE = {"result_sha256", "replay_of", "superseded_by", "git_commit", "git_dirty"}


def result_sha256(result: S.Result) -> str:
    d = result.model_dump(mode="json")
    for k in DETERMINISM_EXCLUDED_TOP:
        d.pop(k, None)
    for k in DETERMINISM_EXCLUDED_PROVENANCE:
        d.get("provenance", {}).pop(k, None)
    return sha256_bytes(canonical_json(d).encode("ascii"))
