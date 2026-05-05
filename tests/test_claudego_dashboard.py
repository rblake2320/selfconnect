"""
tests/test_claudego_dashboard.py — Unit tests for claudego.dashboard (selfconnect embed)

Uses FastAPI TestClient — no real server, no Win32 needed.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

# ── Mock self_connect + approval_partner ─────────────────────────────────────

class _FakeWindowTarget:
    def __init__(self, hwnd, title):
        self.hwnd = hwnd
        self.title = title
        self.exe_name = "WindowsTerminal.exe"

_mock_sc = types.ModuleType("self_connect")
_mock_sc.WindowTarget = _FakeWindowTarget
_mock_sc.get_text_uia = MagicMock(return_value="")
_mock_sc.list_windows = MagicMock(return_value=[])
_mock_sc.send_string = MagicMock()
sys.modules.setdefault("self_connect", _mock_sc)

import importlib, pathlib as _pl
try:
    importlib.import_module("approval_partner")
except ImportError:
    _root = str(_pl.Path(__file__).parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)

from fastapi.testclient import TestClient  # noqa: E402
from claudego.dashboard import app, set_scanner  # noqa: E402
from claudego.scanner import Scanner  # noqa: E402

_scanner = Scanner()
set_scanner(_scanner)
client = TestClient(app)


class TestRoot:
    def test_status_200(self):
        assert client.get("/").status_code == 200

    def test_is_html(self):
        assert "text/html" in client.get("/").headers["content-type"]

    def test_contains_claudego(self):
        assert "ClaudeGo" in client.get("/").text


class TestHealth:
    def test_ok_true(self):
        assert client.get("/api/health").json()["ok"] is True

    def test_scanner_true(self):
        assert client.get("/api/health").json()["scanner"] is True


class TestTerminals:
    def test_shape(self):
        r = client.get("/api/terminals").json()
        assert "terminals" in r
        assert isinstance(r["terminals"], list)


class TestLog:
    def test_shape(self):
        r = client.get("/api/log").json()
        assert "log" in r
        assert isinstance(r["log"], list)


class TestRules:
    def test_get_shape(self):
        r = client.get("/api/rules").json()
        assert {"allow", "deny", "default_action"} <= set(r.keys())

    def test_post_ok(self):
        r = client.post("/api/rules", json={
            "allow": ["Read(*)", "Glob(*)"],
            "deny": ["Bash(rm:*)"],
            "default_action": "escalate",
        })
        assert r.json()["ok"] is True

    def test_post_persists(self):
        client.post("/api/rules", json={
            "allow": ["Write(*)"],
            "deny": ["Bash(curl:*)"],
            "default_action": "deny",
        })
        r = client.get("/api/rules").json()
        assert r["allow"] == ["Write(*)"]
        assert r["default_action"] == "deny"

    def teardown_method(self):
        from approval_partner import PartnerConfig
        cfg = PartnerConfig()
        _scanner.set_rules(cfg.allow_patterns, cfg.deny_patterns, cfg.default_action)


class TestApprove:
    def test_unknown_returns_false(self):
        assert client.post("/api/approve/99999").json()["ok"] is False

    def test_known_approves(self):
        from unittest.mock import patch
        win = _FakeWindowTarget(hwnd=100, title="T")
        _scanner._win_map[100] = win
        with patch("claudego.scanner.send_string") as mock_ss:
            r = client.post("/api/approve/100")
        assert r.json()["ok"] is True
        mock_ss.assert_called_once_with(win, "y\r")
        del _scanner._win_map[100]


class TestDeny:
    def test_unknown_returns_false(self):
        assert client.post("/api/deny/99999").json()["ok"] is False

    def test_known_denies(self):
        from unittest.mock import patch
        win = _FakeWindowTarget(hwnd=101, title="T")
        _scanner._win_map[101] = win
        with patch("claudego.scanner.send_string") as mock_ss:
            r = client.post("/api/deny/101")
        assert r.json()["ok"] is True
        mock_ss.assert_called_once_with(win, "n\r")
        del _scanner._win_map[101]
