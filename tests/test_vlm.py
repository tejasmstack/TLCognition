"""VLM semantic layer: offline determinism, typed abstention, exact agreement arithmetic."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import numpy as np
import pytest

from tlc.core.canonical_json import canonical_json
from tlc.vlm import aggregate as agg
from tlc.vlm.cache import (
    MemoryStore,
    SQLiteStore,
    bundle_hash,
    cache_key,
    make_row,
    response_sha256,
)
from tlc.vlm.errors import VLMCacheMiss, VLMModeError
from tlc.vlm.provider import CropSpec, VLMRequest, VLMResponse, get_provider
from tlc.vlm.read import PROMPT_CROP, build_crops, read_plate_semantics
from tlc.vlm.resources import PROMPT_IDS, load_prompt_paraphrases, load_schema, schema_enums
from tlc.vlm.schema_validate import validate

MODEL = "gemini-2.5-flash-lite-001"
PROV = "gemini"


def plate(seed=0, h=120, w=200):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)


def req(crop_sha="ab" * 32, prompt_version="v1", i=0, rule="full_plate"):
    return VLMRequest(
        prompt_id="lanes", prompt_version=prompt_version, schema_id="lanes", schema_version="v1",
        crop=CropSpec(rule_id=rule, rule_version="v1", box_src_px=(0, 0, 10, 10)),
        crop_bytes_sha256=crop_sha, model_id=MODEL, temperature=1.0, max_output_tokens=512,
        sample_index=i,
    )  # fmt: skip


def synthetic_docs():
    lanes = lambda n: [  # noqa: E731
        {"x_frac": round(0.1 + 0.2 * k, 3), "label": "S" if k == 0 else "R",
         "label_evidence": "pencil strokes"} for k in range(n)
    ]  # fmt: skip
    docs = {
        "lanes": [{"lane_count": 4, "lanes": lanes(4)}] * 4 + [{"lane_count": 3, "lanes": lanes(3)}],
        "bands": [{"header_present": "yes", "header_y1_frac": 0.10 + 0.005 * i,
                   "label_row_present": "yes", "label_row_y0_frac": 0.9, "evidence": "text"}
                  for i in range(5)],
        "front": [{"front_drawn": "no", "front_y_frac": None, "evidence": "none"}] * 5,
        "header": [{"text": "AB12-045", "legible": "clear"}] * 4
        + [{"text": "AB12-046", "legible": "partial", "uncertain_char_indices": [7]}],
    }  # fmt: skip
    return docs


def populate(store, img, docs, n=5):
    crops = build_crops(img)
    from tlc.core.hashing import sha256_bytes

    for pid in PROMPT_IDS:
        spec, png = crops[PROMPT_CROP[pid]]
        for i in range(n):
            r = VLMRequest(prompt_id=pid, prompt_version="v1", schema_id=pid, schema_version="v1",
                           crop=spec, crop_bytes_sha256=sha256_bytes(png), model_id=MODEL,
                           temperature=1.0, max_output_tokens=512, sample_index=i)  # fmt: skip
            raw = canonical_json(docs[pid][i])
            resp = VLMResponse(parsed=docs[pid][i], raw_text=raw, response_sha256=response_sha256(raw))
            store.put(make_row(PROV, r, resp))


# --- null / off ------------------------------------------------------------------------------


def test_off_mode_all_unreadable_with_typed_abstentions():
    read = read_plate_semantics(plate(), None, "off", None, PROV, MODEL)
    assert read.lane_count is None and read.lane_labels is None and read.header_text is None
    assert read.front_present is None and read.bands == {"header_y1_frac": None,
                                                        "label_row_y0_frac": None}  # fmt: skip
    for f in ("lane_count", "lane_labels", "header_y1_frac", "label_row_y0_frac",
              "front_present", "header_text"):  # fmt: skip
        assert read.abstentions[f] == "E_VLM_UNREADABLE", f
    assert read.fields["header_text"].flagged_for_review is True
    assert read.degraded is False and read.model_id is None and read.mode == "off"
    assert read.cache["misses"] == 20 and len(read.cache["bundle_hash"]) == 64
    # deterministic
    read2 = read_plate_semantics(plate(), None, "off", None, PROV, MODEL)
    assert read2.cache["bundle_hash"] == read.cache["bundle_hash"]


def test_off_mode_fields_are_vlmfield_compatible():
    from tlc.schemas.result import VLMField

    read = read_plate_semantics(plate(), None, "off", None, PROV, MODEL)
    for d in read.vlm_fields().values():
        VLMField(**d)


# --- replay ---------------------------------------------------------------------------------


def test_replay_raises_cache_miss_on_empty_store():
    with pytest.raises(VLMCacheMiss) as ei:
        read_plate_semantics(plate(), None, "replay", MemoryStore(), PROV, MODEL)
    assert ei.value.code == "E_VLM_CACHE_MISS"


def test_replay_from_prepopulated_store_is_byte_identical_and_exact():
    img, store = plate(), MemoryStore()
    populate(store, img, synthetic_docs())
    r1 = read_plate_semantics(img, None, "replay", store, PROV, MODEL)
    r2 = read_plate_semantics(img, None, "replay", store, PROV, MODEL)
    assert canonical_json(r1.vlm_fields()) == canonical_json(r2.vlm_fields())
    assert r1.cache == {"hits": 20, "misses": 0, "bundle_hash": r1.cache["bundle_hash"]}
    assert r1.lane_count == 4
    assert r1.fields["lane_count"].agreement == (4 + 0.5) / (5 + 0.5 * 9)
    assert r1.fields["lane_count"].disagreements == [3]
    assert r1.lane_labels == ["S", "R", "R", "R"]
    assert r1.lane_x_frac == [0.1, 0.3, 0.5, 0.7]
    assert r1.bands["header_y1_frac"] == 0.11 and r1.fields["header_y1_frac"].iqr_frac <= 0.05
    assert r1.bands["label_row_y0_frac"] == 0.9
    assert r1.front_present is False
    # header: one differing char -> flagged, char_confidence < 1 at that position only
    ht = r1.fields["header_text"]
    assert ht.flagged_for_review is True and r1.header_text == "AB12-045"
    cc = ht.value["char_confidence"]
    assert cc[:7] == [1.0] * 7 and cc[7] == (4 + 0.5) / (5 + 0.5 * 2) < 1
    assert ht.agreement == (5 + 0.5) / (5 + 0.5 * 2)  # one cluster (+UNREADABLE in |V|)
    assert "header_text" not in r1.abstentions
    assert r1.degraded is False and r1.mode == "replay"


def test_replay_partial_cache_still_raises():
    img, store = plate(), MemoryStore()
    populate(store, img, synthetic_docs(), n=3)
    with pytest.raises(VLMCacheMiss):
        read_plate_semantics(img, None, "replay", store, PROV, MODEL)


# --- cache key ------------------------------------------------------------------------------


def test_cache_key_sensitivity():
    base = cache_key(PROV, req())
    assert len(base) == 64
    assert cache_key(PROV, req()) == base
    assert cache_key(PROV, req(crop_sha="cd" * 32)) != base
    assert cache_key(PROV, req(prompt_version="v2")) != base
    assert cache_key(PROV, req(i=1)) != base
    assert cache_key(PROV, req(rule="label_row")) != base
    assert cache_key("anthropic", req()) != base


def test_cache_key_is_sha256_of_spec_fields():
    import hashlib

    r = req()
    doc = {"provider": PROV, "model_id": MODEL, "prompt_id": "lanes", "prompt_version": "v1",
           "schema_id": "lanes", "schema_version": "v1", "crop_rule": "full_plate",
           "crop_rule_version": "v1", "crop_bytes_sha256": "ab" * 32, "temperature": 1.0,
           "max_output_tokens": 512, "sample_index": 0}  # fmt: skip
    assert cache_key(PROV, r) == hashlib.sha256(canonical_json(doc).encode()).hexdigest()


def test_bundle_hash_order_invariant():
    assert bundle_hash(["b", "a"], ["y", "x"]) == bundle_hash(["a", "b"], ["x", "y"])
    assert bundle_hash(["a"], ["x"]) != bundle_hash(["a"], ["z"])


def test_sqlite_store_roundtrip(tmp_path: Path):
    store = SQLiteStore(tmp_path / "vlm.sqlite")
    doc = {"lane_count": "UNREADABLE", "lanes": []}
    raw = canonical_json(doc)
    row = make_row(PROV, req(), VLMResponse(parsed=doc, raw_text=raw, response_sha256=response_sha256(raw)))
    store.put(row)
    got = store.get(row["cache_key"])
    assert got is not None and json.loads(got["response_json"]) == doc
    store.put({**row, "response_json": "tampered"})  # never overwritten
    assert json.loads(store.get(row["cache_key"])["response_json"]) == doc


# --- provider registry / mode guard -----------------------------------------------------------


@pytest.mark.parametrize("name", ["anthropic", "gemini"])
@pytest.mark.parametrize("mode", ["replay", "off"])
def test_live_provider_construction_fails_outside_live(name, mode):
    with pytest.raises(VLMModeError):
        get_provider(name, mode, model_id=MODEL)
    from tlc.vlm.anthropic import AnthropicProvider
    from tlc.vlm.gemini import GeminiProvider

    with pytest.raises(VLMModeError):
        (AnthropicProvider if name == "anthropic" else GeminiProvider)(mode=mode, model_id=MODEL)


def test_live_stubs_build_requests_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    a = get_provider("anthropic", "live", model_id=MODEL)
    g = get_provider("gemini", "live", model_id=MODEL)
    ba = a.build_body(req(), b"png")
    assert ba["tools"][0]["input_schema"] == load_schema("lanes", "v1")
    assert ba["temperature"] == 1.0 and ba["tool_choice"]["type"] == "tool"
    bg = g.build_body(req(i=3), b"png")
    gc = bg["generationConfig"]
    assert gc["responseMimeType"] == "application/json"
    assert "UNREADABLE" in gc["responseSchema"]["properties"]["lane_count"]["enum"]
    assert "{anchor}" not in bg["contents"][0]["parts"][1]["text"]  # critique paraphrase filled


# --- schema and prompt hygiene --------------------------------------------------------------


@pytest.mark.parametrize("pid", PROMPT_IDS)
def test_every_enum_has_unreadable(pid):
    schema = load_schema(pid, "v1")
    enums = schema_enums(schema)
    assert enums, "schema has no enums"
    for e in enums:
        assert "UNREADABLE" in e, e
    from tlc.vlm.null import unreadable_document

    assert validate(unreadable_document(schema), schema) == []


def test_lanes_schema_enums_match_spec():
    s = load_schema("lanes", "v1")
    assert s["properties"]["lane_count"]["enum"] == [1, 2, 3, 4, 5, 6, 7, 8, "UNREADABLE"]
    assert s["properties"]["lanes"]["items"]["properties"]["label"]["enum"] == [
        "S", "co", "R", "sd", "blank", "other", "UNREADABLE"]  # fmt: skip
    assert load_schema("header", "v1")["properties"]["legible"]["enum"] == ["clear", "partial", "UNREADABLE"]
    assert load_schema("front", "v1")["properties"]["front_drawn"]["enum"] == ["yes", "no", "UNREADABLE"]


@pytest.mark.parametrize("pid", PROMPT_IDS)
def test_prompt_mandatory_requirements(pid):
    paras = load_prompt_paraphrases(pid, "v1")
    assert len(paras) == 4
    for p in paras:
        assert "UNREADABLE" in p and "CORRECT answer" in p
        assert "evidence" in p
        assert "Do NOT infer" in p and "chemical plausibility" in p
    assert any("{anchor}" in p and "why it may be wrong" in p for p in paras)


def test_validator_rejects_bad_docs():
    s = load_schema("lanes", "v1")
    assert validate({"lane_count": 9, "lanes": []}, s)
    assert validate({"lane_count": 2, "lanes": [{"x_frac": 1.5, "label": "S", "label_evidence": ""}]}, s)
    assert validate({"lane_count": 2}, s)
    assert validate({"lane_count": True, "lanes": []}, s)


# --- aggregation unit tests -----------------------------------------------------------------


def test_categorical_rules():
    fr = agg.categorical(["S", "S", "R", "co", "sd"], 7)
    assert fr.value is None and fr.reason == "E_VLM_DISAGREEMENT"
    fr = agg.categorical(["UNREADABLE"] * 5, 7)
    assert fr.reason == "E_VLM_UNREADABLE" and fr.agreement == 5.5 / 8.5
    fr = agg.categorical(["S"] * 5, 7)
    assert fr.value == "S" and fr.disagreements is None and fr.entropy == 0.0


def test_quasi_continuous_rules():
    assert agg.quasi_continuous([0.1, 0.11, 0.1, 0.12, 0.1]).value == 0.1
    fr = agg.quasi_continuous([0.1, 0.3, 0.1, 0.5, 0.1])
    assert fr.value is None and fr.reason == "E_VLM_DISAGREEMENT" and fr.iqr_frac > 0.05
    assert agg.quasi_continuous([None, None, None, 0.1, 0.1]).reason == "E_VLM_UNREADABLE"


def test_free_text_confusion_classes_and_clustering():
    assert agg.normalize_text("a b0 l") == agg.normalize_text("AB O I")
    fr = agg.free_text(["AB12", "ab12", "AB1Z", "XYZQ", "AB12"])
    assert fr.value["text"] == "AB12" and fr.disagreements == ["XYZQ"]
    assert fr.flagged_for_review is True
    fr = agg.free_text(["AAAA", "BBBB", "CCCC", "DDDD", "EEEE"])
    assert fr.reason == "E_VLM_DISAGREEMENT" and fr.flagged_for_review is True


def test_invalid_output_abstention():
    read = agg.SemanticRead()
    agg.aggregate_lanes([{"lane_count": 4, "lanes": []}] * 2, 5, read)
    assert read.abstentions["lane_count"] == "E_VLM_INVALID_OUTPUT"


# --- offline proof -------------------------------------------------------------------------


def test_socket_guard_blocks_network():
    from tests.conftest import NetworkDisabledInTests

    with pytest.raises(NetworkDisabledInTests):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_vlm_package_does_not_import_pipeline():
    import subprocess
    import sys

    code = ("import sys, tlc.vlm.read, tlc.vlm.anthropic, tlc.vlm.gemini; "
            "print([m for m in sys.modules if m.startswith('tlc.pipeline')])")  # fmt: skip
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
