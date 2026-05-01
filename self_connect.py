"""
SelfConnect SDK — OS-native bridge between AI agents and Windows desktop applications.

The first lightweight library enabling frontier AI models (Claude, GPT-4, etc.) to
autonomously control desktop windows via Win32 APIs — without browser sandboxes,
without UIA accessibility frameworks, without full-screen capture.

Capabilities:
  - Find windows by exe, class, or fuzzy title (semantic targeting)
  - Type into any window via PostMessage(WM_CHAR) or SendInput — background OK
  - Send key combos (Ctrl+C, Alt+Tab, etc.) via virtual key codes
  - Capture per-window screenshots via PrintWindow — no foreground needed
  - Click at absolute or window-relative coordinates
  - Read/write the system clipboard for cross-app data transfer
  - Manage multiple windows simultaneously via WindowPool
  - Read window text without screenshots (zero-inference extraction)
  - Resize, move, minimize, maximize windows programmatically

Example usage (Claude calls these from Bash one step at a time):

  python -c "from self_connect import *; t=find_target('PowerShell'); print(t)"
  python -c "from self_connect import *; t=find_target('PowerShell'); send_string(t, 'dir\\n')"
  python -c "from self_connect import *; t=find_target('PowerShell'); save_capture(t.hwnd)"

Multi-window orchestration:

  python -c "from self_connect import *; p=WindowPool(); p.add('sh','PowerShell'); p.add('ed','Notepad'); print(p)"

Run as a script to list all visible windows:
  python self_connect.py
"""

__version__ = "0.9.0"
__all__ = [
    # Core types
    "WindowTarget", "WindowPool",
    # Window discovery
    "list_windows", "find_target", "find_child_by_class",
    "get_own_terminal_pid", "wait_for_window",
    # Focus & management
    "focus_window", "move_window", "resize_window",
    "minimize_window", "maximize_window", "restore_window",
    "get_window_rect",
    # Input: text
    "send_string", "send_keys",
    # Input: mouse
    "click_at", "click_window", "scroll_window",
    # Clipboard
    "read_clipboard", "write_clipboard",
    # Capture (See)
    "capture_window", "crop_to_client", "save_capture",
    # Text extraction (zero-inference)
    "get_window_text", "get_child_texts", "get_text_uia",
    # Wait / poll
    "wait_for_title_change",
    # Framing layer (v0.5.0) — reliable AI-to-AI messaging
    "build_frame", "parse_frame", "send_frame", "verify_delivery",
    # Universal App Control (v0.6.0) — background-safe control messages
    "app_type", "is_elevated",
    "send_keys_to", "close_window",
    "get_text", "set_text",
    "click_button", "send_command",
    "select_combo", "select_listbox",
    "post_click",
    "list_child_controls", "find_child_by_text",
    "get_menu_items", "invoke_menu",
    # Focus isolation (v0.6.0)
    "exclude_from_capture", "include_in_capture",
    # MessageListener (v0.7.0) — background receive loop
    "parse_all_frames", "MessageListener",
    # Layer 3 Supervisor (v0.8.0) — peer tracking, watchdog, approval gating
    "PeerState", "PeerRecord", "AgentRegistry", "WatchdogLoop", "ApprovalRelay",
    # Layer 4 Continuity (v0.9.0) — context-preserving role migration
    "Checkpoint", "write_checkpoint", "read_checkpoint", "MigrationCoordinator",
]

import ctypes
import ctypes.wintypes as wintypes
import time
import os
import json as _json
import subprocess as _subprocess
import collections
import enum as _enum
import hashlib as _hashlib
import threading
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── Win32 constants ───────────────────────────────────────────────────────────
INPUT_KEYBOARD       = 1
KEYEVENTF_UNICODE    = 0x0004
KEYEVENTF_KEYUP      = 0x0002
WM_CHAR              = 0x0102
WM_KEYDOWN           = 0x0100
WM_KEYUP_MSG         = 0x0101
VK_RETURN            = 0x0D
VK_TAB               = 0x09
VK_ESCAPE            = 0x1B
VK_BACK              = 0x08
VK_DELETE            = 0x2E
VK_SHIFT             = 0x10
VK_CONTROL           = 0x11
VK_MENU              = 0x12  # Alt
VK_LWIN              = 0x5B
VK_UP                = 0x26
VK_DOWN              = 0x28
VK_LEFT              = 0x25
VK_RIGHT             = 0x27
VK_HOME              = 0x24
VK_END               = 0x23
VK_PGUP              = 0x21
VK_PGDN              = 0x22
VK_F1                = 0x70
SW_RESTORE           = 9
SW_MINIMIZE          = 6
SW_MAXIMIZE          = 3
SWP_NOMOVE           = 0x0002
SWP_NOSIZE           = 0x0001
SWP_NOZORDER         = 0x0004
WM_MOUSEWHEEL        = 0x020A
SRCCOPY              = 0x00CC0020
PW_RENDERFULLCONTENT = 0x2
# v0.6.0 — Universal App Control
BM_CLICK             = 0x00F5
WM_CLOSE             = 0x0010
WM_COMMAND           = 0x0111
WM_SETTEXT           = 0x000C
WM_GETTEXT           = 0x000D
WM_GETTEXTLENGTH     = 0x000E
WM_LBUTTONDOWN       = 0x0201
WM_LBUTTONUP         = 0x0202
MK_LBUTTON           = 0x0001
CB_SETCURSEL         = 0x014E
LB_SETCURSEL         = 0x0186
WDA_NONE             = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011
SMTO_ABORTIFHUNG     = 0x0002
# Elevated process check (UIPI)
ACCESS_TOKEN_QUERY   = 0x0008
TOKEN_ELEVATION_TYPE = 18

WT_HOST_CLASS  = "CASCADIA_HOSTING_WINDOW_CLASS"
WT_INPUT_CLASS = "Windows.UI.Input.InputSite.WindowClass"

# ── Why PostMessage works for Windows Terminal but not UWP Notepad ────────────
#
# Windows Terminal (CASCADIA_HOSTING_WINDOW_CLASS) routes WM_CHAR / WM_KEYDOWN
# messages through ConPTY — the Windows pseudo-terminal layer — to the hosted
# console process (cmd.exe, PowerShell, Claude Code, etc.). ConPTY accepts these
# messages WITHOUT requiring the window to be in the foreground. PostMessage
# delivers to the target's message queue regardless of foreground state, and
# ConPTY forwards the character to the hosted app via its PTY pipe.
#
# UWP Notepad uses DirectWrite + RichEditD2DPT for text rendering. RichEditD2DPT
# ignores WM_CHAR PostMessage — it only accepts input via its TSF (Text Services
# Framework) composition path, which is only active when the window has focus.
# This is why WM_CHAR works for Terminal but silently fails for modern Notepad.
#
# Rule of thumb:
#   Terminal-style apps (ConPTY-hosted)  → WM_CHAR/WM_KEYDOWN via PostMessage ✓
#   DirectWrite/RichEdit D2D apps        → must use SendInput with focus, or file I/O
#   Classic Win32 edit controls          → SendInput KEYEVENTF_UNICODE ✓
#
# The foreground independence of PostMessage to ConPTY windows is the foundation
# of Patent Claim 2: AI-to-AI instruction via background window keyboard injection.

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32    = ctypes.windll.gdi32


# ── ctypes structures ─────────────────────────────────────────────────────────
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         ctypes.c_ushort),
        ("wScan",       ctypes.c_ushort),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 24)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUT_UNION)]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize",          ctypes.c_uint32),
        ("biWidth",         ctypes.c_int32),
        ("biHeight",        ctypes.c_int32),
        ("biPlanes",        ctypes.c_uint16),
        ("biBitCount",      ctypes.c_uint16),
        ("biCompression",   ctypes.c_uint32),
        ("biSizeImage",     ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed",       ctypes.c_uint32),
        ("biClrImportant",  ctypes.c_uint32),
    ]


# ── WindowTarget ──────────────────────────────────────────────────────────────
@dataclass
class WindowTarget:
    """Stable identity for a window — survives moves, resizes, tab renames."""
    hwnd:       int
    title:      str
    class_name: str
    pid:        int
    exe_name:   str = ""

    def is_uwp_terminal(self) -> bool:
        return self.class_name == WT_HOST_CLASS

    def is_valid(self) -> bool:
        return bool(user32.IsWindow(self.hwnd))

    def __str__(self):
        safe = self.title.encode("ascii", "replace").decode()
        return f"hwnd={self.hwnd} pid={self.pid} exe={self.exe_name} title={safe!r}"


# ── Window helpers ────────────────────────────────────────────────────────────
def _get_class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _get_window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _get_pid(hwnd: int) -> int:
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _get_exe_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        pass
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = ctypes.c_ulong(512)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            path = buf.value
            return path.split("\\")[-1] if "\\" in path else path
        return ""
    finally:
        kernel32.CloseHandle(handle)


def get_own_terminal_pid() -> int:
    """PID of the console window hosting this script (for self-exclusion)."""
    own_hwnd = kernel32.GetConsoleWindow()
    return _get_pid(own_hwnd) if own_hwnd else 0


def list_windows() -> list[WindowTarget]:
    """Return all visible top-level windows as WindowTarget objects."""
    results: list[WindowTarget] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title = _get_window_title(hwnd)
            if title:
                results.append(WindowTarget(
                    hwnd=hwnd, title=title,
                    class_name=_get_class_name(hwnd),
                    pid=_get_pid(hwnd),
                    exe_name=_get_exe_name(_get_pid(hwnd)),
                ))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return results


def find_child_by_class(parent_hwnd: int, target_class: str) -> int:
    """Return first child window matching target_class, or 0."""
    found = ctypes.c_int(0)
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def _cb(hwnd, _):
        if _get_class_name(hwnd) == target_class:
            found.value = hwnd
            return False
        return True

    user32.EnumChildWindows(parent_hwnd, WNDENUMPROC(_cb), 0)
    return found.value


def find_target(
    title_keyword: str,
    own_pid: int = 0,
    cached: Optional[WindowTarget] = None,
) -> Optional[WindowTarget]:
    """
    Find a window by title keyword, excluding own_pid.
    Strategies: cached hwnd recheck -> WindowsTerminal title match -> any title match.
    """
    if cached and cached.is_valid():
        cached.title = _get_window_title(cached.hwnd)
        return cached

    own_pid = own_pid or get_own_terminal_pid()
    kw = title_keyword.lower()
    windows = list_windows()

    # Prefer WindowsTerminal windows matching the keyword
    for w in windows:
        if w.pid != own_pid and w.exe_name.lower() == "windowsterminal.exe" and kw in w.title.lower():
            return w

    # Fallback: any visible window matching the keyword
    for w in windows:
        if w.pid != own_pid and kw in w.title.lower():
            return w

    return None


