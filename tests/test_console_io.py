"""tests/test_console_io.py — Unit tests for WriteConsoleInput / ReadConsoleOutput

Tests mock kernel32 to avoid requiring actual console attachment.
"""
import ctypes
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestResolveConsolePid:
    def test_returns_target_pid_when_no_console_child(self):
        from self_connect import WindowTarget, _resolve_console_pid
        target = WindowTarget(hwnd=12345, title="test", class_name="test", pid=9999, exe_name="wt.exe")
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.children.return_value = []
            mock_proc.return_value.parent.return_value = None
            result = _resolve_console_pid(target)
        assert result == 9999

    def test_returns_openconsole_pid_when_found(self):
        from self_connect import WindowTarget, _resolve_console_pid
        target = WindowTarget(hwnd=12345, title="test", class_name="test", pid=1000, exe_name="wt.exe")
        mock_child = MagicMock()
        mock_child.name.return_value = "OpenConsole.exe"
        mock_child.pid = 1001
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.children.return_value = [mock_child]
            result = _resolve_console_pid(target)
        assert result == 1001

    def test_returns_conhost_pid_when_found(self):
        from self_connect import WindowTarget, _resolve_console_pid
        target = WindowTarget(hwnd=12345, title="test", class_name="test", pid=2000, exe_name="wt.exe")
        mock_child = MagicMock()
        mock_child.name.return_value = "conhost.exe"
        mock_child.pid = 2001
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.children.return_value = [mock_child]
            result = _resolve_console_pid(target)
        assert result == 2001

    def test_case_insensitive_match(self):
        from self_connect import WindowTarget, _resolve_console_pid
        target = WindowTarget(hwnd=1, title="t", class_name="t", pid=100, exe_name="x.exe")
        mock_child = MagicMock()
        mock_child.name.return_value = "OPENCONSOLE.EXE"
        mock_child.pid = 101
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.children.return_value = [mock_child]
            result = _resolve_console_pid(target)
        assert result == 101

    def test_finds_sibling_conhost(self):
        from self_connect import WindowTarget, _resolve_console_pid
        target = WindowTarget(hwnd=1, title="t", class_name="t", pid=100, exe_name="wt.exe")
        mock_sibling = MagicMock()
        mock_sibling.name.return_value = "conhost.exe"
        mock_sibling.pid = 102
        mock_parent = MagicMock()
        mock_parent.children.return_value = [mock_sibling]
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.children.return_value = []
            mock_proc.return_value.parent.return_value = mock_parent
            result = _resolve_console_pid(target)
        assert result == 102

    def test_returns_target_pid_on_psutil_exception(self):
        from self_connect import WindowTarget, _resolve_console_pid
        target = WindowTarget(hwnd=1, title="t", class_name="t", pid=777, exe_name="x.exe")
        with patch("psutil.Process", side_effect=Exception("no such process")):
            result = _resolve_console_pid(target)
        assert result == 777


class TestWriteConsoleInput:
    def test_empty_string_returns_true(self):
        from self_connect import _write_console_input
        result = _write_console_input(0, "")
        assert result is True

    def test_returns_false_on_attach_failure(self):
        from self_connect import _write_console_input
        with patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "AttachConsole", return_value=False):
            result = _write_console_input(99999, "hello")
        assert result is False

    def test_returns_false_on_invalid_handle(self):
        from self_connect import _write_console_input
        # Code now uses CreateFileW("CONIN$") instead of GetStdHandle — patch that
        INVALID_HANDLE = ctypes.wintypes.HANDLE(-1).value
        with patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "AttachConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=INVALID_HANDLE):
            result = _write_console_input(1234, "test")
        assert result is False


class TestReadConsoleOutput:
    def test_returns_none_on_attach_failure(self):
        from self_connect import _read_console_output
        with patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "AttachConsole", return_value=False):
            result = _read_console_output(99999)
        assert result is None

    def test_returns_none_on_invalid_handle(self):
        from self_connect import _read_console_output
        # Code now uses CreateFileW("CONOUT$") instead of GetStdHandle — patch that
        INVALID_HANDLE = ctypes.wintypes.HANDLE(-1).value
        with patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "AttachConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=INVALID_HANDLE):
            result = _read_console_output(1234)
        assert result is None

    def test_returns_none_on_buffer_info_failure(self):
        from self_connect import _read_console_output
        # Patch CreateFileW to return a valid-looking handle, then fail on GetConsoleScreenBufferInfo
        with patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "AttachConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=42), \
             patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=True), \
             patch.object(ctypes.windll.kernel32, "GetConsoleScreenBufferInfo", return_value=False):
            result = _read_console_output(1234)
        assert result is None


class TestSendStringModeParam:
    """Verify send_string accepts mode parameter without error."""

    def test_mode_param_accepted(self):
        # Just verify the function signature accepts mode — don't actually send
        import inspect

        from self_connect import send_string
        sig = inspect.signature(send_string)
        assert "mode" in sig.parameters
        # Canonical SDK defaults to the safe, per-hwnd-targeted PostMessage path;
        # the console fast path is opt-in ("console"/"auto"). See send_string docstring.
        assert sig.parameters["mode"].default == "postmessage"
