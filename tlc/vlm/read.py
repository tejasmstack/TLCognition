"""``read_plate_semantics``: crops -> n samples per prompt (cache first) -> validate -> aggregate.

Crops are taken at NATIVE resolution from the rectified plate (F13/F15: never upscale) and
PNG-encoded; the cache key is the crop's bytes.  5 samples at temperature 1.0 -- temperature 0
would give five near-identical samples and an agreement of 1.0 that means nothing (§7.9.3).
"""

from __future__ import annotations

import asyncio
import io
from typing import Any, Literal

import numpy as np
from PIL import Image

from tlc.core.hashing import sha256_bytes
from tlc.vlm import aggregate as agg
from tlc.vlm.cache import VLMStore, bundle_hash, cache_key, make_row
from tlc.vlm.errors import E_VLM_UNAVAILABLE, VLMCacheMiss, VLMUnavailable
from tlc.vlm.provider import CropSpec, VLMProvider, VLMRequest, get_provider
from tlc.vlm.resources import PROMPT_IDS, load_schema
from tlc.vlm.schema_validate import validate

CROP_RULE_VERSION = "v1"
PROMPT_VERSIONS = {"bands": "v1", "lanes": "v1", "front": "v1", "header": "v1"}
SCHEMA_VERSIONS = dict(PROMPT_VERSIONS)
PROMPT_CROP = {"bands": "full_plate", "lanes": "label_row", "front": "full_plate",
               "header": "header_band"}  # fmt: skip
# Crop rule v1: header = top 18 % of the rectified plate, label row = bottom 18 %.
HEADER_FRAC, LABEL_ROW_FRAC = 0.18, 0.18
MAX_OUTPUT_TOKENS = 512
Mode = Literal["live", "replay", "off"]


def build_crops(rectified: np.ndarray) -> dict[str, tuple[CropSpec, bytes]]:
    h, w = rectified.shape[:2]
    boxes = {
        "full_plate": (0, 0, w, h),
        "header_band": (0, 0, w, max(1, int(round(h * HEADER_FRAC)))),
        "label_row": (0, min(h - 1, int(round(h * (1 - LABEL_ROW_FRAC)))), w, h),
    }
    out = {}
    for rule, (x0, y0, x1, y1) in boxes.items():
        buf = io.BytesIO()
        Image.fromarray(rectified[y0:y1, x0:x1]).save(buf, format="PNG", compress_level=6)
        spec = CropSpec(rule_id=rule, rule_version=CROP_RULE_VERSION, box_src_px=(x0, y0, x1, y1))
        out[rule] = (spec, buf.getvalue())
    return out


def repair_hook(provider: VLMProvider, req: VLMRequest, image: bytes, errors: list[str]) -> Any:
    """ONE repair attempt (§7.9.2).  Null/Replay have nothing to resend, so the hook returns
    None; live providers may override by exposing ``repair`` -- absent here by design so a
    live repair is a deliberate later addition, not an accident."""
    fn = getattr(provider, "repair", None)
    return fn(req, image, errors) if fn is not None else None


async def _draw(provider: VLMProvider, provider_name: str, store: VLMStore | None,
                req: VLMRequest, image: bytes, read: agg.SemanticRead) -> dict | None:  # fmt: skip
    key = cache_key(provider_name, req)
    row = store.get(key) if store is not None else None
    if row is not None and row.get("response_json") is not None:
        from tlc.vlm.cache import row_to_response

        resp = row_to_response(row)
        read.cache["hits"] = int(read.cache.get("hits", 0)) + 1
    else:
        resp = await provider.complete(req, image)
        read.cache["misses"] = int(read.cache.get("misses", 0)) + 1
        if store is not None and read.mode == "live":
            store.put(make_row(provider_name, req, resp))
    read.attempts += resp.attempts
    read.cost["input_tokens"] = int(read.cost.get("input_tokens", 0)) + resp.input_tokens
    read.cost["output_tokens"] = int(read.cost.get("output_tokens", 0)) + resp.output_tokens
    read.cost["usd"] = float(read.cost.get("usd", 0.0)) + resp.cost_usd
    read._keys.append(key)
    read._shas.append(resp.response_sha256)
    schema = load_schema(req.schema_id, req.schema_version)
    errs = validate(resp.parsed, schema)
    if not errs:
        return resp.parsed
    repaired = repair_hook(provider, req, image, errs)
    if repaired is not None and not validate(repaired, schema):
        return repaired
    read.invalid_samples += 1
    return None