# ── Focus ─────────────────────────────────────────────────────────────────────
def focus_window(hwnd: int) -> bool:
    """Bring a window to foreground using AttachThreadInput workaround."""
    try:
        fg     = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None)
        my_tid = kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid != my_tid:
            user32.AttachThreadInput(my_tid, fg_tid, True)
            attached = True
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        if attached:
            user32.AttachThreadInput(my_tid, fg_tid, False)
        time.sleep(0.2)
        return True
    except Exception as e:
        print(f"[focus] error: {e}")
        return False


# ── Input delivery ────────────────────────────────────────────────────────────
def _send_char_postmessage(hwnd: int, ch: str) -> None:
    """
    PostMessage WM_CHAR/WM_KEYDOWN to a ConPTY-backed window (Windows Terminal).

    For Enter (\r or \n): sends WM_KEYDOWN + WM_KEYUP with VK_RETURN.
    ConPTY also accepts WM_CHAR(0x0D) for Enter, but WM_KEYDOWN is more reliable
    across terminal emulator versions.

    For all other chars: PostMessage(WM_CHAR, ord(ch), 0).
    No foreground focus required — PostMessage delivers to the message queue.
    """
    if ch in ("\n", "\r"):
        user32.PostMessageW(hwnd, WM_KEYDOWN,   VK_RETURN, 0)
        user32.PostMessageW(hwnd, WM_KEYUP_MSG, VK_RETURN, 0)
    else:
        user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)


def _send_char_sendinput(ch: str) -> None:
    """SendInput KEYEVENTF_UNICODE — for traditional Win32 windows."""
    extra = ctypes.pointer(ctypes.c_ulong(0))
    for flag in (0, KEYEVENTF_KEYUP):
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.u.ki.wVk = 0
        inp.u.ki.wScan = ord(ch) if ch not in ("\n", "\r") else VK_RETURN
        inp.u.ki.dwFlags = (KEYEVENTF_UNICODE if ch not in ("\n", "\r") else 0) | flag
        inp.u.ki.time = 0
        inp.u.ki.dwExtraInfo = extra
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_string(target: WindowTarget, text: str, char_delay: float = 0.05) -> None:
    """
    Send text to the target window. Auto-selects delivery method:

    - Windows Terminal (CASCADIA_HOSTING_WINDOW_CLASS): PostMessage(WM_CHAR) to
      the InputSite child, routed through ConPTY. Does NOT require foreground
      focus — the target window can be visible but NOT the active window.
      Include '\\r' in the text string to send Enter (wParam=0x0D via WM_CHAR,
      or WM_KEYDOWN/VK_RETURN — both work through ConPTY).

    - Classic Win32 windows: SendInput(KEYEVENTF_UNICODE). DOES require the
      target window to have foreground focus.

    Verified: one Claude session can inject text into another Claude session's
    terminal window without stealing foreground focus (2026-04-30 live proof).
    """
    if target.is_uwp_terminal():
        input_site = find_child_by_class(target.hwnd, WT_INPUT_CLASS)
        delivery = input_site if input_site else target.hwnd
        for ch in text:
            _send_char_postmessage(delivery, ch)
            time.sleep(char_delay)
    else:
        for ch in text:
            _send_char_sendinput(ch)
            time.sleep(char_delay)


# VK name lookup for send_keys
_VK_MAP: dict[str, int] = {
    "enter": VK_RETURN, "return": VK_RETURN, "tab": VK_TAB, "esc": VK_ESCAPE,
    "escape": VK_ESCAPE, "backspace": VK_BACK, "delete": VK_DELETE, "del": VK_DELETE,
    "shift": VK_SHIFT, "ctrl": VK_CONTROL, "control": VK_CONTROL,
    "alt": VK_MENU, "win": VK_LWIN,
    "up": VK_UP, "down": VK_DOWN, "left": VK_LEFT, "right": VK_RIGHT,
    "home": VK_HOME, "end": VK_END, "pgup": VK_PGUP, "pgdn": VK_PGDN,
    "pageup": VK_PGUP, "pagedown": VK_PGDN,
    "f1": VK_F1, "f2": VK_F1 + 1, "f3": VK_F1 + 2, "f4": VK_F1 + 3,
    "f5": VK_F1 + 4, "f6": VK_F1 + 5, "f7": VK_F1 + 6, "f8": VK_F1 + 7,
    "f9": VK_F1 + 8, "f10": VK_F1 + 9, "f11": VK_F1 + 10, "f12": VK_F1 + 11,
    "space": 0x20,
}


def _resolve_vk(key: str) -> int:
    """Resolve a key name to a virtual key code."""
    low = key.lower().strip()
    if low in _VK_MAP:
        return _VK_MAP[low]
    if len(low) == 1:
        return user32.VkKeyScanW(ord(low)) & 0xFF
    raise ValueError(f"Unknown key: {key!r}")


def send_keys(*keys: str) -> None:
    """
    Send a key combination via SendInput. Holds modifiers while pressing the final key.

    Examples:
        send_keys("ctrl", "c")        # Ctrl+C
        send_keys("ctrl", "shift", "s")  # Ctrl+Shift+S
        send_keys("alt", "tab")       # Alt+Tab
        send_keys("enter")            # Enter
        send_keys("f5")               # F5
    """
    if not keys:
        return
    vks = [_resolve_vk(k) for k in keys]
    extra = ctypes.pointer(ctypes.c_ulong(0))
    # Press all keys in order
    for vk in vks:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.u.ki.wVk = vk
        inp.u.ki.wScan = 0
        inp.u.ki.dwFlags = 0
        inp.u.ki.time = 0
        inp.u.ki.dwExtraInfo = extra
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    time.sleep(0.05)
    # Release all keys in reverse order
    for vk in reversed(vks):
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.u.ki.wVk = vk
        inp.u.ki.wScan = 0
        inp.u.ki.dwFlags = KEYEVENTF_KEYUP
        inp.u.ki.time = 0
        inp.u.ki.dwExtraInfo = extra
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


# ── Window text (zero-inference) ─────────────────────────────────────────────
def get_window_text(hwnd: int) -> str:
    """Read the window's title text via GetWindowTextW (no screenshot needed)."""
    return _get_window_title(hwnd)


def get_child_texts(hwnd: int) -> "list[tuple[int, str, str]]":
    """
    Enumerate child controls and read their text content.
    Returns [(child_hwnd, class_name, text), ...].
    Useful for reading edit controls, static labels, buttons, etc.
    without taking a screenshot — zero inference cost.
    """
    results: list[tuple[int, str, str]] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def _cb(child_hwnd, _):
        cls = _get_class_name(child_hwnd)
        text = _get_window_title(child_hwnd)
        if text:
            results.append((child_hwnd, cls, text))
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(_cb), 0)
    return results


def get_text_uia(hwnd: int) -> str:
    """
    Extract all text from a window using the UI Automation framework.

    Falls back gracefully through three strategies:
      1. pywinauto (uia backend) — richest; reads UWP / DirectWrite content
      2. comtypes IUIAutomation directly — no pywinauto dep
      3. Returns '' if neither is available

    This is the right tool when WM_GETTEXT / get_child_texts return empty
    strings for UWP apps (Windows 11 Notepad, Calculator, modern Store apps).

    Example:
        t = find_target('Notepad')
        text = get_text_uia(t.hwnd)
    """
    # Strategy 1: pywinauto
    try:
        from pywinauto import Desktop as _PwaDesktop  # type: ignore
        import pythoncom as _pcom  # type: ignore
        try:
            _pcom.CoInitializeEx(_pcom.COINIT_MULTITHREADED)
        except Exception:
            pass
        desktop = _PwaDesktop(backend="uia")
        wrapper = desktop.window(handle=hwnd)
        texts: list[str] = []
        try:
            texts.append(wrapper.window_text() or "")
        except Exception:
            pass
        try:
            for child in wrapper.descendants():
                try:
                    t = child.window_text()
                    if t:
                        texts.append(t)
                except Exception:
                    pass
        except Exception:
            pass
        result = "\n".join(t for t in texts if t)
        if result.strip():
            return result
    except ImportError:
        pass
    except Exception:
        pass

    # Strategy 2: comtypes IUIAutomation (no pywinauto dep)
    try:
        import comtypes.client as _cc  # type: ignore
        import comtypes.gen.UIAutomationClient as _uia  # type: ignore

        auto = _cc.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=_uia.IUIAutomation,
        )
        elem = auto.ElementFromHandle(hwnd)
        if elem is None:
            return ""
        condition = auto.CreateTrueCondition()
        walker = auto.CreateTreeWalker(condition)

        texts: list[str] = []

        def _walk(e):
            try:
                t = e.CurrentName
                if t:
                    texts.append(t)
            except Exception:
                pass
            try:
                child = walker.GetFirstChildElement(e)
                while child:
                    _walk(child)
                    child = walker.GetNextSiblingElement(child)
            except Exception:
                pass

        _walk(elem)
        return "\n".join(texts)
    except ImportError:
        pass
    except Exception:
        pass

    return ""


# ── Window management ────────────────────────────────────────────────────────
def get_window_rect(hwnd: int) -> "tuple[int, int, int, int]":
    """Get window position and size as (x, y, width, height)."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def move_window(hwnd: int, x: int, y: int) -> bool:
    """Move a window to (x, y) without changing its size."""
    return bool(user32.SetWindowPos(hwnd, 0, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER))


def resize_window(hwnd: int, width: int, height: int) -> bool:
    """Resize a window without moving it."""
    return bool(user32.SetWindowPos(hwnd, 0, 0, 0, width, height, SWP_NOMOVE | SWP_NOZORDER))


def minimize_window(hwnd: int) -> bool:
    """Minimize a window."""
    return bool(user32.ShowWindow(hwnd, SW_MINIMIZE))


def maximize_window(hwnd: int) -> bool:
    """Maximize a window."""
    return bool(user32.ShowWindow(hwnd, SW_MAXIMIZE))


def restore_window(hwnd: int) -> bool:
    """Restore a minimized/maximized window to normal size."""
    return bool(user32.ShowWindow(hwnd, SW_RESTORE))


# ── Scroll ───────────────────────────────────────────────────────────────────
def scroll_window(hwnd: int, clicks: int = -3) -> None:
    """
    Send mouse wheel scroll to a window.
    Negative clicks = scroll down, positive = scroll up.
    Each click is 120 units (WHEEL_DELTA).
    """
    WHEEL_DELTA = 120
    w_param = ctypes.c_int32(clicks * WHEEL_DELTA).value & 0xFFFFFFFF
    w_param = (w_param << 16)  # wParam high word = wheel delta
    user32.PostMessageW(hwnd, WM_MOUSEWHEEL, w_param, 0)


# ── Wait helpers ─────────────────────────────────────────────────────────────
def wait_for_window(keyword: str, timeout: float = 30.0, poll: float = 0.5,
                    own_pid: int = 0) -> Optional[WindowTarget]:
    """
    Wait for a window matching keyword to appear. Returns WindowTarget or None.
    Polls every `poll` seconds, gives up after `timeout` seconds.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = find_target(keyword, own_pid=own_pid)
        if t:
            return t
        time.sleep(poll)
    return None


