"""Canonical JSON — spec 03 §7.2.2 item 5.

allow_nan=False is deliberate: a NaN in the output is a bug; the schema requires null plus a
refusal reason instead.
"""

import json
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def canonical_json_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("ascii")
