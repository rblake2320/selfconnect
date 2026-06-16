import builtins
import ctypes
import sys
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Win32 hardening tests require Windows", allow_module_level=True)

import _win32_abi as abi
import self_connect as sc


def test_win32_handle_types_are_pointer_sized():
    assert ctypes.sizeof(abi.HWND) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(abi.LPARAM) == ctypes.sizeof(ctypes.c_void_p)
    assert sc.user32.EnumWindows.argtypes == [abi.WNDENUMPROC, abi.LPARAM]
    assert sc.user32.EnumChildWindows.argtypes == [abi.HWND, abi.WNDENUMPROC, abi.LPARAM]
    assert sc.user32.PostMessageW.argtypes == [abi.HWND, abi.UINT, abi.WPARAM, abi.LPARAM]


def test_handle_value_preserves_64_bit_values():
    value = 0xDEADBEEFCAFEBABE
    assert abi.handle_value(ctypes.c_void_p(value)) == value
    assert abi.handle_value(value) == value


def test_capabilities_namespace_is_cached_and_complete():
    assert isinstance(sc.capabilities, dict)
    assert set(sc.capabilities) == {
        "win32",
        "uia_text",
        "uia_events",
        "printwindow",
        "named_pipe_impersonation",
        "tpm_identity",
    }
    assert sc.capabilities["win32"] is True
    assert all(isinstance(v, bool) for v in sc.capabilities.values())


def test_message_listener_survives_without_pythoncom(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pythoncom":
            raise ModuleNotFoundError("pythoncom unavailable in core install")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    listener = sc.MessageListener(own_hwnd=0, poll=0.01)
    listener.start()
    time.sleep(0.05)
    try:
        assert listener.is_running()
    finally:
        listener.stop(timeout=1.0)
