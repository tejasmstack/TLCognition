"""LIVE STUB -- AnthropicProvider (Messages API, tool-use with ``input_schema`` for constrained
output).  Never exercised in tests; the request shape is what matters here.  Requires
ANTHROPIC_API_KEY at call time only."""

from __future__ import annotations

import base64
import json
import time

from tlc.vlm.cache import response_sha256
from tlc.vlm.live import LiveBase
from tlc.vlm.provider import VLMRequest, VLMResponse
from tlc.vlm.resources import load_schema, prompt_for_sample

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicProvider(LiveBase):
    name = "anthropic"
    api_key_env = "ANTHROPIC_API_KEY"

    def build_body(self, req: VLMRequest, image: bytes, anchor: str | None = None,
                   repair_note: str | None = None) -> dict:  # fmt: skip
        schema = load_schema(req.schema_id, req.schema_version)
        text = prompt_for_sample(req.prompt_id, req.prompt_version, req.sample_index, anchor)
        if repair_note:
            text += f"\n\nYour previous output failed validation: {repair_note}. Fix it."
        tool = {"name": f"report_{req.schema_id}", "description": "Structured plate reading",
                "input_schema": schema}  # fmt: skip
        return {
            "model": req.model_id,
            "max_tokens": req.max_output_tokens,
            "temperature": req.temperature,  # 1.0 -- self-consistency needs diversity (§7.9.3)
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": text},
            ]}],
        }  # fmt: skip

    async def complete(self, req: VLMRequest, image: bytes) -> VLMResponse:
        headers = {"x-api-key": self.api_key(), "anthropic-version": API_VERSION,
                   "content-type": "application/json"}  # fmt: skip
        t0 = time.monotonic()
        r = await self._post_with_retry(API_URL, headers, self.build_body(req, image))
        data = r.json()
        tool_use = next(b for b in data["content"] if b["type"] == "tool_use")
        raw = json.dumps(tool_use["input"], sort_keys=True, separators=(",", ":"))
        usage = data.get("usage", {})
        return VLMResponse(
            parsed=tool_use["input"], raw_text=raw, response_sha256=response_sha256(raw),
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
            cached_tokens=usage.get("cache_read_input_tokens", 0),
            latency_ms=int((time.monotonic() - t0) * 1000), attempts=1,
        )  # fmt: skip
