"""Production one-shot Win32 seat-response channel.

The server owns the pipe name, DACL, instance identifier, and receiver key.
Client identity evidence comes only from the connected named-pipe handle and
Windows access tokens; callers never provide a SID as evidence.
"""

from __future__ import annotations

import ctypes
import os
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sc_identity import AgentIdentity
from sc_seat_identity import (
    _canonical,
    _sha256,
    create_challenge,
    key_id,
    secure_channel_evidence,
    trusted_receiver_public_key,
)

MAX_SEAT_RESPONSE_BYTES = 1024 * 1024


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("private seat response channels require Windows")


def _frame(value: dict[str, Any]) -> bytes:
    body = _canonical(value)
    if len(body) > MAX_SEAT_RESPONSE_BYTES:
        raise ValueError("seat response frame exceeds size limit")
    return struct.pack("<I", len(body)) + body


def _unframe(raw: bytes) -> dict[str, Any]:
    if len(raw) < 4:
        raise ValueError("seat response frame is truncated")
    length = struct.unpack("<I", raw[:4])[0]
    if length > MAX_SEAT_RESPONSE_BYTES or len(raw) != length + 4:
        raise ValueError("seat response frame length is invalid")
    from sc_seat_identity import canonical_json_loads

    value = canonical_json_loads(raw[4:])
    if not isinstance(value, dict):
        raise ValueError("seat response frame must be an object")
    return value


def _client_pid(pipe_handle: int) -> int:
    _require_windows()
    kernel32 = ctypes.windll.kernel32
    kernel32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
    kernel32.GetNamedPipeClientProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    pid = wintypes.ULONG()
    if not kernel32.GetNamedPipeClientProcessId(pipe_handle, ctypes.byref(pid)):
        raise OSError(f"GetNamedPipeClientProcessId failed ({kernel32.GetLastError()})")
    if pid.value <= 0:
        raise OSError("named-pipe client process ID is invalid")
    return int(pid.value)


def windows_process_start_time_100ns(pid: int) -> int:
    """Return the kernel creation FILETIME for PID reuse-resistant binding."""
    _require_windows()
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if handle in (0, -1, ctypes.c_void_p(-1).value):
        raise OSError(f"OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) failed ({kernel32.GetLastError()})")
    created = wintypes.FILETIME()
    exited = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise OSError(f"GetProcessTimes failed ({kernel32.GetLastError()})")
        return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def load_or_create_receiver_identity(
    private_key_path: str | Path,
    *,
    authority_public_key_hex: str,
    allow_create: bool = False,
) -> AgentIdentity:
    """Load an independently persisted receiver key; creation is explicit."""
    target = Path(private_key_path)
    if target.exists():
        identity = AgentIdentity.from_private_pem(target.read_bytes(), label="seat-response-receiver")
    elif allow_create:
        identity = AgentIdentity.generate("seat-response-receiver")
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_suffix(target.suffix + ".tmp")
        staged.write_bytes(identity.private_pem(allow_private_export=True))
        os.chmod(staged, 0o600)
        os.replace(staged, target)
        if os.name == "nt":
            from sc_guarded_submit import _protect_evidence_path

            _protect_evidence_path(target)
    else:
        raise FileNotFoundError("seat response receiver key is not provisioned")
    if identity.public_key_hex == authority_public_key_hex:
        raise ValueError("seat response receiver key must differ from authority key")
    return identity


@dataclass(frozen=True)
class SeatPipeEndpoint:
    address: str
    instance_id: str
    expected_logon_sid: str

    @classmethod
    def create(cls) -> SeatPipeEndpoint:
        _require_windows()
        from sc_guarded_submit import _current_logon_sid, make_private_pipe_address

        address = make_private_pipe_address()
        return cls(
            address=address,
            instance_id=_sha256(address.encode("utf-8")),
            expected_logon_sid=_current_logon_sid(),
        )

    @property
    def address_sha256(self) -> str:
        return _sha256(self.address.encode("utf-8"))


def create_live_challenge(
    endpoint: SeatPipeEndpoint,
    *,
    enrollment: dict[str, Any],
    operation_sha256: str,
    tab_snapshot_sha256: str,
    server_nonce: str,
    authority_identity: Any,
    expected_peer_pid: int,
    expected_peer_process_start_100ns: int,
    issue_store: str | Path,
    now: float | None = None,
    ttl_seconds: float = 15.0,
) -> dict[str, Any]:
    """Create the production challenge from a receiver-owned endpoint."""
    return create_challenge(
        enrollment=enrollment,
        operation_sha256=operation_sha256,
        tab_snapshot_sha256=tab_snapshot_sha256,
        response_address_sha256=endpoint.address_sha256,
        server_nonce=server_nonce,
        authority_identity=authority_identity,
        expected_peer_sid=endpoint.expected_logon_sid,
        expected_pipe_instance=endpoint.instance_id,
        expected_peer_pid=expected_peer_pid,
        expected_peer_process_start_100ns=expected_peer_process_start_100ns,
        issue_store=issue_store,
        now=now,
        ttl_seconds=ttl_seconds,
    )


