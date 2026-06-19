"""
claudego/tray.py — System tray icon for ClaudeGo.

Displays a coloured dot in the Windows system tray:
  green  — running, no pending approvals
  yellow — approval prompt waiting
  red    — stuck / error

Usage::

    from claudego.tray import run_tray

    def my_status() -> str:
        return "ok"  # or "pending" or "stuck"

    run_tray("http://localhost:8765", my_status)  # blocks; run in daemon thread
"""

from __future__ import annotations

import threading
import webbrowser
from collections.abc import Callable

from PIL import Image, ImageDraw

try:
    import pystray
    from pystray import MenuItem as Item
    _PYSTRAY_AVAILABLE = True
except ImportError:
    _PYSTRAY_AVAILABLE = False

# Status values the caller may return from get_status_fn
STATUS_OK = "ok"
STATUS_PENDING = "pending"
STATUS_STUCK = "stuck"

_ICON_SIZE = 64
_COLORS: dict[str, tuple[int, int, int]] = {
    STATUS_OK: (34, 197, 94),      # green
    STATUS_PENDING: (234, 179, 8),  # yellow
    STATUS_STUCK: (239, 68, 68),    # red
}


def _make_icon(status: str) -> Image.Image:
    """Return a solid-circle PIL image for the given status."""
    color = _COLORS.get(status, _COLORS[STATUS_OK])
    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.ellipse(
        [margin, margin, _ICON_SIZE - margin, _ICON_SIZE - margin],
        fill=color,
    )
    return img


def run_tray(
    url: str,
    get_status_fn: Callable[[], str],
    title: str = "ClaudeGo",
) -> None:
    """
    Start the system tray icon.  Blocks until the user clicks Quit.

    Call from a daemon thread so the main process can exit cleanly::

        t = threading.Thread(target=run_tray, args=(url, status_fn), daemon=True)
        t.start()

    Parameters
    ----------
    url:
        Dashboard URL opened on left-click or "Open Dashboard".
    get_status_fn:
        Zero-argument callable returning one of ``"ok"``, ``"pending"``,
        or ``"stuck"``.  Called every time the tray icon refreshes.
    title:
        Tooltip / app name shown in the taskbar.
    """
    if not _PYSTRAY_AVAILABLE:
        print("[tray] pystray not installed — tray icon disabled. "
              "Install with: pip install pystray pillow")
        return

    def _open_dashboard(icon, item):
        webbrowser.open(url)

    def _quit(icon, item):
        icon.stop()

    def _on_left_click(icon):
        webbrowser.open(url)

    menu = pystray.Menu(
        Item("Open Dashboard", _open_dashboard, default=True),
        pystray.Menu.SEPARATOR,
        Item("Quit", _quit),
    )

    icon = pystray.Icon(
        name=title,
        icon=_make_icon(STATUS_OK),
        title=title,
        menu=menu,
    )

    # Refresh icon colour whenever status changes
    def _update_loop():
        last_status: str | None = None
        while icon.visible or last_status is None:
            try:
                status = get_status_fn()
            except Exception:
                status = STATUS_STUCK
            if status != last_status:
                icon.icon = _make_icon(status)
                icon.title = f"{title} — {status}"
                last_status = status
            threading.Event().wait(1.0)

    updater = threading.Thread(target=_update_loop, daemon=True)
    updater.start()

    # Left-click handler (pystray fires default menu item on left-click)
    icon.run_detached()

    # Block caller until icon stops
    icon.join()
