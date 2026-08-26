"""Committed prompt and schema files.  ``prompts/<id>/<ver>.md`` holds four paraphrases split by
``## paraphrase`` headings; ``schemas/<id>/<ver>.json`` is a JSON Schema with UNREADABLE in
every enum."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

ROOT = Path(__file__).parent
PROMPT_IDS = ("bands", "lanes", "front", "header")


@cache
def load_schema(schema_id: str, version: str) -> dict:
    return json.loads((ROOT / "schemas" / schema_id / f"{version}.json").read_text())


@cache
def load_prompt_paraphrases(prompt_id: str, version: str) -> tuple[str, ...]:
    text = (ROOT / "prompts" / prompt_id / f"{version}.md").read_text()
    parts = [p.strip() for p in text.split("\n## paraphrase")]
    preamble, paraphrases = parts[0], parts[1:]
    out = []
    for p in paraphrases:
        body = p.split("\n", 1)[1] if "\n" in p else ""
        out.append(preamble + "\n\n" + body.strip())
    return tuple(out)


def prompt_for_sample(prompt_id: str, version: str, sample_index: int, anchor: str | None) -> str:
    """Spec 01 §7.2: vary nuisance -- cycle paraphrases by sample index.  The critique paraphrase
    (the last one) needs a proposed reading; without an anchor it degrades to a plain read."""
    paras = load_prompt_paraphrases(prompt_id, version)
    text = paras[sample_index % len(paras)]
    return text.replace("{anchor}", anchor if anchor is not None else "(no prior reading)")


def schema_enums(schema: object) -> list[list]:
    """Every enum list anywhere in a schema (used by the UNREADABLE-everywhere test)."""
    found: list[list] = []
    if isinstance(schema, dict):
        if "enum" in schema:
            found.append(schema["enum"])
        for v in schema.values():
            found += schema_enums(v)
    elif isinstance(schema, list):
        for v in schema:
            found += schema_enums(v)
    return found
