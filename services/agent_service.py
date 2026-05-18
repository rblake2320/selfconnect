"""
AgentService — Thin wrapper around the inter-terminal mesh.

Uses self_connect.py for Win32 inter-terminal communication.
Gracefully degrades on non-Windows or when self_connect is not importable.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# self_connect.py lives in the parent directory
_parent_dir = str(Path(__file__).resolve().parent.parent)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

_AGENT_AVAILABLE = False

try:
    from self_connect import (
        WindowTarget,
        get_text_uia,
        list_windows,
        send_string,
    )

    _AGENT_AVAILABLE = True
except ImportError:
    _AGENT_AVAILABLE = False
    log.warning("self_connect not importable — AgentService in no-op mode")

# approval_partner for the approve() method
_APPROVAL_AVAILABLE = False

try:
    from approval_partner import PartnerConfig, decide, extract_tool_call

    _APPROVAL_AVAILABLE = True
except ImportError:
    log.info("approval_partner not importable — AgentService.approve unavailable")


def _find_peer(hwnd: int) -> WindowTarget | None:
    """Locate a WindowTarget by hwnd from the current window list."""
    if not _AGENT_AVAILABLE:
        return None
    try:
        for w in list_windows():
            if w.hwnd == hwnd:
                return w
    except Exception:
        pass
    return None


class AgentService:
    """Stateless interface for inter-terminal agent messaging."""

    @staticmethod
    def list_peers() -> list[dict]:
        """List running Claude Code terminal windows as dicts.

        Returns list of dicts with keys: hwnd, title.
        Never raises — returns empty list on failure.
        """
        if not _AGENT_AVAILABLE:
            return []
        try:
            windows = list_windows()
            return [{"hwnd": w.hwnd, "title": w.title} for w in windows]
        except Exception as exc:
            log.debug("AgentService.list_peers failed: %s", exc)
            return []

    @staticmethod
    def send(hwnd: int, message: str) -> bool:
        """Send a string to a peer terminal via PostMessage(WM_CHAR).

        Returns True on success, False on any failure.
        """
        if not _AGENT_AVAILABLE:
            return False
        try:
            target = _find_peer(hwnd)
            if target is None:
                log.debug("AgentService.send: hwnd %d not found", hwnd)
                return False
            send_string(target, message)
            return True
        except Exception as exc:
            log.debug("AgentService.send failed: %s", exc)
            return False

    @staticmethod
    def read(hwnd: int) -> str:
        """Read current text from a terminal via UIA accessibility.

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

    @staticmethod
    def approve(hwnd: int) -> str:
        """Evaluate approval prompt on a peer and inject y/n.

        Uses the approval_partner rules engine to decide. Returns:
        - 'y' if approved and injected
        - 'n' if denied and injected
        - '' if no prompt found or policy engine unavailable
        """
        if not _AGENT_AVAILABLE or not _APPROVAL_AVAILABLE:
            return ""
        try:
            text = get_text_uia(hwnd) or ""
            if not text:
                return ""
            tool = extract_tool_call(text)
            if not tool:
                return ""
            result = decide(tool, PartnerConfig())
            target = _find_peer(hwnd)
            if target is None:
                return ""
            if result is True:
                send_string(target, "y\r")
                return "y"
            elif result is False:
                send_string(target, "n\r")
                return "n"
            # Unknown — do not inject
            return ""
        except Exception as exc:
            log.debug("AgentService.approve failed: %s", exc)
            return ""
