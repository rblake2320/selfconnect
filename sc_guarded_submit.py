"""Candidate guarded Win32 submission with processing-bound peer ACKs.

The public submit path fixes all actuation, guard, transport, audit, and replay
authorities. Dependency injection exists only in the private test harness.
"""

from __future__ import annotations

import ctypes
import contextlib
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import struct
import subprocess
import threading
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
PROCESSOR_ATTESTATION_BOUNDARY = (
    "processed_input_sha256 is a governed adapter attestation, not an independent observation; "
    "the adapter must durably deduplicate admission_id and implement recover()"
)
EVIDENCE_BOUNDARY = (
    "local DACL and hash-chain evidence resists cross-logon replacement and detects later tampering; "
    "same-logon administrators and off-host compromise are outside this candidate claim"
)
_BIDI_CONTROLS = {
    0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069,
}
_OVERLAPPED: Any = None
_PENDING_IO: list[tuple[int, Any, int]] = []
_PENDING_IO_LOCK = threading.Lock()


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
class AckKey:
    secret: bytes
    not_before: float = 0.0
    expires_at: float = float("inf")
    revoked: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.secret, bytes) or len(self.secret) < MIN_KEY_BYTES:
            raise ValueError("peer ACK keys must contain at least 32 bytes")
        for value, name in ((self.not_before, "not_before"), (self.expires_at, "expires_at")):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(float(value)):
                raise ValueError(f"{name} must be numeric")
        if self.expires_at <= self.not_before:
            raise ValueError("key expiry must follow activation")


@dataclass(frozen=True)
class AckKeyRing:
    """Validated active key set with overlap, expiry, and revocation."""

    keys: dict[str, bytes | AckKey]

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError("at least one peer ACK key is required")
        normalized: dict[str, AckKey] = {}
        for key_id, key in self.keys.items():
            if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key_id):
                raise ValueError("key IDs must contain 1-64 safe ASCII characters")
            normalized[key_id] = key if isinstance(key, AckKey) else AckKey(key)
        object.__setattr__(self, "keys", MappingProxyType(normalized))

    def resolve(self, key_id: str, *, now: float | None = None) -> bytes:
        try:
            key = self.keys[key_id]
        except KeyError as exc:
            raise AckVerificationError("unknown peer ACK key ID") from exc
        current = time.time() if now is None else _validate_timestamp(now, "key resolution time")
        if key.revoked or current < key.not_before or current >= key.expires_at:
            raise AckVerificationError("peer ACK key is inactive")
        return key.secret

    def active_key_ids(self, *, now: float | None = None) -> tuple[str, ...]:
        current = time.time() if now is None else _validate_timestamp(now, "key resolution time")
        return tuple(key_id for key_id, key in self.keys.items() if not key.revoked and key.not_before <= current < key.expires_at)


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
    response_key_id: str = ""


