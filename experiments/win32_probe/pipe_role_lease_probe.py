"""pipe_role_lease_probe.py - pipe-authenticated mesh role lease proof.

This proof combines:

- a Windows named-pipe control-plane message;
- `ImpersonateNamedPipeClient` to bind the request to an OS caller identity;
- monotonic role generations;
- a UI fallback gate that rejects stale generation/HWND tuples.

It does not send UI input. It proves the control-plane guard that should sit in
front of future WM_CHAR/UI fallback sends.
"""
# ruff: noqa: I001

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from sc_mesh_lease import RoleLeaseTable, hash_sid


PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_MESSAGE = 0x00000004
PIPE_READMODE_MESSAGE = 0x00000002
PIPE_WAIT = 0x00000000
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
ERROR_PIPE_CONNECTED = 535
INVALID_HANDLE = ctypes.c_size_t(-1).value
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
SECURITY_SQOS_PRESENT = 0x00100000
SECURITY_IMPERSONATION = 0x00020000


class PipeLeaseVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "NA"


@dataclass
class PipeLeaseProofRecord:
    verdict: PipeLeaseVerdict = PipeLeaseVerdict.NA
    na_reason: str = ""
    pipe_name_hash: str = ""
    impersonated: bool = False
    owner_sid_hash: str = ""
    initial_generation: int = 0
    migrated_generation: int = 0
    current_allowed: bool = False
    stale_generation_rejected: bool = False
    stale_hwnd_rejected: bool = False
    renewed: bool = False
    latency_ms: float = 0.0
    redacted: bool = True
    notes: list[str] | None = None


_k32 = ctypes.windll.kernel32
_a32 = ctypes.windll.advapi32

_k32.CreateNamedPipeW.restype = ctypes.c_void_p
_k32.CreateNamedPipeW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    wt.DWORD,
    ctypes.c_void_p,
]
_k32.CreateFileW.restype = ctypes.c_void_p
_k32.CreateFileW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    wt.DWORD,
    ctypes.c_void_p,
    wt.DWORD,
    wt.DWORD,
    ctypes.c_void_p,
]
_k32.ConnectNamedPipe.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_k32.ConnectNamedPipe.restype = wt.BOOL
_k32.ReadFile.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.c_void_p,
]
_k32.ReadFile.restype = wt.BOOL
_k32.WriteFile.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.c_void_p,
]
_k32.WriteFile.restype = wt.BOOL
_k32.FlushFileBuffers.argtypes = [ctypes.c_void_p]
_k32.DisconnectNamedPipe.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.restype = wt.BOOL
_k32.GetCurrentThread.restype = ctypes.c_void_p

_a32.ImpersonateNamedPipeClient.argtypes = [ctypes.c_void_p]
_a32.ImpersonateNamedPipeClient.restype = wt.BOOL
_a32.RevertToSelf.restype = wt.BOOL
_a32.OpenThreadToken.argtypes = [
    ctypes.c_void_p,
    wt.DWORD,
    wt.BOOL,
    ctypes.POINTER(ctypes.c_void_p),
]
_a32.OpenThreadToken.restype = wt.BOOL
_a32.GetTokenInformation.argtypes = [
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
]
_a32.GetTokenInformation.restype = wt.BOOL
_a32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]
_a32.ConvertSidToStringSidW.restype = wt.BOOL
_k32.LocalFree.argtypes = [ctypes.c_void_p]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_: ClassVar = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]


class TOKEN_USER_STRUCT(ctypes.Structure):
    _fields_: ClassVar = [("User", SID_AND_ATTRIBUTES)]


def _invalid(handle) -> bool:
    if handle is None:
        return True
    return ctypes.c_size_t(handle).value == INVALID_HANDLE


def _get_impersonated_sid() -> str:
    token = ctypes.c_void_p()
    if not _a32.OpenThreadToken(_k32.GetCurrentThread(), TOKEN_QUERY, True, ctypes.byref(token)):
        return ""
    try:
        needed = wt.DWORD(0)
        _a32.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(needed))
        if needed.value <= 0:
            return ""
        buf = ctypes.create_string_buffer(needed.value)
        if not _a32.GetTokenInformation(token, TOKEN_USER, buf, needed, ctypes.byref(needed)):
            return ""
        user = ctypes.cast(buf, ctypes.POINTER(TOKEN_USER_STRUCT)).contents
        sid_string = wt.LPWSTR()
        if not _a32.ConvertSidToStringSidW(user.User.Sid, ctypes.byref(sid_string)):
            return ""
        try:
            return sid_string.value or ""
        finally:
            _k32.LocalFree(sid_string)
    finally:
        _k32.CloseHandle(token)


