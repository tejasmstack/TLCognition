"""Typed VLM errors.  Each carries a stable ``code`` that ends up in abstention records."""


class VLMError(Exception):
    code = "E_VLM"

    def __init__(self, msg: str = "") -> None:
        super().__init__(msg or self.code)


class VLMCacheMiss(VLMError):
    code = "E_VLM_CACHE_MISS"


class VLMModeError(VLMError):
    """A live provider was requested while ``mode != 'live'`` (§7.9.6)."""

    code = "E_VLM_MODE"


class VLMUnavailable(VLMError):
    """Circuit breaker open or budget exhausted (§7.9.4) -> degrade, never fail."""

    code = "E_VLM_UNAVAILABLE"


class VLMInvalidOutput(VLMError):
    code = "E_VLM_INVALID_OUTPUT"


# Abstention reason codes used by aggregate.py
E_VLM_DISAGREEMENT = "E_VLM_DISAGREEMENT"
E_VLM_UNREADABLE = "E_VLM_UNREADABLE"
E_VLM_INVALID_OUTPUT = "E_VLM_INVALID_OUTPUT"
E_VLM_UNAVAILABLE = "E_VLM_UNAVAILABLE"
