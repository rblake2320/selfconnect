"""Owned native Win32 UI for the live visual-specialist proof."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import ClassVar

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.SetWindowTextW.restype = wintypes.BOOL
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
BN_CLICKED = 0
SW_SHOW = 5
BUTTON_ID = 1001

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASS(ctypes.Structure):
    _fields_: ClassVar[list[tuple[str, object]]] = [
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


state_label = wintypes.HWND()


@WNDPROC
def window_proc(hwnd, message, wparam, lparam):
    if message == WM_COMMAND:
        control_id = int(wparam) & 0xFFFF
        notification = (int(wparam) >> 16) & 0xFFFF
        if control_id == BUTTON_ID and notification == BN_CLICKED:
            user32.SetWindowTextW(state_label, "STATE: COMPLETE")
            Path(os.environ["SC_STATE_FILE"]).write_text("COMPLETE", encoding="ascii")
            user32.InvalidateRect(hwnd, None, True)
            return 0
    if message == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, message, wparam, lparam)


def main() -> None:
    global state_label
    instance = kernel32.GetModuleHandleW(None)
    class_name = f"SelfConnectVisualProof{os.getpid()}"
    window_class = WNDCLASS()
    window_class.lpfnWndProc = window_proc
    window_class.hInstance = instance
    window_class.hCursor = user32.LoadCursorW(None, 32512)
    window_class.hbrBackground = 6
    window_class.lpszClassName = class_name
    if not user32.RegisterClassW(ctypes.byref(window_class)):
        raise ctypes.WinError()
    hwnd = user32.CreateWindowExW(
        0,
        class_name,
        os.environ["SC_WINDOW_TITLE"],
        0x00CF0000,
        200,
        180,
        620,
        360,
        None,
        None,
        instance,
        None,
    )
    if not hwnd:
        raise ctypes.WinError()
    user32.CreateWindowExW(
        0,
        "STATIC",
        "SelfConnect Visual Specialist Proof",
        0x50000000,
        40,
        35,
        520,
        40,
        hwnd,
        None,
        instance,
        None,
    )
    state_label = user32.CreateWindowExW(
        0,
        "STATIC",
        "STATE: READY",
        0x50000000,
        40,
        105,
        520,
        45,
        hwnd,
        None,
        instance,
        None,
    )
    user32.CreateWindowExW(
        0,
        "BUTTON",
        "Advance",
        0x50010000,
        210,
        205,
        180,
        55,
        hwnd,
        wintypes.HMENU(BUTTON_ID),
        instance,
        None,
    )
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)
    if not user32.SetWindowTextW(hwnd, os.environ["SC_WINDOW_TITLE"]):
        raise ctypes.WinError()
    Path(os.environ["SC_READY_FILE"]).write_text(str(hwnd), encoding="ascii")
    message = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))


if __name__ == "__main__":
    main()
