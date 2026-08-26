"""ReplayProvider: cache-only.  Raises ``VLMCacheMiss`` (E_VLM_CACHE_MISS) on a miss and has
no code path that can reach the network."""

from __future__ import annotations

from typing import Any

from tlc.vlm.cache import VLMStore, cache_key, row_to_response
from tlc.vlm.errors import VLMCacheMiss
from tlc.vlm.provider import VLMRequest, VLMResponse


class ReplayProvider:
    name = "replay"

    def __init__(self, store: VLMStore, provider_name: str, **_: Any) -> None:
        # ``provider_name`` is the provider that *recorded* the rows (e.g. "gemini"): it is part
        # of the cache key, so replay must look up under the same name.
        self.store = store
        self.provider_name = provider_name

    async def complete(self, req: VLMRequest, image: bytes) -> VLMResponse:
        key = cache_key(self.provider_name, req)
        row = self.store.get(key)
        if row is None or row.get("response_json") is None:
            raise VLMCacheMiss(
                f"no cached response for {req.prompt_id}/{req.prompt_version} "
                f"sample {req.sample_index} (key {key[:12]}...)"
            )
        return row_to_response(row)
