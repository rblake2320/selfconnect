"""
claudego/notifier.py — Windows toast notifications for ClaudeGo.

Wraps winotify so it is optional: if winotify is not installed the
functions log to stdout instead of raising.

Usage::

    from claudego.notifier import notify_approval_needed, notify_auto_approved, notify_stuck

    notify_approval_needed("Bash(npm install)", "my-project")
    notify_auto_approved("Bash(git status)")
    notify_stuck("my-project")
"""

from __future__ import annotations

APP_ID = "ClaudeGo"

try:
    from winotify import Notification
    from winotify import audio as _audio
    _WINOTIFY_AVAILABLE = True
except ImportError:
    _WINOTIFY_AVAILABLE = False
    _audio = None  # type: ignore[assignment]


def _toast(title: str, body: str, *, sound: bool = False) -> None:
    """Send a Windows toast or fall back to stdout."""
    if not _WINOTIFY_AVAILABLE:
        print(f"[notifier] {title}: {body}")
        return
    toast = Notification(
        app_id=APP_ID,
        title=title,
        msg=body,
        duration="short",
    )
    if sound and _audio is not None:
        toast.set_audio(_audio.Default, loop=False)
    toast.show()


def notify_approval_needed(tool: str, title: str) -> None:
    """
    Fire a toast when Claude Code pauses for a tool approval.

    Parameters
    ----------
    tool:
        The tool string, e.g. ``"Bash(npm install react)"``.
    title:
        Window title or project name shown as context.
    """
    _toast(
        title="ClaudeGo — Approval needed",
        body=f"{tool}\n{title}",
        sound=True,
    )


def notify_auto_approved(tool: str) -> None:
    """
    Fire a quiet toast when ClaudeGo auto-approves a known-safe tool.

    Parameters
    ----------
    tool:
        The tool string that was approved.
    """
    _toast(
        title="ClaudeGo — Auto-approved",
        body=tool,
        sound=False,
    )


def notify_stuck(title: str) -> None:
    """
    Fire an urgent toast when a Claude terminal appears stuck.

    Parameters
    ----------
    title:
        Window title or project name of the stuck terminal.
    """
    _toast(
        title="ClaudeGo — Claude is stuck",
        body=f"No activity detected in: {title}",
        sound=True,
    )