@dataclass(frozen=True)
class PeerAck:
    schema: str
    key_id: str
    message_id: str
    challenge: str
    ack_nonce: str
    input_sha256: str
    processed_input_sha256: str
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
    processed_input_sha256: str | None = None,
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
    attested_digest = input_sha256 if processed_input_sha256 is None else processed_input_sha256
    body = {
        "schema": ACK_SCHEMA,
        "key_id": key_id,
        "message_id": message_id,
        "challenge": _validate_hex(challenge, "challenge", TOKEN_BYTES),
        "ack_nonce": _validate_hex(ack_nonce, "ack nonce", TOKEN_BYTES),
        "input_sha256": _validate_hex(input_sha256, "input digest", 32),
        "processed_input_sha256": _validate_hex(attested_digest, "processed input digest", 32),
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
        "input_sha256", "processed_input_sha256", "sender", "receiver", "decision", "issued_at",
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
    _validate_hex(decoded["processed_input_sha256"], "processed input digest", 32)
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
    required = {
        "schema", "key_id", "message_id", "challenge", "input_sha256", "sender", "receiver",
        "issued_at", "response_key_id",
    }
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
        _protect_evidence_path(self.path)
        with contextlib.closing(self._connect()) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > 2:
                raise AckReplayError("peer ACK finalization schema is newer than this implementation")
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
                    raw_ack BLOB,
                    key_id TEXT,
                    decision TEXT,
                    issued_at REAL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'audited')),
                    created_at REAL NOT NULL,
                    audited_at REAL,
                    PRIMARY KEY (sender, receiver, message_id),
                    UNIQUE (sender, challenge),
                    UNIQUE (sender, ack_nonce)
                )
                """
            )
            columns = {row[1]: str(row[2]).upper() for row in connection.execute("PRAGMA table_info(peer_ack_finalization)")}
            required_base = {
                "sender", "receiver", "message_id", "challenge", "ack_nonce", "input_sha256",
                "ack_sha256", "state", "created_at", "audited_at",
            }
            current_names = required_base | {"raw_ack", "key_id", "decision", "issued_at"}
            if not required_base <= columns.keys() or not set(columns) <= current_names:
                raise AckReplayError("unrecognized peer ACK finalization schema")
            expected_types = {
                "sender": "TEXT", "receiver": "TEXT", "message_id": "TEXT", "challenge": "TEXT",
                "ack_nonce": "TEXT", "input_sha256": "TEXT", "ack_sha256": "TEXT", "state": "TEXT",
                "created_at": "REAL", "audited_at": "REAL", "raw_ack": "BLOB", "key_id": "TEXT",
                "decision": "TEXT", "issued_at": "REAL",
            }
            if any(columns[name] != expected_types[name] for name in columns):
                raise AckReplayError("peer ACK finalization schema type mismatch")
            additions = {"raw_ack": "BLOB", "key_id": "TEXT", "decision": "TEXT", "issued_at": "REAL"}
            for name, sql_type in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE peer_ack_finalization ADD COLUMN {name} {sql_type}")
            connection.execute("PRAGMA user_version=2")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def finalize(self, ack: PeerAck, raw: bytes, audit: Callable[[str], None]) -> None:
        ack_sha256 = hashlib.sha256(raw).hexdigest()
        values = (ack.sender, ack.receiver, ack.message_id, ack.challenge, ack.ack_nonce, ack.input_sha256, ack_sha256)
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT challenge, ack_nonce, input_sha256, ack_sha256, state FROM peer_ack_finalization "
                "WHERE sender=? AND receiver=? AND message_id=?",
                values[:3],
            ).fetchone()
            if existing is None:
                try:
                    connection.execute(
                        "INSERT INTO peer_ack_finalization "
                        "(sender, receiver, message_id, challenge, ack_nonce, input_sha256, ack_sha256, "
                        "raw_ack, key_id, decision, issued_at, state, created_at, audited_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)",
                        (*values, sqlite3.Binary(raw), ack.key_id, ack.decision, ack.issued_at, time.time()),
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
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE peer_ack_finalization SET state='audited', audited_at=? "
                "WHERE sender=? AND receiver=? AND message_id=? AND ack_sha256=? AND state='pending'",
                (time.time(), ack.sender, ack.receiver, ack.message_id, ack_sha256),
            ).rowcount
            connection.commit()
            if updated != 1:
                raise AckReplayError("peer ACK finalization state changed unexpectedly")

    def list_pending(self) -> list[dict[str, Any]]:
        """Return authenticated envelopes awaiting audit; this never actuates input."""
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT sender, receiver, message_id, challenge, ack_nonce, input_sha256, ack_sha256, "
                "raw_ack, key_id, decision, issued_at, created_at FROM peer_ack_finalization "
                "WHERE state='pending' ORDER BY created_at"
            ).fetchall()
        names = ("sender", "receiver", "message_id", "challenge", "ack_nonce", "input_sha256", "ack_sha256",
                 "raw_ack", "key_id", "decision", "issued_at", "created_at")
        return [dict(zip(names, row, strict=True)) for row in rows]

    def reconcile_pending(self, audit: Callable[[dict[str, Any]], None]) -> int:
        """Audit pending authenticated ACKs idempotently without repeating submission."""
        reconciled = 0
        for envelope in self.list_pending():
            audit(envelope)
            with contextlib.closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                updated = connection.execute(
                    "UPDATE peer_ack_finalization SET state='audited', audited_at=? "
                    "WHERE sender=? AND receiver=? AND message_id=? AND ack_sha256=? AND state='pending'",
                    (time.time(), envelope["sender"], envelope["receiver"], envelope["message_id"], envelope["ack_sha256"]),
                ).rowcount
                connection.commit()
            reconciled += int(updated == 1)
        return reconciled


def list_pending_acks(replay_path: str | Path) -> list[dict[str, Any]]:
    """List durable authenticated ACK envelopes awaiting audit reconciliation."""
    return DurableAckFinalizer(replay_path).list_pending()


def reconcile_pending_acks(replay_path: str | Path, event_log_path: str | Path) -> int:
    """Recover pending ACK audit state only; physical submission is never repeated."""
    store = DurableAckFinalizer(replay_path)

    def audit(envelope: dict[str, Any]) -> None:
        raw = envelope.get("raw_ack")
        if not isinstance(raw, bytes) or not raw:
            raise AckReplayError("legacy pending ACK lacks a recoverable authenticated envelope")
        data = {key: value for key, value in envelope.items() if key not in {"raw_ack", "created_at"}}
        sc_mesh_registry.append_event(
            "guarded_submit_acknowledged", status="acknowledged", summary="reconciled authenticated peer ACK",
            data={**data, "recovery": "audit_only_no_physical_submit"}, event_log_path=event_log_path,
            strict=True, strict_idempotency_key=f"guarded-ack:{envelope['ack_sha256']}",
        )

    return store.reconcile_pending(audit)


class SubprocessAckProcessor:
    """Killable governed adapter using a strict JSON stdin/stdout contract."""

    idempotent_by_admission_id = True

    def __init__(self, command: list[str] | tuple[str, ...]) -> None:
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise ValueError("governed processor command is required")
        self.command = tuple(command)

    def _invoke(self, mode: str, request: PeerAckRequest, admission_id: str, deadline: float) -> tuple[str, str]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("governed processor deadline expired")
        payload = _canonical_bytes({"mode": mode, "admission_id": admission_id, "request": asdict(request)})
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            completed = subprocess.run(
                self.command, input=payload, capture_output=True, check=False,
                timeout=remaining, creationflags=flags,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("governed processor was killed at its deadline") from exc
        if completed.returncode != 0 or len(completed.stdout) > 4096:
            raise AckVerificationError("governed processor failed")
        try:
            result = json.loads(completed.stdout)
        except Exception as exc:
            raise AckVerificationError("governed processor returned malformed JSON") from exc
        if not isinstance(result, dict) or set(result) != {"admission_id", "mode", "decision", "input_sha256"}:
            raise AckVerificationError("governed processor result schema mismatch")
        if result["admission_id"] != admission_id or result["mode"] != mode:
            raise AckVerificationError("governed processor result binding mismatch")
        return result["decision"], result["input_sha256"]

    def process(self, request: PeerAckRequest, admission_id: str, deadline: float) -> tuple[str, str]:
        return self._invoke("process", request, admission_id, deadline)

    def recover(self, request: PeerAckRequest, admission_id: str, deadline: float) -> tuple[str, str]:
        return self._invoke("recover", request, admission_id, deadline)


class DurableReceiverAdmissionStore:
    """Crash-safe receiver admission/result store, before governed processing."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _protect_evidence_path(self.path)
        with contextlib.closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS receiver_admission (
                    sender TEXT NOT NULL, key_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    challenge TEXT NOT NULL, request_sha256 TEXT NOT NULL, request_body BLOB NOT NULL,
                    admission_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL CHECK (state IN ('admitted', 'processing', 'completed')),
                    lease_owner TEXT, lease_boot_id INTEGER, lease_expires_tick REAL,
                    decision TEXT, processed_input_sha256 TEXT, response BLOB,
                    created_at REAL NOT NULL, completed_at REAL,
                    PRIMARY KEY (sender, key_id, message_id, challenge)
                )
                """
            )
            expected = {
                "sender", "key_id", "message_id", "challenge", "request_sha256", "request_body",
                "admission_id", "state", "lease_owner", "lease_boot_id", "lease_expires_tick", "decision",
                "processed_input_sha256", "response", "created_at", "completed_at",
            }
            actual = {row[1] for row in connection.execute("PRAGMA table_info(receiver_admission)")}
            if actual != expected:
                raise AckReplayError("unrecognized receiver admission schema")
            connection.execute("PRAGMA user_version=1")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def admit(self, request: PeerAckRequest, raw: bytes) -> tuple[str, str, bytes | None]:
        digest = hashlib.sha256(raw).hexdigest()
        key = (request.sender, request.key_id, request.message_id, request.challenge)
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_sha256, admission_id, state, response FROM receiver_admission "
                "WHERE sender=? AND key_id=? AND message_id=? AND challenge=?", key,
            ).fetchone()
            if row is None:
                admission_id = secrets.token_hex(TOKEN_BYTES)
                connection.execute(
                    "INSERT INTO receiver_admission VALUES (?, ?, ?, ?, ?, ?, ?, 'admitted', NULL, NULL, NULL, NULL, NULL, NULL, ?, NULL)",
                    (*key, digest, sqlite3.Binary(raw), admission_id, time.time()),
                )
                connection.commit()
                return admission_id, "admitted", None
            if not hmac.compare_digest(row[0], digest):
                connection.rollback()
                raise AckReplayError("receiver admission binding conflict")
            connection.commit()
            return str(row[1]), str(row[2]), bytes(row[3]) if row[3] is not None else None

    def claim(self, admission_id: str, deadline: float) -> str | None:
        owner = secrets.token_hex(TOKEN_BYTES)
        import psutil

        boot_id = int(psutil.boot_time() * 1_000_000)
        now_tick = time.monotonic()
        # Keep the lease past the processor deadline so timeout kill/wait
        # completes before another server may enter recover().
        lease_seconds = max(0.1, deadline - time.monotonic()) + 5.0
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE receiver_admission SET state='processing', lease_owner=?, lease_boot_id=?, lease_expires_tick=? "
                "WHERE admission_id=? AND (state='admitted' OR (state='processing' AND "
                "(lease_boot_id<>? OR lease_expires_tick<?)))",
                (owner, boot_id, now_tick + lease_seconds, admission_id, boot_id, now_tick),
            ).rowcount
            connection.commit()
        return owner if updated == 1 else None

    def wait_completed(self, admission_id: str, deadline: float) -> bytes:
        while time.monotonic() < deadline:
            with contextlib.closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT state, response FROM receiver_admission WHERE admission_id=?", (admission_id,),
                ).fetchone()
            if row is not None and row[0] == "completed":
                return bytes(row[1])
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        raise TimeoutError("receiver admission is already governed by another claimant")

    def complete(self, admission_id: str, owner: str, decision: str, digest: str, response: bytes) -> None:
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, decision, processed_input_sha256, response FROM receiver_admission WHERE admission_id=?",
                (admission_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise AckReplayError("receiver admission disappeared")
            if row[0] == "completed":
                if row[1] != decision or row[2] != digest or bytes(row[3]) != response:
                    connection.rollback()
                    raise AckReplayError("receiver result conflict")
                connection.commit()
                return
            updated = connection.execute(
                "UPDATE receiver_admission SET state='completed', lease_owner=NULL, lease_boot_id=NULL, lease_expires_tick=NULL, "
                "decision=?, processed_input_sha256=?, response=?, completed_at=? "
                "WHERE admission_id=? AND lease_owner=? AND state='processing'",
                (decision, digest, sqlite3.Binary(response), time.time(), admission_id, owner),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise AckReplayError("receiver processing lease was lost")
            connection.commit()


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
    enter: Callable[[TargetIdentity], dict[str, Any]]
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


def _production_enter(target: TargetIdentity) -> dict[str, Any]:
    import self_connect as sc

    return sc.hardware_enter_checked(target.hwnd, expected_identity=asdict(target))


def _request_body(request: PeerAckRequest) -> dict[str, Any]:
    return asdict(request)


def _protect_evidence_path(path: Path) -> None:
    """Best-effort owner-only local evidence ACL; off-host admins remain out of scope."""
    if os.name != "nt":
        if path.exists():
            os.chmod(path, 0o600)
        return
    if not path.exists():
        path.touch(mode=0o600, exist_ok=True)
    _configure_security_api()
    sid = _current_logon_sid()
    sddl = f"D:P(A;;FA;;;SY)(A;;FA;;;{sid})"
    descriptor = ctypes.c_void_p()
    if not ctypes.windll.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise OSError("cannot create evidence security descriptor")
    try:
        if not ctypes.windll.advapi32.SetFileSecurityW(str(path), 4, descriptor):
            raise OSError("cannot protect evidence file")
    finally:
        ctypes.windll.kernel32.LocalFree(descriptor)


def _token_logon_sid(token: int) -> str:
    from ctypes import wintypes

    needed = wintypes.DWORD()
    ctypes.windll.advapi32.GetTokenInformation(token, 2, None, 0, ctypes.byref(needed))
    buffer = ctypes.create_string_buffer(needed.value)
    if not ctypes.windll.advapi32.GetTokenInformation(token, 2, buffer, needed, ctypes.byref(needed)):
        raise OSError("GetTokenInformation(TokenGroups) failed")

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    offset = (ctypes.sizeof(wintypes.DWORD) + ctypes.alignment(SID_AND_ATTRIBUTES) - 1) & -ctypes.alignment(SID_AND_ATTRIBUTES)
    groups = ctypes.cast(ctypes.addressof(buffer) + offset, ctypes.POINTER(SID_AND_ATTRIBUTES))
    for index in range(count):
        if groups[index].Attributes & 0xC0000000 == 0xC0000000:
            text_sid = wintypes.LPWSTR()
            if not ctypes.windll.advapi32.ConvertSidToStringSidW(groups[index].Sid, ctypes.byref(text_sid)):
                raise OSError("ConvertSidToStringSidW failed")
            try:
                return str(text_sid.value)
            finally:
                ctypes.windll.kernel32.LocalFree(text_sid)
    raise OSError("current token has no logon SID")


def _current_logon_sid(*, thread: bool = False) -> str:
    from ctypes import wintypes

    _configure_security_api()
    token = wintypes.HANDLE()
    opened = (
        ctypes.windll.advapi32.OpenThreadToken(ctypes.windll.kernel32.GetCurrentThread(), 0x0008, True, ctypes.byref(token))
        if thread else
        ctypes.windll.advapi32.OpenProcessToken(ctypes.windll.kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token))
    )
    if not opened:
        raise OSError("OpenToken failed")
    try:
        return _token_logon_sid(token)
    finally:
        ctypes.windll.kernel32.CloseHandle(token)


def _configure_security_api() -> None:
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentThread.restype = wintypes.HANDLE
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenThreadToken.restype = wintypes.BOOL
    advapi32.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]


def make_private_pipe_address() -> str:
    """Create a non-guessable name scoped to the current Windows logon SID."""
    if os.name != "nt":
        raise OSError("private named pipes require Windows")
    sid_hash = hashlib.sha256(_current_logon_sid().encode("ascii")).hexdigest()[:16]
    return rf"\\.\pipe\selfconnect_guarded_{sid_hash}_{secrets.token_hex(16)}"


def _validate_private_pipe_address(address: str) -> None:
    if os.name != "nt" or not address.startswith("\\\\.\\pipe\\"):
        raise ValueError("a local Windows named-pipe address is required")
    sid_hash = hashlib.sha256(_current_logon_sid().encode("ascii")).hexdigest()[:16]
    leaf = address.removeprefix("\\\\.\\pipe\\")
    if not re.fullmatch(rf"selfconnect_guarded_{sid_hash}_[0-9a-f]{{32}}", leaf):
        raise ValueError("pipe name must be unique and owned by the current logon SID")


class RawJsonNamedPipeClient:
    """Authenticated raw-byte named-pipe client with one total deadline."""

    def __init__(self, address: str, keyring: AckKeyRing) -> None:
        _validate_private_pipe_address(address)
        self.address = address
        self.keyring = keyring
        _configure_pipe_api()

    def receive(self, request: PeerAckRequest, timeout: float) -> bytes:
        deadline = time.monotonic() + _validate_duration(timeout, "ack_timeout", MAX_ACK_AGE_SECONDS)
        wire = _wire_encode(_request_body(request), key_id=request.key_id, key=self.keyring.resolve(request.key_id))
        handle = _open_pipe(self.address, deadline)
        try:
            _write_all(handle, wire, deadline)
            response = _read_frame(handle, deadline)
            _write_all(handle, b"\x06", deadline)
            return response
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)


class ProcessingAckServer:
    """One-shot private receiver with durable admission before governed processing."""

    def __init__(self, address: str, keyring: AckKeyRing, key_id: str, admission_path: str | Path) -> None:
        _validate_private_pipe_address(address)
        keyring.resolve(key_id)
        self.address = address
        self.keyring = keyring
        self.key_id = key_id
        self.admissions = DurableReceiverAdmissionStore(admission_path)
        self.logon_sid = _current_logon_sid()
        _configure_pipe_api()

    def serve_once(self, processor: SubprocessAckProcessor, timeout: float = 30.0) -> None:
        if not isinstance(processor, SubprocessAckProcessor):
            raise TypeError("receiver processor must be the killable governed subprocess adapter")
        deadline = time.monotonic() + _validate_duration(timeout, "server_timeout", MAX_ACK_AGE_SECONDS)
        handle = _create_pipe(self.address)
        try:
            _connect_pipe(handle, deadline)
            raw_request = _read_frame(handle, deadline)
            if not ctypes.windll.advapi32.ImpersonateNamedPipeClient(handle):
                raise OSError(f"ImpersonateNamedPipeClient failed ({ctypes.windll.kernel32.GetLastError()})")
            try:
                if not hmac.compare_digest(_current_logon_sid(thread=True), self.logon_sid):
                    raise AckVerificationError("named-pipe client logon SID denied")
            finally:
                ctypes.windll.advapi32.RevertToSelf()
            request = _validate_request(raw_request, keyring=self.keyring, max_age_seconds=timeout)
            if request.response_key_id != self.key_id:
                raise AckVerificationError("peer ACK response signing key binding denied")
            admission_id, state, response = self.admissions.admit(request, raw_request)
            if state == "completed":
                _write_all(handle, response or b"", deadline)
                _read_pipe_confirmation(handle, deadline)
                return
            lease_owner = self.admissions.claim(admission_id, deadline)
            if lease_owner is None:
                response = self.admissions.wait_completed(admission_id, deadline)
                _write_all(handle, response, deadline)
                _read_pipe_confirmation(handle, deadline)
                return
            if state == "admitted":
                decision, processed_input_sha256 = processor.process(request, admission_id, deadline)
            else:
                decision, processed_input_sha256 = processor.recover(request, admission_id, deadline)
            if time.monotonic() >= deadline:
                raise TimeoutError("governed receiver processor exceeded deadline")
            if decision not in ACK_DECISIONS:
                raise AckVerificationError("governed receiver processor returned invalid decision")
            _validate_hex(processed_input_sha256, "adapter attested input digest", 32)
            if processed_input_sha256 != request.input_sha256:
                decision = "rejected"
            response = sign_peer_ack(
                keyring=self.keyring,
                key_id=self.key_id,
                message_id=request.message_id,
                challenge=request.challenge,
                ack_nonce=secrets.token_hex(TOKEN_BYTES),
                input_sha256=request.input_sha256,
                processed_input_sha256=processed_input_sha256,
                sender=request.receiver,
                receiver=request.sender,
                decision=decision,
            )
            self.admissions.complete(admission_id, lease_owner, decision, processed_input_sha256, response)
            _write_all(handle, response, deadline)
            _read_pipe_confirmation(handle, deadline)
        finally:
            ctypes.windll.kernel32.CancelIoEx(handle, None)
            ctypes.windll.kernel32.DisconnectNamedPipe(handle)
            ctypes.windll.kernel32.CloseHandle(handle)


def _open_pipe(address: str, deadline: float) -> int:
    kernel32 = ctypes.windll.kernel32
    while time.monotonic() < deadline:
        remaining_ms = max(1, min(int((deadline - time.monotonic()) * 1000), 100))
        kernel32.WaitNamedPipeW(address, remaining_ms)
        handle = kernel32.CreateFileW(address, 0xC0000000, 0, None, 3, 0x40000000, None)
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
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]
    globals()["_OVERLAPPED"] = OVERLAPPED
    kernel32.CancelIoEx.restype = wintypes.BOOL
    kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
    kernel32.GetOverlappedResult.restype = wintypes.BOOL
    kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]


def _create_pipe(address: str) -> int:
    from ctypes import wintypes

    descriptor = ctypes.c_void_p()
    sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;{_current_logon_sid()})"
    if not ctypes.windll.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise OSError("pipe DACL creation failed")

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wintypes.BOOL)]
    attributes = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
    try:
        # FIRST_PIPE_INSTANCE + OVERLAPPED; REJECT_REMOTE_CLIENTS.
        handle = ctypes.windll.kernel32.CreateNamedPipeW(
            address, 0x00080003 | 0x40000000, 0x00000008, 1,
            MAX_ACK_BYTES + 4, MAX_ACK_BYTES + 4, 0, ctypes.byref(attributes),
        )
    finally:
        ctypes.windll.kernel32.LocalFree(descriptor)
    if handle in (0, -1, ctypes.c_void_p(-1).value):
        raise OSError("CreateNamedPipeW failed")
    return int(handle)


def _connect_pipe(handle: int, deadline: float) -> None:
    kernel32 = ctypes.windll.kernel32
    _overlapped_call(handle, deadline, lambda overlapped, _count: kernel32.ConnectNamedPipe(handle, ctypes.byref(overlapped)), "connect")


def _overlapped_call(handle: int, deadline: float, start: Callable[[Any, Any], int], operation: str) -> int:
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    event = kernel32.CreateEventW(None, True, False, None)
    if not event:
        raise OSError("CreateEventW failed")
    overlapped = _OVERLAPPED()
    overlapped.hEvent = event
    count = wintypes.DWORD()
    retained = False
    try:
        started = start(overlapped, count)
        error = int(kernel32.GetLastError()) if not started else 0
        if started:
            return int(count.value)
        if not started and error == 535 and operation == "connect":
            return 0
        if not started and error != 997:
            raise OSError(f"named-pipe {operation} failed ({error})")
        remaining = deadline - time.monotonic()
        if remaining <= 0 or kernel32.WaitForSingleObject(event, max(1, int(remaining * 1000))) != 0:
            kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
            _retain_cancelled_io(handle, overlapped, event)
            retained = True
            raise TimeoutError(f"peer acknowledgement {operation} deadline expired")
        if not kernel32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(count), False):
            raise OSError(f"named-pipe {operation} completion failed")
        return int(count.value)
    finally:
        if not retained:
            kernel32.CloseHandle(event)


def _retain_cancelled_io(handle: int, overlapped: Any, event: int) -> None:
    """Keep OVERLAPPED memory alive until cancellation completion without blocking cleanup."""
    item = (handle, overlapped, event)
    with _PENDING_IO_LOCK:
        _PENDING_IO.append(item)

    def reap() -> None:
        ctypes.windll.kernel32.WaitForSingleObject(event, 0xFFFFFFFF)
        ctypes.windll.kernel32.CloseHandle(event)
        with _PENDING_IO_LOCK:
            if item in _PENDING_IO:
                _PENDING_IO.remove(item)

    threading.Thread(target=reap, name="selfconnect-cancelled-pipe-io", daemon=True).start()


def _write_all(handle: int, data: bytes, deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("peer acknowledgement write deadline expired")
    buffer = ctypes.create_string_buffer(data)
    written = _overlapped_call(
        handle, deadline,
        lambda overlapped, count: ctypes.windll.kernel32.WriteFile(handle, buffer, len(data), ctypes.byref(count), ctypes.byref(overlapped)),
        "write",
    )
    if written != len(data) or time.monotonic() > deadline:
        raise TimeoutError("named-pipe write was partial or exceeded deadline")


def _read_frame(handle: int, deadline: float) -> bytes:
    collected = bytearray()
    expected: int | None = None
    while time.monotonic() < deadline:
        size = (4 - len(collected)) if expected is None and len(collected) < 4 else ((expected or MAX_ACK_BYTES + 4) - len(collected))
        if size <= 0:
            raise AckVerificationError("peer frame exceeds size limit")
        buffer = ctypes.create_string_buffer(size)
        read = _overlapped_call(
            handle, deadline,
            lambda overlapped, count: ctypes.windll.kernel32.ReadFile(handle, buffer, size, ctypes.byref(count), ctypes.byref(overlapped)),
            "read",
        )
        if read <= 0:
            raise OSError("named-pipe closed before complete frame")
        collected.extend(buffer.raw[:read])
        if expected is None and len(collected) >= 4:
            expected = struct.unpack("<I", collected[:4])[0] + 4
            if expected > MAX_ACK_BYTES + 4:
                raise AckVerificationError("peer frame exceeds size limit")
        if expected is not None and len(collected) == expected:
            return bytes(collected)
    raise TimeoutError("peer acknowledgement read deadline expired")


def _read_pipe_confirmation(handle: int, deadline: float) -> None:
    buffer = ctypes.create_string_buffer(1)
    read = _overlapped_call(
        handle, deadline,
        lambda overlapped, count: ctypes.windll.kernel32.ReadFile(
            handle, buffer, 1, ctypes.byref(count), ctypes.byref(overlapped),
        ),
        "confirmation read",
    )
    if read != 1 or buffer.raw[:1] != b"\x06":
        raise AckVerificationError("peer response confirmation is invalid")


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
    body_evidence: dict[str, Any] = {}

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
        details = {**body_evidence, **extra}
        record = outcome(state, error, **details)
        try:
            audit(
                "guarded_submit_ambiguous" if state == "ambiguous" else "guarded_submit_refused",
                state, error=error, **details,
            )
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
        body_evidence.update({"chars_requested": len(text), "chars_accepted": "unknown"})
        return audit_failure("ambiguous", f"body_staged_exception:{type(exc).__name__}", chars_accepted="unknown")
    body_evidence.update({
        "body": body,
        "chars_requested": body.get("chars_requested") if isinstance(body, dict) else len(text),
        "chars_accepted": body.get("chars_accepted") if isinstance(body, dict) else "unknown",
    })
    accepted = (
        isinstance(body, dict) and body.get("ok") is True
        and int(body.get("chars_requested", -1)) == len(text)
        and int(body.get("chars_accepted", -1)) == len(text)
    )
    if not accepted:
        accepted_count = body.get("chars_accepted") if isinstance(body, dict) else None
        state = "refused" if accepted_count == 0 else "ambiguous"
        error = "body_transport_zero_accepted" if state == "refused" else "body_staged_partial_or_unknown"
        return audit_failure(state, error, body=body, chars_accepted=accepted_count)
    try:
        _, focus_guard = _guard(target, authorities.snapshot, "before_focus")
    except Exception as exc:
        return audit_failure("ambiguous", f"body_staged_target_guard_exception_before_focus:{type(exc).__name__}", body=body)
    if not focus_guard["ok"]:
        return audit_failure("ambiguous", "body_staged_target_guard_failed_before_focus", guard=focus_guard, body=body)
    try:
        focus_result = authorities.focus(target.hwnd)
    except Exception as exc:
        return audit_failure("ambiguous", f"body_staged_focus_exception:{type(exc).__name__}", body=body)
    if not isinstance(focus_result, dict) or focus_result.get("ok") is not True:
        return audit_failure("ambiguous", "body_staged_focus_failed", focus=focus_result, body=body)
    try:
        _, enter_guard = _guard(target, authorities.snapshot, "immediately_before_hardware_enter")
    except Exception as exc:
        return audit_failure("ambiguous", f"body_staged_target_guard_exception_before_enter:{type(exc).__name__}", body=body)
    if not enter_guard["ok"]:
        return audit_failure("ambiguous", "body_staged_target_guard_failed_before_hardware_enter", guard=enter_guard, body=body)
    try:
        enter_result = authorities.enter(target)
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
        receiver=receiver, issued_at=time.time(), response_key_id=key_id,
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
                "processed_input_sha256": ack.processed_input_sha256,
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
    reconcile_pending_acks(replay_path, event_log_path)
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
    "ACK_SCHEMA", "REQUEST_SCHEMA", "AckKey", "AckKeyRing", "AckReplayError",
    "AckVerificationError", "DurableAckFinalizer", "DurableReceiverAdmissionStore",
    "PeerAck", "PeerAckRequest", "SubprocessAckProcessor",
    "ProcessingAckServer", "RawJsonNamedPipeClient", "TargetIdentity",
    "guarded_submit", "list_pending_acks", "make_private_pipe_address",
    "reconcile_pending_acks", "sign_peer_ack", "verify_peer_ack",
]
