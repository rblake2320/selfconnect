import sys
import tempfile
from pathlib import Path
from typing import ClassVar

import pytest
import sc_cli
import sc_mcp
import sc_mesh_registry


class _FakeWindow:
    hwnd = 0x1234
    pid = 4321
    exe_name = "WindowsTerminal.exe"
    class_name = "CASCADIA_HOSTING_WINDOW_CLASS"
    title = "Claude Test"


class _FakeNotepadWindow:
    hwnd = 0x5678
    pid = 8765
    exe_name = "Notepad.exe"
    class_name = "Notepad"
    title = "scratch - Notepad"


class _FakeSelfConnect:
    __version__ = "test"
    capabilities: ClassVar[dict[str, bool]] = {"win32": True}

    def __init__(self):
        self.sent = []
        self.windows = [_FakeWindow()]

    def list_windows(self):
        return self.windows

    def send_string(self, target, payload, char_delay=0.05):
        self.sent.append((target, payload, char_delay))


def test_parse_hwnd_accepts_decimal_and_hex():
    assert sc_cli.parse_hwnd("1234") == 1234
    assert sc_cli.parse_hwnd("0x4D2") == 1234
    assert sc_cli.parse_hwnd(1234) == 1234


def test_cli_input_gate_is_closed_by_default(monkeypatch):
    monkeypatch.delenv("SELFCONNECT_ALLOW_INPUT", raising=False)
    assert sc_cli.input_allowed() is False
    assert sc_cli.input_allowed(explicit=True) is True
    monkeypatch.setenv("SELFCONNECT_ALLOW_INPUT", "1")
    assert sc_cli.input_allowed() is True


def test_mcp_input_gate_is_closed_by_default(monkeypatch):
    monkeypatch.delenv("SELFCONNECT_MCP_ALLOW_INPUT", raising=False)
    assert sc_mcp._mcp_input_allowed() is False
    monkeypatch.setenv("SELFCONNECT_MCP_ALLOW_INPUT", "true")
    assert sc_mcp._mcp_input_allowed() is True


def test_mesh_registry_infers_agent_type():
    assert sc_mesh_registry.infer_agent_type("codex 1") == "codex"
    assert sc_mesh_registry.infer_agent_type("ckaude 1") == "claude"
    assert sc_mesh_registry.infer_agent_type("Team Gemini") == "gemini"


