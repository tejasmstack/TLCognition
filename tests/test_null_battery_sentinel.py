"""Per-commit null-battery SENTINEL (spec 05 §12.8; A-016).

A reduced battery — 20 synthetic blanks through the shipped CONFIG_GRID_v1 ensemble at the
REPORTED tier of whatever operating point the SHIPPED pipeline config resolves to (M-018: never a
filename written here) — whose job is regression detection,
not the gate. The full 200-blank battery (scripts/gate4_check.py) is the Gate 4 evidence and
the nightly artifact. The FP-per-blank number is printed so drift is visible before it crosses.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from tlc.pipeline.ensemble import run_ensemble_lane
from tlc.pipeline.geometry import analyse_geometry
from tlc.pipeline.noise import estimate_noise, prepass_exclusion_mask
from tlc.pipeline.prep import rectify_and_mask
from tlc.synth.generator import make_plate
from tlc.synth.spec import Handwriting, PlateSpec

ROOT = Path(__file__).resolve().parent.parent
GRID = ROOT / "config" / "ensemble" / "CONFIG_GRID_v1.json"


def _shipped_operating_point() -> Path:
    from tlc.config.loader import load_pipeline
    from tlc.jobs.service import DEFAULT_PIPELINE_VERSION

    doc, _, _ = load_pipeline(DEFAULT_PIPELINE_VERSION)
    return ROOT / doc["operating_point"]["ref"]


OP_POINT = _shipped_operating_point()
N_BLANKS = 20
SENTINEL_BOUND = 0.5   # looser than the gate's 0.2 on purpose: n=20 is a tripwire, not a measurement


def _grid():
    from tlc.pipeline.configs import Config

    doc = json.loads(GRID.read_text())
    cfgs, w = [], []
    for row in doc["configs"]:
        m, rest = row["key"].split("@")
        r, sv, ex, pm = rest.split("/")
        cfgs.append(Config(m, int(r), sv, ex, pm))
        w.append(row["weight"])
    return cfgs, np.array(w)


@pytest.mark.slow
def test_null_battery_sentinel():
    if not GRID.exists() or not OP_POINT.exists():
        pytest.skip("CONFIG_GRID_v1 or the shipped operating point is not present")
    tier = json.loads(OP_POINT.read_text())["tiers"]["reported"]
    op = {"a_star": tier["agreement_min"], "p_star": tier["p_med_max"], "z_star": tier["z_med_min"]}
    grid, weights = _grid()
    fp = 0
    for i in range(N_BLANKS):
        seed = 12000 + i
        rng = np.random.Generator(np.random.PCG64(seed))
        spec = PlateSpec(
            plate_w=int(rng.integers(85, 160)), plate_h=int(rng.integers(150, 300)), spots=(),
            clip_fraction=float(rng.choice([0.0, 0.14])), tilt_deg=float(rng.uniform(0.4, 4.1)),
            handwriting=Handwriting.HEADER_LABELS if i % 2 else Handwriting.NONE,
        )
        img, gt = make_plate(spec, seed=seed)
        geo = analyse_geometry(img)
        pp = rectify_and_mask(img, geo)
        band = (int(gt.header_band[1] + 2), int(gt.origin_row - 4))
        excl = prepass_exclusion_mask(
            pp.green, pp.valid, 1.18 * 0.18 * gt.lane_pitch,
            ((0, gt.header_band[1]), (gt.label_band[0], pp.green.shape[0])),
        )
        noise = estimate_noise(pp.green, pp.valid, excl)
        od_cache: dict = {}
        for lane in range(spec.n_lanes):
            spots, _ = run_ensemble_lane(
                grid, weights, pp.green, pp.valid, noise, excl, lane, gt.lane_centres_x[lane],
                gt.lane_pitch, list(gt.lane_centres_x), band, seed=seed, n_surrogates=30,
                od_cache=od_cache,
            )
            fp += sum(
                1 for s in spots
                if s.agreement >= op["a_star"] and s.p_med <= op["p_star"] and s.z_med >= op.get("z_star", 0.0)
            )
    rate = fp / N_BLANKS
    print(f"\nNULL-BATTERY SENTINEL: {fp} phantoms / {N_BLANKS} synthetic blanks = {rate:.2f} per plate "
          f"(gate 0.2 on the full battery; sentinel tripwire {SENTINEL_BOUND})")
    assert rate <= SENTINEL_BOUND
