"""Candidate guarded Win32 submission with processing-bound peer ACKs.

The public submit path fixes all actuation, guard, transport, audit, and replay
authorities. Dependency injection exists only in the private test harness.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import struct
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

import sc_mesh_registry

ACK_SCHEMA = "selfconnect.peer-ack.v2"
REQUEST_SCHEMA = "selfconnect.peer-ack-request.v1"
WIRE_PREFIX = b"SCACK1"
ACK_DECISIONS = frozenset({"accepted", "rejected"})
DEFAULT_ACK_MAX_AGE_SECONDS = 300.0
MAX_ACK_AGE_SECONDS = 900.0
MAX_ACK_BYTES = 16 * 1024
MAX_INPUT_BYTES = 64 * 1024
MIN_KEY_BYTES = 32
TOKEN_BYTES = 32
_BIDI_CONTROLS = {
    0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069,
}


class GuardedSubmitError(RuntimeError):
    """Base error for guarded submission validation."""


class AckVerificationError(GuardedSubmitError):
    """Raised when a peer request or ACK is malformed or misbound."""


class AckReplayError(AckVerificationError):
    """Raised when an already finalized peer ACK is presented again."""


@dataclass(frozen=True)
class TargetIdentity:
    hwnd: int
    pid: int
    exe_name: str
    class_name: str
    title: str
    exe_path: str
    process_start_time_ns: int

    def __post_init__(self) -> None:
        if self.hwnd <= 0 or self.pid <= 0 or self.process_start_time_ns <= 0:
            raise ValueError("target hwnd, pid, and process start time must be positive")
        if not all((self.exe_name, self.class_name, self.title, self.exe_path)):
            raise ValueError("target exe, class, title, and executable path are required")

    @classmethod
    def from_window(cls, window: Any) -> TargetIdentity:
        import psutil

        process = psutil.Process(int(window.pid))
        return cls(
            hwnd=int(window.hwnd),
            pid=int(window.pid),
            exe_name=str(window.exe_name),
            class_name=str(window.class_name),
            title=str(window.title),
            exe_path=str(process.exe()),
            process_start_time_ns=int(process.create_time() * 1_000_000_000),
        )


@dataclass(frozen=True)
class AckKeyRing:
    """Validated key-id resolver supporting explicit rotation overlap."""

    keys: dict[str, bytes]

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError("at least one peer ACK key is required")
        for key_id, key in self.keys.items():
            if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key_id):
                raise ValueError("key IDs must contain 1-64 safe ASCII characters")
            if not isinstance(key, bytes) or len(key) < MIN_KEY_BYTES:
                raise ValueError("peer ACK keys must contain at least 32 bytes")
        object.__setattr__(self, "keys", MappingProxyType(dict(self.keys)))

    def resolve(self, key_id: str) -> bytes:
        try:
            return self.keys[key_id]
        except KeyError as exc:
            raise AckVerificationError("unknown peer ACK key ID") from exc


@dataclass(frozen=True)
class PeerAckRequest:
    schema: str
    key_id: str
    message_id: str
    challenge: str
    input_sha256: str
    sender: str
    receiver: str
    issued_at: float


@dataclass(frozen=True)
class PeerAck:
    schema: str
    key_id: str
    message_id: str
    challenge: str
    ack_nonce: str
    input_sha256: str
    sender: str
    receiver: str
    decision: str
    issued_at: float
    signature: str = ""


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _validate_duration(value: float, name: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0 or normalized > maximum:
        raise ValueError(f"{name} must be greater than zero and at most {maximum}")
    return normalized


def _validate_timestamp(value: Any, name: str = "issued_at") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AckVerificationError(f"{name} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise AckVerificationError(f"{name} must be a finite number")
    return normalized


def _validate_hex(value: Any, name: str, byte_count: int) -> str:
    if not isinstance(value, str) or len(value) != byte_count * 2:
        raise AckVerificationError(f"invalid {name}")
    if any(character not in "0123456789abcdef" for character in value):
        raise AckVerificationError(f"invalid {name}")
    return value


def _wire_encode(body: dict[str, Any], *, key_id: str, key: bytes) -> bytes:
    raw_body = _canonical_bytes(body)
    signature = hmac.new(key, raw_body, hashlib.sha256).hexdigest()
    header = b" ".join((WIRE_PREFIX, key_id.encode("ascii"), str(len(raw_body)).encode("ascii"), signature.encode("ascii")))
    payload = header + b"\n" + raw_body
    if len(payload) > MAX_ACK_BYTES:
        raise ValueError("authenticated peer frame exceeds size limit")
    return struct.pack("<I", len(payload)) + payload


def _wire_decode(frame: bytes, *, keyring: AckKeyRing) -> tuple[dict[str, Any], str, str]:
    if len(frame) < 5 or len(frame) > MAX_ACK_BYTES + 4:
        raise AckVerificationError("peer frame size is invalid")
    declared = struct.unpack("<I", frame[:4])[0]
    if declared != len(frame) - 4 or declared > MAX_ACK_BYTES:
        raise AckVerificationError("peer frame length mismatch")
    payload = frame[4:]
    header, separator, raw_body = payload.partition(b"\n")
    if not separator or len(header) > 256:
        raise AckVerificationError("peer frame header is invalid")
    parts = header.split(b" ")
    if len(parts) != 4 or parts[0] != WIRE_PREFIX:
        raise AckVerificationError("peer frame header schema mismatch")
    try:
        key_id = parts[1].decode("ascii")
        body_length = int(parts[2].decode("ascii"))
        signature = parts[3].decode("ascii")
    except (UnicodeError, ValueError) as exc:
        raise AckVerificationError("peer frame header encoding is invalid") from exc
    if body_length != len(raw_body):
        raise AckVerificationError("peer frame raw-body length mismatch")
    _validate_hex(signature, "peer frame signature", 32)
    expected = hmac.new(keyring.resolve(key_id), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise AckVerificationError("peer frame signature mismatch")

    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for field, item in pairs:
            if field in value:
                raise AckVerificationError("authenticated peer frame contains duplicate fields")
            value[field] = item
        return value

    try:
        decoded = json.loads(raw_body.decode("utf-8"), object_pairs_hook=strict_object)
    except AckVerificationError:
        raise
    except Exception as exc:
        raise AckVerificationError("authenticated peer frame JSON is malformed") from exc
    if not isinstance(decoded, dict):
        raise AckVerificationError("authenticated peer frame body must be an object")
    return decoded, key_id, signature


def sign_peer_ack(
    *,
    keyring: AckKeyRing,
    key_id: str,
    message_id: str,
    challenge: str,
    ack_nonce: str,
    input_sha256: str,
    sender: str,
    receiver: str,
    decision: str,
    issued_at: float | None = None,
) -> bytes:
    if decision not in ACK_DECISIONS:
        raise ValueError("unsupported peer ACK decision")
    timestamp = time.time() if issued_at is None else issued_at
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(float(timestamp)):
        raise ValueError("issued_at must be finite and must not be bool")
    body = {
        "schema": ACK_SCHEMA,
        "key_id": key_id,
        "message_id": message_id,
        "challenge": _validate_hex(challenge, "challenge", TOKEN_BYTES),
        "ack_nonce": _validate_hex(ack_nonce, "ack nonce", TOKEN_BYTES),
        "input_sha256": _validate_hex(input_sha256, "input digest", 32),
        "sender": sender,
        "receiver": receiver,
        "decision": decision,
        "issued_at": float(timestamp),
    }
    return _wire_encode(body, key_id=key_id, key=keyring.resolve(key_id))


def verify_peer_ack(
    raw: bytes,
    *,
    keyring: AckKeyRing,
    key_id: str,
    message_id: str,
    challenge: str,
    input_sha256: str,
    sender: str,
    receiver: str,
    now: float | None = None,
    max_age_seconds: float = DEFAULT_ACK_MAX_AGE_SECONDS,
) -> PeerAck:
    max_age = _validate_duration(max_age_seconds, "max_age_seconds", MAX_ACK_AGE_SECONDS)
    decoded, wire_key_id, signature = _wire_decode(raw, keyring=keyring)
    required = {
        "schema", "key_id", "message_id", "challenge", "ack_nonce",
        "input_sha256", "sender", "receiver", "decision", "issued_at",
    }
    if set(decoded) != required:
        raise AckVerificationError("peer acknowledgement schema mismatch")
    if any(not isinstance(decoded[field], str) for field in required - {"issued_at"}):
        raise AckVerificationError("peer acknowledgement field types are invalid")
    issued_at = _validate_timestamp(decoded["issued_at"])
    checks = {
        "schema": (decoded["schema"], ACK_SCHEMA),
        "key_id": (decoded["key_id"], key_id),
        "wire_key_id": (wire_key_id, key_id),
        "message_id": (decoded["message_id"], message_id),
        "challenge": (decoded["challenge"], challenge),
        "input_sha256": (decoded["input_sha256"], input_sha256),
        "sender": (decoded["sender"], sender),
        "receiver": (decoded["receiver"], receiver),
    }
    for field, (actual, expected) in checks.items():
        if not hmac.compare_digest(actual, expected):
            raise AckVerificationError(f"peer acknowledgement {field} mismatch")
    _validate_hex(decoded["challenge"], "challenge", TOKEN_BYTES)
    _validate_hex(decoded["ack_nonce"], "ack nonce", TOKEN_BYTES)
    _validate_hex(decoded["input_sha256"], "input digest", 32)
    if decoded["decision"] not in ACK_DECISIONS:
        raise AckVerificationError("unsupported peer acknowledgement decision")
    current = time.time() if now is None else _validate_timestamp(now, "now")
    age = current - issued_at
    if age < -5.0 or age > max_age:
        raise AckVerificationError("peer acknowledgement outside freshness window")
    return PeerAck(signature=signature, issued_at=issued_at, **{key: decoded[key] for key in required if key != "issued_at"})


def _validate_request(raw: bytes, *, keyring: AckKeyRing, max_age_seconds: float) -> PeerAckRequest:
    max_age = _validate_duration(max_age_seconds, "max_age_seconds", MAX_ACK_AGE_SECONDS)
    decoded, wire_key_id, _signature = _wire_decode(raw, keyring=keyring)
    required = {"schema", "key_id", "message_id", "challenge", "input_sha256", "sender", "receiver", "issued_at"}
    if set(decoded) != required or any(not isinstance(decoded[field], str) for field in required - {"issued_at"}):
        raise AckVerificationError("peer ACK request schema mismatch")
    issued_at = _validate_timestamp(decoded["issued_at"])
    if decoded["schema"] != REQUEST_SCHEMA or decoded["key_id"] != wire_key_id:
        raise AckVerificationError("peer ACK request binding mismatch")
    _validate_hex(decoded["challenge"], "challenge", TOKEN_BYTES)
    _validate_hex(decoded["input_sha256"], "input digest", 32)
    age = time.time() - issued_at
    if age < -5.0 or age > max_age:
        raise AckVerificationError("peer ACK request outside freshness window")
    return PeerAckRequest(issued_at=issued_at, **{key: decoded[key] for key in required if key != "issued_at"})


class DurableAckFinalizer:
    """Single durable authority for pending-to-audited ACK finalization."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS peer_ack_finalization (
                    sender TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    challenge TEXT NOT NULL,
                    ack_nonce TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    ack_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'audited')),
                    created_at REAL NOT NULL,
                    audited_at REAL,
                    PRIMARY KEY (sender, receiver, message_id),
                    UNIQUE (sender, challenge),
                    UNIQUE (sender, ack_nonce)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def finalize(self, ack: PeerAck, raw: bytes, audit: Callable[[str], None]) -> None:
        ack_sha256 = hashlib.sha256(raw).hexdigest()
        values = (ack.sender, ack.receiver, ack.message_id, ack.challenge, ack.ack_nonce, ack.input_sha256, ack_sha256)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT challenge, ack_nonce, input_sha256, ack_sha256, state FROM peer_ack_finalization "
                "WHERE sender=? AND receiver=? AND message_id=?",
                values[:3],
            ).fetchone()
            if existing is None:
                try:
                    connection.execute(
                        "INSERT INTO peer_ack_finalization VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)",
                        (*values, time.time()),
                    )
                    connection.commit()
                except sqlite3.IntegrityError as exc:
                    connection.rollback()
                    raise AckReplayError("peer ACK challenge or nonce replay rejected") from exc
            else:
                if tuple(existing[:4]) != values[3:] or existing[4] == "audited":
                    connection.rollback()
                    raise AckReplayError("peer acknowledgement replay rejected")
                connection.commit()
        audit(ack_sha256)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE peer_ack_finalization SET state='audited', audited_at=? "
                "WHERE sender=? AND receiver=? AND message_id=? AND ack_sha256=? AND state='pending'",
                (time.time(), ack.sender, ack.receiver, ack.message_id, ack_sha256),
            ).rowcount
            connection.commit()
            if updated != 1:
                raise AckReplayError("peer ACK finalization state changed unexpectedly")