def _create_pipe(pipe_name: str):
    handle = _k32.CreateNamedPipeW(
        pipe_name,
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
        1,
        8192,
        8192,
        0,
        None,
    )
    if _invalid(handle):
        raise OSError(f"CreateNamedPipeW failed: {ctypes.GetLastError()}")
    return handle


def _read_json(handle) -> tuple[dict[str, object], bool, str]:
    connected = bool(_k32.ConnectNamedPipe(handle, None))
    if not connected and ctypes.GetLastError() != ERROR_PIPE_CONNECTED:
        raise OSError(f"ConnectNamedPipe failed: {ctypes.GetLastError()}")

    buf = ctypes.create_string_buffer(8192)
    read = wt.DWORD(0)
    if not _k32.ReadFile(handle, buf, len(buf), ctypes.byref(read), None):
        raise OSError(f"ReadFile failed: {ctypes.GetLastError()}")
    payload = json.loads(buf.raw[: read.value].decode("utf-8"))

    # Impersonate after reading: Windows impersonates the client that sent the
    # last message on this pipe instance.
    impersonated = bool(_a32.ImpersonateNamedPipeClient(handle))
    sid = _get_impersonated_sid() if impersonated else ""
    try:
        return payload, impersonated, sid
    finally:
        _a32.RevertToSelf()


def _write_json(pipe_name: str, payload: dict[str, object]) -> None:
    handle = _k32.CreateFileW(
        pipe_name,
        GENERIC_READ | GENERIC_WRITE,
        0,
        None,
        OPEN_EXISTING,
        SECURITY_SQOS_PRESENT | SECURITY_IMPERSONATION,
        None,
    )
    if _invalid(handle):
        raise OSError(f"CreateFileW failed: {ctypes.GetLastError()}")
    try:
        data = json.dumps(payload).encode("utf-8")
        written = wt.DWORD(0)
        if not _k32.WriteFile(handle, data, len(data), ctypes.byref(written), None):
            raise OSError(f"WriteFile failed: {ctypes.GetLastError()}")
    finally:
        _k32.CloseHandle(handle)


def pipe_roundtrip(pipe_name: str, payload: dict[str, object]) -> tuple[dict[str, object], bool, str]:
    result: dict[str, object] = {}
    error: list[BaseException] = []
    ready = threading.Event()

    def server() -> None:
        handle = None
        try:
            handle = _create_pipe(pipe_name)
            ready.set()
            request, impersonated, sid = _read_json(handle)
            result["request"] = request
            result["impersonated"] = impersonated
            result["sid"] = sid
        except BaseException as exc:
            error.append(exc)
            ready.set()
        finally:
            if handle is not None:
                _k32.FlushFileBuffers(handle)
                _k32.DisconnectNamedPipe(handle)
                _k32.CloseHandle(handle)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    if not ready.wait(5):
        raise TimeoutError("pipe server did not become ready")
    time.sleep(0.05)
    _write_json(pipe_name, payload)
    thread.join(5)
    if error:
        raise error[0]
    if thread.is_alive():
        raise TimeoutError("pipe server did not finish")
    return result["request"], bool(result["impersonated"]), str(result["sid"])


