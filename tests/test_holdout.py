"""Gate 6's hold-out isolation: the pipeline must not be able to read the hold-out truth.

A `partition = 'holdout'` column in the same database is a promise. These tests check the location.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tlc.calibration.calibrate import MIN_PLATES, fit
from tlc.pipeline.flags import Refusal
from tlc.storage.db import init_schema, make_engine
from tlc.storage.repositories import Repo

ROOT = Path(__file__).resolve().parents[1]


def _repo(tmp_path: Path) -> Repo:
    engine = make_engine(tmp_path / "tlc.sqlite")
    init_schema(engine)
    return Repo(engine)


def _seed(repo: Repo, n: int = 6) -> None:
    for i in range(n):
        image_id = f"img_{i:020d}"
        repo.upsert_image(image_id, f"{i:064d}", 10, "image/png", 100, 200, None, "/dev/null", "x.png", None, {})
        part = "holdout" if i % 3 == 2 else ("calibrate" if i % 3 == 1 else "tune")
        repo.upsert_label_record(f"lab_{i:020d}", image_id, "agreed", 2, {"verdict": "agreed"},
                                 {"bands": [{"lane_index": 0, "y_frac": 0.5}]}, part, [f"cor_{i}"])


def test_no_module_under_tlc_reads_the_holdout_location():
    """The sealed store is reachable from tools/ and from a human, not from the pipeline."""
    offenders = []
    for py in sorted((ROOT / "tlc").rglob("*.py")):
        text = py.read_text()
        # the partition NAME is legitimate pipeline vocabulary; a PATH into the sealed store is not
        # the partition NAME is legitimate pipeline vocabulary, and a method that DELETES the truth
        # is fine; what tlc/ must never contain is a PATH into the sealed store
        for needle in ('"holdout/', "'holdout/", "holdout_dir", "HOLDOUT_DIR", "MANIFEST.json"):
            if needle in text:
                offenders.append(f"{py.relative_to(ROOT)}: {needle}")
    assert not offenders, "tlc/ must not reference the hold-out store: " + "; ".join(offenders)
    # the one hold-out-aware repository method may remove the truth, never return it
    seal = (ROOT / "tlc" / "storage" / "repositories.py").read_text()
    body = seal[seal.index("def seal_holdout"):]
    body = body[:body.index("\n    def ", 1)] if "\n    def " in body[1:] else body
    assert "UPDATE label_records SET payload_json='{}'" in body
    assert "SELECT" not in body.upper().replace("SELECTION", ""), "seal_holdout must not read the payload"


def test_label_records_can_exclude_the_holdout_partition(tmp_path):
    repo = _repo(tmp_path)
    _seed(repo)
    everything = repo.label_records()
    fittable = repo.label_records(partitions=("tune", "calibrate"))
    assert {r["partition"] for r in everything} == {"tune", "calibrate", "holdout"}
    assert {r["partition"] for r in fittable} == {"tune", "calibrate"}
    assert len(fittable) < len(everything)


def test_sealing_removes_the_truth_from_the_database(tmp_path):
    repo = _repo(tmp_path)
    _seed(repo, n=9)
    holdout_dir = tmp_path / "holdout"
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "seal_holdout.py"), "--data-dir", str(tmp_path),
         "--holdout-dir", str(holdout_dir)],
        capture_output=True, text=True, cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert r.returncode == 0, r.stderr
    sealed = sorted(p for p in holdout_dir.glob("img_*.json"))
    assert sealed, r.stdout
    for p in sealed:
        doc = json.loads(p.read_text())
        assert doc["payload"]["bands"], "the truth is in the sealed file"
        assert not (os.stat(p).st_mode & 0o222), "sealed files are read-only"
    for row in repo.label_records(partitions=("holdout",)):
        assert row["sealed_at"], "the row records that it was sealed"
        assert json.loads(row["payload_json"]) == {}, "the truth is no longer in the database"
    assert (holdout_dir / "MANIFEST.json").exists()


def test_sealing_twice_is_a_no_op(tmp_path):
    repo = _repo(tmp_path)
    _seed(repo, n=9)
    holdout_dir = tmp_path / "holdout"
    args = [sys.executable, str(ROOT / "tools" / "seal_holdout.py"), "--data-dir", str(tmp_path),
            "--holdout-dir", str(holdout_dir)]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    first = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, env=env)
    second = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, env=env)
    assert first.returncode == 0 and second.returncode == 0
    assert "no unsealed hold-out labels" in second.stdout
    assert len(json.loads((holdout_dir / "MANIFEST.json").read_text())) == len(list(holdout_dir.glob("img_*.json")))


def test_calibration_still_refuses_without_enough_labels(tmp_path):
    """The sealed store changes nothing about NN4: no map exists until Gate 6 is met."""
    repo = _repo(tmp_path)
    _seed(repo, n=9)
    fittable = repo.label_records(partitions=("tune", "calibrate"))
    out = fit([0.6] * len(fittable), [1.0] * len(fittable), [r["image_id"] for r in fittable])
    assert isinstance(out, Refusal) and out.code == "E_UNCALIBRATED"
    assert out.evidence["required"] == MIN_PLATES


def test_committed_holdout_dir_holds_no_secrets():
    """Whatever is in holdout/ today must be either nothing or sealed files — never a stray export."""
    d = ROOT / "holdout"
    if not d.exists():
        pytest.skip("no hold-out directory yet")
    for p in d.iterdir():
        assert p.name in ("MANIFEST.json", ".gitkeep") or p.suffix == ".json", f"unexpected file {p.name}"
