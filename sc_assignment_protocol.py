"""Authenticated, durable coordinator-to-seat assignment protocol.

Screen text, window visibility, registry rows, and caller-supplied copies of an
assignment payload are never authority.  A worker acts only after consuming a
coordinator-signed assignment which embeds the bounded payload and binds the
authority-enrolled seat, exact target, exact TerminalTab, and response channel.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from sc_guarded_submit import TargetIdentity
from sc_identity import AgentIdentity
from sc_seat_identity import key_id, verify_enrollment
from sc_terminal_tab import TerminalTabIdentity

ASSIGNMENT_SCHEMA = "selfconnect-assignment-v2"
RECEIPT_SCHEMA = "selfconnect-assignment-state-receipt-v2"
ACK_SCHEMA = "selfconnect-assignment-receipt-ack-v1"
MAX_ASSIGNMENT_TTL_SECONDS = 300.0
MAX_RECEIPT_TTL_SECONDS = 60.0
MAX_ACK_TTL_SECONDS = 60.0
MAX_CLOCK_SKEW_SECONDS = 5.0
MAX_PAYLOAD_BYTES = 16_384
MAX_DETAIL_BYTES = 4_096
MAX_IDENTITY_BYTES = 8_192
ASSIGNMENT_STATES = frozenset({"accepted", "working", "blocked", "completed", "rejected"})
TERMINAL_STATES = frozenset({"completed", "rejected"})
_SAFE_ID = __import__("re").compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_TRANSITIONS = {
    None: frozenset({"accepted"}),
    "accepted": frozenset({"working", "blocked", "rejected"}),
    "working": frozenset({"working", "blocked", "completed", "rejected"}),
    "blocked": frozenset({"blocked", "working", "rejected"}),
    "completed": frozenset(),
    "rejected": frozenset(),
}
_ZERO_HASH = "0" * 64


class AssignmentVerificationError(ValueError):
    """An authenticated assignment record failed verification."""


class AssignmentReplayError(AssignmentVerificationError):
    """A durable replay, fork, restart, or ordering violation occurred."""


def _reject_constant(value: str) -> None:
    raise AssignmentVerificationError(f"non-finite JSON number {value!r} is forbidden")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise AssignmentVerificationError(f"duplicate JSON member {name!r}")
        result[name] = value
    return result


def _json_loads(raw: str, label: str) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except AssignmentVerificationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AssignmentVerificationError(f"{label} JSON is invalid") from exc


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, RuntimeError) as exc:
        raise AssignmentVerificationError("record is not stable canonical JSON") from exc


def _snapshot(value: Any, label: str, *, require_object: bool = True) -> Any:
    """Return one immutable-by-convention JSON snapshot before verification.

    Encoded JSON is parsed with duplicate-name rejection.  In-memory values are
    canonicalized once, then parsed back, so later mutation of caller mappings
    cannot change the value which was verified or committed.
    """
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AssignmentVerificationError(f"{label} is not UTF-8 JSON") from exc
    if isinstance(value, str):
        snap = _json_loads(value, label)
    else:
        snap = _json_loads(_canonical(value).decode("ascii"), label)
    if require_object and type(snap) is not dict:
        raise AssignmentVerificationError(f"{label} must be a JSON object")
    return snap


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _record_hash(record: Any, label: str) -> tuple[dict[str, Any], str]:
    snap = _snapshot(record, label)
    return snap, _sha256(_canonical(snap))


def _safe_id(value: Any, name: str) -> str:
    if type(value) is not str or _SAFE_ID.fullmatch(value) is None:
        raise AssignmentVerificationError(f"invalid {name}")
    return value


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise AssignmentVerificationError(f"invalid {name}")
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssignmentVerificationError(f"invalid {name}")
    result = float(value)
    if not math.isfinite(result):
        raise AssignmentVerificationError(f"invalid {name}")
    return result


def _hex(value: Any, name: str, size: int = 32) -> str:
    if type(value) is not str or len(value) != size * 2 or value != value.lower():
        raise AssignmentVerificationError(f"invalid {name}")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise AssignmentVerificationError(f"invalid {name}") from exc
    if len(raw) != size:
        raise AssignmentVerificationError(f"invalid {name}")
    return value


def _public_key(value: Any, name: str) -> str:
    return _hex(value, name, 32)


def _bounded_text(value: Any, name: str, limit: int) -> str:
    if type(value) is not str or not value:
        raise AssignmentVerificationError(f"invalid {name}")
    if unicodedata.normalize("NFC", value) != value:
        raise AssignmentVerificationError(f"{name} must use NFC-normalized Unicode")
    for character in value:
        category = unicodedata.category(character)
        if category.startswith("C") or category in {"Zl", "Zp"}:
            raise AssignmentVerificationError(f"{name} contains a forbidden control character")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AssignmentVerificationError(f"invalid {name}") from exc
    if len(encoded) > limit:
        raise AssignmentVerificationError(f"{name} exceeds {limit} UTF-8 bytes")
    return value


def _bounded_detail(value: Any) -> tuple[dict[str, Any], str]:
    detail = _snapshot(value, "receipt detail")

    def validate_text(node: Any, location: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                _bounded_text(key, f"{location} key", MAX_DETAIL_BYTES)
                validate_text(child, f"{location}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                validate_text(child, f"{location}[{index}]")
        elif isinstance(node, str):
            _bounded_text(node, location, MAX_DETAIL_BYTES)

    validate_text(detail, "receipt detail")
    raw = _canonical(detail)
    if len(raw) > MAX_DETAIL_BYTES:
        raise AssignmentVerificationError("receipt detail exceeds the bounded size")
    return detail, _sha256(raw)


def _identity_document(value: Any, label: str, expected_type: type[Any]) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    document = _snapshot(value, label)
    if len(_canonical(document)) > MAX_IDENTITY_BYTES:
        raise AssignmentVerificationError(f"{label} exceeds the bounded size")
    try:
        expected_type(**document)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise AssignmentVerificationError(f"{label} schema is invalid") from exc
    return document


def _identity_bindings(
    target_identity: Any, terminal_tab_identity: Any
) -> tuple[str, str, dict[str, Any]]:
    target = _identity_document(target_identity, "target identity", TargetIdentity)
    tab = _identity_document(terminal_tab_identity, "TerminalTab identity", TerminalTabIdentity)
    exact = (
        (tab["window_hwnd"], target["hwnd"]),
        (tab["window_pid"], target["pid"]),
        (tab["window_process_start_time_ns"], target["process_start_time_ns"]),
    )
    if any(left != right for left, right in exact):
        raise AssignmentVerificationError("TerminalTab identity targets a different terminal")
    return _sha256(_canonical(target)), _sha256(_canonical(tab)), tab


def _channel_hash(value: Any) -> str:
    channel = _snapshot(value, "response channel binding")
    raw = _canonical(channel)
    if not channel or len(raw) > MAX_IDENTITY_BYTES:
        raise AssignmentVerificationError("response channel binding is invalid")
    return _sha256(raw)


def _signed(body: dict[str, Any], identity: Any) -> dict[str, Any]:
    # Signing APIs receive exactly one role identity.  Coordinator and seat
    # private keys therefore never coexist in a signing call.
    frozen = _snapshot(body, "record body")
    signature = identity.sign(_canonical(frozen))
    return {**frozen, "signature_b64": base64.b64encode(signature).decode("ascii")}


def _verified_body(record: Any, public_key_hex: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    snap = _snapshot(record, label)
    body = dict(snap)
    signature = body.pop("signature_b64", None)
    if type(signature) is not str:
        raise AssignmentVerificationError(f"{label} signature is invalid")
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
    except (ValueError, TypeError) as exc:
        raise AssignmentVerificationError(f"{label} signature is invalid") from exc
    if not AgentIdentity.verify_with_pubkey_hex(
        _public_key(public_key_hex, f"{label} public key"),
        _canonical(body),
        signature_bytes,
    ):
        raise AssignmentVerificationError(f"{label} signature is invalid")
    return snap, body


def _verified_enrollment(enrollment: Any, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    snap = _snapshot(enrollment, "receiver enrollment")
    try:
        body = verify_enrollment(snap, **kwargs)
    except (TypeError, ValueError) as exc:
        raise AssignmentVerificationError(
            f"receiver enrollment verification failed: {exc}"
        ) from exc
    _safe_id(body.get("birth_id"), "enrollment birth_id")
    _positive_int(body.get("generation"), "enrollment generation")
    _hex(body.get("seat_epoch"), "enrollment seat_epoch")
    _hex(body.get("seat_key_id"), "enrollment seat key id")
    _public_key(body.get("seat_public_key_hex"), "enrollment seat public key")
    _hex(body.get("authority_key_id"), "enrollment authority key id")
    issued = _finite(body.get("issued_at"), "enrollment issued_at")
    expires = _finite(body.get("expires_at"), "enrollment expires_at")
    if expires <= issued:
        raise AssignmentVerificationError("receiver enrollment validity interval is invalid")
    return snap, body


class AssignmentStateStore:
    """SQLite consume/retry ledger with exact signed-record snapshots."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS assignment_v2 (
                    kind TEXT NOT NULL CHECK(kind IN ('issued','consumed')),
                    assignment_id TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    record_json BLOB NOT NULL,
                    recorded_at REAL NOT NULL,
                    PRIMARY KEY(kind, assignment_id),
                    UNIQUE(kind, record_sha256)
                );
                CREATE TABLE IF NOT EXISTS receipt_v2 (
                    kind TEXT NOT NULL CHECK(kind IN ('emitted','consumed')),
                    assignment_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    state TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    prior_receipt_sha256 TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    record_json BLOB NOT NULL,
                    detail_sha256 TEXT NOT NULL,
                    result_sha256 TEXT,
                    commit_sha256 TEXT,
                    recorded_at REAL NOT NULL,
                    PRIMARY KEY(kind, assignment_id, sequence),
                    UNIQUE(kind, assignment_id, idempotency_key),
                    UNIQUE(kind, nonce),
                    UNIQUE(kind, record_sha256)
                );
                CREATE TABLE IF NOT EXISTS receipt_ack_v1 (
                    kind TEXT NOT NULL CHECK(kind IN ('emitted','consumed')),
                    receipt_sha256 TEXT NOT NULL,
                    assignment_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    ack_sha256 TEXT NOT NULL,
                    ack_json BLOB NOT NULL,
                    recorded_at REAL NOT NULL,
                    PRIMARY KEY(kind, receipt_sha256),
                    UNIQUE(kind, ack_sha256)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _decode(blob: Any, label: str) -> dict[str, Any]:
        if not isinstance(blob, bytes):
            blob = bytes(blob)
        return _snapshot(blob, label)

    def record_assignment(self, kind: str, assignment: Any) -> dict[str, Any]:
        if kind not in {"issued", "consumed"}:
            raise ValueError("invalid assignment store kind")
        snap, digest = _record_hash(assignment, "assignment")
        assignment_id = _safe_id(snap.get("assignment_id"), "assignment_id")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO assignment_v2 VALUES (?,?,?,?,?)",
                    (kind, assignment_id, digest, _canonical(snap), time.time()),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise AssignmentReplayError(f"assignment {kind} replay rejected") from exc
        return snap

    def require_assignment(self, kind: str, assignment: Any) -> dict[str, Any]:
        snap, digest = _record_hash(assignment, "assignment")
        assignment_id = _safe_id(snap.get("assignment_id"), "assignment_id")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT record_sha256, record_json FROM assignment_v2 "
                "WHERE kind=? AND assignment_id=?",
                (kind, assignment_id),
            ).fetchone()
        if row is None or not secrets.compare_digest(str(row[0]), digest):
            raise AssignmentVerificationError(f"assignment is not durably {kind}")
        stored = self._decode(row[1], "stored assignment")
        if not secrets.compare_digest(_sha256(_canonical(stored)), digest):
            raise AssignmentVerificationError("stored assignment integrity failed")
        return stored

    def emit_receipt(
        self,
        *,
        assignment: dict[str, Any],
        idempotency_key: str,
        state: str,
        detail_sha256: str,
        result_sha256: str | None,
        builder: Callable[[int, str], dict[str, Any]],
    ) -> dict[str, Any]:
        assignment_id = _safe_id(assignment.get("assignment_id"), "assignment_id")
        idem = _safe_id(idempotency_key, "receipt idempotency_key")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT state,detail_sha256,result_sha256,record_json FROM receipt_v2 "
                "WHERE kind='emitted' AND assignment_id=? AND idempotency_key=?",
                (assignment_id, idem),
            ).fetchone()
            if existing is not None:
                if (existing[0], existing[1], existing[2]) != (
                    state,
                    detail_sha256,
                    result_sha256,
                ):
                    connection.rollback()
                    raise AssignmentReplayError("receipt retry key was reused with different content")
                record = self._decode(existing[3], "stored emitted receipt")
                connection.commit()
                return record
            latest = connection.execute(
                "SELECT sequence,state,record_sha256 FROM receipt_v2 "
                "WHERE kind='emitted' AND assignment_id=? ORDER BY sequence DESC LIMIT 1",
                (assignment_id,),
            ).fetchone()
            sequence = 1 if latest is None else int(latest[0]) + 1
            previous_state = None if latest is None else str(latest[1])
            prior_hash = _ZERO_HASH if latest is None else str(latest[2])
            if state not in _TRANSITIONS[previous_state]:
                connection.rollback()
                raise AssignmentReplayError("receipt state transition is invalid")
            receipt = _snapshot(builder(sequence, prior_hash), "emitted receipt")
            digest = _sha256(_canonical(receipt))
            try:
                connection.execute(
                    "INSERT INTO receipt_v2 VALUES "
                    "('emitted',?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        assignment_id,
                        sequence,
                        state,
                        idem,
                        receipt["nonce"],
                        prior_hash,
                        digest,
                        _canonical(receipt),
                        detail_sha256,
                        result_sha256,
                        None,
                        time.time(),
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise AssignmentReplayError("receipt emission fork or replay rejected") from exc
        return receipt

    def consume_receipt(self, receipt: dict[str, Any]) -> tuple[bool, str]:
        assignment_id = _safe_id(receipt.get("assignment_id"), "assignment_id")
        sequence = _positive_int(receipt.get("sequence"), "receipt sequence")
        state = receipt.get("state")
        if state not in ASSIGNMENT_STATES:
            raise AssignmentVerificationError("invalid receipt state")
        digest = _sha256(_canonical(receipt))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            same_sequence = connection.execute(
                "SELECT record_sha256,commit_sha256 FROM receipt_v2 "
                "WHERE kind='consumed' AND assignment_id=? AND sequence=?",
                (assignment_id, sequence),
            ).fetchone()
            if same_sequence is not None:
                if secrets.compare_digest(str(same_sequence[0]), digest):
                    connection.commit()
                    return False, str(same_sequence[1])
                connection.rollback()
                raise AssignmentReplayError("receipt sequence fork rejected")
            latest = connection.execute(
                "SELECT sequence,state,record_sha256 FROM receipt_v2 "
                "WHERE kind='consumed' AND assignment_id=? ORDER BY sequence DESC LIMIT 1",
                (assignment_id,),
            ).fetchone()
            expected_sequence = 1 if latest is None else int(latest[0]) + 1
            if sequence < expected_sequence:
                connection.rollback()
                raise AssignmentReplayError("receipt replay or backward sequence rejected")
            if sequence > expected_sequence:
                connection.rollback()
                raise AssignmentReplayError("receipt sequence gap rejected")
            assignment = connection.execute(
                "SELECT record_sha256 FROM assignment_v2 WHERE kind='issued' AND assignment_id=?",
                (assignment_id,),
            ).fetchone()
            if assignment is None:
                connection.rollback()
                raise AssignmentVerificationError("assignment is not durably issued")
            prior_hash = _ZERO_HASH if latest is None else str(latest[2])
            if not secrets.compare_digest(receipt["prior_receipt_sha256"], prior_hash):
                connection.rollback()
                raise AssignmentReplayError("receipt prior hash fork rejected")
            previous_state = None if latest is None else str(latest[1])
            if state not in _TRANSITIONS[previous_state]:
                connection.rollback()
                raise AssignmentReplayError("receipt state transition is invalid")
            commit_hash = _sha256(
                _canonical(
                    {
                        "schema": "selfconnect-receipt-commit-v1",
                        "assignment_id": assignment_id,
                        "sequence": sequence,
                        "receipt_sha256": digest,
                        "commit_nonce": secrets.token_hex(32),
                    }
                )
            )
            try:
                connection.execute(
                    "INSERT INTO receipt_v2 VALUES "
                    "('consumed',?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        assignment_id,
                        sequence,
                        state,
                        receipt["idempotency_key"],
                        receipt["nonce"],
                        prior_hash,
                        digest,
                        _canonical(receipt),
                        receipt["detail_sha256"],
                        receipt["result_sha256"],
                        commit_hash,
                        time.time(),
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise AssignmentReplayError("receipt replay rejected") from exc
        return True, commit_hash

    def require_receipt(self, kind: str, receipt: Any) -> tuple[dict[str, Any], str | None]:
        if kind not in {"emitted", "consumed"}:
            raise ValueError("invalid receipt store kind")
        snap, digest = _record_hash(receipt, "receipt")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT record_sha256,record_json,commit_sha256 FROM receipt_v2 "
                "WHERE kind=? AND assignment_id=? AND sequence=?",
                (kind, snap.get("assignment_id"), snap.get("sequence")),
            ).fetchone()
        if row is None or not secrets.compare_digest(str(row[0]), digest):
            raise AssignmentVerificationError(f"receipt is not durably {kind}")
        stored = self._decode(row[1], "stored receipt")
        if _sha256(_canonical(stored)) != digest:
            raise AssignmentVerificationError("stored receipt integrity failed")
        return stored, None if row[2] is None else str(row[2])

    def emit_ack(
        self,
        receipt: dict[str, Any],
        builder: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        digest = _sha256(_canonical(receipt))
        assignment_id = receipt["assignment_id"]
        sequence = receipt["sequence"]
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            committed = connection.execute(
                "SELECT commit_sha256 FROM receipt_v2 WHERE kind='consumed' "
                "AND assignment_id=? AND sequence=? AND record_sha256=?",
                (assignment_id, sequence, digest),
            ).fetchone()
            if committed is None or committed[0] is None:
                connection.rollback()
                raise AssignmentVerificationError("receipt was not committed before ACK")
            existing = connection.execute(
                "SELECT ack_json FROM receipt_ack_v1 WHERE kind='emitted' AND receipt_sha256=?",
                (digest,),
            ).fetchone()
            if existing is not None:
                ack = self._decode(existing[0], "stored receipt ACK")
                connection.commit()
                return ack
            ack = _snapshot(builder(str(committed[0])), "receipt ACK")
            ack_hash = _sha256(_canonical(ack))
            try:
                connection.execute(
                    "INSERT INTO receipt_ack_v1 VALUES ('emitted',?,?,?,?,?,?)",
                    (digest, assignment_id, sequence, ack_hash, _canonical(ack), time.time()),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise AssignmentReplayError("receipt ACK fork rejected") from exc
        return ack

    def consume_ack(self, ack: dict[str, Any]) -> None:
        digest = _sha256(_canonical(ack))
        receipt_hash = ack["receipt_sha256"]
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            emitted = connection.execute(
                "SELECT record_sha256 FROM receipt_v2 WHERE kind='emitted' "
                "AND assignment_id=? AND sequence=?",
                (ack["assignment_id"], ack["sequence"]),
            ).fetchone()
            if emitted is None or not secrets.compare_digest(str(emitted[0]), receipt_hash):
                connection.rollback()
                raise AssignmentVerificationError("ACK does not name a durably emitted receipt")
            try:
                connection.execute(
                    "INSERT INTO receipt_ack_v1 VALUES ('consumed',?,?,?,?,?,?)",
                    (
                        receipt_hash,
                        ack["assignment_id"],
                        ack["sequence"],
                        digest,
                        _canonical(ack),
                        time.time(),
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise AssignmentReplayError("receipt ACK replay rejected") from exc


_ASSIGNMENT_FIELDS = {
    "schema",
    "assignment_id",
    "payload",
    "payload_sha256",
    "coordinator_birth_id",
    "coordinator_generation",
    "coordinator_key_id",
    "receiver_birth_id",
    "receiver_generation",
    "receiver_key_id",
    "receiver_seat_epoch",
    "receiver_enrollment",
    "receiver_enrollment_sha256",
    "target_identity_sha256",
    "terminal_tab_identity_sha256",
    "response_receiver_key_id",
    "response_channel_sha256",
    "nonce",
    "issued_at",
    "expires_at",
}


def issue_assignment(
    payload: str,
    *,
    coordinator_identity: Any,
    coordinator_birth_id: str,
    coordinator_generation: int,
    receiver_enrollment: Any,
    authority_public_key_hex: str,
    target_identity: Any,
    terminal_tab_identity: Any,
    response_receiver_public_key_hex: str,
    response_channel: Any,
    store: AssignmentStateStore,
    revoked_coordinator_key_ids: frozenset[str] = frozenset(),
    revoked_seat_key_ids: frozenset[str] = frozenset(),
    now: float | None = None,
    ttl_seconds: float = 60.0,
) -> dict[str, Any]:
    """Issue and durably record one coordinator-signed inline assignment."""
    payload = _bounded_text(payload, "assignment payload", MAX_PAYLOAD_BYTES)
    ttl = _finite(ttl_seconds, "assignment TTL")
    if not 0 < ttl <= MAX_ASSIGNMENT_TTL_SECONDS:
        raise AssignmentVerificationError("assignment TTL is invalid")
    issued = time.time() if now is None else _finite(now, "assignment time")
    enrollment, enrolled = _verified_enrollment(
        receiver_enrollment,
        authority_public_key_hex=_public_key(authority_public_key_hex, "authority public key"),
        revoked_key_ids=revoked_seat_key_ids,
        now=issued,
    )
    coordinator_key = _public_key(
        str(coordinator_identity.public_key_hex), "coordinator public key"
    )
    coordinator_id = key_id(coordinator_key)
    if coordinator_id in revoked_coordinator_key_ids:
        raise AssignmentVerificationError("coordinator key is revoked")
    if coordinator_id == enrolled["seat_key_id"]:
        raise AssignmentVerificationError("coordinator and receiver keys must be distinct")
    expires = issued + ttl
    if expires * 1000 > _finite(enrolled["expires_at"], "enrollment expires_at"):
        raise AssignmentVerificationError("assignment outlives the receiver enrollment")
    target_hash, tab_hash, tab = _identity_bindings(
        target_identity, terminal_tab_identity
    )
    if tab["peer_birth_id"] != enrolled["birth_id"]:
        raise AssignmentVerificationError("TerminalTab peer birth_id is not the enrolled seat")
    receiver_key = _public_key(
        response_receiver_public_key_hex, "response receiver public key"
    )
    body = {
        "schema": ASSIGNMENT_SCHEMA,
        "assignment_id": secrets.token_hex(16),
        "payload": payload,
        "payload_sha256": _sha256(payload.encode("utf-8")),
        "coordinator_birth_id": _safe_id(coordinator_birth_id, "coordinator birth_id"),
        "coordinator_generation": _positive_int(
            coordinator_generation, "coordinator generation"
        ),
        "coordinator_key_id": coordinator_id,
        "receiver_birth_id": enrolled["birth_id"],
        "receiver_generation": enrolled["generation"],
        "receiver_key_id": enrolled["seat_key_id"],
        "receiver_seat_epoch": enrolled["seat_epoch"],
        "receiver_enrollment": enrollment,
        "receiver_enrollment_sha256": _sha256(_canonical(enrollment)),
        "target_identity_sha256": target_hash,
        "terminal_tab_identity_sha256": tab_hash,
        "response_receiver_key_id": key_id(receiver_key),
        "response_channel_sha256": _channel_hash(response_channel),
        "nonce": secrets.token_hex(32),
        "issued_at": issued,
        "expires_at": expires,
    }
    assignment = _signed(body, coordinator_identity)
    return store.record_assignment("issued", assignment)


def _verify_assignment(
    assignment: Any,
    *,
    pinned_coordinator_public_key_hex: str,
    authority_public_key_hex: str,
    expected_coordinator_birth_id: str,
    expected_coordinator_generation: int,
    expected_receiver_birth_id: str,
    expected_receiver_generation: int,
    expected_target_identity: Any,
    expected_terminal_tab_identity: Any,
    expected_response_receiver_public_key_hex: str,
    expected_response_channel: Any,
    revoked_coordinator_key_ids: frozenset[str],
    revoked_seat_key_ids: frozenset[str],
    now: float | None,
    enforce_assignment_freshness: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    coordinator_key = _public_key(
        pinned_coordinator_public_key_hex, "pinned coordinator public key"
    )
    snap, body = _verified_body(assignment, coordinator_key, "assignment")
    if set(body) != _ASSIGNMENT_FIELDS or body.get("schema") != ASSIGNMENT_SCHEMA:
        raise AssignmentVerificationError("assignment fields are invalid")
    coordinator_id = key_id(coordinator_key)
    if coordinator_id in revoked_coordinator_key_ids:
        raise AssignmentVerificationError("coordinator key is revoked")
    if body["coordinator_key_id"] != coordinator_id:
        raise AssignmentVerificationError("assignment coordinator key is not pinned")
    if body["coordinator_birth_id"] != _safe_id(
        expected_coordinator_birth_id, "expected coordinator birth_id"
    ):
        raise AssignmentVerificationError("assignment coordinator birth_id mismatch")
    if body["coordinator_generation"] != _positive_int(
        expected_coordinator_generation, "expected coordinator generation"
    ):
        raise AssignmentVerificationError("assignment coordinator generation mismatch")
    current = time.time() if now is None else _finite(now, "verification time")
    enrollment, enrolled = _verified_enrollment(
        body["receiver_enrollment"],
        authority_public_key_hex=_public_key(authority_public_key_hex, "authority public key"),
        revoked_key_ids=revoked_seat_key_ids,
        now=current,
    )
    enrollment_hash = _sha256(_canonical(enrollment))
    exact_enrollment = {
        "receiver_birth_id": enrolled["birth_id"],
        "receiver_generation": enrolled["generation"],
        "receiver_key_id": enrolled["seat_key_id"],
        "receiver_seat_epoch": enrolled["seat_epoch"],
        "receiver_enrollment_sha256": enrollment_hash,
    }
    if any(body.get(field) != value for field, value in exact_enrollment.items()):
        raise AssignmentVerificationError("assignment receiver enrollment binding mismatch")
    if body["receiver_birth_id"] != _safe_id(
        expected_receiver_birth_id, "expected receiver birth_id"
    ) or body["receiver_generation"] != _positive_int(
        expected_receiver_generation, "expected receiver generation"
    ):
        raise AssignmentVerificationError("assignment receiver seat identity mismatch")
    if coordinator_id == body["receiver_key_id"]:
        raise AssignmentVerificationError("coordinator and receiver keys are not distinct")
    target_hash, tab_hash, tab = _identity_bindings(
        expected_target_identity, expected_terminal_tab_identity
    )
    if tab["peer_birth_id"] != body["receiver_birth_id"]:
        raise AssignmentVerificationError("TerminalTab peer birth_id mismatch")
    expected_bindings = {
        "target_identity_sha256": target_hash,
        "terminal_tab_identity_sha256": tab_hash,
        "response_receiver_key_id": key_id(
            _public_key(
                expected_response_receiver_public_key_hex,
                "expected response receiver public key",
            )
        ),
        "response_channel_sha256": _channel_hash(expected_response_channel),
    }
    if any(body.get(field) != value for field, value in expected_bindings.items()):
        raise AssignmentVerificationError("assignment target or response-channel binding mismatch")
    _safe_id(body["assignment_id"], "assignment_id")
    payload = _bounded_text(body["payload"], "assignment payload", MAX_PAYLOAD_BYTES)
    if not secrets.compare_digest(
        _hex(body["payload_sha256"], "payload digest"),
        _sha256(payload.encode("utf-8")),
    ):
        raise AssignmentVerificationError("assignment inline payload digest mismatch")
    _hex(body["nonce"], "assignment nonce")
    issued = _finite(body["issued_at"], "assignment issued_at")
    expires = _finite(body["expires_at"], "assignment expires_at")
    if expires <= issued or expires - issued > MAX_ASSIGNMENT_TTL_SECONDS:
        raise AssignmentVerificationError("assignment validity interval is invalid")
    if expires * 1000 > _finite(enrolled["expires_at"], "enrollment expires_at"):
        raise AssignmentVerificationError("assignment outlives receiver enrollment")
    if enforce_assignment_freshness and (
        issued > current + MAX_CLOCK_SKEW_SECONDS or current > expires
    ):
        raise AssignmentVerificationError("assignment is outside its freshness window")
    return snap, body, enrolled


def verify_consume_assignment(
    assignment: Any,
    *,
    store: AssignmentStateStore,
    revoked_coordinator_key_ids: frozenset[str] = frozenset(),
    revoked_seat_key_ids: frozenset[str] = frozenset(),
    **verification: Any,
) -> dict[str, Any]:
    """Verify, durably consume, and return the signed inline payload/body."""
    snap, body, _enrolled = _verify_assignment(
        assignment,
        revoked_coordinator_key_ids=revoked_coordinator_key_ids,
        revoked_seat_key_ids=revoked_seat_key_ids,
        enforce_assignment_freshness=True,
        **verification,
    )
    store.record_assignment("consumed", snap)
    return body


_RECEIPT_FIELDS = {
    "schema",
    "assignment_id",
    "assignment_sha256",
    "payload_sha256",
    "coordinator_birth_id",
    "coordinator_generation",
    "coordinator_key_id",
    "receiver_birth_id",
    "receiver_generation",
    "receiver_key_id",
    "receiver_seat_epoch",
    "receiver_enrollment_sha256",
    "target_identity_sha256",
    "terminal_tab_identity_sha256",
    "response_receiver_key_id",
    "response_channel_sha256",
    "sequence",
    "prior_receipt_sha256",
    "state",
    "detail",
    "detail_sha256",
    "result_sha256",
    "idempotency_key",
    "nonce",
    "issued_at",
    "expires_at",
}


def emit_state_receipt(
    assignment: Any,
    *,
    seat_identity: Any,
    authority_public_key_hex: str,
    current_target_identity: Any,
    current_terminal_tab_identity: Any,
    current_response_receiver_public_key_hex: str,
    current_response_channel: Any,
    state: str,
    detail: Any,
    result_sha256: str | None,
    idempotency_key: str,
    store: AssignmentStateStore,
    revoked_coordinator_key_ids: frozenset[str] = frozenset(),
    revoked_seat_key_ids: frozenset[str] = frozenset(),
    now: float | None = None,
    ttl_seconds: float = 30.0,
) -> dict[str, Any]:
    """Emit a seat-only receipt; retrying the same key returns exact bytes."""
    admitted = store.require_assignment("consumed", assignment)
    if state not in ASSIGNMENT_STATES:
        raise AssignmentVerificationError("unsupported assignment state")
    detail_body, detail_hash = _bounded_detail(detail)
    if result_sha256 is not None:
        result_sha256 = _hex(result_sha256, "result digest")
    if state == "completed" and result_sha256 is None:
        raise AssignmentVerificationError("completed receipt requires result_sha256")
    if state != "completed" and result_sha256 is not None:
        raise AssignmentVerificationError("only completed receipt may carry result_sha256")
    issued = time.time() if now is None else _finite(now, "receipt time")
    if admitted["coordinator_key_id"] in frozenset(revoked_coordinator_key_ids):
        raise AssignmentVerificationError("assignment coordinator key is revoked")
    _enrollment, enrolled = _verified_enrollment(
        admitted["receiver_enrollment"],
        authority_public_key_hex=_public_key(authority_public_key_hex, "authority public key"),
        revoked_key_ids=frozenset(revoked_seat_key_ids),
        now=issued,
    )
    live_target_hash, live_tab_hash, live_tab = _identity_bindings(
        current_target_identity, current_terminal_tab_identity
    )
    live_bindings = {
        "receiver_key_id": enrolled["seat_key_id"],
        "receiver_seat_epoch": enrolled["seat_epoch"],
        "target_identity_sha256": live_target_hash,
        "terminal_tab_identity_sha256": live_tab_hash,
        "response_receiver_key_id": key_id(
            _public_key(
                current_response_receiver_public_key_hex,
                "current response receiver public key",
            )
        ),
        "response_channel_sha256": _channel_hash(current_response_channel),
    }
    if live_tab["peer_birth_id"] != admitted["receiver_birth_id"] or any(
        admitted.get(field) != value for field, value in live_bindings.items()
    ):
        raise AssignmentVerificationError(
            "receipt live seat, target, or response-channel binding mismatch"
        )
    seat_key = _public_key(str(seat_identity.public_key_hex), "seat public key")
    if key_id(seat_key) != admitted["receiver_key_id"]:
        raise AssignmentVerificationError("receipt signer is not the assigned seat")
    ttl = _finite(ttl_seconds, "receipt TTL")
    if not 0 < ttl <= MAX_RECEIPT_TTL_SECONDS:
        raise AssignmentVerificationError("receipt TTL is invalid")
    idem = _safe_id(idempotency_key, "receipt idempotency_key")

    def build(sequence: int, prior_hash: str) -> dict[str, Any]:
        body = {
            "schema": RECEIPT_SCHEMA,
            "assignment_id": admitted["assignment_id"],
            "assignment_sha256": _sha256(_canonical(admitted)),
            "payload_sha256": admitted["payload_sha256"],
            "coordinator_birth_id": admitted["coordinator_birth_id"],
            "coordinator_generation": admitted["coordinator_generation"],
            "coordinator_key_id": admitted["coordinator_key_id"],
            "receiver_birth_id": admitted["receiver_birth_id"],
            "receiver_generation": admitted["receiver_generation"],
            "receiver_key_id": admitted["receiver_key_id"],
            "receiver_seat_epoch": admitted["receiver_seat_epoch"],
            "receiver_enrollment_sha256": admitted["receiver_enrollment_sha256"],
            "target_identity_sha256": admitted["target_identity_sha256"],
            "terminal_tab_identity_sha256": admitted["terminal_tab_identity_sha256"],
            "response_receiver_key_id": admitted["response_receiver_key_id"],
            "response_channel_sha256": admitted["response_channel_sha256"],
            "sequence": sequence,
            "prior_receipt_sha256": prior_hash,
            "state": state,
            "detail": detail_body,
            "detail_sha256": detail_hash,
            "result_sha256": result_sha256,
            "idempotency_key": idem,
            "nonce": secrets.token_hex(32),
            "issued_at": issued,
            "expires_at": issued + ttl,
        }
        return _signed(body, seat_identity)

    return store.emit_receipt(
        assignment=admitted,
        idempotency_key=idem,
        state=state,
        detail_sha256=detail_hash,
        result_sha256=result_sha256,
        builder=build,
    )


def verify_consume_state_receipt(
    receipt: Any,
    assignment: Any,
    *,
    store: AssignmentStateStore,
    revoked_coordinator_key_ids: frozenset[str] = frozenset(),
    revoked_seat_key_ids: frozenset[str] = frozenset(),
    now: float | None = None,
    **verification: Any,
) -> dict[str, Any]:
    """Verify and commit one receipt, allowing only byte-identical retries."""
    assignment_snap, assignment_body, enrolled = _verify_assignment(
        assignment,
        revoked_coordinator_key_ids=revoked_coordinator_key_ids,
        revoked_seat_key_ids=revoked_seat_key_ids,
        now=now,
        enforce_assignment_freshness=False,
        **verification,
    )
    store.require_assignment("issued", assignment_snap)
    receipt_snap, body = _verified_body(
        receipt, enrolled["seat_public_key_hex"], "assignment receipt"
    )
    if set(body) != _RECEIPT_FIELDS or body.get("schema") != RECEIPT_SCHEMA:
        raise AssignmentVerificationError("assignment receipt fields are invalid")
    exact = {
        "assignment_id": assignment_body["assignment_id"],
        "assignment_sha256": _sha256(_canonical(assignment_snap)),
        "payload_sha256": assignment_body["payload_sha256"],
        "coordinator_birth_id": assignment_body["coordinator_birth_id"],
        "coordinator_generation": assignment_body["coordinator_generation"],
        "coordinator_key_id": assignment_body["coordinator_key_id"],
        "receiver_birth_id": enrolled["birth_id"],
        "receiver_generation": enrolled["generation"],
        "receiver_key_id": enrolled["seat_key_id"],
        "receiver_seat_epoch": enrolled["seat_epoch"],
        "receiver_enrollment_sha256": assignment_body["receiver_enrollment_sha256"],
        "target_identity_sha256": assignment_body["target_identity_sha256"],
        "terminal_tab_identity_sha256": assignment_body["terminal_tab_identity_sha256"],
        "response_receiver_key_id": assignment_body["response_receiver_key_id"],
        "response_channel_sha256": assignment_body["response_channel_sha256"],
    }
    if any(body.get(field) != value for field, value in exact.items()):
        raise AssignmentVerificationError("assignment receipt binding mismatch")
    _positive_int(body["sequence"], "receipt sequence")
    if body["state"] not in ASSIGNMENT_STATES:
        raise AssignmentVerificationError("assignment receipt state is invalid")
    detail, detail_hash = _bounded_detail(body["detail"])
    if detail != body["detail"] or not secrets.compare_digest(
        _hex(body["detail_sha256"], "receipt detail digest"), detail_hash
    ):
        raise AssignmentVerificationError("receipt structured detail digest mismatch")
    result_hash = body["result_sha256"]
    if body["state"] == "completed":
        _hex(result_hash, "result digest")
    elif result_hash is not None:
        raise AssignmentVerificationError("non-completed receipt has a result digest")
    _safe_id(body["idempotency_key"], "receipt idempotency_key")
    _hex(body["nonce"], "receipt nonce")
    _hex(body["prior_receipt_sha256"], "prior receipt digest")
    current = time.time() if now is None else _finite(now, "verification time")
    issued = _finite(body["issued_at"], "receipt issued_at")
    expires = _finite(body["expires_at"], "receipt expires_at")
    if expires <= issued or expires - issued > MAX_RECEIPT_TTL_SECONDS:
        raise AssignmentVerificationError("receipt validity interval is invalid")
    if issued < _finite(assignment_body["issued_at"], "assignment issued_at") - MAX_CLOCK_SKEW_SECONDS:
        raise AssignmentVerificationError("receipt predates its assignment")
    if issued > current + MAX_CLOCK_SKEW_SECONDS or current > expires:
        raise AssignmentVerificationError("assignment receipt is outside its freshness window")
    newly_committed, commit_hash = store.consume_receipt(receipt_snap)
    return {**body, "newly_committed": newly_committed, "commit_sha256": commit_hash}


_ACK_FIELDS = {
    "schema",
    "assignment_id",
    "assignment_sha256",
    "receipt_sha256",
    "receipt_commit_sha256",
    "sequence",
    "state",
    "coordinator_key_id",
    "receiver_key_id",
    "receiver_seat_epoch",
    "target_identity_sha256",
    "terminal_tab_identity_sha256",
    "response_receiver_key_id",
    "response_channel_sha256",
    "nonce",
    "issued_at",
    "expires_at",
}


def acknowledge_state_receipt(
    receipt: Any,
    *,
    coordinator_identity: Any,
    store: AssignmentStateStore,
    revoked_coordinator_key_ids: frozenset[str] = frozenset(),
    now: float | None = None,
    ttl_seconds: float = 30.0,
) -> dict[str, Any]:
    """Create a coordinator-only ACK, but only after durable receipt commit."""
    receipt_snap, _digest = _record_hash(receipt, "receipt")
    coordinator_key = _public_key(
        str(coordinator_identity.public_key_hex), "coordinator public key"
    )
    if key_id(coordinator_key) != receipt_snap.get("coordinator_key_id"):
        raise AssignmentVerificationError("ACK signer is not the assignment coordinator")
    if key_id(coordinator_key) in frozenset(revoked_coordinator_key_ids):
        raise AssignmentVerificationError("ACK coordinator key is revoked")
    issued = time.time() if now is None else _finite(now, "ACK time")
    ttl = _finite(ttl_seconds, "ACK TTL")
    if not 0 < ttl <= MAX_ACK_TTL_SECONDS:
        raise AssignmentVerificationError("ACK TTL is invalid")

    def build(commit_hash: str) -> dict[str, Any]:
        body = {
            "schema": ACK_SCHEMA,
            "assignment_id": receipt_snap["assignment_id"],
            "assignment_sha256": receipt_snap["assignment_sha256"],
            "receipt_sha256": _sha256(_canonical(receipt_snap)),
            "receipt_commit_sha256": commit_hash,
            "sequence": receipt_snap["sequence"],
            "state": receipt_snap["state"],
            "coordinator_key_id": receipt_snap["coordinator_key_id"],
            "receiver_key_id": receipt_snap["receiver_key_id"],
            "receiver_seat_epoch": receipt_snap["receiver_seat_epoch"],
            "target_identity_sha256": receipt_snap["target_identity_sha256"],
            "terminal_tab_identity_sha256": receipt_snap["terminal_tab_identity_sha256"],
            "response_receiver_key_id": receipt_snap["response_receiver_key_id"],
            "response_channel_sha256": receipt_snap["response_channel_sha256"],
            "nonce": secrets.token_hex(32),
            "issued_at": issued,
            "expires_at": issued + ttl,
        }
        return _signed(body, coordinator_identity)

    return store.emit_ack(receipt_snap, build)


def verify_consume_ack(
    ack: Any,
    receipt: Any,
    assignment: Any,
    *,
    store: AssignmentStateStore,
    pinned_coordinator_public_key_hex: str,
    authority_public_key_hex: str,
    expected_target_identity: Any,
    expected_terminal_tab_identity: Any,
    expected_response_receiver_public_key_hex: str,
    expected_response_channel: Any,
    revoked_coordinator_key_ids: frozenset[str] = frozenset(),
    revoked_seat_key_ids: frozenset[str] = frozenset(),
    now: float | None = None,
) -> dict[str, Any]:
    """Worker verifies and consumes the coordinator's post-commit ACK."""
    assignment_snap, assignment_hash = _record_hash(assignment, "assignment")
    receipt_snap, receipt_hash = _record_hash(receipt, "receipt")
    store.require_assignment("consumed", assignment_snap)
    store.require_receipt("emitted", receipt_snap)
    coordinator_key = _public_key(
        pinned_coordinator_public_key_hex, "pinned coordinator public key"
    )
    coordinator_id = key_id(coordinator_key)
    if coordinator_id in frozenset(revoked_coordinator_key_ids):
        raise AssignmentVerificationError("ACK coordinator key is revoked")
    current = time.time() if now is None else _finite(now, "verification time")
    _enrollment, enrolled = _verified_enrollment(
        assignment_snap["receiver_enrollment"],
        authority_public_key_hex=_public_key(authority_public_key_hex, "authority public key"),
        revoked_key_ids=frozenset(revoked_seat_key_ids),
        now=current,
    )
    target_hash, tab_hash, tab = _identity_bindings(
        expected_target_identity, expected_terminal_tab_identity
    )
    if tab["peer_birth_id"] != enrolled["birth_id"]:
        raise AssignmentVerificationError("ACK TerminalTab peer birth_id mismatch")
    ack_snap, body = _verified_body(ack, coordinator_key, "receipt ACK")
    if set(body) != _ACK_FIELDS or body.get("schema") != ACK_SCHEMA:
        raise AssignmentVerificationError("receipt ACK fields are invalid")
    exact = {
        "assignment_id": assignment_snap["assignment_id"],
        "assignment_sha256": assignment_hash,
        "receipt_sha256": receipt_hash,
        "sequence": receipt_snap["sequence"],
        "state": receipt_snap["state"],
        "coordinator_key_id": coordinator_id,
        "receiver_key_id": enrolled["seat_key_id"],
        "receiver_seat_epoch": enrolled["seat_epoch"],
        "target_identity_sha256": target_hash,
        "terminal_tab_identity_sha256": tab_hash,
        "response_receiver_key_id": key_id(
            _public_key(
                expected_response_receiver_public_key_hex,
                "expected response receiver public key",
            )
        ),
        "response_channel_sha256": _channel_hash(expected_response_channel),
    }
    if any(body.get(field) != value for field, value in exact.items()):
        raise AssignmentVerificationError("receipt ACK binding mismatch")
    _hex(body["receipt_commit_sha256"], "receipt commit digest")
    _hex(body["nonce"], "ACK nonce")
    issued = _finite(body["issued_at"], "ACK issued_at")
    expires = _finite(body["expires_at"], "ACK expires_at")
    if expires <= issued or expires - issued > MAX_ACK_TTL_SECONDS:
        raise AssignmentVerificationError("ACK validity interval is invalid")
    if issued > current + MAX_CLOCK_SKEW_SECONDS or current > expires:
        raise AssignmentVerificationError("receipt ACK is outside its freshness window")
    store.consume_ack(ack_snap)
    return body


def poll_state_receipts(
    assignment: Any,
    *,
    receipt_source: Callable[[], Any | None],
    source_guard: Callable[[], bool],
    target_guard: Callable[[], bool],
    until_states: Iterable[str],
    timeout_seconds: float,
    poll_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    **verification: Any,
) -> dict[str, Any]:
    """Poll authenticated receipts; UIA/OCR observations are never inputs."""
    if not callable(source_guard) or not callable(target_guard):
        raise AssignmentVerificationError("source and target guards are required")
    timeout = _finite(timeout_seconds, "poll timeout")
    interval = _finite(poll_seconds, "poll interval")
    wanted = frozenset(until_states)
    if timeout <= 0 or interval <= 0 or not wanted or not wanted <= ASSIGNMENT_STATES:
        raise AssignmentVerificationError("invalid receipt polling policy")
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            guarded = source_guard() is True and target_guard() is True
        except Exception:
            guarded = False
        if not guarded:
            raise AssignmentVerificationError("assignment source or target guard failed closed")
        receipt = receipt_source()
        if receipt is not None:
            verified = verify_consume_state_receipt(
                receipt, assignment, **verification
            )
            if verified["state"] in wanted:
                return verified
        sleep(min(interval, max(0.0, deadline - clock())))
    raise TimeoutError("authenticated assignment receipt deadline expired")


# Explicit names matching the protocol verbs.  There is no legacy API which can
# accept an external payload in place of the coordinator-signed inline payload.
create_assignment = issue_assignment
admit_assignment = verify_consume_assignment
create_state_receipt = emit_state_receipt
verify_state_receipt = verify_consume_state_receipt


__all__ = [
    "ACK_SCHEMA",
    "ASSIGNMENT_SCHEMA",
    "ASSIGNMENT_STATES",
    "MAX_DETAIL_BYTES",
    "MAX_PAYLOAD_BYTES",
    "RECEIPT_SCHEMA",
    "TERMINAL_STATES",
    "AssignmentReplayError",
    "AssignmentStateStore",
    "AssignmentVerificationError",
    "acknowledge_state_receipt",
    "admit_assignment",
    "create_assignment",
    "create_state_receipt",
    "emit_state_receipt",
    "issue_assignment",
    "poll_state_receipts",
    "verify_consume_ack",
    "verify_consume_assignment",
    "verify_consume_state_receipt",
    "verify_state_receipt",
]
