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


def test_mesh_registry_update_tracks_sharpness_counters(monkeypatch):
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

        updated = sc_mesh_registry.update_agent(
            "codex-1",
            token_estimate=125_000,
            compact_count=1,
            missed_acks=1,
            registry_path=path,
        )
        assert updated["ok"] is True
        assert updated["agent"]["token_estimate"] == 125_000
        assert updated["agent"]["compact_count"] == 1
        assert updated["agent"]["missed_acks"] == 1
    finally:
        temp_dir.cleanup()


def test_mesh_registry_health_report_marks_old_stale_agent_red():
    now = 10_000.0
    registry = {
        "agents": [
            {
                "role": "claude-1",
                "birth_id": "claude-1-test",
                "status": "active",
                "created_at": now - (5 * 60 * 60),
                "last_seen": now - (20 * 60),
                "token_estimate": 190_000,
                "compact_count": 2,
                "missed_acks": 0,
            }
        ]
    }

    report = sc_mesh_registry.health_report(registry, now=now)
    item = report["agents"][0]
    assert item["risk"] == "red"
    assert item["action"] == "compact_or_replace"
    assert "session_age>=4h" in item["reasons"]
    assert "heartbeat_stale>=15m" in item["reasons"]
    assert "tokens>=180k" in item["reasons"]


def test_mesh_registry_health_report_keeps_fresh_agent_green():
    now = 10_000.0
    registry = {
        "agents": [
            {
                "role": "codex-1",
                "birth_id": "codex-1-test",
                "status": "active",
                "created_at": now - 60,
                "last_seen": now - 10,
            }
        ]
    }

    report = sc_mesh_registry.health_report(registry, now=now)
    item = report["agents"][0]
    assert item["risk"] == "green"
    assert item["action"] == "continue"