def _snapshot_target(hwnd: int) -> tuple[Any | None, TargetIdentity | None]:
    import self_connect as sc

    window = next((item for item in sc.list_windows() if int(item.hwnd) == int(hwnd)), None)
    return (window, TargetIdentity.from_window(window)) if window is not None else (None, None)


def _guard(expected: TargetIdentity, snapshot: Callable[[int], tuple[Any | None, TargetIdentity | None]], stage: str):
    window, actual = snapshot(expected.hwnd)
    if window is None or actual is None:
        return None, {"ok": False, "stage": stage, "error": "target_missing"}
    mismatches = [field for field in asdict(expected) if getattr(actual, field) != getattr(expected, field)]
    return window, {
        "ok": not mismatches,
        "stage": stage,
        "expected": asdict(expected),
        "actual": asdict(actual),
        "mismatches": mismatches,
        "error": "" if not mismatches else "target_identity_mismatch",
    }


def _validate_input(text: str) -> bytes:
    if not isinstance(text, str) or not text:
        raise ValueError("submitted text must be a non-empty string")
    forbidden = []
    for character in text:
        codepoint = ord(character)
        if (
            codepoint < 0x20 or 0x7F <= codepoint <= 0x9F
            or codepoint in _BIDI_CONTROLS or codepoint in {0x2028, 0x2029}
        ):
            forbidden.append(f"U+{codepoint:04X}")
    if forbidden:
        raise ValueError(f"submitted text contains forbidden controls: {', '.join(forbidden)}")
    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("submitted text contains invalid Unicode") from exc
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError("submitted text exceeds byte limit")
    return encoded