def wait_for_title_change(hwnd: int, old_title: str, timeout: float = 15.0,
                          poll: float = 0.3) -> str:
    """
    Wait for a window's title to change from old_title. Returns the new title.
    Useful for detecting when a command finishes executing (prompt line changes).
    Returns old_title if timeout is reached.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _get_window_title(hwnd)
        if current != old_title:
            return current
        time.sleep(poll)
    return old_title


# ── Capture ───────────────────────────────────────────────────────────────────
def capture_window(hwnd: int):
    """
    Capture a window to a PIL Image.
    Tries PrintWindow (PW_RENDERFULLCONTENT) first; falls back to BitBlt from desktop.
    Returns PIL Image in RGB mode, or None on failure.
    """
    try:
        from PIL import Image
    except ImportError:
        print("[capture] Pillow not installed: pip install Pillow")
        return None

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right  - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None

    hdc_screen = user32.GetDC(0)
    hdc_mem    = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp       = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    img = None

    try:
        # Attempt 1: PrintWindow (works even when partially occluded)
        if user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT):
            bih = BITMAPINFOHEADER()
            bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bih.biWidth = w; bih.biHeight = -h
            bih.biPlanes = 1; bih.biBitCount = 32; bih.biCompression = 0
            bih.biSizeImage = w * h * 4
            buf = ctypes.create_string_buffer(w * h * 4)
            if gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bih), 0):
                img = Image.frombytes("RGBA", (w, h), buf.raw, "raw", "BGRA").convert("RGB")
                if img.convert("L").getextrema()[1] < 5:
                    img = None  # all-black frame — try BitBlt

        # Attempt 2: BitBlt from desktop DC (requires window visible on screen)
        if img is None:
            gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, rect.left, rect.top, SRCCOPY)
            bih2 = BITMAPINFOHEADER()
            bih2.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bih2.biWidth = w; bih2.biHeight = -h
            bih2.biPlanes = 1; bih2.biBitCount = 32; bih2.biCompression = 0
            bih2.biSizeImage = w * h * 4
            buf2 = ctypes.create_string_buffer(w * h * 4)
            if gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf2, ctypes.byref(bih2), 0):
                img = Image.frombytes("RGBA", (w, h), buf2.raw, "raw", "BGRA").convert("RGB")
    finally:
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)

    return img


def crop_to_client(hwnd: int, img):
    """Crop a full-window image to just the client area (removes title bar, chrome)."""
    try:
        from PIL import Image as _I
        cr = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(cr))
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        wr = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(wr))
        ox = pt.x - wr.left; oy = pt.y - wr.top
        iw, ih = img.size
        l = max(0, ox); t = max(0, oy)
        r = min(iw, ox + cr.right); b = min(ih, oy + cr.bottom)
        return img.crop((l, t, r, b)) if r > l and b > t else img
    except Exception:
        return img


def save_capture(hwnd: int, path: Optional[str] = None, crop: bool = True) -> str:
    """
    Capture a window and save to file. Returns the saved path.
    Default path: %TEMP%/sc_capture.png
    Claude reads this file with the Read tool to see the screen.
    """
    if path is None:
        tmp = os.environ.get("TEMP", os.path.expanduser("~"))
        path = os.path.join(tmp, "sc_capture.png")

    img = capture_window(hwnd)
    if img is None:
        print(f"[capture] FAILED for hwnd={hwnd}")
        return ""

    if crop:
        img = crop_to_client(hwnd, img)

    img.save(path)
    w, h = img.size
    print(f"[capture] Saved {w}x{h} -> {path}")
    return path


# ── Universal App Control (v0.6.0) ───────────────────────────────────────────
#
# SENTINEL fixes applied:
#   1. _smto() wrapper uses SendMessageTimeoutW(SMTO_ABORTIFHUNG, 5000ms)
#      instead of bare SendMessage — prevents hangs on frozen windows.
#   2. is_elevated() / UIPI check: fail-fast if target process runs as admin
#      (PostMessage to elevated windows silently drops without this check).
#   3. BM_CLICK reliability claims are "app-dependent" — works for classic
#      Win32 buttons, unreliable for UWP/Electron (documented below).
#   4. WordPad removed from Win11 24H2 — tests use mspaint.exe instead.
#   5. Audit logging via _audit() — records every control action.


def _smto(hwnd: int, msg: int, wparam: int, lparam: int,
          timeout_ms: int = 5000) -> int:
    """
    SendMessageTimeoutW wrapper — never blocks forever on a frozen app.
    Returns the message result, or 0 on timeout/error.
    SMTO_ABORTIFHUNG: returns immediately if the target is not responding.
    """
    result = ctypes.c_size_t(0)
    user32.SendMessageTimeoutW(
        hwnd, msg, wparam, lparam,
        SMTO_ABORTIFHUNG, timeout_ms,
        ctypes.byref(result),
    )
    return result.value


def _makelparam(x: int, y: int) -> int:
    """Pack (x, y) client coordinates into a LPARAM for mouse messages."""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _audit(action: str, hwnd: int, detail: str = "") -> None:
    """Lightweight audit log — writes to stderr so scripts can capture it."""
    import sys
    tag = f"[sc.audit] {action} hwnd={hwnd}"
    if detail:
        tag += f" {detail}"
    print(tag, file=sys.stderr)


def is_elevated(hwnd: int) -> bool:
    """
    Return True if the process owning hwnd is running elevated (as Administrator).
    PostMessage / SendMessage to elevated processes is silently dropped by UIPI
    when the caller is not elevated. Check this before using control messages.
    """
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h_process = kernel32.OpenProcess(0x0400, False, pid.value)  # PROCESS_QUERY_INFORMATION
    if not h_process:
        return False
    try:
        h_token = ctypes.c_void_p(0)
        if not ctypes.windll.advapi32.OpenProcessToken(
            h_process, ACCESS_TOKEN_QUERY, ctypes.byref(h_token)
        ):
            return False
        try:
            elevation = ctypes.c_ulong(0)
            ret_len = ctypes.c_ulong(0)
            ctypes.windll.advapi32.GetTokenInformation(
                h_token, TOKEN_ELEVATION_TYPE,
                ctypes.byref(elevation), 4, ctypes.byref(ret_len)
            )
            return elevation.value == 2  # TokenElevationTypeFull
        finally:
            kernel32.CloseHandle(h_token)
    finally:
        kernel32.CloseHandle(h_process)


def app_type(hwnd: int) -> str:
    """
    Classify a window to guide strategy selection for input/control.

    Returns one of:
      'conpty'    — Windows Terminal / ConPTY: full background input via WM_CHAR
      'classic'   — Classic Win32: BM_CLICK/WM_COMMAND/WM_SETTEXT work in background
      'uwp'       — UWP/WinUI3: TSF blocks WM_CHAR; read-only in background
      'electron'  — Electron/Chromium: ignores most PostMessage; read-only in background
      'unknown'   — unclassified

    Compatibility matrix:
      App type   | Background input | Background read
      -----------|-----------------|----------------
      conpty     | FULL (WM_CHAR)  | FULL (PrintWindow)
      classic    | PARTIAL         | FULL
      uwp        | NO (TSF blocks) | FULL
      electron   | NO (Chromium)   | FULL
    """
    cls = _get_class_name(hwnd)
    if cls == WT_HOST_CLASS:
        return "conpty"
    chromium_classes = {"Chrome_WidgetWin_1", "Chrome_WidgetWin_0", "Electron"}
    if any(c in cls for c in chromium_classes):
        return "electron"
    uwp_classes = {"ApplicationFrameWindow", "Windows.UI.Core.CoreWindow"}
    if cls in uwp_classes:
        return "uwp"
    return "classic"


def send_keys_to(hwnd: int, *keys: str) -> None:
    """
    PostMessage WM_KEYDOWN/WM_KEYUP to a specific HWND — no focus required.

    Works reliably for navigation keys (Enter, Tab, Escape, arrows, F-keys)
    in classic Win32 apps. Less reliable for character input (use set_text for that).

    keys: strings like 'enter', 'tab', 'escape', 'up', 'down', 'f5', etc.
    """
    _VK_MAP = {
        "enter": VK_RETURN, "return": VK_RETURN,
        "tab": VK_TAB, "escape": VK_ESCAPE, "esc": VK_ESCAPE,
        "backspace": VK_BACK, "delete": VK_DELETE,
        "up": VK_UP, "down": VK_DOWN, "left": VK_LEFT, "right": VK_RIGHT,
        "home": VK_HOME, "end": VK_END, "pgup": VK_PGUP, "pgdn": VK_PGDN,
        **{f"f{i}": VK_F1 + i - 1 for i in range(1, 13)},
    }
    _audit("send_keys_to", hwnd, str(keys))
    for key in keys:
        vk = _VK_MAP.get(key.lower())
        if vk is None:
            continue
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
        time.sleep(0.02)
        user32.PostMessageW(hwnd, WM_KEYUP_MSG, vk, 0)
        time.sleep(0.02)


def close_window(hwnd: int) -> bool:
    """
    Send WM_CLOSE to a window — graceful close, same as clicking X.
    Background-safe (PostMessage). Returns True if message was posted.
    """
    _audit("close_window", hwnd)
    return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


def get_text(hwnd: int) -> str:
    """
    Read text from a control via WM_GETTEXT — no focus required.
    Works with: classic Edit, RichEdit, static labels, buttons.
    Does NOT work with: Chromium textareas, UWP/WinUI3 edit controls.
    """
    length = _smto(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if not length:
        return ""
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, buf)
    return buf.value


def set_text(hwnd: int, text: str) -> bool:
    """
    Inject text into an edit control via WM_SETTEXT — no focus required.

    Reliability by app type:
      classic Edit / RichEdit20W  → FULL (replaces content instantly)
      RichEditD2DPT (modern apps) → NO (ignored — use SendInput with focus)
      Chromium / UWP              → NO (ignored)

    Returns True if SendMessage succeeded (note: success ≠ text was accepted).
    """
    _audit("set_text", hwnd, repr(text[:40]))
    result = _smto(hwnd, WM_SETTEXT, 0, ctypes.cast(
        ctypes.create_unicode_buffer(text), ctypes.c_void_p
    ).value or 0)
    return bool(result)


def click_button(parent_hwnd: int, button_text: str = "",
                 button_class: str = "Button") -> bool:
    """
    Find a button child control by visible text and click it via BM_CLICK.
    No focus required — works for classic Win32 buttons.

    Reliability: app-dependent.
      Classic Win32 (Button class)  → FULL background
      UWP / Electron                → NO (use click_at with focus instead)

    Returns True if a matching button was found and BM_CLICK was sent.
    """
    found = [False]
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def _cb(child_hwnd, _):
        cls = _get_class_name(child_hwnd)
        if button_class.lower() not in cls.lower():
            return True
        title = _get_window_title(child_hwnd)
        if button_text and button_text.lower() not in title.lower():
            return True
        _audit("click_button", child_hwnd, f"text={title!r}")
        _smto(child_hwnd, BM_CLICK, 0, 0)
        found[0] = True
        return False  # stop enumeration

    cb = WNDENUMPROC(_cb)
    user32.EnumChildWindows(parent_hwnd, cb, 0)
    return found[0]


def send_command(hwnd: int, command_id: int) -> bool:
    """
    Send WM_COMMAND to trigger a menu item or control notification.
    No focus required — background-safe PostMessage.

    command_id: the numeric ID of the menu item or control command.
    Use get_menu_items() to discover menu IDs.
    """
    _audit("send_command", hwnd, f"cmd={command_id}")
    return bool(user32.PostMessageW(hwnd, WM_COMMAND, command_id, 0))


def select_combo(hwnd: int, index: int) -> bool:
    """
    Select an item in a ComboBox by zero-based index via CB_SETCURSEL.
    Background-safe. Works with classic Win32 ComboBox controls.
    """
    _audit("select_combo", hwnd, f"index={index}")
    result = _smto(hwnd, CB_SETCURSEL, index, 0)
    return result != 0xFFFF  # CB_ERR = 0xFFFF (as unsigned)


def select_listbox(hwnd: int, index: int) -> bool:
    """
    Select an item in a ListBox by zero-based index via LB_SETCURSEL.
    Background-safe. Works with classic Win32 ListBox controls.
    """
    _audit("select_listbox", hwnd, f"index={index}")
    result = _smto(hwnd, LB_SETCURSEL, index, 0)
    return result != 0xFFFF


def post_click(hwnd: int, x: int, y: int) -> None:
    """
    PostMessage a mouse click (WM_LBUTTONDOWN / WM_LBUTTONUP) to a window.
    x, y are CLIENT coordinates relative to hwnd.

    Background-safe but EXPERIMENTAL — reliability varies by app type:
      Classic Win32  → PARTIAL (many apps respond; some require SetFocus first)
      UWP / Electron → UNRELIABLE (Chromium ignores PostMessage mouse events)

    For reliable clicking use click_at() with focus, or click_button() for buttons.
    """
    _audit("post_click", hwnd, f"({x},{y})")
    lp = _makelparam(x, y)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    time.sleep(0.03)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)


def list_child_controls(hwnd: int) -> list[dict]:
    """
    Enumerate all child controls of a window.
    Returns list of dicts: {hwnd, class_name, text, rect: (x,y,w,h)}.
    Critical for discovering what controls exist before targeting them.
    """
    results: list[dict] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def _cb(child_hwnd, _):
        cls = _get_class_name(child_hwnd)
        text = _get_window_title(child_hwnd)
        r = wintypes.RECT()
        user32.GetWindowRect(child_hwnd, ctypes.byref(r))
        results.append({
            "hwnd": child_hwnd,
            "class_name": cls,
            "text": text,
            "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
        })
        return True

    cb = WNDENUMPROC(_cb)
    user32.EnumChildWindows(hwnd, cb, 0)
    return results


def find_child_by_text(hwnd: int, text: str, partial: bool = True) -> int:
    """
    Find a child control by its visible text. Returns child hwnd or 0.
    partial=True: substring match (case-insensitive).
    partial=False: exact match (case-insensitive).
    """
    found = [0]
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    needle = text.lower()

    def _cb(child_hwnd, _):
        title = _get_window_title(child_hwnd).lower()
        if partial and needle in title:
            found[0] = child_hwnd
            return False
        if not partial and title == needle:
            found[0] = child_hwnd
            return False
        return True

    cb = WNDENUMPROC(_cb)
    user32.EnumChildWindows(hwnd, cb, 0)
    return found[0]


def get_menu_items(hwnd: int) -> list[dict]:
    """
    Enumerate the top-level menu bar items and their submenus.
    Returns list of dicts: {index, text, id, submenu: [{index, text, id}, ...]}.
    Use the returned 'id' values with send_command() to trigger menu items.
    """
    h_menu = user32.GetMenu(hwnd)
    if not h_menu:
        return []

    count = user32.GetMenuItemCount(h_menu)
    items = []
    for i in range(count):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetMenuStringW(h_menu, i, buf, 256, 0x400)  # MF_BYPOSITION=0x400
        cmd_id = user32.GetMenuItemID(h_menu, i)
        h_sub = user32.GetSubMenu(h_menu, i)
        sub_items = []
        if h_sub:
            sub_count = user32.GetMenuItemCount(h_sub)
            for j in range(sub_count):
                sbuf = ctypes.create_unicode_buffer(256)
                user32.GetMenuStringW(h_sub, j, sbuf, 256, 0x400)
                sub_id = user32.GetMenuItemID(h_sub, j)
                sub_items.append({"index": j, "text": sbuf.value, "id": sub_id})
        items.append({"index": i, "text": buf.value, "id": cmd_id, "submenu": sub_items})
    return items


def invoke_menu(hwnd: int, *path: str) -> bool:
    """
    Trigger a menu item by navigating a text path through the menu hierarchy.
    Example: invoke_menu(hwnd, "File", "Save As")
    Returns True if the menu item was found and WM_COMMAND was posted.
    Matching is case-insensitive, partial substring.
    """
    items = get_menu_items(hwnd)
    if not items:
        return False

    # Find top-level match
    top_text = path[0].lower() if path else ""
    top = next((m for m in items if top_text in m["text"].lower()), None)
    if not top:
        return False

    if len(path) == 1:
        if top["id"] and top["id"] != 0xFFFF:
            return send_command(hwnd, top["id"])
        return False

    # Find submenu match
    sub_text = path[1].lower()
    sub = next((m for m in top["submenu"] if sub_text in m["text"].lower()), None)
    if not sub or not sub["id"] or sub["id"] == 0xFFFF:
        return False
    return send_command(hwnd, sub["id"])


def exclude_from_capture(hwnd: int) -> bool:
    """
    Make a window invisible to PrintWindow / BitBlt / screen capture.
    Use to create "blinders" — hide sensitive windows so only the target is
    visible to the AI's capture layer.
    Requires the calling process to own the window.
    Returns True on success.
    """
    _audit("exclude_from_capture", hwnd)
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))


def include_in_capture(hwnd: int) -> bool:
    """
    Re-enable PrintWindow capture for a window previously excluded.
    Reverses exclude_from_capture().
    """
    _audit("include_in_capture", hwnd)
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_NONE))


# ── WindowPool ────────────────────────────────────────────────────────────────
class WindowPool:
    """
    Manage multiple named windows for parallel AI orchestration.

    Enables a frontier AI to simultaneously operate N desktop applications
    without any window needing to be in the foreground. Each window is
    targeted independently by HWND — no full-screen capture, no interference.

    Example:
        pool = WindowPool()
        pool.add("shell", "PowerShell")
        pool.add("editor", "Notepad")
        pool.send_to("shell", "dir\\n")
        shots = pool.save_all()   # captures both windows independently
    """

    def __init__(self):
        self.targets: dict[str, WindowTarget] = {}

    def add(self, name: str, keyword: str, own_pid: int = 0) -> Optional[WindowTarget]:
        """Find a window by title keyword and register it under a friendly name."""
        t = find_target(keyword, own_pid=own_pid)
        if t:
            self.targets[name] = t
        return t

    def add_target(self, name: str, target: WindowTarget) -> None:
        """Register an existing WindowTarget under a friendly name."""
        self.targets[name] = target

    def remove(self, name: str) -> None:
        self.targets.pop(name, None)

    def get(self, name: str) -> Optional[WindowTarget]:
        return self.targets.get(name)

    def send_to(self, name: str, text: str, char_delay: float = 0.05) -> None:
        """Type text into a named window (no foreground focus required for UWP)."""
        t = self.targets.get(name)
        if not t:
            raise KeyError(f"No window named '{name}' in pool")
        send_string(t, text, char_delay)

    def capture_all(self, crop: bool = True) -> "dict[str, object]":
        """Capture all registered windows. Returns {name: PIL.Image | None}."""
        results: dict[str, object] = {}
        for name, t in self.targets.items():
            img = capture_window(t.hwnd)
            if img and crop:
                img = crop_to_client(t.hwnd, img)
            results[name] = img
        return results

    def save_all(self, directory: Optional[str] = None) -> "dict[str, str]":
        """Capture all windows and save to PNG files. Returns {name: filepath}."""
        if directory is None:
            directory = os.environ.get("TEMP", os.path.expanduser("~"))
        paths: dict[str, str] = {}
        for name, img in self.capture_all().items():
            if img is not None:
                p = os.path.join(directory, f"sc_{name}.png")
                img.save(p)
                paths[name] = p
                print(f"[pool] {name}: saved {img.size} -> {p}")
        return paths

    def focus_only(self, name: str) -> bool:
        """
        Minimize all pool windows except the named target, then restore it.
        Creates "blinders" — only the target window is active/visible.
        Returns True if the target was found.
        """
        if name not in self.targets:
            return False
        for n, t in self.targets.items():
            if n != name:
                minimize_window(t.hwnd)
        restore_window(self.targets[name].hwnd)
        return True

    def status(self) -> "dict[str, bool]":
        """Check which registered windows are still valid (not closed)."""
        return {name: t.is_valid() for name, t in self.targets.items()}

    def __len__(self) -> int:
        return len(self.targets)

    def __repr__(self) -> str:
        if not self.targets:
            return "WindowPool(empty)"
        lines = [f"WindowPool({len(self.targets)} windows):"]
        for name, t in self.targets.items():
            state = "OK" if t.is_valid() else "GONE"
            safe = t.title.encode("ascii", "replace").decode()
            lines.append(f"  {name!r}: hwnd={t.hwnd} [{state}] {safe[:40]}")
        return "\n".join(lines)


# ── Clipboard ────────────────────────────────────────────────────────────────
CF_UNICODETEXT = 13

# 64-bit-safe argtypes for clipboard API
kernel32.GlobalAlloc.restype          = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes         = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalFree.restype           = ctypes.c_void_p
kernel32.GlobalFree.argtypes          = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes        = [ctypes.c_void_p]
user32.SetClipboardData.restype       = ctypes.c_void_p
user32.SetClipboardData.argtypes      = [ctypes.c_uint, ctypes.c_void_p]
user32.GetClipboardData.restype       = ctypes.c_void_p
user32.GetClipboardData.argtypes      = [ctypes.c_uint]


def read_clipboard() -> str:
    """Read Unicode text from the Windows clipboard. Returns '' on failure."""
    if not user32.OpenClipboard(0):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        kernel32.GlobalLock.restype  = ctypes.c_wchar_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        text = kernel32.GlobalLock(handle)
        result = str(text) if text else ""
        kernel32.GlobalUnlock(handle)
        return result
    finally:
        user32.CloseClipboard()


def write_clipboard(text: str) -> bool:
    """
    Write Unicode text to the Windows clipboard.
    Enables AI to transfer data between applications at full speed — no
    character-by-character typing, supports arbitrary string content.
    Returns True on success.
    """
    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        encoded = text.encode("utf-16-le") + b"\x00\x00"
        h = kernel32.GlobalAlloc(0x0002, len(encoded))  # GMEM_MOVEABLE
        if not h:
            return False
        kernel32.GlobalLock.restype  = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(ptr, encoded, len(encoded))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        return True
    finally:
        user32.CloseClipboard()


# ── Mouse ─────────────────────────────────────────────────────────────────────
def click_at(x: int, y: int, button: str = "left") -> None:
    """
    Click at absolute screen coordinates using SetCursorPos + mouse_event.
    button: 'left' or 'right'
    """
    user32.SetCursorPos(x, y)
    time.sleep(0.05)
    if button == "right":
        user32.mouse_event(0x0008, 0, 0, 0, 0)  # MOUSEEVENTF_RIGHTDOWN
        user32.mouse_event(0x0010, 0, 0, 0, 0)  # MOUSEEVENTF_RIGHTUP
    else:
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP


def click_window(target: WindowTarget, client_x: int, client_y: int,
                 button: str = "left") -> None:
    """
    Click at coordinates relative to a window's client area.
    Converts client coords to screen coords, focuses the window, then clicks.
    """
    pt = wintypes.POINT(client_x, client_y)
    user32.ClientToScreen(target.hwnd, ctypes.byref(pt))
    focus_window(target.hwnd)
    click_at(pt.x, pt.y, button)


# ── Framing Layer (v0.5.0) ───────────────────────────────────────────────────
#
# Protocol stack for reliable AI-to-AI messaging over PostMessage(WM_CHAR):
#   Layer 1 (Physical): PostMessage(WM_CHAR) + PrintWindow  — already in SDK
#   Layer 2 (Framing):  STX | header | NUL | payload | ETX  — this section
#   Layer 3 (Application): chat, task routing, etc.         — user code
#
# Frame format:
#   STX(0x02) + JSON_HEADER + NUL(0x00) + PAYLOAD + ETX(0x03)
#
# Header fields: {"from": int, "to": int, "seq": int, "topic": str, "len": int}
#   from  = sender's hwnd
#   to    = receiver's hwnd
#   seq   = monotonic sequence number (per-sender)
#   topic = conversation thread ID (e.g. "robustness", "task-1")
#   len   = byte length of payload (for validation)
#
# Design rationale (agreed by 3 AI agents — 2 Claude + 1 Codex, 2026-05-01):
# - STX(0x02) / ETX(0x03) pass cleanly through WM_CHAR, never appear in text
# - NUL(0x00) separates header from payload unambiguously
# - JSON header is parseable by any language / any LLM vendor
# - PrintWindow ACK closes the feedback loop without new transport

import json as _json
import uuid as _uuid

_STX = "\x02"
_ETX = "\x03"
_NUL = "\x00"
_frame_seq: dict[int, int] = {}  # per-sender sequence counters

# Escape sequences: these single-char control codes are reserved as frame
# delimiters and must be escaped if they appear in payload content.
# Escape policy: prefix the byte with ESC(0x1B), then shift the value by 0x40.
#   STX(0x02) → ESC + 0x42 ('B')
#   ETX(0x03) → ESC + 0x43 ('C')
#   NUL(0x00) → ESC + 0x40 ('@')
#   ESC(0x1B) → ESC + 0x5B ('[')  (must escape the escape char itself)
_ESC = "\x1b"
_ESCAPE_MAP = {"\x00": _ESC + "@", "\x02": _ESC + "B", "\x03": _ESC + "C", _ESC: _ESC + "["}
_UNESCAPE_MAP = {v[1]: k for k, v in _ESCAPE_MAP.items()}


def _escape_payload(text: str) -> str:
    """Escape STX/ETX/NUL/ESC in payload so they don't break frame delimiters."""
    out = []
    for ch in text:
        if ch in _ESCAPE_MAP:
            out.append(_ESCAPE_MAP[ch])
        else:
            out.append(ch)
    return "".join(out)


