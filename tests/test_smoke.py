"""Phase 0 smoke: the package imports and the canonical JSON contract holds."""

import pytest

from tlc.core.canonical_json import canonical_json


def test_import_tlc():
    import tlc  # noqa: F401


def test_canonical_json_is_sorted_compact_ascii():
    assert canonical_json({"b": 1, "a": [1.5, "ü"]}) == '{"a":[1.5,"\\u00fc"],"b":1}'


def test_canonical_json_rejects_nan():
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})
