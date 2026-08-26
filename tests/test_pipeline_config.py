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
    doc, config_hash, ref = load_pipeline("0.5.0")
    assert doc["version"] == "0.5.0" and ref.endswith("v0.5.0.toml")
    assert len(config_hash) == 64
    assert doc["capture_gates"]["green_clip_max"] == 0.15
    assert doc["photometry"]["od_transform"] == "log10"
