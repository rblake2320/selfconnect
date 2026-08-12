from __future__ import annotations

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
    assert canonical_json_loads('{"n":9223372036854775807}')["n"] == 9223372036854775807


def test_unicode_policy_rejects_non_nfc_but_non_bmp_round_trips():
    with pytest.raises(ValueError, match="not NFC"):
        canonical_json_loads('{"name":"e\\u0301"}')
    value = canonical_json_loads('{"agent":"\\ud83e\\udd16","name":"\\u00e9"}')
    assert canonical_json_loads(_canonical(value)) == value
