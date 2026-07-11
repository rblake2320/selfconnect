"""SelfConnect ETW provider smoke test.

This is intentionally small and isolated. It proves the process can register an
ETW provider and emit string events through Advapi32 without adding a runtime
dependency to the SDK.

Run:

    python experiments/win32_probe/etw_provider.py --message "hello"

Success means the ETW calls returned ERROR_SUCCESS. Consuming these events in a
SOC pipeline still needs a schema/manifest or TraceLogging/EventPipe strategy.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import time
import uuid
from typing import Any

PROVIDER_ID = uuid.UUID("9f6f3f4f-0d11-47d0-a2e7-2b17f4bb5c10")


class GUID(ctypes.Structure):
    _fields_ = [  # noqa: RUF012
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> GUID:
        raw = value.bytes_le
        return cls(
            int.from_bytes(raw[0:4], "little"),
            int.from_bytes(raw[4:6], "little"),
            int.from_bytes(raw[6:8], "little"),
            (ctypes.c_ubyte * 8).from_buffer_copy(raw[8:16]),
        )


_advapi = ctypes.windll.advapi32
_advapi.EventRegister.argtypes = [
    ctypes.POINTER(GUID),
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_ulonglong),
]
_advapi.EventRegister.restype = ctypes.c_ulong
_advapi.EventWriteString.argtypes = [
    ctypes.c_ulonglong,
    ctypes.c_ubyte,
    ctypes.c_ulonglong,
    ctypes.c_wchar_p,
]
_advapi.EventWriteString.restype = ctypes.c_ulong
_advapi.EventUnregister.argtypes = [ctypes.c_ulonglong]
_advapi.EventUnregister.restype = ctypes.c_ulong


def write_etw_string(message: str, *, level: int = 4, keyword: int = 0) -> dict[str, Any]:
    """Register a provider, write one string event, and unregister."""
    provider = GUID.from_uuid(PROVIDER_ID)
    handle = ctypes.c_ulonglong(0)
    register_status = int(_advapi.EventRegister(
        ctypes.byref(provider),
        None,
        None,
        ctypes.byref(handle),
    ))
    write_status: int | None = None
    unregister_status: int | None = None
    try:
        if register_status == 0:
            write_status = int(_advapi.EventWriteString(
                handle.value,
                ctypes.c_ubyte(level),
                ctypes.c_ulonglong(keyword),
                message,
            ))
    finally:
        if handle.value:
            unregister_status = int(_advapi.EventUnregister(handle.value))

    return {
        "provider_id": str(PROVIDER_ID),
        "registered": register_status == 0,
        "register_status": register_status,
        "write_status": write_status,
        "unregister_status": unregister_status,
        "ok": register_status == 0 and write_status == 0 and unregister_status == 0,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SelfConnect ETW provider smoke test")
    parser.add_argument("--message", default="", help="event string to emit")
    parser.add_argument("--level", type=int, default=4, help="ETW event level")
    parser.add_argument("--keyword", type=lambda x: int(x, 0), default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    message = args.message or json.dumps({
        "event": "selfconnect.etw_probe",
        "ts": time.time(),
        "agent": "codex",
    }, separators=(",", ":"))
    result = write_etw_string(message, level=args.level, keyword=args.keyword)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