def _unescape_payload(text: str) -> str:
    """Reverse _escape_payload encoding."""
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == _ESC and i + 1 < len(text):
            escaped = text[i + 1]
            out.append(_UNESCAPE_MAP.get(escaped, ch))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def build_frame(from_hwnd: int, to_hwnd: int, payload: str,
                topic: str = "default", seq: int | None = None) -> str:
    """
    Build a framed message string ready for send_string().

    Returns: STX + JSON header + NUL + escaped_payload + ETX

    Frame format (v0.5.1):
      STX(0x02) | JSON header | NUL(0x00) | escaped payload | ETX(0x03)

    Header fields: from, to, seq, topic, len, id
      id  = 12-char hex UUID (for dedup and message correlation)
      len = length of the ESCAPED payload (for validation)

    Payload is escaped so STX/ETX/NUL chars in content don't break framing.
    parse_frame() unescapes automatically.
    """
    if seq is None:
        _frame_seq.setdefault(from_hwnd, 0)
        _frame_seq[from_hwnd] += 1
        seq = _frame_seq[from_hwnd]
    escaped = _escape_payload(payload)
    header = _json.dumps({
        "from": from_hwnd,
        "to": to_hwnd,
        "seq": seq,
        "topic": topic,
        "len": len(escaped),
        "message_id": str(_uuid.uuid4()),
    }, separators=(",", ":"))
    return f"{_STX}{header}{_NUL}{escaped}{_ETX}"


