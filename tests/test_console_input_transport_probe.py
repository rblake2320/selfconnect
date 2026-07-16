"""Deterministic safety and truth-boundary tests for the live console probe."""

from experiments.win32_probe import console_input_transport_probe as probe


def test_marker_requires_exact_target_process_effect(tmp_path):
    marker = tmp_path / "marker.txt"
    marker.write_text("wrong", encoding="utf-8")
    assert probe._wait_for_marker(marker, "expected", 0.01) is False
    marker.write_text("expected\n", encoding="utf-8")
    assert probe._wait_for_marker(marker, "expected", 0.01) is True


def test_probe_requires_explicit_input_permission():
    result = probe.run_probe(allow_input=False)

    assert result.verdict == "NA"
    assert result.reason == "explicit --allow-input required"
    assert result.transport_accepted is False
    assert result.independent_process_effect is False


def test_external_target_requires_complete_expectations(monkeypatch):
    monkeypatch.setattr(probe.sys, "platform", "win32")

    result = probe.run_probe(allow_input=True, hwnd=1234)

    assert result.verdict == "FAIL"
    assert "requires pid, exe, class, and title" in result.reason


def test_sanitized_record_contains_hashes_not_payload():
    record = probe.ConsoleInputProof(
        verdict="PASS",
        title_hash="a" * 64,
        sentinel_hash="b" * 64,
        independent_process_effect=True,
        delivery_verified=True,
    )

    data = probe._sanitize(record)

    assert data["redacted"] is True
    assert "sentinel" not in data
    assert "screen_text" not in data