class SeatResponseReceiver:
    """One connection, one bounded response, one OS-derived signed observation."""

    def __init__(
        self,
        endpoint: SeatPipeEndpoint,
        challenge: dict[str, Any],
        receiver_identity: AgentIdentity,
        receiver_trust_store: str | Path,
    ) -> None:
        _require_windows()
        from sc_guarded_submit import _configure_pipe_api

        _configure_pipe_api()
        if challenge.get("response_address_sha256") != endpoint.address_sha256:
            raise ValueError("seat challenge targets a different response endpoint")
        if challenge.get("expected_pipe_instance") != endpoint.instance_id:
            raise ValueError("seat challenge targets a different pipe instance")
        if challenge.get("expected_peer_sid") != endpoint.expected_logon_sid:
            raise ValueError("seat challenge logon SID was not receiver-derived")
        if challenge.get("authority_key_id") == key_id(receiver_identity.public_key_hex):
            raise ValueError("seat response receiver key must differ from authority key")
        receiver_id = key_id(receiver_identity.public_key_hex)
        if trusted_receiver_public_key(receiver_id, receiver_trust_store) != receiver_identity.public_key_hex:
            raise ValueError("seat response receiver key is not independently pinned")
        self.endpoint = endpoint
        self.challenge = challenge
        self.receiver_identity = receiver_identity

    def serve_once(self, timeout: float = 15.0) -> dict[str, Any]:
        from sc_guarded_submit import (
            _connect_pipe,
            _create_pipe,
            _current_logon_sid,
            _read_frame,
            _read_pipe_confirmation,
            _write_all,
        )

        if not 0 < timeout <= 60.0:
            raise ValueError("seat response timeout is invalid")
        deadline = time.monotonic() + timeout
        handle = _create_pipe(self.endpoint.address)
        try:
            _connect_pipe(handle, deadline)
            client_pid = _client_pid(handle)
            client_start = windows_process_start_time_100ns(client_pid)
            raw_payload = _read_frame(handle, deadline)
            if not ctypes.windll.advapi32.ImpersonateNamedPipeClient(handle):
                raise OSError(f"ImpersonateNamedPipeClient failed ({ctypes.windll.kernel32.GetLastError()})")
            try:
                client_sid = _current_logon_sid(thread=True)
            finally:
                if not ctypes.windll.advapi32.RevertToSelf():
                    raise OSError("RevertToSelf failed")
            if client_sid != self.endpoint.expected_logon_sid:
                raise PermissionError("named-pipe client logon SID denied")
            if client_pid != self.challenge.get("expected_peer_pid"):
                raise PermissionError("named-pipe client PID does not match the challenged seat")
            if client_start != self.challenge.get("expected_peer_process_start_100ns"):
                raise PermissionError("named-pipe client process start does not match the challenged seat")
            payload = _unframe(raw_payload)
            if payload.get("challenge_sha256") != _sha256(_canonical(self.challenge)):
                raise ValueError("seat response does not bind the issued challenge")
            if payload.get("server_nonce") != self.challenge.get("server_nonce"):
                raise ValueError("seat response server nonce is invalid")
            evidence = secure_channel_evidence(
                self.challenge,
                receiver_identity=self.receiver_identity,
                peer_sid=client_sid,
                pipe_instance=self.endpoint.instance_id,
                client_pid=client_pid,
                client_process_start_100ns=client_start,
            )
            _write_all(handle, _frame(evidence), deadline)
            _read_pipe_confirmation(handle, deadline)
            return {"payload": payload, "channel_evidence": evidence}
        finally:
            ctypes.windll.kernel32.CancelIoEx(handle, None)
            ctypes.windll.kernel32.DisconnectNamedPipe(handle)
            ctypes.windll.kernel32.CloseHandle(handle)


def send_seat_response(
    endpoint: SeatPipeEndpoint,
    challenge: dict[str, Any],
    proof: dict[str, Any],
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Seat-side client. The response contains receiver-signed OS evidence."""
    _require_windows()
    if not 0 < timeout <= 60.0:
        raise ValueError("seat response timeout is invalid")
    from sc_guarded_submit import _configure_pipe_api, _open_pipe, _read_frame, _write_all

    _configure_pipe_api()
    deadline = time.monotonic() + timeout
    handle = _open_pipe(endpoint.address, deadline)
    try:
        request = {
            "challenge_sha256": _sha256(_canonical(challenge)),
            "server_nonce": challenge["server_nonce"],
            "proof": proof,
        }
        _write_all(handle, _frame(request), deadline)
        evidence = _unframe(_read_frame(handle, deadline))
        _write_all(handle, b"\x06", deadline)
        return evidence
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
