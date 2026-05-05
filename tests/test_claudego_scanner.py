"""
tests/test_claudego_scanner.py — Unit tests for claudego.scanner (selfconnect embed)

No Win32 desktop needed — all Win32/approval_partner calls are mocked.
"""
from __future__ import annotations

import sys
import types
import time
from unittest.mock import MagicMock

import pytest

# ── Mock self_connect + approval_partner before import ───────────────────────

class _FakeWindowTarget:
    def __init__(self, hwnd: int, title: str, exe: str = "WindowsTerminal.exe"):
        self.hwnd = hwnd
        self.title = title
        self.exe_name = exe


_mock_sc = types.ModuleType("self_connect")
_mock_sc.WindowTarget = _FakeWindowTarget
_mock_sc.get_text_uia = MagicMock(return_value="")
_mock_sc.list_windows = MagicMock(return_value=[])
_mock_sc.send_string = MagicMock()
sys.modules.setdefault("self_connect", _mock_sc)

# Mock approval_partner if not already importable from root
import importlib
try:
    importlib.import_module("approval_partner")
except ImportError:
    import sys as _sys
    import pathlib as _pl
    _ROOT = _pl.Path(__file__).parent.parent
    if str(_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_ROOT))

from claudego.scanner import (  # noqa: E402
    Scanner,
    ScanEvent,
    EVENT_TERMINAL_DISCOVERED,
    EVENT_AUTO_APPROVED,
    EVENT_AUTO_DENIED,
    EVENT_MANUAL_APPROVED,
    EVENT_MANUAL_DENIED,
)
from approval_partner import PartnerConfig  # type: ignore[import]


# ── PartnerConfig ─────────────────────────────────────────────────────────────

class TestPartnerConfig:
    def test_defaults(self):
        cfg = PartnerConfig()
        assert cfg.default_action == "escalate"
        assert cfg.dry_run is False
        assert len(cfg.allow_patterns) > 0
        assert len(cfg.deny_patterns) > 0

    def test_invalid_default_action_raises(self):
        with pytest.raises(ValueError, match="default_action"):
            PartnerConfig(default_action="maybe")


# ── ScanEvent ─────────────────────────────────────────────────────────────────

class TestScanEvent:
    def test_to_dict_shape(self):
        evt = ScanEvent(
            event_type=EVENT_AUTO_APPROVED,
            hwnd=1,
            title="t",
            tool_call="Bash(git:push)",
            rule_matched="Bash(git:*)",
        )
        d = evt.to_dict()
        assert d["event_type"] == EVENT_AUTO_APPROVED
        assert d["hwnd"] == 1
        assert d["tool_call"] == "Bash(git:push)"
        assert d["rule_matched"] == "Bash(git:*)"

    def test_timestamp_auto_set(self):
        before = time.time()
        evt = ScanEvent(EVENT_TERMINAL_DISCOVERED, hwnd=1, title="t")
        after = time.time()
        assert before <= evt.timestamp <= after


# ── Scanner ───────────────────────────────────────────────────────────────────

class TestScanner:
    def setup_method(self):
        self.scanner = Scanner()

    def test_empty_state(self):
        assert self.scanner.get_terminals() == []
        assert self.scanner.get_audit_log() == []

    def test_get_rules_shape(self):
        rules = self.scanner.get_rules()
        assert "allow" in rules and "deny" in rules and "default_action" in rules

    def test_set_rules(self):
        self.scanner.set_rules(["Read(*)"], ["Bash(rm:*)"], "deny")
        r = self.scanner.get_rules()
        assert r["allow"] == ["Read(*)"]
        assert r["deny"] == ["Bash(rm:*)"]
        assert r["default_action"] == "deny"

    def test_audit_log_limit(self):
        for i in range(30):
            self.scanner._record(ScanEvent(EVENT_AUTO_APPROVED, hwnd=i, title="t"))
        assert len(self.scanner.get_audit_log(limit=5)) == 5

    def test_callback_fires(self):
        received = []
        self.scanner.add_callback(received.append)
        evt = ScanEvent(EVENT_TERMINAL_DISCOVERED, hwnd=1, title="t")
        self.scanner._record(evt)
        assert received == [evt]

    def test_manual_approve_unknown_hwnd(self):
        assert self.scanner.manual_approve(99999) is False

    def test_manual_deny_unknown_hwnd(self):
        assert self.scanner.manual_deny(99999) is False

    def test_manual_approve_sends_y(self):
        from unittest.mock import patch
        win = _FakeWindowTarget(hwnd=10, title="T")
        self.scanner._win_map[10] = win
        with patch("claudego.scanner.send_string") as mock_ss:
            assert self.scanner.manual_approve(10) is True
            mock_ss.assert_called_once_with(win, "y\r")

    def test_manual_deny_sends_n(self):
        from unittest.mock import patch
        win = _FakeWindowTarget(hwnd=11, title="T")
        self.scanner._win_map[11] = win
        with patch("claudego.scanner.send_string") as mock_ss:
            assert self.scanner.manual_deny(11) is True
            mock_ss.assert_called_once_with(win, "n\r")

    def test_dry_run_skips_inject(self):
        from unittest.mock import patch
        s = Scanner(dry_run=True)
        win = _FakeWindowTarget(hwnd=12, title="T")
        s._win_map[12] = win
        with patch("claudego.scanner.send_string") as mock_ss:
            s.manual_approve(12)
            mock_ss.assert_not_called()

    def test_manual_approve_emits_event(self):
        from unittest.mock import patch
        received = []
        self.scanner.add_callback(received.append)
        win = _FakeWindowTarget(hwnd=20, title="T")
        self.scanner._win_map[20] = win
        with patch("claudego.scanner.send_string"):
            self.scanner.manual_approve(20)
        assert any(e.event_type == EVENT_MANUAL_APPROVED for e in received)

    def test_manual_deny_emits_event(self):
        from unittest.mock import patch
        received = []
        self.scanner.add_callback(received.append)
        win = _FakeWindowTarget(hwnd=21, title="T")
        self.scanner._win_map[21] = win
        with patch("claudego.scanner.send_string"):
            self.scanner.manual_deny(21)
        assert any(e.event_type == EVENT_MANUAL_DENIED for e in received)
