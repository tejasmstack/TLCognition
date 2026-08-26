"""Pre-registered hypothesis registry (spec 02 §5): content-hashed; adding one is a code change."""

import json
from functools import lru_cache
from pathlib import Path

from tlc.core.hashing import sha256_bytes

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "config" / "insight" / "hypotheses.json"


@lru_cache(maxsize=1)
def load_registry() -> tuple[dict, str]:
    raw = REGISTRY_PATH.read_bytes()
    doc = json.loads(raw)
    for h in doc["hypotheses"]:
        if not h.get("falsifier"):
            raise ValueError(f"{h['id']}: a hypothesis without a falsifier does not ship (§5.3)")
    return doc, sha256_bytes(raw)


def hypothesis(hid: str) -> dict:
    doc, _ = load_registry()
    return next(h for h in doc["hypotheses"] if h["id"] == hid)


def registered(cls: str | None = None, family: str | None = None) -> list[dict]:
    doc, _ = load_registry()
    return [h for h in doc["hypotheses"] if h["status"] == "registered"
            and (cls is None or h["class"] == cls) and (family is None or h["family"] == family)]
