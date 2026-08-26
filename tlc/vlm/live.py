"""Shared plumbing for live providers: §7.9.4 policy table, retry with full-jitter backoff,
circuit breaker, and the mode guard.  Importable with no API key; never used in tests."""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any

import httpx

from tlc.vlm.errors import VLMModeError, VLMUnavailable

POLICY: dict[str, Any] = {
    "connect_timeout_s": 5.0,
    "read_timeout_s": 60.0,
    "budget_wall_s_per_run": 300.0,
    "max_cost_usd_per_run": 0.05,
    "retry_on_status": (408, 429, 500, 502, 503, 504),
    "no_retry_on_status": (400, 401, 403, 422),
    "backoff_base_s": 1.0,
    "backoff_cap_s": 30.0,
    "max_attempts": 4,
    "retry_after_cap_s": 60.0,
    "breaker_failures": 5,
    "breaker_open_s": 120.0,
}


class CircuitBreaker:
    def __init__(self) -> None:
        self.failures = 0
        self.opened_at: float | None = None
        self.half_open_probe_inflight = False

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at < POLICY["breaker_open_s"]:
            return False
        if self.half_open_probe_inflight:  # half-open: exactly one probe
            return False
        self.half_open_probe_inflight = True
        return True

    def success(self) -> None:
        self.failures, self.opened_at, self.half_open_probe_inflight = 0, None, False

    def failure(self) -> None:
        self.failures += 1
        self.half_open_probe_inflight = False
        if self.failures >= POLICY["breaker_failures"]:
            self.opened_at = time.monotonic()


class LiveBase:
    """Subclasses implement ``_build(req, image) -> (url, headers, json_body)`` and
    ``_parse(json) -> (raw_text, usage)``.  Constructible only in mode="live"."""

    name = "live"
    api_key_env = ""

    def __init__(self, mode: str, model_id: str, **_: Any) -> None:
        if mode != "live":
            raise VLMModeError(f"{type(self).__name__} may only be constructed with mode='live'")
        self.model_id = model_id
        self.breaker = CircuitBreaker()
        self.spent_usd = 0.0
        self.started = time.monotonic()
        self.retries = 0

    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise VLMUnavailable(f"{self.api_key_env} not set")
        return key

    def _check_budget(self) -> None:
        if time.monotonic() - self.started > POLICY["budget_wall_s_per_run"]:
            raise VLMUnavailable("wall budget exhausted")
        if self.spent_usd >= POLICY["max_cost_usd_per_run"]:
            raise VLMUnavailable("cost budget exhausted")
        if not self.breaker.allow():
            raise VLMUnavailable("circuit breaker open")

    async def _post_with_retry(self, url: str, headers: dict, body: dict) -> httpx.Response:
        timeout = httpx.Timeout(connect=POLICY["connect_timeout_s"], read=POLICY["read_timeout_s"],
                                write=30.0, pool=5.0)  # fmt: skip
        last: Exception | None = None
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(POLICY["max_attempts"]):
                self._check_budget()
                try:
                    r = await client.post(url, headers=headers, json=body)
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                    last, retry_after = e, None
                else:
                    if r.status_code < 400:
                        self.breaker.success()
                        return r
                    if r.status_code in POLICY["no_retry_on_status"] or (
                        r.status_code not in POLICY["retry_on_status"]
                    ):
                        self.breaker.failure()
                        raise VLMUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
                    last = VLMUnavailable(f"HTTP {r.status_code}")
                    retry_after = r.headers.get("Retry-After")
                self.breaker.failure()
                self.retries += 1
                sleep = random.uniform(0, min(POLICY["backoff_cap_s"],
                                              POLICY["backoff_base_s"] * 2**attempt))  # fmt: skip
                if retry_after is not None:
                    try:
                        sleep = min(float(retry_after), POLICY["retry_after_cap_s"])
                    except ValueError:
                        pass
                await asyncio.sleep(sleep)
        raise VLMUnavailable(f"max attempts exceeded: {last}")
