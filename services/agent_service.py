"""
AgentService — Stable API for inter-terminal messaging via Win32 primitives.

Wraps self_connect's core functions for sending, reading, and discovering peers.
Gracefully degrades on non-Windows or when ctypes is unavailable.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:
    from self_connect import (
        WindowTarget,
        get_text_uia,
        list_windows,
        send_string,
        submit_claude_input,
    )

    _AGENT_AVAILABLE = True
except ImportError:
    _AGENT_AVAILABLE = False
    log.warning(
        "self_connect not importable — AgentService degraded to no-op mode"
    )


class AgentService:
    """Stable interface for inter-terminal agent messaging."""

    @property
    def is_available(self) -> bool:
        """Whether Win32 primitives are importable."""
        return _AGENT_AVAILABLE

    def send(self, hwnd: int, message: str) -> bool:
        """Send a string to the target window via PostMessage(WM_CHAR).

        Returns True on success, False on any failure.
        """
        if not _AGENT_AVAILABLE:
            return False
        try:
            target = self._find_peer(hwnd)
            if target is None:
                log.debug("AgentService.send: hwnd %d not found", hwnd)
                return False
            send_string(target, message)
            return True
        except Exception as exc:
            log.debug("AgentService.send failed: %s", exc)
            return False

    def read(self, hwnd: int) -> str:
        """Read text from the target window via UIA accessibility.

        Returns the text content, or empty string on failure.
        """
        if not _AGENT_AVAILABLE:
            return ""
        try:
            result = get_text_uia(hwnd)
            return result or ""
        except Exception as exc:
            log.debug("AgentService.read failed: %s", exc)
            return ""

    def submit(self, hwnd: int) -> bool:
        """Submit (press Enter) on a Claude Code terminal.

        Returns True on success, False on failure.
        """
        if not _AGENT_AVAILABLE:
            return False
        try:
            target = self._find_peer(hwnd)
            if target is None:
                return False
            submit_claude_input(target)
            return True
        except Exception as exc:
            log.debug("AgentService.submit failed: %s", exc)
            return False

    def list_peers(self) -> list[dict]:
        """List all visible windows as peer candidates.

        Returns list of dicts with keys: hwnd, title, pid.
        Never raises — returns empty list on failure.
        """
        if not _AGENT_AVAILABLE:
            return []
        try:
            windows = list_windows()
            return [
                {"hwnd": w.hwnd, "title": w.title, "pid": w.pid}
                for w in windows
            ]
        except Exception as exc:
            log.debug("AgentService.list_peers failed: %s", exc)
            return []

    def find_peer(self, hwnd: int) -> object | None:
        """Find a specific peer by hwnd.

        Returns the WindowTarget if found, None otherwise.
        """
        if not _AGENT_AVAILABLE:
            return None
        return self._find_peer(hwnd)

    def _find_peer(self, hwnd: int) -> WindowTarget | None:
        """Internal: locate WindowTarget by hwnd."""
        try:
            windows = list_windows()
            for w in windows:
                if w.hwnd == hwnd:
                    return w
            return None
        except Exception:
            return None
