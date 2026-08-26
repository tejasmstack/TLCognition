"""Emit the frozen result JSON Schema to schemas/result_v1.schema.json.

Deterministic bytes: sorted keys, indent=2, ASCII, trailing newline. Run from the
repo root with `uv run python scripts/emit_schema.py`.
"""

import json
from pathlib import Path

from tlc.schemas.result import Result

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "schemas" / "result_v1.schema.json"


def render() -> str:
    """Render the Result JSON Schema as deterministic text."""
    schema = Result.model_json_schema()
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = render()
    OUT_PATH.write_text(text, encoding="ascii")
    print(f"wrote {OUT_PATH} ({len(text.encode('ascii'))} bytes)")


if __name__ == "__main__":
    main()