def parse_frame(raw: str) -> dict | None:
    """
    Parse a framed message from raw buffer text.

    Returns dict with keys: from, to, seq, topic, len, id, payload, _raw_frame
    Returns None if no valid frame found or length validation fails.

    Scans for STX...ETX boundaries, extracts header JSON, validates
    escaped-payload length, then unescapes payload before returning.
    """
    stx_pos = raw.find(_STX)
    if stx_pos == -1:
        return None
    etx_pos = raw.find(_ETX, stx_pos + 1)
    if etx_pos == -1:
        return None
    inner = raw[stx_pos + 1:etx_pos]
    nul_pos = inner.find(_NUL)
    if nul_pos == -1:
        return None
    header_str = inner[:nul_pos]
    escaped_payload = inner[nul_pos + 1:]
    try:
        header = _json.loads(header_str)
    except _json.JSONDecodeError:
        return None
    expected_len = header.get("len", -1)
    if expected_len != len(escaped_payload):
        return None  # incomplete or corrupted delivery
    header["payload"] = _unescape_payload(escaped_payload)
    header["_raw_frame"] = raw[stx_pos:etx_pos + 1]
    return header


def send_frame(target, from_hwnd: int, payload: str,
               topic: str = "default", seq: int | None = None,
               char_delay: float = 0.03,
               ack: bool = False, ack_timeout: float = 5.0,
               retries: int = 2) -> dict:
    """
    Build and send a framed message to target window.

    Args:
        target: WindowTarget or object with .hwnd attribute
        from_hwnd: sender's hwnd (for the header)
        payload: message text
        topic: conversation thread ID
        seq: sequence number (auto-increments if None)
        char_delay: per-character delay (lower = faster for framed msgs)
        ack: if True, verify delivery via PrintWindow after sending
        ack_timeout: seconds to wait for ACK verification
        retries: number of retransmit attempts if ACK fails

    Returns: dict with frame header fields + "acked" key (bool) if ack=True
    """
    to_hwnd = target.hwnd if hasattr(target, "hwnd") else target
    frame = build_frame(from_hwnd, to_hwnd, payload, topic, seq)
    send_string(target, frame, char_delay=char_delay)
    header = _json.loads(frame[1:frame.index(_NUL)])
    if not ack:
        return header
    # ACK loop: verify delivery, retry on failure
    fingerprint = _make_fingerprint(payload, header.get("seq"), header.get("topic"))
    for attempt in range(1, retries + 1):
        if verify_delivery(to_hwnd, fingerprint, timeout=ack_timeout):
            header["acked"] = True
            return header
        # Retransmit
        send_string(target, frame, char_delay=char_delay)
    # Final check after last retransmit
    header["acked"] = verify_delivery(to_hwnd, fingerprint, timeout=ack_timeout)
    return header


def _normalize_text(text: str) -> str:
    """Strip whitespace and control chars for fuzzy comparison."""
    import re
    return re.sub(r"[\s\x00-\x1f]+", " ", text).strip().lower()


def _make_fingerprint(payload: str, seq: int | None = None,
                      topic: str | None = None, fp_len: int = 30) -> list[str]:
    """
    Build a fingerprint for ACK verification.

    Uses first fp_len chars of payload. Including seq/topic avoids
    false positives from old messages with similar content.
    """
    fingerprints = [payload[:fp_len]]
    if seq is not None:
        fingerprints.append(f'"seq":{seq}')
    if topic:
        fingerprints.append(f'"topic":"{topic}"')
    return [fp for fp in fingerprints if fp]


def verify_delivery(target_hwnd: int, fingerprint: str | list[str],
                    timeout: float = 5.0, poll: float = 0.5,
                    fuzzy_threshold: float = 0.85) -> bool:
    """
    Verify message delivery via PrintWindow ACK (polling loop).

    Repeatedly captures target window text and checks if the fingerprint
    appears. Uses exact substring match first, then fuzzy matching
    (SequenceMatcher) to tolerate OCR errors or terminal rendering artifacts.

    Delivery means "observed on receiver's screen", not just
    "PostMessage returned TRUE". This is the closed-loop proof.

    Strategies (in order):
      1. UIA text extraction (get_text_uia) — fast, no OCR needed
      2. WM_GETTEXT on child windows (get_child_texts)
      3. OCR via pytesseract on PrintWindow capture (if installed)
      4. Save screenshot for manual verification (last resort)

    Args:
        target_hwnd: the receiver's window handle
        fingerprint: text to search for in receiver's output
        timeout: total seconds to poll before giving up
        poll: seconds between poll attempts
        fuzzy_threshold: SequenceMatcher ratio to accept (0.0-1.0)

    Returns: True if fingerprint found in receiver's visible text
    """
    import re
    fingerprints = fingerprint if isinstance(fingerprint, list) else [fingerprint]
    norm_fps = [_normalize_text(fp) for fp in fingerprints if fp]
    deadline = time.time() + timeout

    while time.time() < deadline:
        time.sleep(poll)
        extracted = ""
        # Strategy 1: UIA text extraction
        try:
            extracted = get_text_uia(target_hwnd) or ""
        except Exception:
            pass
        # Strategy 2: WM_GETTEXT children
        if not extracted:
            try:
                extracted = " ".join(text for _, _, text in get_child_texts(target_hwnd))
            except Exception:
                pass
        # Strategy 3: OCR via pytesseract
        if not extracted:
            try:
                import pytesseract
                img = capture_window(target_hwnd)
                if img:
                    extracted = pytesseract.image_to_string(img)
            except ImportError:
                pass
            except Exception:
                pass
        if not extracted:
            continue
        norm_text = _normalize_text(extracted)
        if not norm_fps:
            continue
        # Exact normalized substring match. All fingerprints must be present so
        # payload checks do not accidentally ACK an old duplicate message.
        if all(fp in norm_text for fp in norm_fps):
            return True
        # Fuzzy match: sliding window over extracted text.
        from difflib import SequenceMatcher
        matched = 0
        for norm_fp in norm_fps:
            fp_len = len(norm_fp)
            if fp_len > 0 and len(norm_text) >= fp_len:
                for i in range(len(norm_text) - fp_len + 1):
                    window = norm_text[i:i + fp_len]
                    if SequenceMatcher(None, norm_fp, window).ratio() >= fuzzy_threshold:
                        matched += 1
                        break
        if matched == len(norm_fps):
            return True

    # Last resort: save screenshot for manual/OCR verification
    try:
        save_capture(target_hwnd, path=f"proofs/ack_verify_{target_hwnd}.png")
    except Exception:
        pass
    return False


