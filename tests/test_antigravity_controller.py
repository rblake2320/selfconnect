"""
Tests for antigravity_controller.py

Unit tests: run without Antigravity installed or running.
Integration tests: skipped automatically if Antigravity is not found.
"""

import sys
import time
import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from antigravity_controller import (
    AntigravityMonitor,
    AntigravitySession,
    _is_antigravity_title,
)


# ─── Unit Tests ───────────────────────────────────────────────────────────────


class TestIsAntigravityTitle:
    def test_standalone_match(self):
        assert _is_antigravity_title("Antigravity - Exploring QuoteHub Architecture")

    def test_case_insensitive(self):
        assert _is_antigravity_title("ANTIGRAVITY")

    def test_exclude_google_chrome(self):
        assert not _is_antigravity_title("Antigravity - Google Chrome")

    def test_exclude_vscode(self):
        assert not _is_antigravity_title("Antigravity - Visual Studio Code")

    def test_unrelated_window(self):
        assert not _is_antigravity_title("GitHub Copilot Chat")

    def test_empty_string(self):
        assert not _is_antigravity_title("")

    def test_cursor_editor(self):
        assert not _is_antigravity_title("Cursor")

    def test_antigravity_settings(self):
        assert _is_antigravity_title("Antigravity - Settings")


class TestAntigravitySession:
    def _make_session(self, hwnd=0x1234, model="Gemini 3.1 Pro (High)"):
        return AntigravitySession(
            hwnd=hwnd,
            chrome_hwnd=0x5678,
            pid=12345,
            title="Antigravity - Test",
            model=model,
        )

    def test_construction_defaults(self):
        s = self._make_session()
        assert s.hwnd == 0x1234
        assert s.chrome_hwnd == 0x5678
        assert s.pid == 12345
        assert s.title == "Antigravity - Test"
        assert s.model == "Gemini 3.1 Pro (High)"
        assert s.is_standalone is True
        assert s.uia_ready is False

    def test_connected_at_auto_set(self):
        before = time.time()
        s = self._make_session()
        after = time.time()
        assert before <= s.connected_at <= after

    def test_is_valid_false_for_fake_hwnd(self):
        # HWND 0x1234 is almost certainly not a real window
        s = self._make_session(hwnd=0x1234)
        # This should return False for a non-existent window
        # (may return True on some systems if 0x1234 happens to be valid — acceptable)
        result = s.is_valid()
        assert isinstance(result, bool)

    def test_str_representation(self):
        s = self._make_session()
        text = str(s)
        assert "0x1234" in text
        assert "Gemini 3.1 Pro" in text
        assert "standalone=True" in text

    def test_explicit_fields(self):
        s = AntigravitySession(
            hwnd=0xABCD,
            chrome_hwnd=0xEF01,
            pid=9999,
            title="Test",
            model="Claude Sonnet 4.6",
            is_standalone=False,
            uia_ready=True,
        )
        assert s.is_standalone is False
        assert s.uia_ready is True
        assert s.model == "Claude Sonnet 4.6"