@dataclass(frozen=True)
class _Authorities:
    snapshot: Callable[[int], tuple[Any | None, TargetIdentity | None]]
    send_body: Callable[[Any, str, str], dict[str, Any]]
    focus: Callable[[int], dict[str, Any]]
    enter: Callable[[int], dict[str, Any]]
    receive_ack: Callable[[PeerAckRequest, float], bytes]
    finalize_ack: Callable[[PeerAck, bytes, Callable[[str], None]], None]
    audit_append: Callable[..., dict[str, Any]]
    token_hex: Callable[[int], str]


def _production_send_body(window: Any, text: str, transport: str) -> dict[str, Any]:
    import self_connect as sc

    if window.class_name == sc.CONSOLE_HOST_CLASS:
        mode = "console"
    elif window.class_name == sc.WT_HOST_CLASS:
        mode = "postmessage"
    else:
        return {"ok": False, "error": "guarded_body_transport_class_denied"}
    if transport not in {"auto", mode}:
        return {"ok": False, "error": "guarded_body_transport_override_denied"}
    return sc.send_string(window, text, mode=mode)


def _production_focus(hwnd: int) -> dict[str, Any]:
    import self_connect as sc

    return sc.focus_window_checked(hwnd)


def _production_enter(hwnd: int) -> dict[str, Any]:
    import self_connect as sc

    return sc.hardware_enter_checked(hwnd)


