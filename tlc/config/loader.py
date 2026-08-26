"""PipelineConfig loader (spec 03 §7.10): the TOML document is loaded verbatim, hashed, and
embedded in every result. Released files are immutable; CI checks config/pipeline/HASHES.txt."""

import tomllib
from pathlib import Path

from tlc.core.hashing import sha256_bytes, sha256_canonical

ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_DIR = ROOT / "config" / "pipeline"


def load_pipeline(version: str) -> tuple[dict, str, str]:
    """Return (config_document, config_hash, config_ref) for a released version."""
    path = PIPELINE_DIR / f"v{version}.toml"
    raw = path.read_bytes()
    doc = tomllib.loads(raw.decode("utf-8"))
    if doc.get("version") != version:
        raise ValueError(f"{path.name} declares version {doc.get('version')!r}, expected {version!r}")
    doc["config_ref"] = str(path.relative_to(ROOT))
    doc["config_file_sha256"] = sha256_bytes(raw)
    return doc, sha256_canonical(doc), doc["config_ref"]


def released_hashes() -> dict[str, str]:
    return {p.name: sha256_bytes(p.read_bytes()) for p in sorted(PIPELINE_DIR.glob("v*.toml"))}
