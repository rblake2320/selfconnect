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

__version__ = "0.5.2"
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
]

import ctypes
import ctypes.wintypes as wintypes
import time
import os
from dataclasses import dataclass
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
