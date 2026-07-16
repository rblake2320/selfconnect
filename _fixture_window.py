"""Headless-CI test fixture: a real, visible, top-level Win32 window living in
its own process.

test_self_connect.py's window-enumeration tests skip when the session has no
visible top-level windows (headless CI). Running this module as a subprocess
provides an *external* window (distinct PID, unique title, non-zero size) so
those tests exercise real assertions — including find_target, which requires a
window owned by a different process.

Usage:
    python _fixture_window.py "<unique title>"
Prints "READY <hwnd>" once the window is up, then pumps messages until the
parent terminates the process.
"""
import ctypes
import sys
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
SW_SHOW = 5
CW_USEDEFAULT = 0x80000000

# Keep the WNDPROC reference alive for the process lifetime (else GC frees it).
_wndproc_ref = None


def main() -> int:
    global _wndproc_ref
    title = sys.argv[1] if len(sys.argv) > 1 else "SelfConnectHeadlessFixture"

    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]

    h_instance = kernel32.GetModuleHandleW(None)

    def _proc(hwnd, msg, wparam, lparam):
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    _wndproc_ref = WNDPROC(_proc)

    cls = WNDCLASSW()
    cls.lpfnWndProc = _wndproc_ref
    cls.hInstance = h_instance
    cls.lpszClassName = "SelfConnectFixtureClass"
    if not user32.RegisterClassW(ctypes.byref(cls)):
        print("REGISTER-FAILED", flush=True)
        return 1

    hwnd = user32.CreateWindowExW(
        0, cls.lpszClassName, title, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        CW_USEDEFAULT, CW_USEDEFAULT, 400, 300, None, None, h_instance, None,
    )
    if not hwnd:
        print("CREATE-FAILED", flush=True)
        return 1

    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)
    print(f"READY {hwnd}", flush=True)

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
