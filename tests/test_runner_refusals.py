"""Refusal totality (spec 05 §12.4 'Refusal totality'; brief NN3): adversarial inputs produce a
typed refusal with reason and remedy — never an exception, never a silent null."""

import json
from pathlib import Path

import numpy as np

from tlc.pipeline.configs import Config
from tlc.pipeline.runner import RunConfig, run_plate

ROOT = Path(__file__).resolve().parent.parent


def _cfg(n_lanes=None, labels=None) -> RunConfig:
    grid = json.loads((ROOT / "config" / "ensemble" / "CONFIG_GRID_v1.json").read_text())
    cfgs, ws = [], []
    for row in grid["configs"][:6]:  # a small sub-grid keeps the test fast; refusal paths do not depend on K
        m, rest = row["key"].split("@")
        r, sv, ex, pm = rest.split("/")
        cfgs.append(Config(m, int(r), sv, ex, pm))
        ws.append(row["weight"])
    return RunConfig(grid=tuple(cfgs), weights=tuple(ws), grid_id=grid["id"], reported_agreement_min=0.7,
                     candidate_agreement_min=0.4, p_med_max=0.0164, z_med_min=0.0, n_surrogates=20,
                     n_lanes=n_lanes, lane_labels=labels)


def test_black_image_refuses_no_plate():
    out = run_plate(np.zeros((160, 120, 3), dtype=np.uint8), _cfg(), seed=1)
    assert out.status == "refused"
    codes = {r.code for r in out.refusals}
    assert "E_NO_PLATE" in codes
    assert all(r.remedy for r in out.refusals)


def test_uniform_green_frame_yields_typed_output_not_exception():
    img = np.zeros((200, 130, 3), dtype=np.uint8)
    img[:, :, 1] = 230
    img[:, :, 0] = 66
    img[:, :, 2] = 177
    out = run_plate(img, _cfg(), seed=2)
    assert out.status in ("refused", "degraded", "succeeded")
    codes = {r.code for r in out.refusals}
    # plate fills the frame: overrun fires on every edge; lane count unknown is refused typed
    assert "E_FRAME_OVERRUN" in codes and "E_LANE_COUNT_UNKNOWN" in codes
    assert all(r.message and r.remedy for r in out.refusals)


def test_unknown_lane_labels_refuse_reference_not_rst_numbers():
    from tlc.synth.generator import make_plate
    from tlc.synth.spec import PlateSpec, SpotSpec

    img, _ = make_plate(PlateSpec(spots=(SpotSpec(lane=1, y_frac=0.5, amplitude_sigma=15.0),), tilt_deg=1.0), seed=3)
    out = run_plate(img, _cfg(n_lanes=4, labels=None), seed=3)
    assert "E_NO_REFERENCE_LANE" in {r.code for r in out.refusals}
    for sp in out.spots:
        assert sp.rst is None and sp.rst_refusal is not None  # no Rst number without a named anchor
