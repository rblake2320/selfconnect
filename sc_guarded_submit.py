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
    "the evidence file has a local owner/SYSTEM DACL and hash-chain; its parent directory must already be "
    "access-controlled by the deployer and is not attested here; parent replacement, same-logon administrators, "
    "and off-host compromise are outside this candidate claim"
)
_BIDI_CONTROLS = {
    0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069,
}
_OVERLAPPED: Any = None
_PIPE_API_LOCK = threading.Lock()
_PENDING_IO: list[tuple[Any, ...]] = []
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
    attempt_nonce: str = ""


@dataclass(frozen=True)
class PeerAck:
    schema: str
    key_id: str
    message_id: str
    challenge: str
    attempt_nonce: str
    ack_nonce: str
    input_sha256: str
    processed_input_sha256: str
    sender: str
    receiver: str
    decision: str
    issued_at: float
    signature: str = ""


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


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
    attempt_nonce: str,
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
        "attempt_nonce": _validate_hex(attempt_nonce, "attempt nonce", TOKEN_BYTES),
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
    attempt_nonce: str,
    input_sha256: str,
    sender: str,
    receiver: str,
    now: float | None = None,
    max_age_seconds: float = DEFAULT_ACK_MAX_AGE_SECONDS,
) -> PeerAck:
    max_age = _validate_duration(max_age_seconds, "max_age_seconds", MAX_ACK_AGE_SECONDS)
    decoded, wire_key_id, signature = _wire_decode(raw, keyring=keyring)
    required = {
        "schema", "key_id", "message_id", "challenge", "attempt_nonce", "ack_nonce",
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
        "attempt_nonce": (decoded["attempt_nonce"], attempt_nonce),
        "input_sha256": (decoded["input_sha256"], input_sha256),
        "sender": (decoded["sender"], sender),
        "receiver": (decoded["receiver"], receiver),
    }
    for field, (actual, expected) in checks.items():
        if not hmac.compare_digest(actual, expected):
            raise AckVerificationError(f"peer acknowledgement {field} mismatch")
    _validate_hex(decoded["challenge"], "challenge", TOKEN_BYTES)
    _validate_hex(decoded["attempt_nonce"], "attempt nonce", TOKEN_BYTES)
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
        "issued_at", "response_key_id", "attempt_nonce",
    }
    if set(decoded) != required or any(not isinstance(decoded[field], str) for field in required - {"issued_at"}):
        raise AckVerificationError("peer ACK request schema mismatch")
    issued_at = _validate_timestamp(decoded["issued_at"])
    if decoded["schema"] != REQUEST_SCHEMA or decoded["key_id"] != wire_key_id:
        raise AckVerificationError("peer ACK request binding mismatch")
    _validate_hex(decoded["challenge"], "challenge", TOKEN_BYTES)
    _validate_hex(decoded["attempt_nonce"], "attempt nonce", TOKEN_BYTES)
    _validate_hex(decoded["input_sha256"], "input digest", 32)
    age = time.time() - issued_at
    if age < -5.0 or age > max_age:
        raise AckVerificationError("peer ACK request outside freshness window")
    return PeerAckRequest(issued_at=issued_at, **{key: decoded[key] for key in required if key != "issued_at"})


def _sqlite_table_info(connection: sqlite3.Connection, table: str) -> list[tuple[Any, ...]]:
    return connection.execute(f"PRAGMA table_info({table})").fetchall()


def _sqlite_unique_sets(connection: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for row in connection.execute(f"PRAGMA index_list({table})"):
        if int(row[2]) != 1:
            continue
        result.add(tuple(str(item[2]) for item in connection.execute(f"PRAGMA index_info({row[1]})")))
    return result


def _normalized_catalog_sql(connection: sqlite3.Connection, table: str) -> str:
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return " ".join(str(row[0]).lower().split()) if row and row[0] else ""


def _attest_table(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[tuple[str, str, int, int], ...],
    unique_sets: set[tuple[str, ...]],
    expected_sql: str,
) -> None:
    actual = tuple((str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5])) for row in _sqlite_table_info(connection, table))
    if actual != columns:
        raise AckReplayError(f"{table} catalog column/PK attestation failed")
    if unique_sets != _sqlite_unique_sets(connection, table):
        raise AckReplayError(f"{table} catalog UNIQUE attestation failed")
    sql = _normalized_catalog_sql(connection, table)
    if sql != " ".join(expected_sql.lower().split()):
        raise AckReplayError(f"{table} canonical catalog attestation failed")
    extras = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE tbl_name=? AND type IN ('trigger','index') AND sql IS NOT NULL LIMIT 1",
        (table,),
    ).fetchone()
    if extras:
        raise AckReplayError(f"{table} unexpected catalog object attestation failed")


