from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "experiments" / "win32_probe"))

import channel_router_composition_probe as probe
from sc_mesh_lease import GOVERNED_PROFILE, RoleLeaseTable


def _leased_target(target: probe.TargetFacts, sid: str = "S-1-5-21-test"):
    table = RoleLeaseTable()
    lease = table.issue(
        mesh="default",
        role=target.role,
        hwnd=target.hwnd,
        pid=target.pid,
        exe_name=target.exe_name,
        class_name=target.class_name,
        title=target.title,
        owner_sid=sid,
        ttl_s=300,
        now=10.0,
        birth_id=target.birth_id,
    )
    return table, probe.replace(target, generation=lease.generation)


def test_classifies_terminal_browser_and_metadata_surfaces():
    terminal, browser, metadata = probe._model_targets()
    assert probe.classify_surface(terminal) == probe.SurfaceKind.TERMINAL
    assert probe.classify_surface(browser) == probe.SurfaceKind.BROWSER
    assert probe.classify_surface(metadata) == probe.SurfaceKind.METADATA


def test_terminal_route_uses_wm_char_under_governed_lease():
    terminal, _browser, _metadata = probe._model_targets()
    table, terminal = _leased_target(terminal)
    route = probe.plan_route(
        terminal,
        action="send",
        expected=probe.ExpectedTarget(
            expected_pid=terminal.pid,
            expected_class=terminal.class_name,
            allow_classes=probe.TERMINAL_CLASSES,
            require_terminal=True,
        ),
        profile=GOVERNED_PROFILE,
        lease_table=table,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert route.allowed is True
    assert route.write_channel == probe.WriteChannel.WM_CHAR
    assert route.mcp_required is False


def test_browser_route_uses_uia_value_invoke():
    _terminal, browser, _metadata = probe._model_targets()
    table, browser = _leased_target(browser)
    route = probe.plan_route(
        browser,
        action="browser_send",
        expected=probe.ExpectedTarget(
            expected_pid=browser.pid,
            expected_class=browser.class_name,
            allow_classes=probe.BROWSER_CLASSES,
            require_browser=True,
        ),
        profile=GOVERNED_PROFILE,
        lease_table=table,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert route.allowed is True
    assert route.write_channel == probe.WriteChannel.UIA_VALUE_INVOKE
    assert route.read_channel == probe.ReadChannel.UIA_TEXT_OR_CAPTURE


def test_metadata_route_stays_off_visible_terminal_text():
    _terminal, _browser, metadata = probe._model_targets()
    table, metadata = _leased_target(metadata)
    route = probe.plan_route(
        metadata,
        action="route_update",
        expected=probe.ExpectedTarget(expected_pid=metadata.pid),
        profile=GOVERNED_PROFILE,
        lease_table=table,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert route.allowed is True
    assert route.write_channel == probe.WriteChannel.FILE_REGISTRY
    assert route.no_visible_metadata is True


def test_governed_route_denies_without_lease_table():
    terminal, _browser, _metadata = probe._model_targets()
    route = probe.plan_route(
        terminal,
        action="send",
        expected=probe.ExpectedTarget(),
        profile=GOVERNED_PROFILE,
        lease_table=None,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert route.allowed is False
    assert route.write_channel == probe.WriteChannel.DENY
    assert "lease table" in route.reason


def test_stale_generation_denies_even_with_valid_target_facts():
    terminal, _browser, _metadata = probe._model_targets()
    table, terminal = _leased_target(terminal)
    stale = probe.replace(terminal, generation=terminal.generation + 1)
    route = probe.plan_route(
        stale,
        action="send",
        expected=probe.ExpectedTarget(
            expected_pid=terminal.pid,
            expected_class=terminal.class_name,
            allow_classes=probe.TERMINAL_CLASSES,
            require_terminal=True,
        ),
        profile=GOVERNED_PROFILE,
        lease_table=table,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert route.allowed is False
    assert "generation" in route.reason


def test_wrong_target_class_denies_before_action():
    _terminal, browser, _metadata = probe._model_targets()
    table, browser = _leased_target(browser)
    wrong = probe.replace(browser, class_name="Notepad")
    route = probe.plan_route(
        wrong,
        action="browser_send",
        expected=probe.ExpectedTarget(
            expected_pid=browser.pid,
            expected_class=browser.class_name,
            allow_classes=probe.BROWSER_CLASSES,
            require_browser=True,
        ),
        profile=GOVERNED_PROFILE,
        lease_table=table,
        owner_sid="S-1-5-21-test",
        now=11.0,
    )
    assert route.allowed is False
    assert "class" in route.reason


def test_model_proof_passes_and_does_not_touch_mcp():
    record = probe.run_model_proof()
    assert record.verdict == probe.ProofVerdict.PASS
    assert record.mcp_touched is False
    assert record.raw_text_included is False
    assert {route.write_channel for route in record.routes} >= {
        probe.WriteChannel.WM_CHAR,
        probe.WriteChannel.UIA_VALUE_INVOKE,
        probe.WriteChannel.FILE_REGISTRY,
    }
    assert all(not route.allowed for route in record.denial_checks)


def test_sanitized_record_has_hashes_not_raw_window_text():
    record = probe.run_model_proof()
    data = probe.sanitize_record(record)
    payload = json.dumps(data)
    assert "SC_ROUTER_TERMINAL_TARGET" not in payload
    assert "SC_ROUTER_BROWSER_TARGET" not in payload
    assert "target_hash" in payload
    assert data["redacted"] is True


def test_write_artifact_outputs_redacted_json():
    record = probe.run_model_proof()
    path = Path(".tmp_channel_router_test.json")
    try:
        probe.write_artifact(record, str(path))
        data = json.loads(path.read_text())
        assert data["verdict"] == "PASS"
        assert data["redacted"] is True
    finally:
        path.unlink(missing_ok=True)
