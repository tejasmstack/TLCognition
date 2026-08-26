"""Content hashing helpers for the four run identifiers (spec 03 §7.2.1)."""

import hashlib
from pathlib import Path
from typing import Any

from tlc.core.canonical_json import canonical_json_bytes


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_canonical(obj: Any) -> str:
    return sha256_bytes(canonical_json_bytes(obj))


def tree_fingerprint(*roots: Path) -> str:
    """sha256 over sorted (relpath, sha256(bytes)) of every .py file under the given roots.

    Used for code_fingerprint over tlc/pipeline/ + tlc/core/ — NOT the git commit; a dirty
    tree must not masquerade as clean.
    """
    entries: list[tuple[str, str]] = []
    for root in roots:
        base = root.parent
        for p in sorted(root.rglob("*.py")):
            entries.append((str(p.relative_to(base)), sha256_file(p)))
    entries.sort()
    return sha256_canonical(entries)
