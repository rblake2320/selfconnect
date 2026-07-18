"""tests/test_console_io.py — Unit tests for WriteConsoleInput / ReadConsoleOutput

Tests mock kernel32 to avoid requiring actual console attachment.
"""
import ctypes
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
        with patch("self_connect._console_process_ids", return_value=([10, 11], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "AttachConsole", return_value=False):
            result = _write_console_input(99999, "hello")
        assert result is False

    def test_returns_false_on_invalid_handle(self):
        from self_connect import _write_console_input
        # Code now uses CreateFileW("CONIN$") instead of GetStdHandle — patch that
        INVALID_HANDLE = ctypes.wintypes.HANDLE(-1).value
        with patch("self_connect._console_process_ids", return_value=([10, 11], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "AttachConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=INVALID_HANDLE):
            result = _write_console_input(1234, "test")
        assert result is False

    def test_same_console_writes_all_records_without_detaching(self):
        from self_connect import _write_console_input_result

        def write_all(_handle, _records, count, written_pointer):
            written_pointer._obj.value = count
            return True

        with patch("self_connect._console_process_ids", return_value=([10, 1234], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "FreeConsole") as free_console, \
             patch.object(ctypes.windll.kernel32, "AttachConsole") as attach_console, \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=42), \
             patch.object(ctypes.windll.kernel32, "WriteConsoleInputW", side_effect=write_all), \
             patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=True):
            result = _write_console_input_result(1234, "ok\r")

        assert result["ok"] is True
        assert result["records_written"] == result["records_requested"] == 6
        assert result["caller_console_restored"] is True
        assert result["restoration_method"] == "same_console"
        free_console.assert_not_called()
        attach_console.assert_not_called()

    def test_console_write_rechecks_deadline_immediately_before_native_write(self):
        from self_connect import _write_console_input_result

        with patch("self_connect._console_process_ids", return_value=([10, 1234], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=42), \
             patch.object(ctypes.windll.kernel32, "WriteConsoleInputW") as native_write, \
             patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=True), \
             patch("self_connect.time.monotonic", return_value=2.0):
            result = _write_console_input_result(1234, "x", deadline=1.0)

        native_write.assert_not_called()
        assert result["ok"] is False
        assert result["error"] == "console_input_exception:TimeoutError"

    def test_cross_console_write_restores_explicit_original_console(self):
        from self_connect import _write_console_input_result

        def write_all(_handle, _records, count, written_pointer):
            written_pointer._obj.value = count
            return True

        with patch("self_connect._console_process_ids", return_value=([10, 11], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True) as free_console, \
             patch.object(ctypes.windll.kernel32, "AttachConsole", return_value=True) as attach_console, \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=42), \
             patch.object(ctypes.windll.kernel32, "WriteConsoleInputW", side_effect=write_all), \
             patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=True):
            result = _write_console_input_result(1234, "x")

        assert result["ok"] is True
        assert result["caller_console_restored"] is True
        assert result["restoration_method"] == "pid:11"
        assert free_console.call_count == 2
        assert [int(call.args[0].value) for call in attach_console.call_args_list] == [1234, 11]

    def test_successful_write_fails_closed_when_caller_restore_fails(self):
        from self_connect import _write_console_input_result

        def write_all(_handle, _records, count, written_pointer):
            written_pointer._obj.value = count
            return True

        with patch("self_connect._console_process_ids", return_value=([10, 11], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "AttachConsole", side_effect=[True, False]), \
             patch.object(ctypes.windll.kernel32, "GetLastError", return_value=5), \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=42), \
             patch.object(ctypes.windll.kernel32, "WriteConsoleInputW", side_effect=write_all), \
             patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=True):
            result = _write_console_input_result(1234, "x")

        assert result["ok"] is False
        assert result["error"] == "caller_console_restore_failed"
        assert result["caller_console_restored"] is False
        assert result["records_written"] == result["records_requested"]
        assert result["winerror"] == 5

    def test_partial_console_write_is_failure(self):
        from self_connect import _write_console_input_result

        def write_partial(_handle, _records, count, written_pointer):
            written_pointer._obj.value = count - 1
            return True

        with patch("self_connect._console_process_ids", return_value=([10, 1234], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=42), \
             patch.object(ctypes.windll.kernel32, "WriteConsoleInputW", side_effect=write_partial), \
             patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=True):
            result = _write_console_input_result(1234, "x")

        assert result["ok"] is False
        assert result["error"] == "console_input_partial_write"
        assert result["records_written"] == 1

    def test_target_attach_failure_restores_explicit_original_console(self):
        from self_connect import _write_console_input_result

        with patch("self_connect._console_process_ids", return_value=([10, 11], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "AttachConsole", side_effect=[False, True]) as attach, \
             patch.object(ctypes.windll.kernel32, "GetLastError", return_value=5):
            result = _write_console_input_result(1234, "x")

        assert result["ok"] is False
        assert result["error"] == "target_console_attach_failed"
        assert result["caller_console_restored"] is True
        assert result["restoration_method"] == "pid:11"
        assert [int(call.args[0].value) for call in attach.call_args_list] == [1234, 11]

    def test_snapshot_failure_does_not_detach_or_attach(self):
        from self_connect import _write_console_input_result

        with patch("self_connect._console_process_ids", return_value=([], 5)), \
             patch.object(ctypes.windll.kernel32, "FreeConsole") as free_console, \
             patch.object(ctypes.windll.kernel32, "AttachConsole") as attach_console:
            result = _write_console_input_result(1234, "x")

        assert result["ok"] is False
        assert result["error"] == "caller_console_snapshot_failed"
        assert result["winerror"] == 5
        free_console.assert_not_called()
        attach_console.assert_not_called()

    def test_cross_console_without_restore_candidate_fails_before_detach(self):
        from self_connect import _write_console_input_result

        with patch("self_connect._console_process_ids", return_value=([10], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "FreeConsole") as free_console, \
             patch.object(ctypes.windll.kernel32, "AttachConsole") as attach_console:
            result = _write_console_input_result(1234, "x")

        assert result["ok"] is False
        assert result["error"] == "caller_console_restore_target_unavailable"
        free_console.assert_not_called()
        attach_console.assert_not_called()


class TestReadConsoleOutput:
    def test_returns_none_on_attach_failure(self):
        from self_connect import _read_console_output
        with patch("self_connect._console_process_ids", return_value=([10, 11], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "FreeConsole", return_value=True), \
             patch.object(ctypes.windll.kernel32, "AttachConsole", side_effect=[False, True]) as attach:
            result = _read_console_output(99999)
        assert result is None
        assert [int(call.args[0].value) for call in attach.call_args_list] == [99999, 11]

    def test_returns_none_on_invalid_handle(self):
        from self_connect import _read_console_output
        # Code now uses CreateFileW("CONOUT$") instead of GetStdHandle — patch that
        INVALID_HANDLE = ctypes.wintypes.HANDLE(-1).value
        with patch("self_connect._console_process_ids", return_value=([10, 1234], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "FreeConsole") as free_console, \
             patch.object(ctypes.windll.kernel32, "AttachConsole") as attach_console, \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=INVALID_HANDLE):
            result = _read_console_output(1234)
        assert result is None
        free_console.assert_not_called()
        attach_console.assert_not_called()

    def test_returns_none_on_buffer_info_failure(self):
        from self_connect import _read_console_output
        # Patch CreateFileW to return a valid-looking handle, then fail on GetConsoleScreenBufferInfo
        with patch("self_connect._console_process_ids", return_value=([10, 1234], 0)), \
             patch.object(ctypes.windll.kernel32, "GetCurrentProcessId", return_value=10), \
             patch.object(ctypes.windll.kernel32, "FreeConsole") as free_console, \
             patch.object(ctypes.windll.kernel32, "AttachConsole") as attach_console, \
             patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=42), \
             patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=True), \
             patch.object(ctypes.windll.kernel32, "GetConsoleScreenBufferInfo", return_value=False):
            result = _read_console_output(1234)
        assert result is None
        free_console.assert_not_called()
        attach_console.assert_not_called()


class TestSendStringModeParam:
    """Verify send_string accepts mode parameter without error."""

    def test_mode_param_accepted(self):
        # Just verify the function signature accepts mode — don't actually send
        import inspect

        from self_connect import send_string
        sig = inspect.signature(send_string)
        assert "mode" in sig.parameters
        assert sig.parameters["mode"].default == "auto"

    def test_auto_uses_console_input_for_consolewindowclass(self):
        from self_connect import WindowTarget, send_string

        target = WindowTarget(123, "Claude", "ConsoleWindowClass", 456, "pwsh.exe")
        console_result = {
            "ok": True,
            "transport": "win32_console_input",
            "records_requested": 10,
            "records_written": 10,
            "error": "",
            "winerror": 0,
            "caller_console_restored": True,
            "restoration_method": "pid:11",
            "delivery_evidence": "console_input_records_written",
            "delivery_verified": False,
        }
        with patch("self_connect._write_console_input_result", return_value=console_result) as writer, \
             patch("self_connect._send_char_postmessage") as post:
            result = send_string(target, "test\r")

        assert result["ok"] is True
        assert result["transport"] == "win32_console_input"
        assert result["delivery_evidence"] == "console_input_records_written"
        assert result["delivery_verified"] is False
        writer.assert_called_once_with(456, "test\r")
        post.assert_not_called()

    def test_auto_retains_wm_char_for_cascadia_and_marks_unverified(self):
        from self_connect import WindowTarget, send_string

        target = WindowTarget(123, "Claude", "CASCADIA_HOSTING_WINDOW_CLASS", 456, "WindowsTerminal.exe")
        with patch("self_connect.find_child_by_class", return_value=789), \
             patch("self_connect._write_console_input_result") as writer, \
             patch("self_connect._send_char_postmessage", return_value=True) as post:
            result = send_string(target, "x\r", char_delay=0)

        assert result["ok"] is True
        assert result["transport"] == "postmessage_wm_char"
        assert result["delivery_evidence"] == "message_queue_acceptance_only"
        assert result["delivery_verified"] is False
        assert [call.args for call in post.call_args_list] == [(789, "x"), (789, "\r")]
        writer.assert_not_called()

    def test_postmessage_is_rejected_for_consolewindowclass(self):
        from self_connect import WindowTarget, send_string

        target = WindowTarget(123, "Claude", "ConsoleWindowClass", 456, "pwsh.exe")
        with patch("self_connect._send_char_postmessage") as post:
            result = send_string(target, "test", mode="postmessage")

        assert result["ok"] is False
        assert result["error"] == "postmessage_transport_requires_cascadia"
        assert result["delivery_evidence"] == "none"
        post.assert_not_called()

    def test_postmessage_partial_acceptance_is_failure_not_delivery(self):
        from self_connect import WindowTarget, send_string

        target = WindowTarget(123, "Claude", "CASCADIA_HOSTING_WINDOW_CLASS", 456, "WindowsTerminal.exe")
        with patch("self_connect.find_child_by_class", return_value=789), \
             patch("self_connect._send_char_postmessage", side_effect=[True, False]) as post:
            result = send_string(target, "xy", char_delay=0)

        assert result["ok"] is False
        assert result["chars_requested"] == 2
        assert result["chars_accepted"] == 1
        assert result["delivery_evidence"] == "message_queue_acceptance_only"
        assert result["delivery_verified"] is False
        assert result["error"] == "postmessage_queue_rejected"
        assert post.call_count == 2


def test_window_pool_send_to_returns_transport_record():
    from self_connect import WindowPool, WindowTarget

    pool = WindowPool()
    pool.add_target(
        "console",
        WindowTarget(123, "Claude", "ConsoleWindowClass", 456, "pwsh.exe"),
    )
    accepted = {
        "ok": True,
        "transport": "win32_console_input",
        "delivery_verified": False,
    }
    with patch("self_connect.send_string", return_value=accepted):
        result = pool.send_to("console", "hello")

    assert result is accepted


def test_window_pool_send_to_raises_on_transport_failure():
    from self_connect import WindowPool, WindowTarget

    pool = WindowPool()
    pool.add_target(
        "console",
        WindowTarget(123, "Claude", "ConsoleWindowClass", 456, "pwsh.exe"),
    )
    with patch(
        "self_connect.send_string",
        return_value={
            "ok": False,
            "transport": "win32_console_input",
            "error": "console_input_write_failed",
        },
    ), pytest.raises(RuntimeError, match="console_input_write_failed"):
        pool.send_to("console", "hello")


def test_send_frame_reports_transport_rejection_without_ack_probe():
    from self_connect import WindowTarget, send_frame

    target = WindowTarget(123, "Claude", "ConsoleWindowClass", 456, "pwsh.exe")
    with patch(
        "self_connect.send_string",
        return_value={
            "ok": False,
            "transport": "win32_console_input",
            "error": "target_console_attach_failed",
        },
    ), patch("self_connect.verify_delivery") as verify:
        result = send_frame(target, 999, "payload", ack=True)

    assert result["transport_accepted"] is False
    assert result["acked"] is False
    assert result["delivery_verified"] is False
    assert result["error"] == "target_console_attach_failed"
    verify.assert_not_called()


def test_send_frame_marks_delivery_only_after_independent_ack():
    from self_connect import WindowTarget, send_frame

    target = WindowTarget(123, "Claude", "CASCADIA_HOSTING_WINDOW_CLASS", 456, "wt.exe")
    with patch(
        "self_connect.send_string",
        return_value={
            "ok": True,
            "transport": "postmessage_wm_char",
            "delivery_verified": False,
        },
    ), patch("self_connect.verify_delivery", return_value=True):
        result = send_frame(target, 999, "payload", ack=True)

    assert result["transport_accepted"] is True
    assert result["acked"] is True
    assert result["delivery_verified"] is True