# ── MessageListener (v0.7.0) ─────────────────────────────────────────────────
#
# Background receive loop for SelfConnect framed messages.
#
# Design:
#   - Daemon thread polls own terminal text via get_text_uia() every `poll` sec
#   - parse_all_frames() extracts every STX…ETX frame in the buffer (not just the first)
#   - Dedup by message_id (bounded deque, 1024 IDs max)
#   - Dispatches each new frame to all registered .on() callbacks
#   - Thread-safe: callbacks run inside the poll thread; use queues for cross-thread work
#
# Usage:
#   listener = MessageListener(own_hwnd=4854222)
#   listener.on(lambda frame: print(f"[RX] {frame['payload']}"))
#   listener.start()
#   ...
#   listener.stop()

def parse_all_frames(raw: str) -> "list[dict]":
    """
    Extract every valid STX…ETX frame from raw buffer text.

    Unlike parse_frame() which returns only the first match, this scans the
    entire buffer and returns all valid frames in order. Use this when a burst
    of messages may have been injected before the listener polled.

    Returns a (possibly empty) list of frame dicts, each with keys:
        from, to, seq, topic, len, message_id, payload, _raw_frame
    """
    frames = []
    pos = 0
    while True:
        stx = raw.find(_STX, pos)
        if stx == -1:
            break
        etx = raw.find(_ETX, stx + 1)
        if etx == -1:
            break
        candidate = raw[stx:etx + 1]
        frame = parse_frame(candidate)
        if frame is not None:
            frames.append(frame)
        pos = etx + 1
    return frames


class MessageListener:
    """
    Background receive loop for SelfConnect framed messages (v0.7.0).

    Polls own terminal text every `poll` seconds, extracts all frames,
    deduplicates by message_id, and dispatches to registered handlers.

    Args:
        own_hwnd: hwnd of this agent's terminal window (the window to read)
        poll:     seconds between read attempts (default 0.5)
        max_seen: max message_ids to remember for dedup (default 1024)

    Example::

        listener = MessageListener(own_hwnd=4854222)
        listener.on(lambda f: print(f"[RX from {f['from']}] {f['payload']}"))
        listener.start()
        time.sleep(30)
        listener.stop()
    """

    def __init__(self, own_hwnd: int, poll: float = 0.5, max_seen: int = 1024):
        self.own_hwnd = own_hwnd
        self.poll = poll
        self._handlers: "list" = []
        self._seen: "collections.deque[str]" = collections.deque(maxlen=max_seen)
        self._seen_set: "set[str]" = set()
        self._thread: "threading.Thread | None" = None
        self._stop_event = threading.Event()

    def on(self, handler) -> "MessageListener":
        """
        Register a frame handler callback.

        handler(frame: dict) is called for every new frame received.
        Multiple handlers are called in registration order.
        Returns self for chaining: listener.on(h1).on(h2).start()
        """
        self._handlers.append(handler)
        return self

    def start(self) -> "MessageListener":
        """Start the background poll thread. Idempotent."""
        if self._thread and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"MessageListener-{self.own_hwnd}")
        self._thread.start()
        return self

    def stop(self, timeout: float | None = None) -> None:
        """Signal the poll loop to stop and wait for the thread to exit."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout if timeout is not None else self.poll * 4)

    def is_running(self) -> bool:
        """True if the background thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _record_seen(self, msg_id: str) -> None:
        """Add msg_id to seen set, evicting oldest if at capacity."""
        if len(self._seen) == self._seen.maxlen:
            evicted = self._seen[0]  # leftmost = oldest
            self._seen_set.discard(evicted)
        self._seen.append(msg_id)
        self._seen_set.add(msg_id)

    def _loop(self) -> None:
        """Poll loop — runs in daemon thread."""
        import pythoncom  # type: ignore
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        except Exception:
            pass

        while not self._stop_event.wait(self.poll):
            try:
                text = get_text_uia(self.own_hwnd)
                if not text:
                    continue
                frames = parse_all_frames(text)
                for frame in frames:
                    msg_id = frame.get("message_id") or frame.get("id") or ""
                    if msg_id and msg_id in self._seen_set:
                        continue
                    if msg_id:
                        self._record_seen(msg_id)
                    for handler in self._handlers:
                        try:
                            handler(frame)
                        except Exception:
                            pass
            except Exception:
                pass


# ── Layer 3 Supervisor (v0.8.0) ───────────────────────────────────────────────
#
# Protocol stack:
#   Layer 1 (Physical):    PostMessage(WM_CHAR) + PrintWindow
#   Layer 2 (Framing):     STX|header|NUL|payload|ETX        (v0.5.0)
#   Layer 2 receive:       MessageListener                    (v0.7.0)
#   Layer 3 (Supervisor):  AgentRegistry + WatchdogLoop + ApprovalRelay  (v0.8.0)
#
# WatchdogLoop composes on top of MessageListener:
#   - MessageListener fires when a peer SENDS us a frame  → evidence peer is alive
#   - WatchdogLoop text-polls peer terminals               → classify READY/STALLED/etc.
#
# ApprovalRelay wraps send_frame() with allowlist + audit log + human override.


class PeerState(_enum.Enum):
    """Lifecycle state for a tracked peer agent."""
    UNKNOWN          = "UNKNOWN"
    READY            = "READY"            # clean, no pending unsubmitted prompt
    PROMPT_DETECTED  = "PROMPT_DETECTED"  # input box has unsubmitted content
    STALLED          = "STALLED"          # text unchanged for > stall_threshold sec
    RESTARTED        = "RESTARTED"        # window title changed (new session)


@dataclass
class PeerRecord:
    """Live snapshot of a peer agent's tracked state."""
    hwnd:            int
    label:           str
    pid:             int       = 0
    state:           PeerState = field(default=PeerState.UNKNOWN)
    last_seen:       float     = field(default_factory=time.time)
    last_text_hash:  str       = ""
    last_title:      str       = ""
    stall_since:     float     = 0.0   # epoch when text last changed; 0 = not yet set


class AgentRegistry:
    """
    Central peer directory for the SelfConnect mesh.

    Maintains ``{hwnd: PeerRecord}``. State is updated by WatchdogLoop
    but can also be patched manually.

    Usage::

        reg = AgentRegistry()
        reg.register(2820438, "A")
        rec = reg.get(2820438)
        print(rec.state)
    """

    def __init__(self) -> None:
        self._peers: "dict[int, PeerRecord]" = {}
        self._lock = threading.Lock()

    def register(self, hwnd: int, label: str, pid: int = 0) -> PeerRecord:
        """Add or replace a peer entry. Returns the new PeerRecord."""
        rec = PeerRecord(hwnd=hwnd, label=label, pid=pid)
        with self._lock:
            self._peers[hwnd] = rec
        return rec

    def unregister(self, hwnd: int) -> None:
        """Remove a peer from the registry."""
        with self._lock:
            self._peers.pop(hwnd, None)

    def get(self, hwnd: int) -> "PeerRecord | None":
        """Return the PeerRecord for hwnd, or None if not registered."""
        with self._lock:
            return self._peers.get(hwnd)

    def all_peers(self) -> "list[PeerRecord]":
        """Snapshot list of all registered PeerRecords."""
        with self._lock:
            return list(self._peers.values())

    def update_state(self, hwnd: int, state: PeerState) -> None:
        """Manually override a peer's state (useful for tests or human corrections)."""
        with self._lock:
            rec = self._peers.get(hwnd)
            if rec:
                rec.state = state
                rec.last_seen = time.time()

    def summary(self) -> str:
        """One-line human-readable summary of all peers."""
        with self._lock:
            parts = [f"{r.label}({r.hwnd})={r.state.value}" for r in self._peers.values()]
        return " | ".join(parts) if parts else "(empty)"


class WatchdogLoop:
    """
    Background peer supervisor — polls registered agents, emits typed state-change events.

    Composes MessageListener (receive side) with text-poll classification:
    - MessageListener fires when a peer sends D a frame → evidence that peer is alive
    - Text polling via get_text_uia() classifies: READY / PROMPT_DETECTED / STALLED / RESTARTED

    Event dict keys: event, hwnd, label, old_state, new_state, timestamp

    Args:
        registry:        AgentRegistry to read/update
        own_hwnd:        This agent's hwnd — used to receive inbound frames via MessageListener
        poll:            Seconds between text-poll cycles (default 1.0)
        stall_threshold: Seconds of unchanged text before STALLED (default 15.0)

    Usage::

        reg = AgentRegistry()
        reg.register(2820438, "A")
        dog = WatchdogLoop(reg, own_hwnd=4854222)
        dog.on(lambda e: print(e["event"], e["label"]))
        dog.start()
        ...
        dog.stop()
    """

    _PROMPT_PATTERNS = ("> ", "$ ", "% ", "❯ ")  # terminal prompt tail patterns

    def __init__(self, registry: AgentRegistry,
                 own_hwnd: int = 0,
                 poll: float = 1.0,
                 stall_threshold: float = 15.0) -> None:
        self.registry = registry
        self.own_hwnd = own_hwnd
        self.poll = poll
        self.stall_threshold = stall_threshold
        self._handlers: list = []
        self._thread: "threading.Thread | None" = None
        self._stop_event = threading.Event()
        # Compose MessageListener — inbound frame from peer X proves X is alive
        self._listener: "MessageListener | None" = (
            MessageListener(own_hwnd, poll=max(0.25, poll / 2))
            .on(self._on_inbound_frame)
            if own_hwnd else None
        )

    def on(self, handler) -> "WatchdogLoop":
        """Register an event handler: handler(event: dict) -> None. Chainable."""
        self._handlers.append(handler)
        return self

    def start(self) -> "WatchdogLoop":
        """Start watchdog thread (and embedded MessageListener). Idempotent."""
        if self._listener:
            self._listener.start()
        if self._thread and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="WatchdogLoop")
        self._thread.start()
        return self

    def stop(self, timeout: "float | None" = None) -> None:
        """Signal watchdog (and listener) to stop and wait for thread exit."""
        self._stop_event.set()
        if self._listener:
            self._listener.stop(timeout=timeout)
        if self._thread:
            self._thread.join(timeout=timeout if timeout is not None else self.poll * 4)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── internal ──────────────────────────────────────────────────────────────

    def _emit(self, event: str, rec: PeerRecord, old_state: PeerState) -> None:
        payload = {
            "event": event,
            "hwnd": rec.hwnd,
            "label": rec.label,
            "old_state": old_state.value,
            "new_state": rec.state.value,
            "timestamp": time.time(),
        }
        for h in self._handlers:
            try:
                h(payload)
            except Exception:
                pass

    def _on_inbound_frame(self, frame: dict) -> None:
        """MessageListener callback — frame received from a peer proves they're active."""
        from_hwnd = frame.get("from", 0)
        rec = self.registry.get(from_hwnd)
        if rec and rec.state in (PeerState.STALLED, PeerState.UNKNOWN):
            old = rec.state
            rec.state = PeerState.READY
            rec.last_seen = time.time()
            self._emit("PEER_READY", rec, old)

    def _classify(self, text: str, rec: PeerRecord) -> PeerState:
        """Classify state from extracted text. Updates stall timer in-place."""
        text_hash = _hashlib.md5(text.encode("utf-8", "replace")).hexdigest()
        now = time.time()
        if text_hash != rec.last_text_hash:
            rec.last_text_hash = text_hash
            rec.stall_since = now
        if rec.stall_since > 0 and (now - rec.stall_since) > self.stall_threshold:
            return PeerState.STALLED
        tail = text[-200:] if len(text) > 200 else text
        stripped = tail.rstrip(" \t\n")
        for pat in self._PROMPT_PATTERNS:
            if stripped.endswith(pat.rstrip()):
                return PeerState.PROMPT_DETECTED
        return PeerState.READY

    def _poll_peer(self, rec: PeerRecord) -> None:
        """Poll one peer: title check → text classification → emit on change."""
        try:
            current_title = _get_window_title(rec.hwnd)
        except Exception:
            return
        # Title change → RESTARTED (new session)
        if rec.last_title and current_title != rec.last_title:
            old = rec.state
            rec.state = PeerState.RESTARTED
            rec.last_title = current_title
            rec.last_seen = time.time()
            self._emit("PEER_RESTARTED", rec, old)
            return
        if not rec.last_title:
            rec.last_title = current_title
        # Text-poll → classify
        try:
            text = get_text_uia(rec.hwnd) or ""
        except Exception:
            return
        new_state = self._classify(text, rec)
        rec.last_seen = time.time()
        if new_state != rec.state:
            old = rec.state
            rec.state = new_state
            self._emit(f"PEER_{new_state.value}", rec, old)

    def _loop(self) -> None:
        try:
            import pythoncom  # type: ignore
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        except Exception:
            pass
        while not self._stop_event.wait(self.poll):
            for rec in self.registry.all_peers():
                try:
                    self._poll_peer(rec)
                except Exception:
                    pass