def _configure_durable_sqlite(connection: sqlite3.Connection) -> None:
    mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
    if not mode or str(mode[0]).lower() != "delete":
        raise AckReplayError("durable SQLite journal mode is not DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    synchronous = connection.execute("PRAGMA synchronous").fetchone()
    if not synchronous or int(synchronous[0]) != 2:
        raise AckReplayError("durable SQLite synchronous mode is not FULL")


FINALIZER_SCHEMA_VERSION = 3
FINALIZER_CREATE_SQL = """
CREATE TABLE peer_ack_finalization (
    sender TEXT NOT NULL, receiver TEXT NOT NULL, message_id TEXT NOT NULL,
    challenge TEXT NOT NULL, ack_nonce TEXT NOT NULL, input_sha256 TEXT NOT NULL,
    ack_sha256 TEXT NOT NULL, raw_ack BLOB NOT NULL, key_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accepted','rejected')), issued_at REAL NOT NULL,
    audit_event_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','audited')),
    created_at REAL NOT NULL, audited_at REAL,
    PRIMARY KEY (sender, receiver, message_id), UNIQUE (sender, challenge), UNIQUE (ack_nonce)
)
"""
FINALIZER_COLUMNS = (
    ("sender", "TEXT", 1, 1), ("receiver", "TEXT", 1, 2), ("message_id", "TEXT", 1, 3),
    ("challenge", "TEXT", 1, 0), ("ack_nonce", "TEXT", 1, 0), ("input_sha256", "TEXT", 1, 0),
    ("ack_sha256", "TEXT", 1, 0), ("raw_ack", "BLOB", 1, 0), ("key_id", "TEXT", 1, 0),
    ("decision", "TEXT", 1, 0), ("issued_at", "REAL", 1, 0), ("audit_event_json", "TEXT", 1, 0),
    ("state", "TEXT", 1, 0), ("created_at", "REAL", 1, 0), ("audited_at", "REAL", 0, 0),
)
LEGACY_FINALIZER_V1_SQL = """
CREATE TABLE peer_ack_finalization (
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
LEGACY_FINALIZER_V2_SQL = LEGACY_FINALIZER_V1_SQL.replace(
    "    state TEXT NOT NULL",
    "    raw_ack BLOB,\n    key_id TEXT,\n    decision TEXT,\n    issued_at REAL,\n    state TEXT NOT NULL",
)
LEGACY_FINALIZER_V1_COLUMNS = (
    ("sender", "TEXT", 1, 1), ("receiver", "TEXT", 1, 2), ("message_id", "TEXT", 1, 3),
    ("challenge", "TEXT", 1, 0), ("ack_nonce", "TEXT", 1, 0), ("input_sha256", "TEXT", 1, 0),
    ("ack_sha256", "TEXT", 1, 0), ("state", "TEXT", 1, 0), ("created_at", "REAL", 1, 0),
    ("audited_at", "REAL", 0, 0),
)
LEGACY_FINALIZER_V2_COLUMNS = (
    *LEGACY_FINALIZER_V1_COLUMNS[:7],
    ("raw_ack", "BLOB", 0, 0), ("key_id", "TEXT", 0, 0), ("decision", "TEXT", 0, 0),
    ("issued_at", "REAL", 0, 0), *LEGACY_FINALIZER_V1_COLUMNS[7:],
)
_ACK_OPERATION_FIELDS = frozenset({
    "message_id", "challenge", "key_id", "response_key_id", "input_sha256", "input_bytes",
    "sender", "receiver", "target",
})
_ACK_RECEIPT_FIELDS = frozenset((*PeerAck.__dataclass_fields__, "ack_sha256"))
_ACK_EVENT_FIELDS = frozenset({
    "event_type", "status", "hwnd", "summary", "data", "strict_idempotency_key",
})


def _canonical_ack_event(ack: PeerAck, raw: bytes, operation: dict[str, Any]) -> dict[str, Any]:
    if set(operation) != _ACK_OPERATION_FIELDS or not isinstance(operation.get("target"), dict):
        raise AckReplayError("peer ACK operation schema is invalid")
    if operation["message_id"] != ack.message_id or operation["challenge"] != ack.challenge:
        raise AckReplayError("peer ACK operation identity binding failed")
    if operation["input_sha256"] != ack.input_sha256:
        raise AckReplayError("peer ACK operation input binding failed")
    if operation["sender"] != ack.receiver or operation["receiver"] != ack.sender:
        raise AckReplayError("peer ACK operation peer binding failed")
    receipt = {**asdict(ack), "ack_sha256": hashlib.sha256(raw).hexdigest()}
    return {
        "event_type": "guarded_submit_acknowledged",
        "status": "acknowledged",
        "hwnd": operation["target"].get("hwnd"),
        "summary": "guarded submit acknowledged",
        "data": {**operation, "ack": receipt},
        "strict_idempotency_key": f"guarded-ack:{receipt['ack_sha256']}",
    }


def _validated_ack_audit_event(
    audit_json: str,
    binding: dict[str, Any],
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        event = json.loads(audit_json)
    except Exception as exc:
        raise AckReplayError("peer ACK canonical audit envelope is malformed") from exc
    if not isinstance(event, dict) or set(event) != _ACK_EVENT_FIELDS:
        raise AckReplayError("peer ACK canonical audit envelope schema is invalid")
    try:
        if _canonical_bytes(event).decode("ascii") != audit_json:
            raise AckReplayError("peer ACK audit envelope is not canonical")
    except (TypeError, ValueError) as exc:
        raise AckReplayError("peer ACK audit envelope is not strict JSON") from exc
    expected_key = f"guarded-ack:{binding['ack_sha256']}"
    if event.get("event_type") != "guarded_submit_acknowledged" or event.get("strict_idempotency_key") != expected_key:
        raise AckReplayError("peer ACK audit envelope identity binding failed")
    data = event.get("data")
    receipt = data.get("ack") if isinstance(data, dict) else None
    operation = {key: value for key, value in data.items() if key != "ack"} if isinstance(data, dict) else {}
    if set(operation) != _ACK_OPERATION_FIELDS or not isinstance(receipt, dict) or set(receipt) != _ACK_RECEIPT_FIELDS:
        raise AckReplayError("peer ACK canonical audit envelope data schema is invalid")
    expected_data = {
        "message_id": binding["message_id"], "challenge": binding["challenge"],
        "input_sha256": binding["input_sha256"], "sender": binding["receiver"], "receiver": binding["sender"],
    }
    expected_receipt = {
        "challenge": binding["challenge"], "ack_nonce": binding["ack_nonce"],
        "decision": binding["decision"], "key_id": binding["key_id"], "ack_sha256": binding["ack_sha256"],
        "sender": binding["sender"], "receiver": binding["receiver"],
    }
    if any(data.get(key) != value for key, value in expected_data.items()):
        raise AckReplayError("peer ACK audit envelope operation binding failed")
    if any(receipt.get(key) != value for key, value in expected_receipt.items()):
        raise AckReplayError("peer ACK audit envelope receipt binding failed")
    if expected is not None and event != expected:
        raise AckReplayError("peer ACK canonical audit envelope exact binding failed")
    return event


class DurableAckFinalizer:
    """Single durable authority for pending-to-audited ACK finalization."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(self.path, timeout=5.0, isolation_level=None)) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > FINALIZER_SCHEMA_VERSION:
                raise AckReplayError("peer ACK finalization schema is newer than this implementation")
            _configure_durable_sqlite(connection)
            exists = bool(_sqlite_table_info(connection, "peer_ack_finalization"))
            if not exists:
                connection.execute("BEGIN EXCLUSIVE")
                connection.execute(FINALIZER_CREATE_SQL)
                connection.execute(f"PRAGMA user_version={FINALIZER_SCHEMA_VERSION}")
                self._attest(connection)
                connection.commit()
            elif version == FINALIZER_SCHEMA_VERSION:
                self._attest(connection)
            else:
                self._migrate_legacy(connection)
        _protect_evidence_path(self.path)

    @staticmethod
    def _attest(connection: sqlite3.Connection) -> None:
        _attest_table(
            connection, "peer_ack_finalization", FINALIZER_COLUMNS,
            {("sender", "receiver", "message_id"), ("sender", "challenge"), ("ack_nonce",)},
            FINALIZER_CREATE_SQL,
        )

    def _migrate_legacy(self, connection: sqlite3.Connection) -> None:
        columns = tuple(str(row[1]) for row in _sqlite_table_info(connection, "peer_ack_finalization"))
        base = ("sender", "receiver", "message_id", "challenge", "ack_nonce", "input_sha256", "ack_sha256")
        v1 = (*base, "state", "created_at", "audited_at")
        v2 = (*base, "raw_ack", "key_id", "decision", "issued_at", "state", "created_at", "audited_at")
        if columns == v1:
            legacy_columns, legacy_sql = LEGACY_FINALIZER_V1_COLUMNS, LEGACY_FINALIZER_V1_SQL
        elif columns == v2:
            legacy_columns, legacy_sql = LEGACY_FINALIZER_V2_COLUMNS, LEGACY_FINALIZER_V2_SQL
        else:
            raise AckReplayError("unrecognized legacy peer ACK finalization schema")
        _attest_table(
            connection, "peer_ack_finalization", legacy_columns,
            {("sender", "receiver", "message_id"), ("sender", "challenge"), ("sender", "ack_nonce")}, legacy_sql,
        )
        if connection.execute("SELECT 1 FROM peer_ack_finalization WHERE state='pending' LIMIT 1").fetchone():
            raise AckReplayError("legacy pending ACK lacks the canonical audit envelope required for recovery")
        has_envelope = "raw_ack" in columns
        connection.execute("BEGIN EXCLUSIVE")
        try:
            connection.execute("ALTER TABLE peer_ack_finalization RENAME TO peer_ack_finalization_legacy")
            connection.execute(FINALIZER_CREATE_SQL)
            if has_envelope:
                connection.execute(
                    "INSERT INTO peer_ack_finalization SELECT sender,receiver,message_id,challenge,ack_nonce,input_sha256,"
                    "ack_sha256,coalesce(raw_ack,x''),coalesce(key_id,'legacy'),coalesce(decision,'rejected'),"
                    "coalesce(issued_at,0),'{}',state,created_at,audited_at FROM peer_ack_finalization_legacy"
                )
            else:
                connection.execute(
                    "INSERT INTO peer_ack_finalization SELECT sender,receiver,message_id,challenge,ack_nonce,input_sha256,"
                    "ack_sha256,x'','legacy','rejected',0,'{}',state,created_at,audited_at "
                    "FROM peer_ack_finalization_legacy"
                )
            connection.execute("DROP TABLE peer_ack_finalization_legacy")
            connection.execute(f"PRAGMA user_version={FINALIZER_SCHEMA_VERSION}")
            self._attest(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        _configure_durable_sqlite(connection)
        return connection

    def finalize(
        self,
        ack: PeerAck,
        raw: bytes,
        audit: Callable[[dict[str, Any]], None],
        audit_event: dict[str, Any],
        operation: dict[str, Any],
    ) -> None:
        ack_sha256 = hashlib.sha256(raw).hexdigest()
        values = (ack.sender, ack.receiver, ack.message_id, ack.challenge, ack.ack_nonce, ack.input_sha256, ack_sha256)
        binding = {
            "sender": ack.sender, "receiver": ack.receiver, "message_id": ack.message_id,
            "challenge": ack.challenge, "ack_nonce": ack.ack_nonce, "input_sha256": ack.input_sha256,
            "ack_sha256": ack_sha256, "key_id": ack.key_id, "decision": ack.decision,
        }
        expected_event = _canonical_ack_event(ack, raw, operation)
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT challenge, ack_nonce, input_sha256, ack_sha256, state, key_id, decision, audit_event_json "
                "FROM peer_ack_finalization "
                "WHERE sender=? AND receiver=? AND message_id=?",
                values[:3],
            ).fetchone()
            if existing is None:
                audit_json = _canonical_bytes(audit_event).decode("ascii")
                event_to_audit = _validated_ack_audit_event(audit_json, binding, expected_event)
                try:
                    connection.execute(
                        "INSERT INTO peer_ack_finalization "
                        "(sender, receiver, message_id, challenge, ack_nonce, input_sha256, ack_sha256, "
                        "raw_ack, key_id, decision, issued_at, audit_event_json, state, created_at, audited_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)",
                        (*values, sqlite3.Binary(raw), ack.key_id, ack.decision, ack.issued_at, audit_json, time.time()),
                    )
                    connection.commit()
                except sqlite3.IntegrityError as exc:
                    connection.rollback()
                    raise AckReplayError("peer ACK challenge or nonce replay rejected") from exc
            else:
                if tuple(existing[:4]) != values[3:] or existing[4] == "audited":
                    connection.rollback()
                    raise AckReplayError("peer acknowledgement replay rejected")
                stored_binding = {**binding, "key_id": str(existing[5]), "decision": str(existing[6])}
                event_to_audit = _validated_ack_audit_event(str(existing[7]), stored_binding, expected_event)
                connection.commit()
        audit(event_to_audit)
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
                "raw_ack, key_id, decision, issued_at, audit_event_json, created_at FROM peer_ack_finalization "
                "WHERE state='pending' ORDER BY created_at"
            ).fetchall()
        names = ("sender", "receiver", "message_id", "challenge", "ack_nonce", "input_sha256", "ack_sha256",
                 "raw_ack", "key_id", "decision", "issued_at", "audit_event_json", "created_at")
        result = [dict(zip(names, row, strict=True)) for row in rows]
        for item in result:
            audit_json = str(item.pop("audit_event_json"))
            item["audit_event"] = _validated_ack_audit_event(audit_json, item)
        return result

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
        event = envelope.get("audit_event")
        if not isinstance(event, dict):
            raise AckReplayError("pending ACK lacks a canonical audit event")
        sc_mesh_registry.append_event(event_log_path=event_log_path, strict=True, **event)

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
        process = subprocess.Popen(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        assert process.stdin is not None and process.stdout is not None
        output: list[bytes] = []

        def bounded_write() -> None:
            try:
                process.stdin.write(payload)
                process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        def bounded_read() -> None:
            try:
                chunk = process.stdout.read(4097)
                output.append(chunk)
                if len(chunk) > 4096 and process.poll() is None:
                    process.kill()
            except (OSError, ValueError):
                pass

        writer = threading.Thread(target=bounded_write, name="selfconnect-processor-input", daemon=True)
        reader = threading.Thread(target=bounded_read, name="selfconnect-processor-output", daemon=True)
        writer.start()
        reader.start()
        try:
            returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            process.kill()
            threading.Thread(target=process.wait, name="selfconnect-processor-reaper", daemon=True).start()
            raise TimeoutError("governed processor direct child was killed at its deadline") from exc
        remaining = max(0.0, deadline - time.monotonic())
        writer.join(timeout=remaining)
        remaining = max(0.0, deadline - time.monotonic())
        reader.join(timeout=remaining)
        stdout = output[0] if output else b""
        if time.monotonic() >= deadline:
            raise TimeoutError("governed processor I/O exceeded deadline")
        if returncode != 0 or len(stdout) > 4096 or writer.is_alive() or reader.is_alive():
            raise AckVerificationError("governed processor failed")
        try:
            result = json.loads(stdout)
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


RECEIVER_SCHEMA_VERSION = 2
RECEIVER_CREATE_SQL = """
CREATE TABLE receiver_admission (
    sender TEXT NOT NULL, receiver TEXT NOT NULL, message_id TEXT NOT NULL, challenge TEXT NOT NULL,
    input_sha256 TEXT NOT NULL, response_key_id TEXT NOT NULL, operation_sha256 TEXT NOT NULL,
    admission_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('admitted','processing','completed')),
    lease_owner TEXT, lease_boot_id INTEGER, lease_expires_tick REAL,
    decision TEXT CHECK (decision IS NULL OR decision IN ('accepted','rejected')),
    processed_input_sha256 TEXT, created_at REAL NOT NULL, completed_at REAL,
    PRIMARY KEY (sender, message_id, challenge)
);
CREATE TABLE receiver_attempt (
    attempt_nonce TEXT NOT NULL PRIMARY KEY, sender TEXT NOT NULL, message_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL UNIQUE, created_at REAL NOT NULL
)
"""
RECEIVER_COLUMNS = (
    ("sender", "TEXT", 1, 1), ("receiver", "TEXT", 1, 0), ("message_id", "TEXT", 1, 2),
    ("challenge", "TEXT", 1, 3), ("input_sha256", "TEXT", 1, 0), ("response_key_id", "TEXT", 1, 0),
    ("operation_sha256", "TEXT", 1, 0), ("admission_id", "TEXT", 1, 0), ("state", "TEXT", 1, 0),
    ("lease_owner", "TEXT", 0, 0), ("lease_boot_id", "INTEGER", 0, 0), ("lease_expires_tick", "REAL", 0, 0),
    ("decision", "TEXT", 0, 0), ("processed_input_sha256", "TEXT", 0, 0),
    ("created_at", "REAL", 1, 0), ("completed_at", "REAL", 0, 0),
)
ATTEMPT_COLUMNS = (
    ("attempt_nonce", "TEXT", 1, 1), ("sender", "TEXT", 1, 0), ("message_id", "TEXT", 1, 0),
    ("request_sha256", "TEXT", 1, 0), ("created_at", "REAL", 1, 0),
)
LEGACY_RECEIVER_COLUMNS = (
    ("sender", "TEXT", 1, 1), ("key_id", "TEXT", 1, 2), ("message_id", "TEXT", 1, 3),
    ("challenge", "TEXT", 1, 4), ("request_sha256", "TEXT", 1, 0), ("request_body", "BLOB", 1, 0),
    ("admission_id", "TEXT", 1, 0), ("state", "TEXT", 1, 0), ("lease_owner", "TEXT", 0, 0),
    ("lease_boot_id", "INTEGER", 0, 0), ("lease_expires_tick", "REAL", 0, 0),
    ("decision", "TEXT", 0, 0), ("processed_input_sha256", "TEXT", 0, 0),
    ("response", "BLOB", 0, 0), ("created_at", "REAL", 1, 0), ("completed_at", "REAL", 0, 0),
)
LEGACY_RECEIVER_CREATE_SQL = """
CREATE TABLE receiver_admission (
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


def _receiver_create_statement(table: str) -> str:
    prefix = f"create table {table} "
    for statement in RECEIVER_CREATE_SQL.split(";"):
        if statement.strip().lower().startswith(prefix):
            return statement.strip()
    raise AssertionError(f"missing receiver schema statement for {table}")


class DurableReceiverAdmissionStore:
    """Stable operation admission plus durable single-use authenticated attempts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(self.path, timeout=5.0, isolation_level=None)) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > RECEIVER_SCHEMA_VERSION:
                raise AckReplayError("receiver admission schema is newer than this implementation")
            _configure_durable_sqlite(connection)
            exists = bool(_sqlite_table_info(connection, "receiver_admission"))
            if not exists:
                connection.execute("BEGIN EXCLUSIVE")
                for statement in RECEIVER_CREATE_SQL.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(f"PRAGMA user_version={RECEIVER_SCHEMA_VERSION}")
                self._attest(connection)
                connection.commit()
            elif version == RECEIVER_SCHEMA_VERSION:
                self._attest(connection)
            else:
                if version != 1:
                    raise AckReplayError("unrecognized legacy receiver schema version")
                _attest_table(
                    connection, "receiver_admission", LEGACY_RECEIVER_COLUMNS,
                    {("sender", "key_id", "message_id", "challenge"), ("admission_id",)},
                    LEGACY_RECEIVER_CREATE_SQL,
                )
                if connection.execute("SELECT 1 FROM receiver_admission LIMIT 1").fetchone():
                    raise AckReplayError("legacy receiver state requires governed offline migration")
                connection.execute("BEGIN EXCLUSIVE")
                connection.execute("DROP TABLE receiver_admission")
                for statement in RECEIVER_CREATE_SQL.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(f"PRAGMA user_version={RECEIVER_SCHEMA_VERSION}")
                self._attest(connection)
                connection.commit()
        _protect_evidence_path(self.path)

    @staticmethod
    def _attest(connection: sqlite3.Connection) -> None:
        _attest_table(
            connection, "receiver_admission", RECEIVER_COLUMNS,
            {("sender", "message_id", "challenge"), ("admission_id",)},
            _receiver_create_statement("receiver_admission"),
        )
        _attest_table(
            connection, "receiver_attempt", ATTEMPT_COLUMNS,
            {("attempt_nonce",), ("request_sha256",)}, _receiver_create_statement("receiver_attempt"),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        _configure_durable_sqlite(connection)
        return connection

    def admit(self, request: PeerAckRequest, raw: bytes) -> tuple[str, str, tuple[str, str] | None]:
        request_digest = hashlib.sha256(raw).hexdigest()
        operation = {
            "sender": request.sender, "receiver": request.receiver, "message_id": request.message_id,
            "challenge": request.challenge, "input_sha256": request.input_sha256,
        }
        operation_digest = hashlib.sha256(_canonical_bytes(operation)).hexdigest()
        key = (request.sender, request.message_id, request.challenge)
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO receiver_attempt VALUES (?, ?, ?, ?, ?)",
                    (request.attempt_nonce, request.sender, request.message_id, request_digest, time.time()),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise AckReplayError("receiver request attempt replay rejected") from exc
            # Attempt consumption is its own durable decision. A later binding
            # rejection or crash must never make this nonce reusable.
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT operation_sha256, admission_id, state, decision, processed_input_sha256 "
                "FROM receiver_admission WHERE sender=? AND message_id=? AND challenge=?", key,
            ).fetchone()
            if row is None:
                admission_id = secrets.token_hex(TOKEN_BYTES)
                connection.execute(
                    "INSERT INTO receiver_admission VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'admitted', NULL,NULL,NULL,NULL,NULL,?,NULL)",
                    (request.sender, request.receiver, request.message_id, request.challenge, request.input_sha256,
                     request.response_key_id, operation_digest, admission_id, time.time()),
                )
                connection.commit()
                return admission_id, "admitted", None
            if not hmac.compare_digest(str(row[0]), operation_digest):
                connection.commit()
                raise AckReplayError("receiver admission binding conflict")
            connection.commit()
            result = (str(row[3]), str(row[4])) if row[2] == "completed" else None
            return str(row[1]), str(row[2]), result

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

    def wait_completed(self, admission_id: str, deadline: float) -> tuple[str, str]:
        while time.monotonic() < deadline:
            with contextlib.closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT state, decision, processed_input_sha256 FROM receiver_admission WHERE admission_id=?", (admission_id,),
                ).fetchone()
            if row is not None and row[0] == "completed":
                return str(row[1]), str(row[2])
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        raise TimeoutError("receiver admission is already governed by another claimant")

    def complete(self, admission_id: str, owner: str, decision: str, digest: str) -> None:
        with contextlib.closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, decision, processed_input_sha256 FROM receiver_admission WHERE admission_id=?",
                (admission_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise AckReplayError("receiver admission disappeared")
            if row[0] == "completed":
                if row[1] != decision or row[2] != digest:
                    connection.rollback()
                    raise AckReplayError("receiver result conflict")
                connection.commit()
                return
            updated = connection.execute(
                "UPDATE receiver_admission SET state='completed', lease_owner=NULL, lease_boot_id=NULL, lease_expires_tick=NULL, "
                "decision=?, processed_input_sha256=?, completed_at=? "
                "WHERE admission_id=? AND lease_owner=? AND state='processing'",
                (decision, digest, time.time(), admission_id, owner),
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
    send_body: Callable[[Any, str, str, float], dict[str, Any]]
    focus: Callable[[int, float], dict[str, Any]]
    enter: Callable[[TargetIdentity, float], dict[str, Any]]
    receive_ack: Callable[[PeerAckRequest, float], bytes]
    finalize_ack: Callable[[PeerAck, bytes, Callable[[dict[str, Any]], None], dict[str, Any], dict[str, Any]], None]
    audit_append: Callable[..., dict[str, Any]]
    token_hex: Callable[[int], str]

    def __post_init__(self) -> None:
        fixed = (_production_send_body, _production_focus, _production_enter)
        if (self.send_body, self.focus, self.enter) != fixed:
            raise ValueError("physical authorities must be the fixed production native authority identities")


@dataclass(frozen=True)
class _InjectedTestAuthorities:
    """Private harness-only injection surface; the public path cannot construct this."""

    snapshot: Callable[[int], tuple[Any | None, TargetIdentity | None]]
    send_body: Callable[[Any, str, str, float], dict[str, Any]]
    focus: Callable[[int, float], dict[str, Any]]
    enter: Callable[[TargetIdentity, float], dict[str, Any]]
    receive_ack: Callable[[PeerAckRequest, float], bytes]
    finalize_ack: Callable[[PeerAck, bytes, Callable[[dict[str, Any]], None], dict[str, Any], dict[str, Any]], None]
    audit_append: Callable[..., dict[str, Any]]
    token_hex: Callable[[int], str]


def _production_send_body(window: Any, text: str, transport: str, deadline: float) -> dict[str, Any]:
    import self_connect as sc

    if time.monotonic() >= deadline:
        raise TimeoutError("body deadline expired before native transport")
    if window.class_name == sc.CONSOLE_HOST_CLASS:
        mode = "console"
    elif window.class_name == sc.WT_HOST_CLASS:
        mode = "postmessage"
    else:
        return {"ok": False, "error": "guarded_body_transport_class_denied"}
    if transport not in {"auto", mode}:
        return {"ok": False, "error": "guarded_body_transport_override_denied"}
    if mode == "console":
        if time.monotonic() >= deadline:
            raise TimeoutError("body deadline expired before console input")
        return sc.send_string(window, text, mode=mode, deadline=deadline)
    input_site = sc.find_child_by_class(window.hwnd, sc.WT_INPUT_CLASS)
    delivery_hwnd = input_site if input_site else window.hwnd
    accepted = 0
    for character in text:
        if time.monotonic() >= deadline:
            return {
                "ok": False, "transport": "postmessage_wm_char", "chars_requested": len(text),
                "chars_accepted": accepted, "delivery_verified": False, "error": "body_deadline_expired",
            }
        if not sc._send_char_postmessage(delivery_hwnd, character):
            return {
                "ok": False, "transport": "postmessage_wm_char", "chars_requested": len(text),
                "chars_accepted": accepted, "delivery_verified": False, "error": "postmessage_queue_rejected",
            }
        accepted += 1
    return {
        "ok": True, "transport": "postmessage_wm_char", "chars_requested": len(text),
        "chars_accepted": accepted, "delivery_verified": False, "delivery_hwnd": int(delivery_hwnd),
    }


def _production_focus(hwnd: int, deadline: float) -> dict[str, Any]:
    import self_connect as sc

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("focus deadline expired before native focus")
    return sc.focus_window_checked(hwnd, settle_seconds=min(0.2, remaining), deadline=deadline)


def _production_enter(target: TargetIdentity, deadline: float) -> dict[str, Any]:
    import self_connect as sc

    if time.monotonic() >= deadline:
        raise TimeoutError("Enter deadline expired before native SendInput")
    return sc.hardware_enter_checked(target.hwnd, expected_identity=asdict(target), deadline=deadline)


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
    advapi32.ImpersonateNamedPipeClient.restype = wintypes.BOOL
    advapi32.ImpersonateNamedPipeClient.argtypes = [wintypes.HANDLE]
    advapi32.RevertToSelf.restype = wintypes.BOOL
    advapi32.RevertToSelf.argtypes = []
    advapi32.SetFileSecurityW.restype = wintypes.BOOL
    advapi32.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]


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
            self.keyring.resolve(request.response_key_id)
            admission_id, state, durable_result = self.admissions.admit(request, raw_request)

            def receipt(result: tuple[str, str]) -> bytes:
                decision, processed_digest = result
                return sign_peer_ack(
                    keyring=self.keyring, key_id=request.response_key_id,
                    message_id=request.message_id, challenge=request.challenge,
                    attempt_nonce=request.attempt_nonce, ack_nonce=secrets.token_hex(TOKEN_BYTES),
                    input_sha256=request.input_sha256, processed_input_sha256=processed_digest,
                    sender=request.receiver, receiver=request.sender, decision=decision,
                )

            if state == "completed":
                _write_all(handle, receipt(durable_result or ("rejected", request.input_sha256)), deadline)
                _read_pipe_confirmation(handle, deadline)
                return
            lease_owner = self.admissions.claim(admission_id, deadline)
            if lease_owner is None:
                durable_result = self.admissions.wait_completed(admission_id, deadline)
                _write_all(handle, receipt(durable_result), deadline)
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
            self.admissions.complete(admission_id, lease_owner, decision, processed_input_sha256)
            _write_all(handle, receipt((decision, processed_input_sha256)), deadline)
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
    with _PIPE_API_LOCK:
        if _OVERLAPPED is None:
            _configure_pipe_api_locked()


def _configure_pipe_api_locked() -> None:
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
    global _OVERLAPPED
    _OVERLAPPED = OVERLAPPED
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


def _overlapped_call(
    handle: int, deadline: float, start: Callable[[Any, Any], int], operation: str,
    keepalive: tuple[Any, ...] = (),
) -> int:
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
            _retain_cancelled_io(handle, overlapped, event, start, keepalive)
            retained = True
            raise TimeoutError(f"peer acknowledgement {operation} deadline expired")
        if not kernel32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(count), False):
            raise OSError(f"named-pipe {operation} completion failed ({int(kernel32.GetLastError())})")
        return int(count.value)
    finally:
        if not retained:
            kernel32.CloseHandle(event)