def test_mesh_registry_rejects_duplicate_active_role(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "mesh.json"

    try:
        first = sc_mesh_registry.register_agent(
            _FakeWindow.hwnd,
            "codex-1",
            registry_path=path,
            expected_class=_FakeWindow.class_name,
        )
        assert first["ok"] is True

        class _OtherWindow(_FakeWindow):
            hwnd = 0x9999
            title = "Other Codex"

        fake.windows = [_OtherWindow()]
        second = sc_mesh_registry.register_agent(
            _OtherWindow.hwnd,
            "codex-1",
            registry_path=path,
            expected_class=_OtherWindow.class_name,
        )
        assert second["ok"] is False
        assert "role already registered" in second["error"]
    finally:
        temp_dir.cleanup()


def test_mesh_registry_tracks_governance_profile(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "mesh.json"

    try:
        registered = sc_mesh_registry.register_agent(
            _FakeWindow.hwnd,
            "codex-1",
            registry_path=path,
            expected_class=_FakeWindow.class_name,
        )
        assert registered["ok"] is True
        assert registered["agent"]["profile"] == "explore"

        updated = sc_mesh_registry.update_agent("codex-1", profile="governed", registry_path=path)
        assert updated["ok"] is True
        assert updated["agent"]["profile"] == "governed"

        rejected = sc_mesh_registry.update_agent("codex-1", profile="unsafe", registry_path=path)
        assert rejected["ok"] is False
        assert "profile must be one of" in rejected["error"]
    finally:
        temp_dir.cleanup()


def test_mesh_registry_tracks_birth_id_and_generation(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "mesh.json"

    try:
        first = sc_mesh_registry.register_agent(
            _FakeWindow.hwnd,
            "B",
            registry_path=path,
            expected_class=_FakeWindow.class_name,
        )
        assert first["ok"] is True
        first_birth = first["agent"]["birth_id"]
        assert first_birth.startswith("b-")
        assert first["agent"]["generation"] == 1
        assert first["agent"]["window_fingerprint"]

        same = sc_mesh_registry.register_agent(
            _FakeWindow.hwnd,
            "B",
            registry_path=path,
            expected_class=_FakeWindow.class_name,
        )
        assert same["ok"] is True
        assert same["agent"]["birth_id"] == first_birth
        assert same["agent"]["generation"] == 1

        class _MigratedWindow(_FakeWindow):
            hwnd = 0x9999
            title = "Team B migrated"

        fake.windows = [_MigratedWindow()]
        migrated = sc_mesh_registry.register_agent(
            _MigratedWindow.hwnd,
            "B",
            registry_path=path,
            expected_class=_MigratedWindow.class_name,
            replace=True,
        )
        assert migrated["ok"] is True
        assert migrated["agent"]["birth_id"] != first_birth
        assert migrated["agent"]["generation"] == 2
    finally:
        temp_dir.cleanup()


def test_mesh_registry_heartbeat_updates_guard_status(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "mesh.json"

    try:
        registered = sc_mesh_registry.register_agent(
            _FakeWindow.hwnd,
            "claude-1",
            registry_path=path,
            expected_class=_FakeWindow.class_name,
        )
        assert registered["ok"] is True

        beat = sc_mesh_registry.heartbeat("claude-1", registry_path=path)
        assert beat["ok"] is True
        assert beat["agent"]["guard_ok"] is True
    finally:
        temp_dir.cleanup()


def test_print_json_escapes_unicode_for_windows_console(capsys):
    assert sc_cli._print_json({"title": "agent ✳"}) == 0
    out = capsys.readouterr().out
    assert "\\u2733" in out


def test_target_guard_requires_expectations(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))

    result = sc_cli.verify_target(_FakeWindow.hwnd)
    assert result["ok"] is False
    assert "no target expectations supplied" in result["errors"]


def test_target_guard_accepts_matching_expected_fields(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))

    result = sc_cli.verify_target(
        _FakeWindow.hwnd,
        expected_pid=_FakeWindow.pid,
        expected_exe=_FakeWindow.exe_name,
        expected_class=_FakeWindow.class_name,
        expected_title="Claude",
    )
    assert result["ok"] is True
    assert all(check["ok"] for check in result["checks"])


def test_target_guard_blocks_non_terminal_by_default(monkeypatch):
    fake = _FakeSelfConnect()
    fake.windows = [_FakeNotepadWindow()]
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))

    result = sc_cli.verify_target(
        _FakeNotepadWindow.hwnd,
        expected_pid=_FakeNotepadWindow.pid,
        expected_class=_FakeNotepadWindow.class_name,
    )
    assert result["ok"] is False
    assert result["is_terminal"] is False
    assert any("not an allowed terminal class" in reason for reason in result["reasons"])


def test_send_text_blocks_without_target_guard(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))

    result = sc_cli.send_text_to_window(_FakeWindow.hwnd, "hello", allow_input=True)
    assert result["ok"] is False
    assert result["error"] == "target verification failed"
    assert fake.sent == []


def test_send_text_allows_matching_target_guard(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))

    result = sc_cli.send_text_to_window(
        _FakeWindow.hwnd,
        "hello",
        submit=True,
        allow_input=True,
        expected_pid=_FakeWindow.pid,
        expected_class=_FakeWindow.class_name,
    )
    assert result["ok"] is True
    assert fake.sent[0][1] == "hello\r"


def test_send_text_explore_mode_unchanged_with_lease_params_omitted(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))

    result = sc_cli.send_text_to_window(
        _FakeWindow.hwnd,
        "hello",
        submit=True,
        allow_input=True,
        expected_pid=_FakeWindow.pid,
        expected_class=_FakeWindow.class_name,
    )
    assert result["ok"] is True
    assert fake.sent[0][1] == "hello\r"
    # Explore path adds no lease_gate key.
    assert "lease_gate" not in result


def test_send_text_governed_denies_without_matching_lease(monkeypatch):
    from sc_mesh_lease import RoleLeaseTable

    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))

    empty_table = RoleLeaseTable()
    result = sc_cli.send_text_to_window(
        _FakeWindow.hwnd,
        "hello",
        allow_input=True,
        expected_pid=_FakeWindow.pid,
        expected_class=_FakeWindow.class_name,
        profile="governed",
        role="agent-a",
        generation=1,
        owner_sid="X",
        lease_table=empty_table,
    )
    assert result["ok"] is False
    assert result["error"] == "governed lease gate denied"
    assert "lease_gate" in result
    assert fake.sent == []