def run_probe(*, output_path: str = "") -> PipeLeaseProofRecord:
    record = PipeLeaseProofRecord(notes=[])
    if sys.platform != "win32":
        record.na_reason = "Win32 platform required"
        return record

    started = time.time()
    pipe_name = rf"\\.\pipe\sc_role_lease_{uuid.uuid4().hex}"
    record.pipe_name_hash = hash_sid(pipe_name)
    table = RoleLeaseTable()

    try:
        initial_request = {
            "op": "lease_acquire",
            "mesh": "default",
            "role": "agent-a",
            "hwnd": 0x11111111,
            "pid": 1001,
            "exe_name": "WindowsTerminal.exe",
            "class_name": "CASCADIA_HOSTING_WINDOW_CLASS",
            "title": "agent-a initial",
        }
        req1, impersonated1, sid1 = pipe_roundtrip(pipe_name, initial_request)
        if not impersonated1 or not sid1:
            record.na_reason = "named-pipe impersonation did not provide caller SID"
            return record
        record.impersonated = impersonated1
        record.owner_sid_hash = hash_sid(sid1)

        lease1 = table.issue(
            mesh=str(req1["mesh"]),
            role=str(req1["role"]),
            hwnd=int(req1["hwnd"]),
            pid=int(req1["pid"]),
            exe_name=str(req1["exe_name"]),
            class_name=str(req1["class_name"]),
            title=str(req1["title"]),
            owner_sid=sid1,
            ttl_s=30,
            now=started,
        )
        record.initial_generation = lease1.generation

        current = table.validate_ui_fallback(
            mesh="default",
            role="agent-a",
            generation=lease1.generation,
            hwnd=lease1.hwnd,
            owner_sid=sid1,
            now=started + 1,
        )
        record.current_allowed = current.ok

        migrate_request = {
            "op": "lease_acquire",
            "mesh": "default",
            "role": "agent-a",
            "hwnd": 0x22222222,
            "pid": 1002,
            "exe_name": "WindowsTerminal.exe",
            "class_name": "CASCADIA_HOSTING_WINDOW_CLASS",
            "title": "agent-a migrated",
        }
        req2, impersonated2, sid2 = pipe_roundtrip(pipe_name, migrate_request)
        if not impersonated2 or hash_sid(sid2) != record.owner_sid_hash:
            record.verdict = PipeLeaseVerdict.FAIL
            record.na_reason = "migrated lease request did not preserve OS caller identity"
            return record

        lease2 = table.issue(
            mesh=str(req2["mesh"]),
            role=str(req2["role"]),
            hwnd=int(req2["hwnd"]),
            pid=int(req2["pid"]),
            exe_name=str(req2["exe_name"]),
            class_name=str(req2["class_name"]),
            title=str(req2["title"]),
            owner_sid=sid2,
            ttl_s=30,
            now=started + 2,
        )
        record.migrated_generation = lease2.generation

        stale_generation = table.validate_ui_fallback(
            mesh="default",
            role="agent-a",
            generation=lease1.generation,
            hwnd=lease2.hwnd,
            owner_sid=sid2,
            now=started + 3,
        )
        record.stale_generation_rejected = not stale_generation.ok and "generation" in stale_generation.reason

        stale_hwnd = table.validate_ui_fallback(
            mesh="default",
            role="agent-a",
            generation=lease2.generation,
            hwnd=lease1.hwnd,
            owner_sid=sid2,
            now=started + 3,
        )
        record.stale_hwnd_rejected = not stale_hwnd.ok and "hwnd" in stale_hwnd.reason

        renewal = table.renew(
            mesh="default",
            role="agent-a",
            generation=lease2.generation,
            hwnd=lease2.hwnd,
            owner_sid=sid2,
            ttl_s=60,
            now=started + 4,
        )
        record.renewed = renewal.ok

        record.latency_ms = (time.time() - started) * 1000
        checks = [
            record.impersonated,
            record.initial_generation == 1,
            record.migrated_generation == 2,
            record.current_allowed,
            record.stale_generation_rejected,
            record.stale_hwnd_rejected,
            record.renewed,
        ]
        record.verdict = PipeLeaseVerdict.PASS if all(checks) else PipeLeaseVerdict.FAIL
        if record.verdict == PipeLeaseVerdict.FAIL:
            record.na_reason = "one or more lease gate checks failed"
        record.notes = [
            "Named pipe request was bound to OS caller identity via ImpersonateNamedPipeClient.",
            "Role lease generation advanced after migration.",
            "UI fallback gate allowed the current tuple and rejected stale generation/HWND tuples.",
            "Raw SID, pipe name, HWNDs, and titles are redacted or hashed in the artifact.",
        ]
        return record
    except OSError as exc:
        record.na_reason = str(exc)
        return record
    finally:
        if output_path and record.verdict == PipeLeaseVerdict.PASS:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(sanitize_record(record), indent=2, sort_keys=True), encoding="utf-8")


def sanitize_record(record: PipeLeaseProofRecord) -> dict[str, object]:
    data = asdict(record)
    data["verdict"] = record.verdict.value
    data["redacted"] = True
    return data


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run named-pipe role lease proof")
    parser.add_argument("--output", default="", help="write redacted PASS artifact to this path")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    record = run_probe(output_path=args.output)
    reason = f" | {record.na_reason}" if record.na_reason else ""
    print(f"[PIPE_ROLE_LEASE] result={record.verdict.value}{reason}")
    if args.verbose or record.verdict != PipeLeaseVerdict.PASS:
        print(json.dumps(sanitize_record(record), indent=2, sort_keys=True))
    return 0 if record.verdict == PipeLeaseVerdict.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
