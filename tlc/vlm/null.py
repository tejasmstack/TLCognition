"""NullProvider: the all-UNREADABLE document for a schema (``vlm_mode="off"``).  Deterministic."""

from __future__ import annotations

from typing import Any

from tlc.vlm.cache import response_sha256
from tlc.vlm.provider import VLMRequest, VLMResponse
from tlc.vlm.resources import load_schema


def unreadable_document(schema: dict[str, Any]) -> Any:
    """Build the minimal valid instance in which every enum is UNREADABLE, every nullable is
    null, every array is empty and every free string is ''."""
    if "enum" in schema:
        return "UNREADABLE" if "UNREADABLE" in schema["enum"] else schema["enum"][0]
    t = schema.get("type")
    ts = t if isinstance(t, list) else [t]
    if "null" in ts:
        return None
    if "object" in ts:
        props = schema.get("properties", {})
        return {k: unreadable_document(props[k]) for k in schema.get("required", []) if k in props}
    if "array" in ts:
        return []
    if "string" in ts:
        return ""
    if "boolean" in ts:
        return False
    return 0


class NullProvider:
    name = "null"

    def __init__(self, **_: Any) -> None:
        pass

    async def complete(self, req: VLMRequest, image: bytes) -> VLMResponse:
        from tlc.core.canonical_json import canonical_json

        doc = unreadable_document(load_schema(req.schema_id, req.schema_version))
        raw = canonical_json(doc)
        return VLMResponse(parsed=doc, raw_text=raw, response_sha256=response_sha256(raw))
