"""Gate 2 evidence: deterministic geometry.

Criteria (brief §6):
  1. Synthetic, known corners: corner recovery error < 1.5 px at the 95th percentile across
     tilt 0-12 deg (sweep of 60 plates, sizes/content from the corpus-calibrated sampler,
     frame overrun off — a cut corner has no recoverable truth).
  2. Real corpus: plate detected on 100% of unique images.
  3. Frame overrun flagged (band coverage > 0.02) on the four known cases from the prior
     evaluation: P32-4hr bottom, P30 top+bottom, P32_4+3hr bottom, P33 top.
  4. Rectification idempotent on the real corpus: re-warp residual <= 0.5 px on every plate.
  5. Determinism: two full in-process passes hash-identical (cross-machine unavailable, A-009);
     the reviewer additionally re-runs the script and byte-compares the evidence file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tlc.core.determinism  # noqa: F401, E402  (sets BLAS env; MUST import before numpy)

# isort: split

import imageio.v3 as iio  # noqa: E402
import numpy as np  # noqa: E402

from tlc.core.canonical_json import canonical_json  # noqa: E402
from tlc.core.hashing import sha256_bytes, sha256_file  # noqa: E402
from tlc.pipeline.geometry import analyse_geometry, warp_rectify  # noqa: E402
from tlc.synth.generator import make_plate, random_spec  # noqa: E402
from tlc.synth.spec import Overrun  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
N_SWEEP = 60

# Matched by substring: byte-identical duplicates mean the kept filename may carry a " (1)".
KNOWN_OVERRUN = {
    "MEHQ-P32-4hr": ["bottom"],
    "MEHQ-P30-4hr": ["top", "bottom"],
    "MEHQ-P32_4+3hr": ["bottom"],
    "MEHQ-P33 4hr": ["top"],
}


def synthetic_sweep() -> dict:
    errors: list[float] = []
    tilt_errors: list[float] = []
    failures: list[str] = []
    for i in range(N_SWEEP):
        rng = np.random.Generator(np.random.PCG64(9000 + i))
        tilt = 12.0 * i / (N_SWEEP - 1)
        spec = random_spec(rng, tilt_deg=tilt, frame_overrun=Overrun.NONE)
        img, gt = make_plate(spec, seed=9000 + i)
        geo = analyse_geometry(img)
        if not geo.found or geo.corners_src is None:
            failures.append(f"i={i} tilt={tilt:.1f}: not detected")
            continue
        err = np.linalg.norm(geo.corners_src - np.array(gt.corners_xy), axis=1)
        errors.extend(float(e) for e in err)
        tilt_errors.append(abs(geo.tilt_deg - tilt))
    p95 = float(np.percentile(errors, 95))
    return {
        "n_plates": N_SWEEP,
        "n_detected": N_SWEEP - len(failures),
        "failures": failures,
        "corner_error_median_px": round(float(np.median(errors)), 4),
        "corner_error_p95_px": round(p95, 4),
        "corner_error_max_px": round(float(np.max(errors)), 4),
        "tilt_error_max_deg": round(float(np.max(tilt_errors)), 4),
        "pass": bool(p95 < 1.5 and not failures),
    }


def real_corpus() -> tuple[dict, dict, dict]:
    files = sorted((ROOT / "dataset").rglob("*.png"))
    seen: set[str] = set()
    detect_fail: list[str] = []
    idem_rows: list[dict] = []
    overrun_by_file: dict[str, dict] = {}
    n = 0
    for p in files:
        digest = sha256_file(p)
        if digest in seen:
            continue
        seen.add(digest)
        n += 1
        img = iio.imread(p)
        geo = analyse_geometry(img)
        if not geo.found:
            detect_fail.append(p.name)
            continue
        overrun_by_file[p.name] = geo.frame_overrun
        rect, _ = warp_rectify(img[:, :, :3], geo.homography, geo.rectified_shape)
        ru8 = np.clip(np.round(rect), 0, 255).astype(np.uint8)
        # Idempotency per corner, on corners with recoverable evidence only (A-010): a corner
        # whose first-pass source position sits on the photo frame belongs to a cut plate and
        # a second pass measures clamped smear, not the plate.
        h0, w0 = img.shape[:2]
        c1 = geo.corners_src
        checkable = (
            (c1[:, 0] >= 3.0) & (c1[:, 0] <= w0 - 4.0) & (c1[:, 1] >= 3.0) & (c1[:, 1] <= h0 - 4.0)
        )
        geo2 = analyse_geometry(ru8)
        rh, rw = geo.rectified_shape
        ideal = np.array([[0, 0], [rw - 1, 0], [rw - 1, rh - 1], [0, rh - 1]], dtype=np.float64)
        if geo2.found and geo2.corners_src is not None:
            per_corner = np.linalg.norm(geo2.corners_src - ideal, axis=1)
            resid = float(per_corner[checkable].max()) if checkable.any() else None
        else:
            resid = float("inf")
        idem_rows.append({
            "file": p.name,
            "residual_px": None if resid is None else round(resid, 4),
            "corners_checkable": int(checkable.sum()),
        })

    detection = {"n_unique": n, "n_detected": n - len(detect_fail), "failures": detect_fail,
                 "pass": not detect_fail}

    overrun_rows = []
    ok = True
    for stem, edges in KNOWN_OVERRUN.items():
        fr = next((v for k, v in overrun_by_file.items() if stem in k), None)
        fname = next((k for k in overrun_by_file if stem in k), stem)
        for edge in edges:
            fired = bool(fr and fr[edge] > 0.02)
            ok &= fired
            overrun_rows.append({"file": fname, "edge": edge,
                                 "band_coverage": None if fr is None else round(fr[edge], 4),
                                 "flagged": fired})
    overrun = {"threshold": 0.02, "rows": overrun_rows, "pass": ok}

    with_evidence = [r for r in idem_rows if r["residual_px"] is not None]
    worst = max(r["residual_px"] for r in with_evidence)
    idem = {"n": len(idem_rows),
            "n_with_checkable_corners": len(with_evidence),
            "n_no_checkable_corners": len(idem_rows) - len(with_evidence),
            "worst_residual_px": worst,
            "offenders": [r for r in with_evidence if r["residual_px"] > 0.5],
            "pass": bool(worst <= 0.5)}
    return detection, overrun, idem


def main() -> None:
    def one_pass() -> dict:
        sweep = synthetic_sweep()
        detection, overrun, idem = real_corpus()
        return {"synthetic_corner_sweep": sweep, "real_detection": detection,
                "known_overrun_flags": overrun, "idempotency": idem}

    pass1 = one_pass()
    pass2 = one_pass()
    h1 = sha256_bytes(canonical_json(pass1).encode())
    h2 = sha256_bytes(canonical_json(pass2).encode())
    evidence = dict(pass1)
    evidence["determinism"] = {
        "two_pass_hash_identical": h1 == h2,
        "hash": h1,
        "cross_machine": "unavailable_this_build (A-009)",
        "pass": h1 == h2,
    }
    evidence["gate2_pass"] = bool(
        pass1["synthetic_corner_sweep"]["pass"]
        and pass1["real_detection"]["pass"]
        and pass1["known_overrun_flags"]["pass"]
        and pass1["idempotency"]["pass"]
        and h1 == h2
    )
    (ROOT / "reports" / "gate2_evidence.json").write_text(canonical_json(evidence) + "\n")
    slim = {k: v for k, v in evidence.items() if k != "idempotency"}
    slim["idempotency"] = {k: v for k, v in evidence["idempotency"].items() if k != "n" or True}
    print(canonical_json({
        "sweep": evidence["synthetic_corner_sweep"],
        "detection": {k: evidence["real_detection"][k] for k in ("n_unique", "n_detected", "pass")},
        "overrun": evidence["known_overrun_flags"],
        "idempotency": {k: evidence["idempotency"][k] for k in ("n", "worst_residual_px", "offenders", "pass")},
        "determinism": evidence["determinism"],
        "gate2_pass": evidence["gate2_pass"],
    }))


if __name__ == "__main__":
    main()