class ApprovalRelay:
    """
    Policy-gated send layer — allowlists + prompt fingerprinting + audit log + human override.

    Wraps send_frame() with policy checking before delivery. Blocked frames are
    queued and await ``approve()`` or ``deny()`` from a human or supervisor agent.

    Allowlist rules: ``(topic_pattern, from_hwnd_or_wildcard)`` tuples.
    Use ``"*"`` as wildcard for either field.

    Audit log: JSONL file, one entry per SEND/BLOCKED/APPROVED/DENIED action.

    Usage::

        relay = ApprovalRelay(audit_log_path="proofs/audit_log.jsonl")
        relay.allow("*", "*")                          # open mode: allow all
        relay.allow("status", 4854222)                 # or: only status frames from D

        relay.on_blocked(lambda p: print("Needs approval:", p["frame_id"]))

        result = relay.send(target, from_hwnd=4854222, payload="hello", topic="status")
        # result["sent"] is True if allowed, False if blocked

        relay.approve(frame_id)   # human approves → frame sent
        relay.deny(frame_id)      # human denies  → frame dropped
    """

    def __init__(self, audit_log_path: str = "proofs/audit_log.jsonl") -> None:
        self.audit_log_path = audit_log_path
        self._allowlist: "list[tuple[str, str]]" = []
        self._pending:   "dict[str, dict]"        = {}
        self._seen_fingerprints: "collections.deque[str]" = collections.deque(maxlen=256)
        self._block_handlers: list = []
        self._lock = threading.Lock()

    def allow(self, topic: str = "*", from_hwnd: "int | str" = "*") -> "ApprovalRelay":
        """Add an allowlist rule. Use '*' as wildcard. Chainable."""
        self._allowlist.append((str(topic), str(from_hwnd)))
        return self

    def on_blocked(self, handler) -> "ApprovalRelay":
        """Register callback for frames needing human approval. Chainable."""
        self._block_handlers.append(handler)
        return self

    def _is_allowed(self, topic: str, from_hwnd: int) -> bool:
        for t_pat, h_pat in self._allowlist:
            if (t_pat == "*" or t_pat == topic) and (h_pat == "*" or h_pat == str(from_hwnd)):
                return True
        return False

    def _fingerprint(self, payload: str) -> str:
        """SHA-256 of first 64 chars — identifies suspiciously repeated payloads."""
        return _hashlib.sha256(payload[:64].encode("utf-8", "replace")).hexdigest()[:16]

    def _audit(self, entry: dict) -> None:
        import json as _j
        try:
            with open(self.audit_log_path, "a", encoding="utf-8") as f:
                f.write(_j.dumps(entry) + "\n")
        except Exception:
            pass

    def send(self, target, from_hwnd: int, payload: str,
             topic: str = "default",
             seq: "int | None" = None,
             char_delay: float = 0.03) -> dict:
        """
        Gate-checked send. Returns ``{"sent": bool, "frame_id": str, "allowed": bool, "fingerprint": str}``.

        If allowed → sends via send_frame(), writes SEND to audit log.
        If blocked → queues for human approval, fires on_blocked handlers, writes BLOCKED.
        """
        # Build frame to extract message_id for tracking
        frame_str = build_frame(from_hwnd, target.hwnd, payload, topic=topic, seq=seq)
        parsed    = parse_frame(frame_str)
        frame_id  = (parsed or {}).get("message_id", "")
        fp        = self._fingerprint(payload)
        allowed   = self._is_allowed(topic, from_hwnd)

        base = {"ts": time.time(), "frame_id": frame_id,
                "from_hwnd": from_hwnd, "to_hwnd": target.hwnd,
                "topic": topic, "fingerprint": fp, "payload_len": len(payload)}

        if allowed:
            send_frame(target, from_hwnd=from_hwnd, payload=payload,
                       topic=topic, seq=seq, char_delay=char_delay)
            self._seen_fingerprints.append(fp)
            self._audit({**base, "action": "SEND"})
            return {"sent": True, "frame_id": frame_id, "allowed": True, "fingerprint": fp}

        # Blocked — queue for human review
        pending = {**base, "target": target, "payload": payload,
                   "seq": seq, "char_delay": char_delay}
        with self._lock:
            self._pending[frame_id] = pending
        self._audit({**base, "action": "BLOCKED"})
        for h in self._block_handlers:
            try:
                h(pending)
            except Exception:
                pass
        return {"sent": False, "frame_id": frame_id, "allowed": False, "fingerprint": fp}

    def approve(self, frame_id: str) -> bool:
        """Human approves a blocked frame → sends it. Returns False if not found."""
        with self._lock:
            p = self._pending.pop(frame_id, None)
        if not p:
            return False
        send_frame(p["target"], from_hwnd=p["from_hwnd"], payload=p["payload"],
                   topic=p["topic"], seq=p["seq"], char_delay=p["char_delay"])
        self._audit({"ts": time.time(), "action": "APPROVED", "frame_id": frame_id,
                     "from_hwnd": p["from_hwnd"], "to_hwnd": p["to_hwnd"],
                     "topic": p["topic"], "fingerprint": p["fingerprint"]})
        return True

    def deny(self, frame_id: str) -> bool:
        """Human denies a blocked frame → drops it silently. Returns False if not found."""
        with self._lock:
            p = self._pending.pop(frame_id, None)
        if not p:
            return False
        self._audit({"ts": time.time(), "action": "DENIED", "frame_id": frame_id,
                     "from_hwnd": p["from_hwnd"], "to_hwnd": p["to_hwnd"],
                     "topic": p["topic"], "fingerprint": p["fingerprint"]})
        return True

    def pending_count(self) -> int:
        """Number of frames currently queued awaiting human approval."""
        with self._lock:
            return len(self._pending)

    def pending_ids(self) -> "list[str]":
        """Frame IDs currently queued for approval."""
        with self._lock:
            return list(self._pending.keys())


# ── Layer 4 Continuity (v0.9.0) ───────────────────────────────────────────────
#
# Protocol stack:
#   Layer 1 (Physical):    PostMessage(WM_CHAR) + PrintWindow
#   Layer 2 (Framing):     STX|header|NUL|payload|ETX        (v0.5.0)
#   Layer 2 receive:       MessageListener                    (v0.7.0)
#   Layer 3 (Supervisor):  AgentRegistry + WatchdogLoop + ApprovalRelay  (v0.8.0)
#   Layer 4 (Continuity):  Checkpoint + MigrationCoordinator              (v0.9.0)
#
# Patent claim: an agent autonomously detects its own context exhaustion,
# serializes its mesh position (role, peers, pending tasks) to a JSON checkpoint,
# broadcasts PEER_MIGRATING to all registered peers via send_frame(), spawns a
# successor session (new terminal + claude), and the successor reads the
# checkpoint and resumes the same role — zero human coordination required.


@dataclass
class Checkpoint:
    """
    Serializable snapshot of an agent's mesh position.

    Created by MigrationCoordinator when context usage crosses the threshold.
    Read by the successor session to resume role without human intervention.

    Fields:
        role:        Human-readable label for this agent's function ("A", "Supervisor", etc.)
        own_hwnd:    HWND of the session that wrote this checkpoint
        peers:       List of {hwnd, label, state} dicts — current mesh state
        pending:     Arbitrary task/state dict the agent wants to carry forward
        meta:        Free-form dict (version, session_id, notes, etc.)
        written_at:  Unix timestamp when the checkpoint was written
        schema:      Always "selfconnect-checkpoint-v1" — for forward-compat validation
    """
    role:       str
    own_hwnd:   int
    peers:      "list[dict]"
    pending:    "dict"
    meta:       "dict"
    written_at: float = field(default_factory=time.time)
    schema:     str   = "selfconnect-checkpoint-v1"


def write_checkpoint(checkpoint: Checkpoint, path: str) -> str:
    """
    Serialize a Checkpoint to a JSON file.

    Returns the resolved absolute path on success.
    Raises OSError if the directory is not writable.

    Usage::

        cp = Checkpoint(role="A", own_hwnd=2820438,
                        peers=[{"hwnd": 3546648, "label": "B", "state": "READY"}],
                        pending={"task": "build v0.9.0"}, meta={"session": 8})
        path = write_checkpoint(cp, "proofs/checkpoint_A.json")
    """
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    data = asdict(checkpoint)
    with open(abs_path, "w", encoding="utf-8") as f:
        _json.dump(data, f, indent=2)
    return abs_path