async def _run(provider: VLMProvider, provider_name: str, store: VLMStore | None,
               crops: dict, model_id: str, n_samples: int, temperature: float,
               read: agg.SemanticRead) -> None:  # fmt: skip
    for pid in PROMPT_IDS:
        spec, png = crops[PROMPT_CROP[pid]]
        sha = sha256_bytes(png)
        valid: list[dict] = []
        try:
            for i in range(n_samples):
                req = VLMRequest(
                    prompt_id=pid, prompt_version=PROMPT_VERSIONS[pid], schema_id=pid,
                    schema_version=SCHEMA_VERSIONS[pid], crop=spec, crop_bytes_sha256=sha,
                    model_id=model_id, temperature=temperature,
                    max_output_tokens=MAX_OUTPUT_TOKENS, sample_index=i,
                )  # fmt: skip
                parsed = await _draw(provider, provider_name, store, req, png, read)
                if parsed is not None:
                    valid.append(parsed)
        except VLMUnavailable:
            # §7.9.4 degradation, never failure: this prompt's fields abstain as unavailable.
            read.degraded = True
            for f in _fields_of(pid):
                read._set(f, agg.FieldRead(None, reason=E_VLM_UNAVAILABLE))
            continue
        agg.AGGREGATORS[pid](valid, n_samples, read)


def _fields_of(pid: str) -> tuple[str, ...]:
    return {"lanes": ("lane_count", "lane_labels"), "bands": ("header_y1_frac", "label_row_y0_frac"),
            "front": ("front_present",), "header": ("header_text",)}[pid]  # fmt: skip


def read_plate_semantics(
    rectified_rgb_u8: np.ndarray,
    source_rgb_u8: np.ndarray | None,
    mode: Mode,
    store: VLMStore | None,
    provider_name: str,
    model_id: str,
    n_samples: int = 5,
    temperature: float = 1.0,
) -> agg.SemanticRead:
    """Off: NullProvider, all-UNREADABLE with typed abstentions.  Replay: store only, raises
    ``VLMCacheMiss`` on a miss.  Live: named provider, new responses stored.  ``source_rgb_u8`` is
    accepted for provenance (CropSpec boxes are reported in the rectified frame under rule v1)."""
    if rectified_rgb_u8.dtype != np.uint8 or rectified_rgb_u8.ndim != 3:
        raise ValueError("rectified_rgb_u8 must be HxWx3 uint8")
    if mode == "off":
        provider = get_provider("null", mode)
    elif mode == "replay":
        if store is None:
            raise VLMCacheMiss("replay mode requires a store")
        provider = get_provider("replay", mode, store=store, provider_name=provider_name)
    else:
        provider = get_provider(provider_name, mode, model_id=model_id)
    read = agg.SemanticRead(mode=mode, model_id=None if mode == "off" else model_id,
                            prompt_bundle=dict(PROMPT_VERSIONS), n_samples=n_samples,
                            temperature=temperature, cache={"hits": 0, "misses": 0})  # fmt: skip
    read._keys, read._shas = [], []  # type: ignore[attr-defined]
    crops = build_crops(rectified_rgb_u8)
    asyncio.run(_run(provider, provider_name, store, crops, model_id, n_samples, temperature, read))
    read.retries = getattr(provider, "retries", 0)
    read.cache["bundle_hash"] = bundle_hash(read._keys, read._shas)  # type: ignore[attr-defined]
    del read._keys, read._shas  # type: ignore[attr-defined]
    return read