def test_mesh_registry_writes_compact_handoff(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "mesh.json"
    handoff_dir = Path(temp_dir.name) / "handoffs"

    try:
        registered = sc_mesh_registry.register_agent(
            _FakeWindow.hwnd,
            "codex-1",
            task="build compact handoff",
            registry_path=path,
            expected_class=_FakeWindow.class_name,
        )
        assert registered["ok"] is True

        result = sc_mesh_registry.write_compact_handoff(
            "codex-1",
            summary="summary body",
            next_action="next body",
            tests="tests body",
            repo_path=temp_dir.name,
            handoff_dir=handoff_dir,
            registry_path=path,
        )
        assert result["ok"] is True
        handoff = Path(result["path"])
        assert handoff.exists()
        text = handoff.read_text(encoding="utf-8")
        assert "# SelfConnect Compact Handoff - codex-1" in text
        assert "summary body" in text
        assert "next body" in text
        assert "tests body" in text
        assert result["agent"]["compact_count"] == 1
        assert result["agent"]["last_handoff_path"] == str(handoff)
        assert result["agent"]["status"] == "handoff"
    finally:
        temp_dir.cleanup()


def test_mesh_registry_event_log_tracks_role_lifecycle(monkeypatch):
    fake = _FakeSelfConnect()
    monkeypatch.setattr(sc_cli, "_load_sc", lambda: fake)
    monkeypatch.setattr(sc_cli, "_window_valid_visible", lambda hwnd: (True, True))
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "mesh.json"

    try:
        registered = sc_mesh_registry.register_agent(
            _FakeWindow.hwnd,
            "B",
            task="initial work",
            registry_path=path,
            expected_class=_FakeWindow.class_name,
        )
        assert registered["ok"] is True
        birth_id = registered["agent"]["birth_id"]

        updated = sc_mesh_registry.update_agent(
            "B",
            status="working",
            task="red team task",
            registry_path=path,
        )
        assert updated["ok"] is True
        removed = sc_mesh_registry.remove_agent("B", registry_path=path)
        assert removed["ok"] is True

        events = sc_mesh_registry.load_events(registry_path=path, limit=10)
        event_types = [item["event_type"] for item in events["events"]]
        assert event_types == ["role_registered", "role_status_updated", "role_removed"]
        assert {item["birth_id"] for item in events["events"]} == {birth_id}
        assert events["events"][1]["task"] == "red team task"
        assert events["events"][2]["summary"] == "role removed from active registry"
    finally:
        temp_dir.cleanup()


def test_mesh_registry_git_snapshot_tracks_current_repo():
    snap = sc_mesh_registry.git_snapshot(Path.cwd())
    assert snap["ok"] is True
    assert snap["repo_path"]
    assert snap["branch"]
    assert len(snap["head"]) == 40
    assert len(snap["head_short"]) == 7
    assert isinstance(snap["dirty"], bool)
    assert isinstance(snap["dirty_count"], int)
    assert isinstance(snap["status_sample"], list)


def test_mesh_registry_git_snapshot_reports_non_repo(monkeypatch):
    class _FakeGitResult:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(sc_mesh_registry, "_run_git", lambda *args, **kwargs: _FakeGitResult())
    snap = sc_mesh_registry.git_snapshot("C:/not-a-repo")
    assert snap["ok"] is False
    assert Path(snap["repo_path"]) == Path("C:/not-a-repo")
    assert "not a git repository" in snap["error"]


def test_mesh_registry_event_log_filters_and_reports_parse_errors():
    temp_dir = tempfile.TemporaryDirectory()
    event_path = Path(temp_dir.name) / "mesh_events.jsonl"

    try:
        first = sc_mesh_registry.append_event(
            "role_registered",
            role="codex-1",
            birth_id="codex-1-a",
            event_log_path=event_path,
        )
        second = sc_mesh_registry.append_event(
            "role_registered",
            role="claude-1",
            birth_id="claude-1-a",
            event_log_path=event_path,
        )
        with event_path.open("a", encoding="utf-8") as fh:
            fh.write("{not valid json}\n")

        by_role = sc_mesh_registry.load_events(role="claude-1", event_log_path=event_path)
        assert by_role["parse_error_count"] == 1
        assert [item["event_id"] for item in by_role["events"]] == [second["event"]["event_id"]]

        by_birth = sc_mesh_registry.load_events(
            birth_id="codex-1-a",
            event_log_path=event_path,
        )
        assert [item["event_id"] for item in by_birth["events"]] == [first["event"]["event_id"]]
    finally:
        temp_dir.cleanup()


def test_mesh_registry_event_log_hash_chain_verifies_and_detects_tamper():
    temp_dir = tempfile.TemporaryDirectory()
    event_path = Path(temp_dir.name) / "mesh_events.jsonl"

    try:
        first = sc_mesh_registry.append_event(
            "task_assigned",
            role="codex-1",
            summary="original task",
            event_log_path=event_path,
        )
        second = sc_mesh_registry.append_event(
            "task_complete",
            role="codex-1",
            summary="done",
            event_log_path=event_path,
        )

        assert first["event"]["prev_event_hash"] == sc_mesh_registry.EVENT_GENESIS_HASH
        assert second["event"]["prev_event_hash"] == first["event"]["event_hash"]
        assert sc_mesh_registry.verify_events(event_log_path=event_path)["ok"] is True

        lines = event_path.read_text(encoding="utf-8").splitlines()
        tampered = lines[0].replace("original task", "changed task")
        event_path.write_text("\n".join([tampered, *lines[1:]]) + "\n", encoding="utf-8")

        verified = sc_mesh_registry.verify_events(event_log_path=event_path)
        assert verified["ok"] is False
        errors = {item["error"] for item in verified["errors"]}
        assert "hash_mismatch" in errors
        assert "chain_break" in errors
    finally:
        temp_dir.cleanup()


def test_mesh_registry_cli_writes_and_reads_manual_event():
    temp_dir = tempfile.TemporaryDirectory()
    registry_path = Path(temp_dir.name) / "mesh.json"
    event_path = Path(temp_dir.name) / "events.jsonl"

    try:
        wrote = sc_mesh_registry.main([
            "--registry",
            str(registry_path),
            "event",
            "--event-log",
            str(event_path),
            "--type",
            "task_assigned",
            "--role",
            "codex-2",
            "--birth-id",
            "codex-2-test",
            "--summary",
            "assigned package audit",
            "--data-json",
            '{"priority":"high"}',
        ])
        assert wrote == 0

        loaded = sc_mesh_registry.load_events(event_log_path=event_path)
        assert loaded["events"][0]["event_type"] == "task_assigned"
        assert loaded["events"][0]["role"] == "codex-2"
        assert loaded["events"][0]["data"] == {"priority": "high"}
        assert loaded["events"][0]["repo"]["ok"] is True
        assert len(loaded["events"][0]["repo"]["head"]) == 40

        verified = sc_mesh_registry.verify_events(event_log_path=event_path)
        assert verified["ok"] is True
        assert verified["events_checked"] == 1
        assert len(verified["head_hash"]) == 64
    finally:
        temp_dir.cleanup()


def test_mesh_registry_watch_report_includes_task_and_risk():
    now = 10_000.0
    registry = {
        "agents": [
            {
                "role": "codex-1",
                "birth_id": "codex-1-test",
                "agent": "codex",
                "profile": "explore",
                "status": "active",
                "created_at": now - 60,
                "last_seen": now - 10,
                "hwnd": 123,
                "task": "current task",
            }
        ]
    }

    report = sc_mesh_registry.watch_report(registry, now=now)
    row = report["agents"][0]
    assert row["role"] == "codex-1"
    assert row["risk"] == "green"
    assert row["task"] == "current task"


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
    assert 'selfconnect-fleet = "sc_fleet_guard:main"' in text
    assert 'selfconnect-bench = "sc_fabric_benchmark:main"' in text
    assert '"sc_cli.py"' in text
    assert '"sc_mcp.py"' in text
    assert '"sc_mesh_registry.py"' in text
    assert '"sc_mesh_lease.py"' in text
    assert '"sc_echo_filter.py"' in text
    assert '"sc_fleet_guard.py"' in text
    assert '"sc_fabric_benchmark.py"' in text
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
    assert '"docs/ARCHITECTURE_AUDIT_2026-06-20.md"' in text
    assert '"docs/ARCHITECTURE_CONSENSUS_2026-06-20.md"' in text
    assert '"docs/FABRIC_V0_BENCHMARK_SPEC.md"' in text
    assert '"docs/FABRIC_V2_BENCHMARK_RESULTS.md"' in text
    assert '"docs/FABRIC_V2_BUILD_TARGETS.md"' in text
    assert '"docs/FABRIC_V2_REAL_LADDER_PROTOCOL.md"' in text
    assert '"docs/FABRIC_V2_STRESS_PROTOCOL.md"' in text
    assert '"docs/FLEET_AUTO_HALT_PROTOCOL.md"' in text
    assert '"docs/PATENT_DESIGN_AROUND_DEFENSE.md"' in text
    assert '"docs/PATENT_EVIDENCE_FREEZE_2026-06-20.md"' in text
    assert '"docs/OPEN_DEFENSE_BUILD_TARGETS.md"' in text
    assert '"docs/PATENT_PRIOR_ART_SNAPSHOT.md"' in text
    assert '"docs/PIPE_ROLE_LEASE_PROOF.md"' in text
    assert '"docs/UIA_ECHO_FILTER_TERMCONTROL.md"' in text
    assert '"docs/PROVEN_VS_UNTESTED.md"' in text
    assert '"docs/SELFCONNECT_PRODUCT_BOUNDARIES.md"' in text
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
