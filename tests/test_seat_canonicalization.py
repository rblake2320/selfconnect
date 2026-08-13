from __future__ import annotations

import json
from pathlib import Path

import pytest
from sc_seat_identity import _canonical, canonical_json_loads


def test_duplicate_json_keys_are_rejected_before_signature_verification():
    with pytest.raises(ValueError, match="duplicate"):
        canonical_json_loads('{"seat":"a","seat":"b"}')


@pytest.mark.parametrize("raw", ['{"n":NaN}', '{"n":Infinity}', '{"n":-Infinity}'])
def test_nonfinite_json_numbers_are_rejected(raw):
    with pytest.raises(ValueError, match="constant"):
        canonical_json_loads(raw)


def test_huge_integers_are_bounded():
    with pytest.raises(ValueError, match="out of range"):
        canonical_json_loads('{"n":9223372036854775808}')
    assert canonical_json_loads('{"n":9007199254740991}')["n"] == 9007199254740991


def test_unicode_policy_normalizes_nfc_and_non_bmp_round_trips():
    assert canonical_json_loads('{"name":"e\\u0301"}')["name"] == "é"
    value = canonical_json_loads('{"agent":"\\ud83e\\udd16","name":"\\u00e9"}')
    assert canonical_json_loads(_canonical(value)) == value


def test_python_matches_shared_cross_language_vectors():
    vectors = json.loads(
        (Path(__file__).parent / "fixtures" / "seat_canonical_vectors.json").read_text(encoding="utf-8")
    )
    for vector in vectors["valid"]:
        assert _canonical(canonical_json_loads(vector["input"])).decode() == vector["canonical"]
    for vector in vectors["invalid"]:
        with pytest.raises(ValueError):
            canonical_json_loads(vector["input"])
