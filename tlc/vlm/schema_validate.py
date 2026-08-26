"""Minimal JSON-Schema (draft-7 subset) validator: type, enum, required, properties, items,
minimum/maximum, maxLength, minItems/maxItems, additionalProperties.  Enough for our four
committed schemas without adding a dependency.  Returns a list of error strings ([] = valid)."""

from __future__ import annotations

from typing import Any

_TYPES = {
    "object": dict, "array": list, "string": str, "number": (int, float),
    "integer": int, "boolean": bool, "null": type(None),
}  # fmt: skip


def _is_type(v: Any, t: str) -> bool:
    if t in ("number", "integer") and isinstance(v, bool):
        return False
    if t == "number" and isinstance(v, float) and v != v:
        return False
    return isinstance(v, _TYPES[t])


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errs: list[str] = []
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is_type(instance, x) for x in ts):
            return [f"{path}: expected type {ts}, got {type(instance).__name__}"]
    if "enum" in schema and instance not in schema["enum"]:
        errs.append(f"{path}: {instance!r} not in enum")
    if isinstance(instance, int | float) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errs.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errs.append(f"{path}: {instance} > maximum {schema['maximum']}")
    if isinstance(instance, str) and "maxLength" in schema and len(instance) > schema["maxLength"]:
        errs.append(f"{path}: string longer than {schema['maxLength']}")
    if isinstance(instance, dict):
        for k in schema.get("required", []):
            if k not in instance:
                errs.append(f"{path}: missing required {k!r}")
        props = schema.get("properties", {})
        for k, v in instance.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: additional property {k!r}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(instance):
                errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs
