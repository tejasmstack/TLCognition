"""OD-field storage: one HDF5 file per run (spec 03 §7.4). The 2-D optical-density field is the
raw data ICH Q2(R2) asks to be reconstructable from; the densitogram is a rendering."""

import hashlib
from pathlib import Path

import h5py
import numpy as np


def write_run_h5(path: Path | str, od: np.ndarray, od_valid: np.ndarray, densitograms: dict[int, np.ndarray],
                 attrs: dict) -> str:
    """Write OD (float32), validity mask, per-lane densitograms; return sha256 of the OD bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    od32 = od.astype(np.float32)
    with h5py.File(path, "w") as f:
        d = f.create_dataset("od", data=od32, compression="gzip", compression_opts=4, shuffle=True)
        d.attrs["unit"] = "OD"
        f.create_dataset("od_valid", data=od_valid.astype(np.uint8), compression="gzip")
        g = f.create_group("densitograms")
        for lane, prof in sorted(densitograms.items()):
            g.create_dataset(f"lane_{lane:02d}", data=prof.astype(np.float32))
        for k, v in attrs.items():
            f.attrs[k] = v
    return hashlib.sha256(od32.tobytes()).hexdigest()


def read_od(path: Path | str) -> tuple[np.ndarray, np.ndarray]:
    with h5py.File(path, "r") as f:
        return f["od"][()], f["od_valid"][()].astype(bool)


def read_densitograms(path: Path | str) -> dict[int, np.ndarray]:
    """The authoritative per-lane traces. The preview in the result JSON is decimated and explicitly
    non-authoritative (spec 03 §7.3.3), so anything that computes — curve alignment, co-spot
    decomposition — reads these."""
    out: dict[int, np.ndarray] = {}
    with h5py.File(path, "r") as f:
        g = f.get("densitograms")
        if g is None:
            return out
        for name in g:
            out[int(str(name).split("_")[1])] = np.asarray(g[name][()], dtype=np.float64)
    return out