def test_send_text_governed_allows_with_matching_lease(monkeypatch):
    from sc_mesh_lease import RoleLeaseTable

    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))

    table = RoleLeaseTable()
    table.issue(
        mesh="default",
        role="agent-a",
        hwnd=int(_FakeWindow.hwnd),
        pid=int(_FakeWindow.pid),
        exe_name=_FakeWindow.exe_name,
        class_name=_FakeWindow.class_name,
        title=_FakeWindow.title,
        owner_sid="X",
        ttl_s=300,
    )
    result = sc_cli.send_text_to_window(
        _FakeWindow.hwnd,
        "hello",
        submit=True,
        allow_input=True,
        expected_pid=_FakeWindow.pid,
        expected_class=_FakeWindow.class_name,
        profile="governed",
        role="agent-a",
        generation=1,
        owner_sid="X",
        lease_table=table,
    )
    assert result["ok"] is True
    assert "lease_gate" in result
    assert result["lease_gate"]["ok"] is True
    assert fake.sent[0][1] == "hello\r"


def test_pyproject_exports_package_adapter_entry_points():
    text = Path("pyproject.toml").read_text()
    assert 'selfconnect = "sc_cli:main"' in text
    assert 'selfconnect-mcp = "sc_mcp:main"' in text
    assert 'selfconnect-mesh = "sc_mesh_registry:main"' in text
    assert '"sc_cli.py"' in text
    assert '"sc_mcp.py"' in text
    assert '"sc_mesh_registry.py"' in text
    assert '"sc_mesh_lease.py"' in text
    assert '"sc_echo_filter.py"' in text
    assert '"experiments/win32_probe/CAPABILITY_BACKLOG.md"' in text
    assert '"experiments/win32_probe/SERVICE_SID_DAEMON.md"' in text
    assert '"experiments/win32_probe/channel_router_composition_probe.py"' in text
    assert '"experiments/win32_probe/chained_channel.py"' in text
    assert '"experiments/win32_probe/browser_local_proof.py"' in text
    assert '"experiments/win32_probe/etw_provider.py"' in text
    assert '"experiments/win32_probe/pipe_role_lease_probe.py"' in text
    assert '"experiments/win32_probe/service_sid_probe.py"' in text
    assert '"experiments/win32_probe/uia_echo_filter_probe.py"' in text
    assert '"experiments/win32_probe/results/browser_local_proof_PASS_redacted.json"' in text
    assert '"experiments/win32_probe/results/channel_router_composition_LIVE_PASS_redacted.json"' in text
    assert '"experiments/win32_probe/results/channel_router_composition_PASS_redacted.json"' in text
    assert '"experiments/win32_probe/results/pipe_role_lease_PASS_redacted.json"' in text
    assert '"experiments/win32_probe/results/uia_echo_filter_PASS_redacted.json"' in text
    assert '"docs/BROWSER_LOCAL_PROOF.md"' in text
    assert '"docs/CHANNEL_ROUTER_COMPOSITION_PROOF.md"' in text
    assert '"docs/CLAIM_EVIDENCE_MATRIX.md"' in text
    assert '"docs/LANCELOT_UAB_COMPETITIVE_DEFENSE.md"' in text
    assert '"docs/AWS_AI_TO_AI_EVIDENCE_INDEX.md"' in text
    assert '"docs/PATENT_DESIGN_AROUND_DEFENSE.md"' in text
    assert '"docs/OPEN_DEFENSE_BUILD_TARGETS.md"' in text
    assert '"docs/PATENT_PRIOR_ART_SNAPSHOT.md"' in text
    assert '"docs/PIPE_ROLE_LEASE_PROOF.md"' in text
    assert '"docs/UIA_ECHO_FILTER_TERMCONTROL.md"' in text
    assert '"docs/PROVEN_VS_UNTESTED.md"' in text
    assert '"skills/selfconnect-win32/**"' in text


def test_mcp_module_imports_without_optional_mcp_dependency():
    assert callable(sc_mcp.build_server)
    assert callable(sc_mcp.main)


@pytest.mark.skipif(sys.platform != "win32", reason="SelfConnect probes require Windows")
def test_doctor_report_shape_on_windows():
    report = sc_cli.doctor_report()
    assert report["package"] == "selfconnect"
    assert report["version"]
    assert isinstance(report["visible_window_count"], int)
    assert set(report["capabilities"]) >= {
        "win32",
        "uia_text",
        "uia_events",
        "printwindow",
        "named_pipe_impersonation",
        "tpm_identity",
    }
    assert "capability_scope" in report
    assert "platform probe" in report["capability_scope"]["tpm_identity"]