def _request_body(request: PeerAckRequest) -> dict[str, Any]:
    return asdict(request)


class RawJsonNamedPipeClient:
    """Authenticated raw-byte named-pipe client with one total deadline."""

    def __init__(self, address: str, keyring: AckKeyRing) -> None:
        if os.name != "nt" or not address.startswith("\\\\.\\pipe\\"):
            raise ValueError("a local Windows named-pipe address is required")
        self.address = address
        self.keyring = keyring
        _configure_pipe_api()

    def receive(self, request: PeerAckRequest, timeout: float) -> bytes:
        deadline = time.monotonic() + _validate_duration(timeout, "ack_timeout", MAX_ACK_AGE_SECONDS)
        wire = _wire_encode(_request_body(request), key_id=request.key_id, key=self.keyring.resolve(request.key_id))
        handle = _open_pipe(self.address, deadline)
        try:
            _write_all(handle, wire, deadline)
            return _read_frame(handle, deadline)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)


class ProcessingAckServer:
    """One-shot receiver that authenticates raw bytes before processing JSON."""

    def __init__(self, address: str, keyring: AckKeyRing, key_id: str) -> None:
        if os.name != "nt" or not address.startswith("\\\\.\\pipe\\"):
            raise ValueError("a local Windows named-pipe address is required")
        keyring.resolve(key_id)
        self.address = address
        self.keyring = keyring
        self.key_id = key_id
        _configure_pipe_api()

    def serve_once(self, processor: Callable[[PeerAckRequest], tuple[str, str]], timeout: float = 30.0) -> None:
        deadline = time.monotonic() + _validate_duration(timeout, "server_timeout", MAX_ACK_AGE_SECONDS)
        handle = _create_pipe(self.address)
        try:
            _connect_pipe(handle)
            raw_request = _read_frame(handle, deadline)
            request = _validate_request(raw_request, keyring=self.keyring, max_age_seconds=timeout)
            if request.key_id != self.key_id:
                raise AckVerificationError("peer ACK request key ID denied")
            decision, processed_input_sha256 = processor(request)
            if processed_input_sha256 != request.input_sha256:
                decision = "rejected"
            response = sign_peer_ack(
                keyring=self.keyring,
                key_id=self.key_id,
                message_id=request.message_id,
                challenge=request.challenge,
                ack_nonce=secrets.token_hex(TOKEN_BYTES),
                input_sha256=processed_input_sha256,
                sender=request.receiver,
                receiver=request.sender,
                decision=decision,
            )
            _write_all(handle, response, deadline)
        finally:
            ctypes.windll.kernel32.FlushFileBuffers(handle)
            ctypes.windll.kernel32.DisconnectNamedPipe(handle)
            ctypes.windll.kernel32.CloseHandle(handle)


