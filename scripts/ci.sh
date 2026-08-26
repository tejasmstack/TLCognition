#!/usr/bin/env bash
# Local CI — the same steps the workflow file runs. Gate evidence is this script's exit code.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== uv sync (frozen) =="
uv sync --frozen --quiet

echo "== ruff =="
uv run ruff check tlc tests scripts

echo "== pytest =="
uv run pytest

echo "CI GREEN"