def _retain_cancelled_io(handle: int, overlapped: Any, event: int, start: Any, keepalive: tuple[Any, ...]) -> None:
    """Retain OVERLAPPED, closure, and native buffers until observed completion."""
    item = (handle, overlapped, event, start, keepalive)
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
        "write", (buffer, data),
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
            "read", (buffer,),
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
        "confirmation read", (buffer,),
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
    authorities: _Authorities | _InjectedTestAuthorities,
    transport: str,
    ack_timeout: float,
    max_ack_age_seconds: float,
    response_key_id: str | None = None,
) -> dict[str, Any]:
    encoded_input = _validate_input(text)
    timeout = _validate_duration(ack_timeout, "ack_timeout", MAX_ACK_AGE_SECONDS)
    operation_deadline = time.monotonic() + timeout
    max_age = _validate_duration(max_ack_age_seconds, "max_ack_age_seconds", MAX_ACK_AGE_SECONDS)
    keyring.resolve(key_id)
    response_key = key_id if response_key_id is None else response_key_id
    keyring.resolve(response_key)
    if not sender or not receiver or sender == receiver:
        raise ValueError("distinct sender and receiver are required")
    input_sha256 = hashlib.sha256(encoded_input).hexdigest()
    message_id = uuid.UUID(bytes=bytes.fromhex(authorities.token_hex(16))).hex
    challenge = authorities.token_hex(TOKEN_BYTES)
    _validate_hex(challenge, "challenge", TOKEN_BYTES)
    base = {
        "message_id": message_id, "challenge": challenge, "key_id": key_id, "response_key_id": response_key,
        "input_sha256": input_sha256, "input_bytes": len(encoded_input),
        "sender": sender, "receiver": receiver, "target": asdict(target),
    }
    body_evidence: dict[str, Any] = {}

    def audit_spec(event_type: str, status: str, *, idempotency_key: str = "", **data: Any) -> dict[str, Any]:
        return {
            "event_type": event_type, "status": status, "hwnd": target.hwnd,
            "summary": f"guarded submit {status}", "data": {**base, **data},
            "strict_idempotency_key": idempotency_key,
        }

    def append_audit(spec: dict[str, Any]):
        return authorities.audit_append(event_log_path=event_log_path, strict=True, **spec)

    def audit(event_type: str, status: str, *, idempotency_key: str = "", **data: Any):
        return append_audit(audit_spec(event_type, status, idempotency_key=idempotency_key, **data))

    def expired(stage: str, *, ambiguous: bool = False, **extra: Any):
        if time.monotonic() < operation_deadline:
            return None
        state = "ambiguous" if ambiguous or body_evidence else "refused"
        return audit_failure(state, f"operation_deadline_expired_{stage}", **extra)

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
    if deadline_result := expired("before_prepare"):
        return deadline_result
    try:
        audit("guarded_submit_prepared", "prepared", guard=first_guard)
    except Exception as exc:
        return outcome("refused", f"audit_prepare_failed:{type(exc).__name__}", guard=first_guard)
    if deadline_result := expired("before_body"):
        return deadline_result
    try:
        body = authorities.send_body(first_window, text, transport, operation_deadline)
    except Exception as exc:
        body_evidence.update({"chars_requested": len(text), "chars_accepted": "unknown"})
        return audit_failure("ambiguous", f"body_staged_exception:{type(exc).__name__}", chars_accepted="unknown")
    body_evidence.update({
        "body": body,
        "chars_requested": body.get("chars_requested") if isinstance(body, dict) else len(text),
        "chars_accepted": body.get("chars_accepted") if isinstance(body, dict) else "unknown",
    })
    if deadline_result := expired("after_body", ambiguous=True):
        return deadline_result
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
    if deadline_result := expired("before_focus", ambiguous=True):
        return deadline_result
    try:
        focus_result = authorities.focus(target.hwnd, operation_deadline)
    except Exception as exc:
        return audit_failure("ambiguous", f"body_staged_focus_exception:{type(exc).__name__}", body=body)
    if not isinstance(focus_result, dict) or focus_result.get("ok") is not True:
        return audit_failure("ambiguous", "body_staged_focus_failed", focus=focus_result, body=body)
    if deadline_result := expired("after_focus", ambiguous=True):
        return deadline_result
    try:
        _, enter_guard = _guard(target, authorities.snapshot, "immediately_before_hardware_enter")
    except Exception as exc:
        return audit_failure("ambiguous", f"body_staged_target_guard_exception_before_enter:{type(exc).__name__}", body=body)
    if not enter_guard["ok"]:
        return audit_failure("ambiguous", "body_staged_target_guard_failed_before_hardware_enter", guard=enter_guard, body=body)
    if deadline_result := expired("before_enter", ambiguous=True):
        return deadline_result
    try:
        enter_result = authorities.enter(target, operation_deadline)
    except Exception as exc:
        return audit_failure("ambiguous", f"hardware_enter_exception:{type(exc).__name__}", body=body)
    if deadline_result := expired("after_enter", ambiguous=True, enter=enter_result):
        return deadline_result
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
        receiver=receiver, issued_at=time.time(), response_key_id=response_key,
        attempt_nonce=authorities.token_hex(TOKEN_BYTES),
    )
    try:
        remaining = operation_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("operation deadline expired before peer ACK")
        raw_ack = authorities.receive_ack(request, remaining)
        if time.monotonic() >= operation_deadline:
            raise TimeoutError("peer ACK total deadline expired before authentication")
        ack = verify_peer_ack(
            raw_ack, keyring=keyring, key_id=response_key, message_id=message_id,
            challenge=challenge, attempt_nonce=request.attempt_nonce,
            input_sha256=input_sha256, sender=receiver,
            receiver=sender, max_age_seconds=max_age,
        )
        if time.monotonic() >= operation_deadline:
            raise TimeoutError("peer ACK total deadline expired during authentication")
        ack_data = {
            "ack": {
                "schema": ack.schema, "key_id": ack.key_id,
                "challenge": ack.challenge, "ack_nonce": ack.ack_nonce,
                "attempt_nonce": ack.attempt_nonce,
                "processed_input_sha256": ack.processed_input_sha256,
                "sender": ack.sender, "receiver": ack.receiver,
                "decision": ack.decision, "issued_at": ack.issued_at,
                "ack_sha256": hashlib.sha256(raw_ack).hexdigest(),
            }
        }

        ack_event = _canonical_ack_event(ack, raw_ack, base)
        authorities.finalize_ack(ack, raw_ack, append_audit, ack_event, base)
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
    response_key_id: str | None = None,
    transport: str = "auto",
    ack_timeout: float = 10.0,
    max_ack_age_seconds: float = DEFAULT_ACK_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Run the fixed-authority candidate guarded-submit transaction."""
    client = RawJsonNamedPipeClient(ack_pipe, keyring)
    _protect_evidence_path(Path(event_log_path))
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
        keyring=keyring, key_id=key_id, response_key_id=response_key_id, event_log_path=event_log_path,
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