def _open_pipe(address: str, deadline: float) -> int:
    kernel32 = ctypes.windll.kernel32
    while time.monotonic() < deadline:
        remaining_ms = max(1, min(int((deadline - time.monotonic()) * 1000), 100))
        kernel32.WaitNamedPipeW(address, remaining_ms)
        handle = kernel32.CreateFileW(address, 0xC0000000, 0, None, 3, 0, None)
        if handle not in (0, -1, ctypes.c_void_p(-1).value):
            return int(handle)
        time.sleep(0.005)
    raise TimeoutError("peer acknowledgement connect deadline expired")


def _configure_pipe_api() -> None:
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel32.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    kernel32.ConnectNamedPipe.restype = wintypes.BOOL
    kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.PeekNamedPipe.restype = wintypes.BOOL
    kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
    kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _create_pipe(address: str) -> int:
    handle = ctypes.windll.kernel32.CreateNamedPipeW(address, 0x00000003, 0, 1, MAX_ACK_BYTES + 4, MAX_ACK_BYTES + 4, 0, None)
    if handle in (0, -1, ctypes.c_void_p(-1).value):
        raise OSError("CreateNamedPipeW failed")
    return int(handle)


def _connect_pipe(handle: int) -> None:
    kernel32 = ctypes.windll.kernel32
    if not kernel32.ConnectNamedPipe(handle, None) and kernel32.GetLastError() != 535:
        raise OSError("ConnectNamedPipe failed")


