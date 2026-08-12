"""Windows Credential Manager helper for SelfConnect symmetric secrets."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_TYPE_GENERIC = 1
_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def _api():
    if os.name != "nt":
        raise OSError("Windows Credential Manager is unavailable")
    return ctypes.WinDLL("Advapi32.dll", use_last_error=True)


def write_secret(target: str, value: bytes) -> None:
    blob = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    item = _Credential(
        Type=_TYPE_GENERIC, TargetName=target, CredentialBlobSize=len(value),
        CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
        Persist=_PERSIST_LOCAL_MACHINE, UserName="SelfConnect",
    )
    api = _api()
    api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    if not api.CredWriteW(ctypes.byref(item), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def read_secret(target: str) -> bytes | None:
    api = _api()
    pointer = ctypes.POINTER(_Credential)()
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.CredReadW.restype = wintypes.BOOL
    if not api.CredReadW(target, _TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return None
        raise ctypes.WinError(error)
    try:
        item = pointer.contents
        return ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
    finally:
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        api.CredFree(pointer)


__all__ = ["read_secret", "write_secret"]
