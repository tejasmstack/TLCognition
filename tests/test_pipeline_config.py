"""Released pipeline configs are immutable (spec 03 §7.2.3): CI hashes every file in
config/pipeline/ against the committed HASHES.txt and fails on any change to an existing version."""

from pathlib import Path

from tlc.config.loader import load_pipeline, released_hashes

ROOT = Path(__file__).resolve().parent.parent


def test_released_configs_unchanged():
    committed = {}
    for line in (ROOT / "config" / "pipeline" / "HASHES.txt").read_text().splitlines():
        h, name = line.split()
        committed[name] = h
    current = released_hashes()
    for name, h in committed.items():
        assert current.get(name) == h, f"{name} changed after release — add a new version instead"


def test_load_pipeline_embeds_and_hashes():
    doc, config_hash, ref = load_pipeline("0.6.0")
    assert doc["version"] == "0.6.0" and ref.endswith("v0.6.0.toml")
    assert len(config_hash) == 64
    assert doc["capture_gates"]["green_clip_max"] == 0.15
    assert doc["photometry"]["od_transform"] == "log10"


def test_superseded_versions_still_load_so_old_runs_replay():
    """An immutable release is only useful if it can still be read: v0.5.0 runs must replay at v0.5.0."""
    doc, _, ref = load_pipeline("0.5.0")
    assert doc["version"] == "0.5.0" and ref.endswith("v0.5.0.toml")
    assert doc["operating_point"]["ref"].endswith("OPERATING_POINT_v1.json")


def test_the_shipped_operating_point_is_the_one_the_config_resolves(tmp_path):
    """M-018: prose said v2, the loader said v1, and the runs believed the loader."""
    import json

    from tlc.jobs.service import DEFAULT_PIPELINE_VERSION

    doc, _, _ = load_pipeline(DEFAULT_PIPELINE_VERSION)
    ref = ROOT / doc["operating_point"]["ref"]
    assert ref.exists(), f"the shipped config points at a missing operating point: {ref}"
    op = json.loads(ref.read_text())
    assert op["id"] == ref.stem
    assert 0.0 < op["tiers"]["reported"]["agreement_min"] <= 1.0
    assert op["tiers"]["candidate"]["agreement_min"] <= op["tiers"]["reported"]["agreement_min"]
    assert op.get("honest_claim"), "an operating point without a stated claim is a number with no meaning"
