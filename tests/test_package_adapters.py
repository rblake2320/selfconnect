import sys
from pathlib import Path

import pytest
import sc_cli
import sc_mcp


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


def test_pyproject_exports_package_adapter_entry_points():
    text = Path("pyproject.toml").read_text()
    assert 'selfconnect = "sc_cli:main"' in text
    assert 'selfconnect-mcp = "sc_mcp:main"' in text
    assert '"sc_cli.py"' in text
    assert '"sc_mcp.py"' in text
    assert '"experiments/win32_probe/CAPABILITY_BACKLOG.md"' in text
    assert '"experiments/win32_probe/chained_channel.py"' in text
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
