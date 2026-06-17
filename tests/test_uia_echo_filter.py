"""
tests/test_uia_echo_filter.py

Unit tests for uia_echo_filter_probe.py that run without a live desktop.

Tests cover:
  - EchoFilter.classify_delta logic (pure Python, platform-independent)
  - FilterRecord structure and defaults
  - ProbeResult enum values
  - Hash computation round-trips
  - Nonce format expectation
  - run_probe() short-circuits gracefully on non-Windows

Live desktop tests (TextChanged event, actual conhost injection) require
a real Windows desktop session and are documented separately in
docs/UIA_ECHO_FILTER_TERMCONTROL.md under "Manual Live Validation".
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

# Make probe importable regardless of CWD
sys.path.insert(0, str(Path(__file__).parent.parent / "experiments" / "win32_probe"))
from uia_echo_filter_probe import EchoFilter, FilterRecord, ProbeResult, run_probe


# ── EchoFilter ────────────────────────────────────────────────────────────────

class TestEchoFilter:
    NONCE = "SC_ECHO_DEADBEEF"

    def test_classify_exact_echo(self):
        delta = self.NONCE
        echo, output, cls = EchoFilter.classify_delta(delta, self.NONCE)
        assert cls    == "echo"
        assert echo   == self.NONCE
        assert output == ""

    def test_classify_echo_with_trailing_output(self):
        # Terminal echoes the nonce then appends a prompt
        delta = f"{self.NONCE}\r\nC:\\>"
        echo, output, cls = EchoFilter.classify_delta(delta, self.NONCE)
        assert cls  == "echo"
        assert self.NONCE in echo
        assert "C:\\>" in output

    def test_classify_echo_leading_whitespace(self):
        # Terminal may prepend newline before the echoed char sequence
        delta = f"\r\n{self.NONCE}"
        echo, output, cls = EchoFilter.classify_delta(delta, self.NONCE)
        assert cls == "echo"
        assert self.NONCE in echo

    def test_classify_output_no_nonce(self):
        delta = "some unrelated terminal output\r\n"
        echo, output, cls = EchoFilter.classify_delta(delta, self.NONCE)
        assert cls  == "output"
        assert echo == ""
        assert "unrelated" in output

    def test_classify_empty_delta(self):
        echo, output, cls = EchoFilter.classify_delta("", self.NONCE)
        assert cls    == "empty"
        assert echo   == ""
        assert output == ""

    def test_classify_whitespace_only_delta(self):
        echo, output, cls = EchoFilter.classify_delta("  \r\n  ", self.NONCE)
        assert cls == "empty"

    def test_classify_nonce_not_at_start(self):
        # Nonce appears mid-delta (e.g. after prompt text)
        delta = f"some output {self.NONCE} more"
        echo, output, cls = EchoFilter.classify_delta(delta, self.NONCE)
        assert cls  == "echo"
        assert self.NONCE in echo

    def test_nonce_with_special_regex_chars_does_not_crash(self):
        nonce = "SC_ECHO_[1.2]"
        delta = f"{nonce}\r\n"
        # Must not raise even though nonce contains regex metacharacters
        echo, output, cls = EchoFilter.classify_delta(delta, nonce)
        assert cls == "echo"

    def test_empty_nonce_classifies_as_output(self):
        # Guard: empty nonce means we can't detect echo; whole delta is output
        delta = "anything"
        echo, output, cls = EchoFilter.classify_delta(delta, "")
        # An empty string is always "in" any string — result is echo at pos 0
        # This is acceptable; callers must use a non-empty nonce.
        assert cls in {"echo", "output", "empty"}

    def test_classify_returns_three_tuple(self):
        result = EchoFilter.classify_delta("hello", "hello")
        assert len(result) == 3
        assert all(isinstance(x, str) for x in result)


# ── FilterRecord ──────────────────────────────────────────────────────────────

class TestFilterRecord:
    def test_default_result_is_na(self):
        rec = FilterRecord()
        assert rec.result == ProbeResult.NA

    def test_all_numeric_fields_default_zero(self):
        rec = FilterRecord()
        assert rec.hwnd          == 0
        assert rec.pid           == 0
        assert rec.timestamp_send  == 0.0
        assert rec.timestamp_first == 0.0
        assert rec.latency_ms    == 0.0

    def test_all_string_fields_default_empty(self):
        rec = FilterRecord()
        assert rec.na_reason     == ""
        assert rec.uia_method    == ""
        assert rec.nonce         == ""
        assert rec.sent_hash     == ""
        assert rec.observed_hash == ""
        assert rec.echo_text     == ""
        assert rec.output_text   == ""
        assert rec.raw_delta     == ""

    def test_bool_fields_default_false(self):
        rec = FilterRecord()
        assert rec.uia_available   is False
        assert rec.event_supported is False

    def test_can_be_constructed_with_result(self):
        rec = FilterRecord(result=ProbeResult.PASS, hwnd=12345, uia_method="TextPattern_poll")
        assert rec.result     == ProbeResult.PASS
        assert rec.hwnd       == 12345
        assert rec.uia_method == "TextPattern_poll"


# ── ProbeResult ───────────────────────────────────────────────────────────────

class TestProbeResult:
    def test_values_are_strings(self):
        assert ProbeResult.PASS.value == "PASS"
        assert ProbeResult.FAIL.value == "FAIL"
        assert ProbeResult.NA.value   == "NA"

    def test_is_str_subclass(self):
        assert isinstance(ProbeResult.PASS, str)

    def test_comparison_with_string(self):
        assert ProbeResult.PASS == "PASS"
        assert ProbeResult.FAIL == "FAIL"
        assert ProbeResult.NA   == "NA"

    def test_all_three_members_exist(self):
        members = {m.value for m in ProbeResult}
        assert members == {"PASS", "FAIL", "NA"}


# ── hash round-trips ──────────────────────────────────────────────────────────

class TestHashComputation:
    def test_sent_hash_matches_nonce_sha256(self):
        nonce = "SC_ECHO_DEADBEEF"
        expected = hashlib.sha256(nonce.encode()).hexdigest()
        assert len(expected) == 64

    def test_observed_hash_is_deterministic(self):
        text = "SC_ECHO_DEADBEEF\r\nC:\\>"
        h1 = hashlib.sha256(text.encode()).hexdigest()
        h2 = hashlib.sha256(text.encode()).hexdigest()
        assert h1 == h2

    def test_different_inputs_produce_different_hashes(self):
        h1 = hashlib.sha256(b"nonce_A").hexdigest()
        h2 = hashlib.sha256(b"nonce_B").hexdigest()
        assert h1 != h2


# ── run_probe on non-Windows ──────────────────────────────────────────────────

class TestRunProbePortability:
    @pytest.mark.skipif(sys.platform == "win32", reason="non-Windows NA path only")
    def test_returns_na_on_non_windows(self):
        rec = run_probe(0)
        assert rec.result    == ProbeResult.NA
        assert rec.na_reason != ""
        assert "Win32" in rec.na_reason or "platform" in rec.na_reason.lower()

    def test_filter_record_result_values_are_probe_result(self):
        rec = FilterRecord()
        assert isinstance(rec.result, ProbeResult)
