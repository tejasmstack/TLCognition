"""Dependency wiring: one RunService per process (spec 03 §7.10: RuntimeSettings from env)."""

import os
from functools import lru_cache

from tlc.jobs.service import RunService


@lru_cache(maxsize=1)
def get_service() -> RunService:
    return RunService(os.environ.get("TLC_DATA_DIR", "data"))
