"""LIVE STUB -- GeminiProvider (generateContent with ``responseSchema`` +
``responseMimeType="application/json"``).  Never exercised in tests.  Requires GEMINI_API_KEY at
call time only."""

from __future__ import annotations

import base64
import json
import time

from tlc.vlm.cache import response_sha256
from tlc.vlm.live import LiveBase
from tlc.vlm.provider import VLMRequest, VLMResponse
from tlc.vlm.resources import load_schema, prompt_for_sample

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _gemini_schema(schema: dict) -> dict:
    """Gemini's OpenAPI subset: no $id/additionalProperties, single type, enums are strings."""
    out = {}
    for k, v in schema.items():
        if k in ("$id", "additionalProperties", "maxLength", "minItems", "maxItems"):
            continue
        if k == "type" and isinstance(v, list):
            non_null = [t for t in v if t != "null"]
            out["type"] = non_null[0] if non_null else "string"
            out["nullable"] = "null" in v
        elif k == "enum":
            out["type"] = "string"
            out["enum"] = [str(e) for e in v]
        elif isinstance(v, dict):
            out[k] = {kk: _gemini_schema(vv) for kk, vv in v.items()} if k == "properties" \
                else _gemini_schema(v)
        else:
            out[k] = v
    return out


class GeminiProvider(LiveBase):
    name = "gemini"
    api_key_env = "GEMINI_API_KEY"

    def build_body(self, req: VLMRequest, image: bytes, anchor: str | None = None,
                   repair_note: str | None = None) -> dict:  # fmt: skip
        text = prompt_for_sample(req.prompt_id, req.prompt_version, req.sample_index, anchor)
        if repair_note:
            text += f"\n\nYour previous output failed validation: {repair_note}. Fix it."
        return {
            "contents": [{"role": "user", "parts": [
                {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(image).decode()}},
                {"text": text},
            ]}],
            "generationConfig": {
                "temperature": req.temperature,  # 1.0 -- self-consistency needs diversity
                "maxOutputTokens": req.max_output_tokens,
                "responseMimeType": "application/json",
                "responseSchema": _gemini_schema(load_schema(req.schema_id, req.schema_version)),
                "mediaResolution": "MEDIA_RESOLUTION_LOW",  # §8.2: OCR saturates below high
            },
        }  # fmt: skip

    async def complete(self, req: VLMRequest, image: bytes) -> VLMResponse:
        headers = {"x-goog-api-key": self.api_key(), "content-type": "application/json"}
        t0 = time.monotonic()
        url = API_URL.format(model=req.model_id)
        r = await self._post_with_retry(url, headers, self.build_body(req, image))
        data = r.json()
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(raw)
        # Gemini enums are strings; restore integer lane_count for the shared schema.
        if isinstance(parsed.get("lane_count"), str) and parsed["lane_count"].isdigit():
            parsed["lane_count"] = int(parsed["lane_count"])
        usage = data.get("usageMetadata", {})
        return VLMResponse(
            parsed=parsed, raw_text=raw, response_sha256=response_sha256(raw),
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            cached_tokens=usage.get("cachedContentTokenCount", 0),
            latency_ms=int((time.monotonic() - t0) * 1000), attempts=1,
        )  # fmt: skip