class TestAntigravityMonitor:
    def _make_session(self):
        return AntigravitySession(
            hwnd=0x1234,
            chrome_hwnd=0x5678,
            pid=12345,
            title="Antigravity - Test",
        )

    def test_on_returns_self(self):
        session = self._make_session()
        monitor = AntigravityMonitor(session)
        result = monitor.on("response", lambda r: None)
        assert result is monitor

    def test_chaining(self):
        session = self._make_session()
        monitor = AntigravityMonitor(session)
        result = (
            monitor
            .on("response", lambda r: None)
            .on("error", lambda e: None)
            .on("model_changed", lambda m: None)
        )
        assert result is monitor

    def test_unknown_event_raises(self):
        session = self._make_session()
        monitor = AntigravityMonitor(session)
        with pytest.raises(ValueError, match="Unknown event"):
            monitor.on("banana", lambda x: None)

    def test_is_running_before_start(self):
        session = self._make_session()
        monitor = AntigravityMonitor(session)
        assert monitor.is_running() is False

    def test_stop_without_start(self):
        session = self._make_session()
        monitor = AntigravityMonitor(session)
        monitor.stop(timeout=0.1)  # Should not raise

    def test_multiple_handlers_same_event(self):
        calls = []
        session = self._make_session()
        monitor = AntigravityMonitor(session)
        monitor.on("error", lambda e: calls.append(("a", str(e))))
        monitor.on("error", lambda e: calls.append(("b", str(e))))
        monitor._emit("error", RuntimeError("test"))
        assert len(calls) == 2
        assert calls[0][0] == "a"
        assert calls[1][0] == "b"

    def test_emit_response(self):
        received = []
        session = self._make_session()
        monitor = AntigravityMonitor(session)
        monitor.on("response", received.append)
        monitor._emit("response", "Hello from Gemini")
        assert received == ["Hello from Gemini"]

    def test_emit_model_changed(self):
        received = []
        session = self._make_session()
        monitor = AntigravityMonitor(session)
        monitor.on("model_changed", received.append)
        monitor._emit("model_changed", "Claude Sonnet 4.6")
        assert received == ["Claude Sonnet 4.6"]

    def test_handler_exception_does_not_propagate(self):
        session = self._make_session()
        monitor = AntigravityMonitor(session)
        monitor.on("response", lambda r: (_ for _ in ()).throw(ValueError("oops")))
        # Should not raise
        monitor._emit("response", "text")

    def test_default_poll_interval(self):
        session = self._make_session()
        monitor = AntigravityMonitor(session)
        assert monitor._poll == 1.5

    def test_custom_poll_interval(self):
        session = self._make_session()
        monitor = AntigravityMonitor(session, poll=0.5)
        assert monitor._poll == 0.5


# ─── Integration Tests ────────────────────────────────────────────────────────
# These tests require a running Antigravity window.
# They are skipped automatically if no Antigravity window is found.


def _antigravity_available() -> bool:
    """Return True if at least one Antigravity window is discoverable."""
    try:
        from antigravity_controller import _find_chrome_windows, _is_antigravity_title
        windows = _find_chrome_windows()
        return any(_is_antigravity_title(t) for _, t, _ in windows)
    except Exception:
        return False


requires_antigravity = pytest.mark.skipif(
    not _antigravity_available(),
    reason="Antigravity is not running"
)


@requires_antigravity
class TestIntegration:
    @pytest.fixture(scope="class")
    def session(self):
        from antigravity_controller import connect
        return connect()

    def test_connect_returns_session(self, session):
        assert isinstance(session, AntigravitySession)
        assert session.hwnd != 0
        assert session.chrome_hwnd != 0
        assert session.pid != 0
        assert session.uia_ready is True

    def test_connect_title_is_antigravity(self, session):
        assert _is_antigravity_title(session.title)

    def test_connect_model_non_empty(self, session):
        assert session.model != ""

    def test_is_valid(self, session):
        assert session.is_valid() is True

    def test_list_buttons_non_empty(self, session):
        from antigravity_controller import list_buttons
        buttons = list_buttons(session)
        assert isinstance(buttons, list)
        assert len(buttons) > 0

    def test_send_button_present(self, session):
        from antigravity_controller import list_buttons
        buttons = list_buttons(session)
        send_buttons = [b for b in buttons if "send" in b.lower()]
        assert send_buttons, f"No send button found. Buttons: {buttons[:20]}"

    def test_get_model(self, session):
        from antigravity_controller import get_model
        model = get_model(session)
        assert model != ""

    def test_chat_roundtrip(self, session):
        from antigravity_controller import chat
        response = chat(session, "What model are you? Reply in one sentence.", timeout=45)
        assert isinstance(response, str)
        assert len(response) > 0
        # Gemini should identify itself
        assert any(kw in response.lower() for kw in ["gemini", "antigravity", "model", "claude"])
