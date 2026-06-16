import sys
from pathlib import Path
from typing import ClassVar

import pytest
import sc_cli
import sc_mcp


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


def test_pyproject_exports_package_adapter_entry_points():
    text = Path("pyproject.toml").read_text()
    assert 'selfconnect = "sc_cli:main"' in text
    assert 'selfconnect-mcp = "sc_mcp:main"' in text
    assert '"sc_cli.py"' in text
    assert '"sc_mcp.py"' in text
    assert '"experiments/win32_probe/CAPABILITY_BACKLOG.md"' in text
    assert '"experiments/win32_probe/SERVICE_SID_DAEMON.md"' in text
    assert '"experiments/win32_probe/chained_channel.py"' in text
    assert '"experiments/win32_probe/etw_provider.py"' in text
    assert '"experiments/win32_probe/service_sid_probe.py"' in text
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