def _write_all(handle: int, data: bytes, deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("peer acknowledgement write deadline expired")
    buffer = ctypes.create_string_buffer(data)
    written = ctypes.c_ulong(0)
    if not ctypes.windll.kernel32.WriteFile(handle, buffer, len(data), ctypes.byref(written), None):
        raise OSError("named-pipe write failed")
    if written.value != len(data) or time.monotonic() > deadline:
        raise TimeoutError("named-pipe write was partial or exceeded deadline")


def _read_frame(handle: int, deadline: float) -> bytes:
    kernel32 = ctypes.windll.kernel32
    collected = bytearray()
    expected: int | None = None
    while time.monotonic() < deadline:
        available = ctypes.c_ulong(0)
        if not kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            raise OSError("named-pipe peek failed")
        if available.value:
            size = min(int(available.value), MAX_ACK_BYTES + 4 - len(collected))
            if size <= 0:
                raise AckVerificationError("peer frame exceeds size limit")
            buffer = ctypes.create_string_buffer(size)
            read = ctypes.c_ulong(0)
            if not kernel32.ReadFile(handle, buffer, size, ctypes.byref(read), None):
                raise OSError("named-pipe read failed")
            collected.extend(buffer.raw[:read.value])
            if expected is None and len(collected) >= 4:
                expected = struct.unpack("<I", collected[:4])[0] + 4
                if expected > MAX_ACK_BYTES + 4:
                    raise AckVerificationError("peer frame exceeds size limit")
            if expected is not None and len(collected) == expected:
                return bytes(collected)
            if expected is not None and len(collected) > expected:
                raise AckVerificationError("peer sent trailing bytes")
        time.sleep(0.005)
    raise TimeoutError("peer acknowledgement read deadline expired")


def _guarded_submit_impl(
    text: str,
    *,
    target: TargetIdentity,
    sender: str,
    receiver: str,
    keyring: AckKeyRing,
    key_id: str,
    event_log_path: str | Path,
    authorities: _Authorities,
    transport: str,
    ack_timeout: float,
    max_ack_age_seconds: float,
) -> dict[str, Any]:
    encoded_input = _validate_input(text)
    timeout = _validate_duration(ack_timeout, "ack_timeout", MAX_ACK_AGE_SECONDS)
    max_age = _validate_duration(max_ack_age_seconds, "max_ack_age_seconds", MAX_ACK_AGE_SECONDS)
    keyring.resolve(key_id)
    if not sender or not receiver or sender == receiver:
        raise ValueError("distinct sender and receiver are required")
    input_sha256 = hashlib.sha256(encoded_input).hexdigest()
    message_id = uuid.UUID(bytes=bytes.fromhex(authorities.token_hex(16))).hex
    challenge = authorities.token_hex(TOKEN_BYTES)
    _validate_hex(challenge, "challenge", TOKEN_BYTES)
    base = {
        "message_id": message_id, "challenge": challenge, "key_id": key_id,
        "input_sha256": input_sha256, "input_bytes": len(encoded_input),
        "sender": sender, "receiver": receiver, "target": asdict(target),
    }

    def audit(event_type: str, status: str, *, idempotency_key: str = "", **data: Any):
        return authorities.audit_append(
            event_type, status=status, hwnd=target.hwnd,
            summary=f"guarded submit {status}", data={**base, **data},
            event_log_path=event_log_path, strict=True,
            strict_idempotency_key=idempotency_key,
        )

    def outcome(state: str, error: str, **extra: Any):
        return {**base, "ok": False, "state": state, "delivery_verified": False, "error": error, **extra}

    def audit_failure(state: str, error: str, **extra: Any):
        record = outcome(state, error, **extra)
        try:
            audit("guarded_submit_ambiguous" if state == "ambiguous" else "guarded_submit_refused", state, error=error)
        except Exception as exc:
            record["audit_error"] = f"{type(exc).__name__}:{exc}"
        return record

    try:
        first_window, first_guard = _guard(target, authorities.snapshot, "before_typing")
    except Exception as exc:
        return outcome("refused", f"target_guard_exception_before_typing:{type(exc).__name__}")
    if not first_guard["ok"]:
        return outcome("refused", "target_guard_failed_before_typing", guard=first_guard)
    try:
        audit("guarded_submit_prepared", "prepared", guard=first_guard)
    except Exception as exc:
        return outcome("refused", f"audit_prepare_failed:{type(exc).__name__}", guard=first_guard)
    try:
        body = authorities.send_body(first_window, text, transport)
    except Exception as exc:
        return audit_failure("refused", f"body_transport_exception:{type(exc).__name__}")
    accepted = (
        isinstance(body, dict) and body.get("ok") is True
        and int(body.get("chars_requested", -1)) == len(text)
        and int(body.get("chars_accepted", -1)) == len(text)
    )
    if not accepted:
        return audit_failure("refused", "body_transport_rejected", body=body)
    try:
        _, focus_guard = _guard(target, authorities.snapshot, "before_focus")
    except Exception as exc:
        return audit_failure("refused", f"target_guard_exception_before_focus:{type(exc).__name__}", body=body)
    if not focus_guard["ok"]:
        return audit_failure("refused", "target_guard_failed_before_focus", guard=focus_guard, body=body)
    try:
        focus_result = authorities.focus(target.hwnd)
    except Exception as exc:
        return audit_failure("refused", f"focus_exception:{type(exc).__name__}", body=body)
    if not isinstance(focus_result, dict) or focus_result.get("ok") is not True:
        return audit_failure("refused", "focus_failed", focus=focus_result, body=body)
    try:
        _, enter_guard = _guard(target, authorities.snapshot, "immediately_before_hardware_enter")
    except Exception as exc:
        return audit_failure("refused", f"target_guard_exception_before_enter:{type(exc).__name__}", body=body)
    if not enter_guard["ok"]:
        return audit_failure("refused", "target_guard_failed_before_hardware_enter", guard=enter_guard, body=body)
    try:
        enter_result = authorities.enter(target.hwnd)
    except Exception as exc:
        return audit_failure("ambiguous", f"hardware_enter_exception:{type(exc).__name__}", body=body)
    try:
        _, after_guard = _guard(target, authorities.snapshot, "immediately_after_hardware_enter")
    except Exception as exc:
        return audit_failure("ambiguous", f"target_guard_exception_after_enter:{type(exc).__name__}", enter=enter_result)
    if not isinstance(enter_result, dict) or enter_result.get("ok") is not True or not after_guard["ok"]:
        return audit_failure("ambiguous", "hardware_enter_not_confirmed", enter=enter_result, guard=after_guard)
    try:
        audit("guarded_submit_submitted", "submitted", enter=enter_result, guard=after_guard, body=body)
    except Exception as exc:
        return audit_failure("ambiguous", f"audit_submitted_failed:{type(exc).__name__}", enter=enter_result)
    request = PeerAckRequest(
        schema=REQUEST_SCHEMA, key_id=key_id, message_id=message_id,
        challenge=challenge, input_sha256=input_sha256, sender=sender,
        receiver=receiver, issued_at=time.time(),
    )
    try:
        ack_deadline = time.monotonic() + timeout
        raw_ack = authorities.receive_ack(request, timeout)
        if time.monotonic() >= ack_deadline:
            raise TimeoutError("peer ACK total deadline expired before authentication")
        ack = verify_peer_ack(
            raw_ack, keyring=keyring, key_id=key_id, message_id=message_id,
            challenge=challenge, input_sha256=input_sha256, sender=receiver,
            receiver=sender, max_age_seconds=max_age,
        )
        if time.monotonic() >= ack_deadline:
            raise TimeoutError("peer ACK total deadline expired during authentication")
        ack_data = {
            "ack": {
                "schema": ack.schema, "key_id": ack.key_id,
                "challenge": ack.challenge, "ack_nonce": ack.ack_nonce,
                "sender": ack.sender, "receiver": ack.receiver,
                "decision": ack.decision, "issued_at": ack.issued_at,
                "ack_sha256": hashlib.sha256(raw_ack).hexdigest(),
            }
        }

        def append_ack(ack_sha256: str) -> None:
            audit(
                "guarded_submit_acknowledged", "acknowledged",
                idempotency_key=f"guarded-ack:{ack_sha256}", **ack_data,
            )

        authorities.finalize_ack(ack, raw_ack, append_ack)
    except Exception as exc:
        return audit_failure("ambiguous", f"peer_ack_failed:{type(exc).__name__}", enter=enter_result)
    return {
        **base, "ok": ack.decision == "accepted",
        "state": "acknowledged" if ack.decision == "accepted" else "peer_rejected",
        "delivery_verified": ack.decision == "accepted", "transport_accepted": True,
        "peer_acknowledged": True, "decision": ack.decision,
        "body": body, "focus": focus_result, "enter": enter_result,
        "guard_after_enter": after_guard, **ack_data,
    }


def guarded_submit(
    text: str,
    *,
    target: TargetIdentity,
    sender: str,
    receiver: str,
    keyring: AckKeyRing,
    key_id: str,
    ack_pipe: str,
    replay_path: str | Path,
    event_log_path: str | Path,
    transport: str = "auto",
    ack_timeout: float = 10.0,
    max_ack_age_seconds: float = DEFAULT_ACK_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Run the fixed-authority candidate guarded-submit transaction."""
    client = RawJsonNamedPipeClient(ack_pipe, keyring)
    finalizer = DurableAckFinalizer(replay_path)
    authorities = _Authorities(
        snapshot=_snapshot_target,
        send_body=_production_send_body,
        focus=_production_focus,
        enter=_production_enter,
        receive_ack=client.receive,
        finalize_ack=finalizer.finalize,
        audit_append=sc_mesh_registry.append_event,
        token_hex=secrets.token_hex,
    )
    return _guarded_submit_impl(
        text, target=target, sender=sender, receiver=receiver,
        keyring=keyring, key_id=key_id, event_log_path=event_log_path,
        authorities=authorities, transport=transport, ack_timeout=ack_timeout,
        max_ack_age_seconds=max_ack_age_seconds,
    )


__all__ = [
    "ACK_SCHEMA", "REQUEST_SCHEMA", "AckKeyRing", "AckReplayError",
    "AckVerificationError", "DurableAckFinalizer", "PeerAck", "PeerAckRequest",
    "ProcessingAckServer", "RawJsonNamedPipeClient", "TargetIdentity",
    "guarded_submit", "sign_peer_ack", "verify_peer_ack",
]
