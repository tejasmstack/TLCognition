"""Cache key (§7.9.5), bundle hash (§7.9.3) and the ``vlm_calls`` store (spec 03 §7.6 DDL)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from tlc.core.canonical_json import canonical_json
from tlc.core.hashing import sha256_bytes, sha256_canonical
from tlc.vlm.provider import VLMRequest, VLMResponse

COLUMNS = (
    "cache_key", "run_id", "provider", "model_id", "prompt_id", "prompt_version",
    "schema_id", "schema_version", "crop_sha256", "crop_rule_version", "sample_index",
    "temperature", "request_json", "response_json", "response_sha256", "input_tokens",
    "output_tokens", "cached_tokens", "cost_usd", "latency_ms", "attempts", "error",
    "created_at",
)  # fmt: skip

DDL = """CREATE TABLE IF NOT EXISTS vlm_calls (
  cache_key TEXT PRIMARY KEY, run_id TEXT, provider TEXT NOT NULL, model_id TEXT NOT NULL,
  prompt_id TEXT NOT NULL, prompt_version TEXT NOT NULL,
  schema_id TEXT NOT NULL, schema_version TEXT NOT NULL,
  crop_sha256 TEXT NOT NULL, crop_rule_version TEXT NOT NULL,
  sample_index INTEGER NOT NULL, temperature REAL NOT NULL,
  request_json TEXT NOT NULL, response_json TEXT, response_sha256 TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,
  cost_usd REAL, latency_ms INTEGER, attempts INTEGER, error TEXT, created_at TEXT NOT NULL)"""


def cache_key(provider: str, req: VLMRequest) -> str:
    """sha256 of canonical JSON of exactly the §7.9.5 fields.  Keyed on the CROP's bytes."""
    return sha256_canonical(
        {
            "provider": provider,
            "model_id": req.model_id,
            "prompt_id": req.prompt_id,
            "prompt_version": req.prompt_version,
            "schema_id": req.schema_id,
            "schema_version": req.schema_version,
            "crop_rule": req.crop.rule_id,
            "crop_rule_version": req.crop.rule_version,
            "crop_bytes_sha256": req.crop_bytes_sha256,
            "temperature": req.temperature,
            "max_output_tokens": req.max_output_tokens,
            "sample_index": req.sample_index,
        }
    )


def bundle_hash(cache_keys: list[str], response_sha256s: list[str]) -> str:
    """§7.9.3: sha256(canonical_json(sorted(cache_keys) + sorted(response_sha256s)))."""
    return sha256_canonical(sorted(cache_keys) + sorted(response_sha256s))


def response_sha256(raw_text: str) -> str:
    return sha256_bytes(raw_text.encode("utf-8"))


def make_row(provider: str, req: VLMRequest, resp: VLMResponse, run_id: str | None = None,
             error: str | None = None) -> dict[str, Any]:  # fmt: skip
    return {
        "cache_key": cache_key(provider, req),
        "run_id": run_id,
        "provider": provider,
        "model_id": req.model_id,
        "prompt_id": req.prompt_id,
        "prompt_version": req.prompt_version,
        "schema_id": req.schema_id,
        "schema_version": req.schema_version,
        "crop_sha256": req.crop_bytes_sha256,
        "crop_rule_version": req.crop.rule_version,
        "sample_index": req.sample_index,
        "temperature": req.temperature,
        "request_json": req.model_dump_json(),
        "response_json": canonical_json(resp.parsed),
        "response_sha256": resp.response_sha256,
        "input_tokens": resp.input_tokens,
        "output_tokens": resp.output_tokens,
        "cached_tokens": resp.cached_tokens,
        "cost_usd": resp.cost_usd,
        "latency_ms": resp.latency_ms,
        "attempts": resp.attempts,
        "error": error,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def row_to_response(row: dict[str, Any]) -> VLMResponse:
    import json

    return VLMResponse(
        parsed=json.loads(row["response_json"]),
        raw_text=row["response_json"],
        response_sha256=row["response_sha256"],
        input_tokens=row.get("input_tokens") or 0,
        output_tokens=row.get("output_tokens") or 0,
        cached_tokens=row.get("cached_tokens") or 0,
        cost_usd=0.0,  # replayed responses cost nothing
        latency_ms=0,
        attempts=row.get("attempts") or 1,
        from_cache=True,
    )


class VLMStore(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def put(self, row: dict[str, Any]) -> None: ...


class MemoryStore:
    """In-memory store for tests.  Same row shape as the SQLite table."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.rows.get(key)

    def put(self, row: dict[str, Any]) -> None:
        self.rows.setdefault(row["cache_key"], row)  # never evicted, never overwritten


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(DDL)
        self.conn.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        cur = self.conn.execute("SELECT * FROM vlm_calls WHERE cache_key = ?", (key,))
        r = cur.fetchone()
        return dict(r) if r is not None else None

    def put(self, row: dict[str, Any]) -> None:
        cols = ", ".join(COLUMNS)
        qs = ", ".join("?" for _ in COLUMNS)
        self.conn.execute(
            f"INSERT OR IGNORE INTO vlm_calls ({cols}) VALUES ({qs})",
            tuple(row.get(c) for c in COLUMNS),
        )
        self.conn.commit()
