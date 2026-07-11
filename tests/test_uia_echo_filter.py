"""
tests/test_uia_echo_filter.py

Unit tests for:
  - experiments/win32_probe/uia_echo_filter_probe.py  (probe layer)
  - sc_echo_filter.py                                  (runtime helper)

All tests run without a live desktop (no Win32 / UIA / COM required).

Live desktop validation (TextChanged event, actual conhost injection) is
documented in docs/UIA_ECHO_FILTER_TERMCONTROL.md.
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import pytest

# ── import probe layer ────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "experiments" / "win32_probe"))

from sc_echo_filter import (
    EchoClassification,
    ReadbackRecord,
    build_record,
    classify,
    split_echo,
)
from uia_echo_filter_probe import (
    EchoFilter,
    FilterRecord,
    ProbeResult,
    run_probe,
)

NONCE = "SC_ECHO_DEADBEEF"


# ══════════════════════════════════════════════════════════════════════════════
# EchoFilter (probe layer — pure Python)
# ══════════════════════════════════════════════════════════════════════════════

class TestEchoFilter:
    def test_classify_exact_echo(self):
        echo, output, cls = EchoFilter.classify_delta(NONCE, NONCE)
        assert cls  == "echo"
        assert NONCE in echo
        assert output == ""

    def test_classify_echo_with_trailing_output(self):
        delta = f"{NONCE}\r\nC:\\>"
        echo, output, cls = EchoFilter.classify_delta(delta, NONCE)
        assert cls == "echo"
        assert NONCE in echo
        assert "C:\\>" in output

    def test_classify_echo_leading_whitespace(self):
        delta = f"\r\n{NONCE}"
        echo, output, cls = EchoFilter.classify_delta(delta, NONCE)
        assert cls == "echo"
        assert NONCE in echo

    def test_classify_output_no_nonce(self):
        delta = "some unrelated terminal output\r\n"
        echo, output, cls = EchoFilter.classify_delta(delta, NONCE)
        assert cls  == "output"
        assert echo == ""
        assert "unrelated" in output

    def test_classify_empty_delta(self):
        echo, output, cls = EchoFilter.classify_delta("", NONCE)
        assert cls  == "empty"
        assert echo == ""
        assert output == ""

    def test_classify_whitespace_only_delta(self):
        echo, output, cls = EchoFilter.classify_delta("  \r\n  ", NONCE)
        assert cls == "empty"

    def test_classify_nonce_mid_delta(self):
        delta = f"some output {NONCE} more"
        echo, output, cls = EchoFilter.classify_delta(delta, NONCE)
        assert cls == "echo"
        assert NONCE in echo

    def test_nonce_with_special_chars_does_not_crash(self):
        nonce = "SC_ECHO_[1.2]"
        delta = f"{nonce}\r\n"
        echo, output, cls = EchoFilter.classify_delta(delta, nonce)
        assert cls == "echo"

    def test_output_before_echo(self):
        delta = f"C:\\Users\\user> {NONCE}"
        echo, output, cls = EchoFilter.classify_delta(delta, NONCE)
        assert cls == "echo"
        assert NONCE in echo

    def test_classify_returns_three_str_tuple(self):
        result = EchoFilter.classify_delta("hello", "hello")
        assert len(result) == 3
        assert all(isinstance(x, str) for x in result)


# ══════════════════════════════════════════════════════════════════════════════
# FilterRecord (probe layer)
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterRecord:
    def test_default_result_is_na(self):
        assert FilterRecord().result == ProbeResult.NA

    def test_numeric_fields_default_zero(self):
        rec = FilterRecord()
        assert rec.hwnd == 0 and rec.pid == 0
        assert rec.timestamp_send == 0.0 and rec.latency_ms == 0.0

    def test_string_fields_default_empty(self):
        rec = FilterRecord()
        for attr in ("na_reason", "uia_method", "nonce", "sent_hash",
                     "observed_hash", "echo_text", "output_text", "raw_delta"):
            assert getattr(rec, attr) == ""

    def test_bool_fields_default_false(self):
        rec = FilterRecord()
        assert rec.uia_available is False
        assert rec.event_supported is False

    def test_construct_with_values(self):
        rec = FilterRecord(result=ProbeResult.PASS, hwnd=12345,
                           uia_method="TextPattern_poll")
        assert rec.result == ProbeResult.PASS
        assert rec.hwnd == 12345


# ══════════════════════════════════════════════════════════════════════════════
# ProbeResult (probe layer)
# ══════════════════════════════════════════════════════════════════════════════

class TestProbeResult:
    def test_values(self):
        assert ProbeResult.PASS == "PASS"
        assert ProbeResult.FAIL == "FAIL"
        assert ProbeResult.NA   == "NA"

    def test_is_str_subclass(self):
        assert isinstance(ProbeResult.PASS, str)

    def test_three_members(self):
        assert {m.value for m in ProbeResult} == {"PASS", "FAIL", "NA"}


# ══════════════════════════════════════════════════════════════════════════════
# run_probe portability (probe layer)
# ══════════════════════════════════════════════════════════════════════════════

class TestRunProbePortability:
    @pytest.mark.skipif(sys.platform == "win32", reason="non-Windows NA path only")
    def test_returns_na_on_non_windows(self):
        rec = run_probe(0)
        assert rec.result == ProbeResult.NA
        assert rec.na_reason != ""

    def test_result_field_is_probe_result_instance(self):
        assert isinstance(FilterRecord().result, ProbeResult)


# ══════════════════════════════════════════════════════════════════════════════
# EchoClassification (runtime helper)
# ══════════════════════════════════════════════════════════════════════════════

class TestEchoClassification:
    def test_all_five_members(self):
        members = {m.value for m in EchoClassification}
        assert members == {
            "echo_only", "external_output", "mixed", "no_signal", "unknown"
        }

    def test_is_str_subclass(self):
        assert isinstance(EchoClassification.ECHO_ONLY, str)

    def test_string_comparison(self):
        assert EchoClassification.EXTERNAL_OUTPUT == "external_output"
        assert EchoClassification.NO_SIGNAL       == "no_signal"


# ══════════════════════════════════════════════════════════════════════════════
# classify() (runtime helper)
# ══════════════════════════════════════════════════════════════════════════════

class TestClassify:
    def test_exact_echo_only(self):
        assert classify(NONCE, NONCE) == EchoClassification.ECHO_ONLY

    def test_echo_with_trailing_prompt(self):
        # cmd.exe: prompt redraw after nonce but no real output beyond artefacts
        delta = f"{NONCE}\r\nC:\\Users\\user>"
        result = classify(delta, NONCE)
        # Could be ECHO_ONLY (prompt stripped) or MIXED — depends on artefact
        # cleaning. Either is acceptable; must not be EXTERNAL_OUTPUT or NO_SIGNAL.
        assert result in (EchoClassification.ECHO_ONLY, EchoClassification.MIXED)

    def test_external_output_no_nonce(self):
        delta = "some model response text here"
        assert classify(delta, NONCE) == EchoClassification.EXTERNAL_OUTPUT

    def test_output_before_echo(self):
        delta = f"C:\\> {NONCE} more real output here"
        assert classify(delta, NONCE) == EchoClassification.MIXED

    def test_output_after_echo(self):
        delta = f"{NONCE} and then some real model output follows"
        assert classify(delta, NONCE) == EchoClassification.MIXED

    def test_empty_delta_is_no_signal(self):
        assert classify("", NONCE) == EchoClassification.NO_SIGNAL

    def test_whitespace_only_delta_is_no_signal(self):
        assert classify("  \r\n\t  ", NONCE) == EchoClassification.NO_SIGNAL

    def test_empty_nonce_is_unknown(self):
        assert classify("some text", "") == EchoClassification.UNKNOWN

    def test_regex_metachar_nonce(self):
        nonce = "SC_ECHO_[1.2+3]"
        delta = f"{nonce} output"
        assert classify(delta, nonce) == EchoClassification.MIXED

    def test_hash_mismatch_scenario(self):
        # classify() doesn't use hashes — it's pure string comparison.
        # This test documents that the caller must verify hashes independently.
        delta = "impostor_nonce_different output"
        assert classify(delta, NONCE) == EchoClassification.EXTERNAL_OUTPUT


# ══════════════════════════════════════════════════════════════════════════════
# split_echo() (runtime helper)
# ══════════════════════════════════════════════════════════════════════════════

class TestSplitEcho:
    def test_exact_echo_no_output(self):
        echo, output = split_echo(NONCE, NONCE)
        assert echo   == NONCE
        assert output == ""

    def test_echo_plus_output(self):
        delta = f"{NONCE}\r\nsome model reply"
        echo, output = split_echo(delta, NONCE)
        assert echo == NONCE
        assert "model reply" in output

    def test_output_only(self):
        echo, output = split_echo("model says hello", NONCE)
        assert echo   == ""
        assert "hello" in output

    def test_empty_delta(self):
        echo, output = split_echo("", NONCE)
        assert echo == "" and output == ""

    def test_empty_nonce(self):
        echo, output = split_echo("hello", "")
        assert echo == ""
        assert "hello" in output

    def test_leading_whitespace_stripped(self):
        delta = f"\r\n  {NONCE} then output"
        echo, output = split_echo(delta, NONCE)
        assert echo == NONCE

    def test_nonce_not_duplicated_in_output(self):
        delta = f"{NONCE} extra"
        echo, output = split_echo(delta, NONCE)
        assert echo   == NONCE
        assert NONCE not in output

    def test_returns_two_strings(self):
        result = split_echo("text", "nonce")
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)


# ══════════════════════════════════════════════════════════════════════════════
# build_record() (runtime helper)
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildRecord:
    def _make(self, delta=NONCE, nonce=NONCE, **kw) -> ReadbackRecord:
        return build_record(delta=delta, nonce=nonce, **kw)

    def test_returns_readback_record(self):
        assert isinstance(self._make(), ReadbackRecord)

    def test_sent_hash_matches_nonce_sha256(self):
        rec = self._make()
        assert rec.sent_hash == hashlib.sha256(NONCE.encode()).hexdigest()

    def test_sent_hash_uses_sent_text_when_provided(self):
        full = f"{NONCE}\r"
        rec = build_record(delta=NONCE, nonce=NONCE, sent_text=full)
        assert rec.sent_hash == hashlib.sha256(full.encode()).hexdigest()

    def test_observed_hash_is_sha256_of_delta(self):
        delta = f"{NONCE}\r\noutput"
        rec = self._make(delta=delta)
        assert rec.observed_hash == hashlib.sha256(delta.encode()).hexdigest()

    def test_sent_and_observed_differ(self):
        delta = f"{NONCE} extra output"
        rec = self._make(delta=delta)
        assert rec.sent_hash != rec.observed_hash

    def test_hwnd_pid_stored(self):
        rec = self._make(hwnd=99999, pid=12345)
        assert rec.hwnd == 99999 and rec.pid == 12345

    def test_readback_method_stored(self):
        rec = self._make(readback_method="TextChanged_event")
        assert rec.readback_method == "TextChanged_event"

    def test_latency_computed_from_timestamps(self):
        t0 = time.time()
        rec = self._make(timestamp_send=t0, timestamp_recv=t0 + 2.5)
        assert abs(rec.latency_ms - 2500.0) < 1.0

    def test_zero_timestamp_send_gives_zero_latency(self):
        rec = self._make(timestamp_send=0.0, timestamp_recv=time.time())
        assert rec.latency_ms == 0.0

    def test_classification_echo_only(self):
        rec = self._make(delta=NONCE, nonce=NONCE)
        assert rec.classification == EchoClassification.ECHO_ONLY

    def test_classification_external_output(self):
        rec = self._make(delta="unrelated model output", nonce=NONCE)
        assert rec.classification == EchoClassification.EXTERNAL_OUTPUT

    def test_classification_mixed(self):
        delta = f"{NONCE} and then a real reply"
        rec = self._make(delta=delta)
        assert rec.classification == EchoClassification.MIXED

    def test_classification_no_signal(self):
        rec = self._make(delta="   ", nonce=NONCE)
        assert rec.classification == EchoClassification.NO_SIGNAL

    def test_echo_and_output_parts_populated(self):
        delta = f"{NONCE}\r\nsome reply"
        rec = self._make(delta=delta)
        assert rec.echo_part  == NONCE
        assert "reply" in rec.output_part

    def test_output_part_truncated_to_512(self):
        long_output = "x" * 1000
        delta = f"{NONCE} {long_output}"
        rec = self._make(delta=delta)
        assert len(rec.output_part) <= 512

    def test_echo_part_truncated_to_256(self):
        long_nonce = "SC_" + "A" * 300
        delta = long_nonce
        rec = build_record(delta=delta, nonce=long_nonce)
        assert len(rec.echo_part) <= 256


# ══════════════════════════════════════════════════════════════════════════════
# ReadbackRecord defaults
# ══════════════════════════════════════════════════════════════════════════════

class TestReadbackRecord:
    def test_defaults(self):
        rec = ReadbackRecord()
        assert rec.nonce          == ""
        assert rec.hwnd           == 0
        assert rec.latency_ms     == 0.0
        assert rec.classification == EchoClassification.UNKNOWN

    def test_construct_with_classification(self):
        rec = ReadbackRecord(classification=EchoClassification.ECHO_ONLY)
        assert rec.classification == EchoClassification.ECHO_ONLY


# ══════════════════════════════════════════════════════════════════════════════
# Hash correctness
# ══════════════════════════════════════════════════════════════════════════════

class TestHashComputation:
    def test_sha256_deterministic(self):
        h1 = hashlib.sha256(b"nonce").hexdigest()
        h2 = hashlib.sha256(b"nonce").hexdigest()
        assert h1 == h2

    def test_sha256_different_inputs_differ(self):
        h1 = hashlib.sha256(b"A").hexdigest()
        h2 = hashlib.sha256(b"B").hexdigest()
        assert h1 != h2

    def test_sha256_hex_length(self):
        assert len(hashlib.sha256(b"test").hexdigest()) == 64