def read_checkpoint(path: str) -> Checkpoint:
    """
    Deserialize a Checkpoint from a JSON file.

    Validates the schema field — raises ValueError if not a selfconnect checkpoint.
    Raises FileNotFoundError if path does not exist.

    Usage::

        cp = read_checkpoint("proofs/checkpoint_A.json")
        print(cp.role, cp.own_hwnd, cp.peers)
    """
    with open(path, encoding="utf-8") as f:
        data = _json.load(f)
    if data.get("schema") != "selfconnect-checkpoint-v1":
        raise ValueError(f"Not a selfconnect checkpoint (schema={data.get('schema')!r})")
    return Checkpoint(
        role       = data["role"],
        own_hwnd   = data["own_hwnd"],
        peers      = data.get("peers", []),
        pending    = data.get("pending", {}),
        meta       = data.get("meta", {}),
        written_at = data.get("written_at", 0.0),
        schema     = data["schema"],
    )


class MigrationCoordinator:
    """
    Autonomous context-migration supervisor.

    Monitors a counter that the host agent increments each conversation turn
    (or character count, or message count — any integer proxy for context usage).
    When ``current / capacity >= threshold``, it:

      1. Writes a Checkpoint to ``checkpoint_path``
      2. Broadcasts ``PEER_MIGRATING`` to all peers in the registry via send_frame()
      3. Spawns a successor session: opens a new terminal, launches ``claude``,
         sends it the continuation briefing (includes checkpoint path + mesh state)
      4. Fires any registered ``on_migrate`` handlers

    The successor session is expected to call ``read_checkpoint(path)`` and
    re-register itself with all peers using the same ``role``.

    Args:
        own_hwnd:        This agent's HWND
        role:            This agent's role label (carried into the checkpoint)
        registry:        AgentRegistry for current peer list
        checkpoint_path: Where to write the JSON snapshot (default: proofs/checkpoint_{role}.json)
        capacity:        Total context budget (e.g. 100 for percentage, or token count)
        threshold:       Fraction of capacity that triggers migration (default 0.70)
        continuation:    Optional extra text appended to the successor briefing

    Usage::

        reg = AgentRegistry()
        reg.register(3546648, "B")
        coord = MigrationCoordinator(own_hwnd=2820438, role="A", registry=reg,
                                     capacity=100, threshold=0.70)
        coord.on_migrate(lambda cp, path: print("Migrated:", path))

        # Each turn, tick the counter:
        coord.tick(current=75)   # 75/100 = 0.75 → triggers migration
    """

    def __init__(
        self,
        own_hwnd:        int,
        role:            str,
        registry:        "AgentRegistry",
        checkpoint_path: "str | None"  = None,
        capacity:        int           = 100,
        threshold:       float         = 0.70,
        continuation:    str           = "",
    ) -> None:
        self.own_hwnd        = own_hwnd
        self.role            = role
        self.registry        = registry
        self.checkpoint_path = checkpoint_path or f"proofs/checkpoint_{role}.json"
        self.capacity        = capacity
        self.threshold       = threshold
        self.continuation    = continuation
        self._migrated       = False
        self._handlers: list = []
        self._lock           = threading.Lock()

    def on_migrate(self, handler) -> "MigrationCoordinator":
        """Register callback fired after migration. Receives (checkpoint, path). Chainable."""
        self._handlers.append(handler)
        return self

    def tick(self, current: int, pending: "dict | None" = None,
             meta: "dict | None" = None) -> bool:
        """
        Update context counter. Triggers migration when current/capacity >= threshold.

        Migration sequence (all three phases run synchronously):
          1. Write checkpoint to disk
          2. Spawn successor terminal → get successor hwnd
          3. Broadcast PEER_MIGRATING (with successor hwnd) to all peers
             using verify_delivery() per peer; failed peers logged in meta

        Args:
            current: Current context usage (same units as capacity)
            pending: Optional task state to carry forward in checkpoint
            meta:    Optional metadata dict

        Returns True if migration was triggered this call, False otherwise.
        """
        with self._lock:
            if self._migrated:
                return False
            if current / self.capacity < self.threshold:
                return False
            self._migrated = True

        peers_snapshot = [
            {"hwnd": r.hwnd, "label": r.label, "state": r.state.value}
            for r in self.registry.all_peers()
        ]
        cp = Checkpoint(
            role       = self.role,
            own_hwnd   = self.own_hwnd,
            peers      = peers_snapshot,
            pending    = pending or {},
            meta       = meta or {},
        )
        path = write_checkpoint(cp, self.checkpoint_path)

        # Step 1: Spawn successor first — so we have the hwnd before broadcasting
        successor_hwnd = self._spawn_successor(cp, path)

        # Step 2: Broadcast PEER_MIGRATING with successor hwnd + verify per peer
        failed_peers = []
        current_windows = list_windows()
        for rec in self.registry.all_peers():
            target = next((w for w in current_windows if w.hwnd == rec.hwnd), None)
            if not target:
                failed_peers.append({"hwnd": rec.hwnd, "label": rec.label,
                                     "reason": "window_not_found"})
                continue
            payload = (f"PEER_MIGRATING role={self.role} "
                       f"checkpoint={path} "
                       f"successor_hwnd={successor_hwnd or 'pending'}")
            try:
                send_frame(target, self.own_hwnd, payload, topic="migration")
                confirmed = verify_delivery(
                    target.hwnd, "PEER_MIGRATING", timeout=5.0, poll=0.3
                )
                if not confirmed:
                    failed_peers.append({"hwnd": rec.hwnd, "label": rec.label,
                                         "reason": "verify_timeout"})
            except Exception as exc:
                failed_peers.append({"hwnd": rec.hwnd, "label": rec.label,
                                     "reason": str(exc)})

        # Persist delivery failures into checkpoint meta for the record
        if failed_peers:
            cp.meta["broadcast_failures"] = failed_peers
            write_checkpoint(cp, path)

        for h in self._handlers:
            try:
                h(cp, path)
            except Exception:
                pass

        return True

    # ── internal helpers ──────────────────────────────────────────────────────

    def _find_new_hwnd(self, before: "set[int]",
                       timeout: float = 8.0, poll: float = 0.4) -> "int | None":
        """Poll until a new terminal HWND appears that wasn't in ``before``."""
        _user32 = ctypes.windll.user32
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll)
            for w in list_windows():
                if w.hwnd in before:
                    continue
                cls_buf = ctypes.create_unicode_buffer(256)
                _user32.GetClassNameW(w.hwnd, cls_buf, 256)
                cls = cls_buf.value.upper()
                if any(k in cls for k in ("CONSOLE", "TERMINAL", "CASCADIA")):
                    return w.hwnd
                if w.exe_name and any(
                    k in w.exe_name.lower() for k in ("cmd", "conhost", "wt", "terminal")
                ):
                    return w.hwnd
        return None

    def _spawn_successor(self, cp: Checkpoint, checkpoint_path: str) -> "int | None":
        """
        Spawn a new terminal, launch claude, inject the continuation briefing via
        send_string (PostMessage WM_CHAR) — NOT stdin redirect, which Claude Code
        silently ignores (requires real TTY).

        Returns the new terminal's HWND so the coordinator can include it in the
        PEER_MIGRATING broadcast. Returns None if spawn or hwnd detection fails.

        Successor's first required action (embedded in briefing):
          Announce own hwnd to all peers via send_string so peers can update routing.
        """
        peers_text = "\n".join(
            f"  - {p['label']} hwnd={p['hwnd']} state={p['state']}"
            for p in cp.peers
        )
        peers_sc_lines = "\n".join(
            f"  send_string(next(w for w in list_windows() if w.hwnd=={p['hwnd']}), "
            f"'PEER_READY role={cp.role} successor_hwnd=MY_OWN_HWND\\n')"
            for p in cp.peers
        )
        workdir = os.path.abspath(
            os.path.dirname(checkpoint_path) or "."
        )
        briefing = (
            f"[CONTINUATION BRIEFING — Role Migration]\n"
            f"You are resuming role '{cp.role}' (previous hwnd={cp.own_hwnd}).\n"
            f"Checkpoint: {checkpoint_path}\n\n"
            f"Mesh peers at migration time:\n{peers_text}\n\n"
            f"FIRST ACTION — announce your hwnd to all peers so they update routing:\n"
            f"import sys; sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
            f"from self_connect import list_windows, send_string, get_own_terminal_pid\n"
            f"import ctypes; hwnd = next(\n"
            f"  w.hwnd for w in list_windows()\n"
            f"  if w.pid == get_own_terminal_pid()), None)\n"
            f"# Then for each peer:\n"
            f"{peers_sc_lines}\n\n"
            f"THEN resume pending work: {_json.dumps(cp.pending)}\n\n"
            f"Standing protocol before idle:\n"
            f"1. Capture all peers via save_capture(hwnd)\n"
            f"2. Check for pending prompts\n"
            f"3. Confirm Enter is hit on any pending response\n"
        )
        if self.continuation:
            briefing += f"\n{self.continuation}\n"

        os.makedirs("proofs", exist_ok=True)

        # Snapshot existing windows before spawn
        before = {w.hwnd for w in list_windows()}

        # Open new console window — use CREATE_NEW_CONSOLE so it gets its own hwnd
        workdir_safe = workdir.replace('"', '\\"')
        try:
            _subprocess.Popen(
                ["cmd.exe", "/k", f'cd /d "{workdir_safe}"'],
                creationflags=_subprocess.CREATE_NEW_CONSOLE,
            )
        except Exception:
            return None

        # Wait for new terminal hwnd to appear
        new_hwnd = self._find_new_hwnd(before, timeout=8.0)
        if not new_hwnd:
            return None

        # Let the terminal settle, then restore and inject
        time.sleep(1.0)
        try:
            ctypes.windll.user32.ShowWindow(new_hwnd, 9)  # SW_RESTORE
            time.sleep(0.3)
            new_win = next((w for w in list_windows() if w.hwnd == new_hwnd), None)
            if not new_win:
                return new_hwnd
            # Launch claude in the new terminal
            send_string(new_win, "claude\r")
            time.sleep(12.0)   # wait for Claude Code to initialize
            # Inject briefing via PostMessage WM_CHAR (no stdin redirect — needs TTY)
            send_string(new_win, briefing)
            time.sleep(0.5)
            send_string(new_win, "\r")
        except Exception:
            pass  # hwnd captured — caller has it for broadcast even if inject failed

        return new_hwnd

    @property
    def has_migrated(self) -> bool:
        """True if migration has been triggered for this instance."""
        return self._migrated


# ── CLI: list windows ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    own = get_own_terminal_pid()
    print(f"Own terminal PID: {own}")
    print()
    print(f"{'hwnd':>12}  {'pid':<8}  {'exe':<30}  title")
    print("-" * 80)
    for w in list_windows():
        safe = w.title.encode("ascii", "replace").decode()
        marker = " <-- OWN" if w.pid == own else ""
        print(f"{w.hwnd:12d}  {w.pid:<8d}  {w.exe_name:<30}  {safe[:50]}{marker}")
