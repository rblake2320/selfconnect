"""Pointer-sized Win32 ctypes definitions used by SelfConnect."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes

HWND = wintypes.HWND
HDC = wintypes.HDC
BOOL = wintypes.BOOL
UINT = wintypes.UINT
DWORD = wintypes.DWORD
WPARAM = wintypes.WPARAM
LPARAM = wintypes.LPARAM
LRESULT = LPARAM
DWORD_PTR = ctypes.c_size_t
WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)


def handle_value(handle) -> int:
    """Normalize ctypes handle callback values to plain Python ints."""
    if handle is None:
        return 0
    if isinstance(handle, int):
        return handle
    return int(handle.value or 0)


def configure_win32_prototypes(user32, kernel32, _gdi32) -> None:
    """Set pointer-sized prototypes for Win32 APIs used by the SDK."""
    user32.EnumWindows.argtypes = [WNDENUMPROC, LPARAM]
    user32.EnumWindows.restype = BOOL
    user32.EnumChildWindows.argtypes = [HWND, WNDENUMPROC, LPARAM]
    user32.EnumChildWindows.restype = BOOL
    user32.IsWindowVisible.argtypes = [HWND]
    user32.IsWindowVisible.restype = BOOL
    user32.IsWindow.argtypes = [HWND]
    user32.IsWindow.restype = BOOL
    user32.GetWindowTextLengthW.argtypes = [HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
    user32.GetWindowThreadProcessId.restype = DWORD
    user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = BOOL
    user32.GetClientRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = BOOL
    user32.ClientToScreen.argtypes = [HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = BOOL
    user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.PostMessageW.restype = BOOL
    user32.SendMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.SendMessageW.restype = LRESULT
    user32.SendMessageTimeoutW.argtypes = [
        HWND,
        UINT,
        WPARAM,
        LPARAM,
        UINT,
        UINT,
        ctypes.POINTER(DWORD_PTR),
    ]
    user32.SendMessageTimeoutW.restype = LRESULT
    user32.PrintWindow.argtypes = [HWND, HDC, UINT]
    user32.PrintWindow.restype = BOOL
    user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
    user32.ShowWindow.restype = BOOL
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.SetForegroundWindow.restype = BOOL
    user32.BringWindowToTop.argtypes = [HWND]
    user32.BringWindowToTop.restype = BOOL
    user32.SetWindowPos.argtypes = [
        HWND,
        HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        UINT,
    ]
    user32.SetWindowPos.restype = BOOL
    user32.AttachThreadInput.argtypes = [DWORD, DWORD, BOOL]
    user32.AttachThreadInput.restype = BOOL
    user32.GetForegroundWindow.restype = HWND
    kernel32.GetConsoleWindow.restype = HWND
