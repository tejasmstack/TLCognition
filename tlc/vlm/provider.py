"""Provider abstraction, exactly per spec 03 §7.9.1, plus the mode-guarded registry (§7.9.6)."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from tlc.vlm.errors import VLMModeError

Mode = Literal["live", "replay", "off"]
LIVE_PROVIDERS = ("anthropic", "gemini")


class CropSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: Literal["header_band", "label_row", "full_plate"]
    rule_version: str
    box_src_px: tuple[int, int, int, int]  # x0, y0, x1, y1 in the *source* frame
    resample: Literal["none"] = "none"  # F13/F15: native resolution, never upscale


class VLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_id: str
    prompt_version: str
    schema_id: str
    schema_version: str
    crop: CropSpec
    crop_bytes_sha256: str
    model_id: str
    temperature: float
    max_output_tokens: int
    sample_index: int


class VLMResponse(BaseModel):
    parsed: dict[str, Any]
    raw_text: str
    response_sha256: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    attempts: int = 1
    from_cache: bool = False


@runtime_checkable
class VLMProvider(Protocol):
    name: str

    async def complete(self, req: VLMRequest, image: bytes) -> VLMResponse: ...


def get_provider(name: str, mode: Mode, **kwargs: Any) -> VLMProvider:
    """Registry.  Raises ``VLMModeError`` at construction if a live provider is requested
    while ``mode != "live"`` -- you cannot accidentally reach the network from replay/off.

    kwargs: ``store`` (replay), ``model_id`` (live), ``schema`` (null).
    """
    if name in LIVE_PROVIDERS and mode != "live":
        raise VLMModeError(f"live provider {name!r} requested in mode={mode!r}")
    if name == "null":
        from tlc.vlm.null import NullProvider

        return NullProvider(**kwargs)
    if name == "replay":
        from tlc.vlm.replay import ReplayProvider

        return ReplayProvider(**kwargs)
    if name == "anthropic":
        from tlc.vlm.anthropic import AnthropicProvider

        return AnthropicProvider(mode=mode, **kwargs)
    if name == "gemini":
        from tlc.vlm.gemini import GeminiProvider

        return GeminiProvider(mode=mode, **kwargs)
    raise KeyError(f"unknown VLM provider {name!r}")
