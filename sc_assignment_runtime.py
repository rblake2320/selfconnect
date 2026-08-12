"""Production composition for signed-inline assignments and state receipts.

The terminal receives the complete coordinator-signed assignment record.  No
caller-supplied payload copy is accepted by the receiver or watchdog paths.
Screen observations remain diagnostic only; state is driven by seat-signed
receipts consumed through :mod:`sc_assignment_protocol`.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import secrets
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from sc_assignment_protocol import (
    AssignmentReplayError,
    AssignmentStateStore,
    AssignmentVerificationError,
    acknowledge_state_receipt,
    emit_state_receipt,
    issue_assignment,
    verify_consume_ack,
    verify_consume_assignment,
)
from sc_assignment_watchdog import AssignmentWatchdog
from sc_guarded_submit import AckKeyRing, DurableAckFinalizer, TargetIdentity, guarded_submit, verify_peer_ack
from sc_identity import AgentIdentity
from sc_seat_identity import key_id
from sc_seat_revocation import resolve_revoked_key_ids
from sc_terminal_tab import TerminalTabGuard, TerminalTabIdentity


class AssignmentDispatchError(RuntimeError):
    """A signed assignment was quarantined before it became admissible."""


def _verify_guarded_delivery_result(
    result: Any,
    wire_assignment: str,
    config: GuardedSubmitConfig,
) -> dict[str, Any]:
    """Re-authenticate the durable peer ACK before dispatch promotion."""
    if type(result) is not dict or type(result.get("ack")) is not dict:
        raise AssignmentVerificationError("guarded delivery result has no authenticated ACK")
    ack_result = result["ack"]
    input_sha256 = hashlib.sha256(wire_assignment.encode("utf-8")).hexdigest()
    required_result = {
        "message_id": result.get("message_id"),
        "challenge": result.get("challenge"),
        "input_sha256": result.get("input_sha256"),
        "sender": result.get("sender"),
        "receiver": result.get("receiver"),
    }
    if (
        required_result["input_sha256"] != input_sha256
        or required_result["sender"] != config.sender
        or required_result["receiver"] != config.receiver
        or any(type(value) is not str for value in required_result.values())
    ):
        raise AssignmentVerificationError("guarded delivery result binding is invalid")
    DurableAckFinalizer(config.replay_path)
    with sqlite3.connect(Path(config.replay_path)) as connection:
        row = connection.execute(
            "SELECT raw_ack,ack_sha256,state FROM peer_ack_finalization WHERE sender=? AND receiver=? AND message_id=?",
            (config.receiver, config.sender, required_result["message_id"]),
        ).fetchone()
    if row is None or row[2] != "audited" or type(row[0]) is not bytes:
        raise AssignmentVerificationError("guarded delivery ACK is not durably audited")
    raw_ack = bytes(row[0])
    if hashlib.sha256(raw_ack).hexdigest() != row[1] or ack_result.get("ack_sha256") != row[1]:
        raise AssignmentVerificationError("guarded delivery ACK hash is invalid")
    response_key_id = config.key_id if config.response_key_id is None else config.response_key_id
    ack = verify_peer_ack(
        raw_ack,
        keyring=config.keyring,
        key_id=response_key_id,
        message_id=required_result["message_id"],
        challenge=required_result["challenge"],
        attempt_nonce=ack_result.get("attempt_nonce"),
        input_sha256=input_sha256,
        operation_sha256=ack_result.get("operation_sha256"),
        sender=config.receiver,
        receiver=config.sender,
        max_age_seconds=config.max_ack_age_seconds,
    )
    expected_ack = {
        "schema": ack.schema,
        "key_id": ack.key_id,
        "challenge": ack.challenge,
        "ack_nonce": ack.ack_nonce,
        "attempt_nonce": ack.attempt_nonce,
        "operation_sha256": ack.operation_sha256,
        "processed_input_sha256": ack.processed_input_sha256,
        "sender": ack.sender,
        "receiver": ack.receiver,
        "decision": ack.decision,
        "issued_at": ack.issued_at,
        "ack_sha256": row[1],
    }
    if ack_result != expected_ack or ack.decision != "accepted" or ack.processed_input_sha256 != input_sha256:
        raise AssignmentVerificationError("guarded delivery ACK content is invalid")
    return {
        "message_id": ack.message_id,
        "ack_sha256": row[1],
        "input_sha256": input_sha256,
        "operation_sha256": ack.operation_sha256,
    }


class SeatRevocationResolver:
    """Mandatory signed/fresh resolver with launch-monotonic rollback memory."""

    def __init__(self, trust_root: RuntimeTrustRoot, *, clock: Callable[[], float]) -> None:
        if type(trust_root) is not RuntimeTrustRoot:
            raise TypeError("revocation resolver requires the provisioned runtime trust root")
        self._trust_root = trust_root
        self.store_path = trust_root.path("revocation_store")
        self.trust_path = trust_root.path("revocation_trust")
        self._clock = clock
        self._last_version = 0
        self._last_digest = "0" * 64

    def __call__(self) -> frozenset[str]:
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(float(now)):
            raise AssignmentVerificationError("revocation resolver time is invalid")
        try:
            revoked = resolve_revoked_key_ids(self.store_path, self.trust_path, now=float(now))
            document = _snapshot(self.store_path.read_bytes(), "seat revocation store")
        except Exception as exc:
            raise AssignmentVerificationError(f"signed seat revocation resolution failed: {exc}") from exc
        snapshot = document.get("snapshot")
        if type(snapshot) is not dict or type(snapshot.get("version")) is not int:
            raise AssignmentVerificationError("seat revocation snapshot version is invalid")
        version = snapshot["version"]
        digest = hashlib.sha256(_canonical(snapshot)).hexdigest()
        if version < self._last_version or (version == self._last_version and digest != self._last_digest):
            raise AssignmentVerificationError("seat revocation snapshot replay or rollback rejected")
        self._last_version, self._last_digest = version, digest
        self._trust_root.verify_and_advance_revocation(version, digest)
        return revoked


def _resolve_revocations(resolver: SeatRevocationResolver) -> frozenset[str]:
    if type(resolver) is not SeatRevocationResolver:
        raise TypeError("an exact signed SeatRevocationResolver is required")
    return resolver()


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
        raise AssignmentVerificationError("runtime value is not canonical JSON") from exc


def _snapshot(value: Any, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, item in pairs:
            if name in result:
                raise AssignmentVerificationError(f"{label} has duplicate member {name!r}")
            result[name] = item
        return result

    try:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        raw = value if isinstance(value, str) else _canonical(value).decode("ascii")
        snapshot = json.loads(raw, object_pairs_hook=unique_object)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AssignmentVerificationError(f"{label} is not stable JSON") from exc
    if type(snapshot) is not dict:
        raise AssignmentVerificationError(f"{label} must be an object")
    return snapshot


@dataclass(frozen=True)
class AssignmentBindings:
    """Exact identities shared by coordinator verification and the seat broker."""

    authority_public_key_hex: str
    coordinator_public_key_hex: str
    coordinator_birth_id: str
    coordinator_generation: int
    receiver_birth_id: str
    receiver_generation: int
    target_identity: TargetIdentity
    terminal_tab_identity: TerminalTabIdentity
    response_receiver_public_key_hex: str
    response_channel: dict[str, Any]

    def __post_init__(self) -> None:
        if type(self.target_identity) is not TargetIdentity:
            raise TypeError("an exact TargetIdentity is required")
        if type(self.terminal_tab_identity) is not TerminalTabIdentity:
            raise TypeError("an exact TerminalTabIdentity is required")
        tab = self.terminal_tab_identity
        target = self.target_identity
        if (
            tab.window_hwnd,
            tab.window_pid,
            tab.window_process_start_time_ns,
        ) != (target.hwnd, target.pid, target.process_start_time_ns):
            raise ValueError("TerminalTabIdentity does not bind the exact target")
        if tab.peer_birth_id != self.receiver_birth_id:
            raise ValueError("TerminalTabIdentity does not bind the receiver seat")
        if type(self.response_channel) is not dict or not self.response_channel:
            raise TypeError("an explicit response-channel binding is required")
        object.__setattr__(self, "response_channel", _snapshot(self.response_channel, "response channel"))

    def verification(
        self,
        revoked_key_ids: frozenset[str],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if type(revoked_key_ids) is not frozenset:
            raise TypeError("an exact signed revocation result is required")
        values: dict[str, Any] = {
            "pinned_coordinator_public_key_hex": self.coordinator_public_key_hex,
            "authority_public_key_hex": self.authority_public_key_hex,
            "expected_coordinator_birth_id": self.coordinator_birth_id,
            "expected_coordinator_generation": self.coordinator_generation,
            "expected_receiver_birth_id": self.receiver_birth_id,
            "expected_receiver_generation": self.receiver_generation,
            "expected_target_identity": self.target_identity,
            "expected_terminal_tab_identity": self.terminal_tab_identity,
            "expected_response_receiver_public_key_hex": self.response_receiver_public_key_hex,
            "expected_response_channel": copy.deepcopy(self.response_channel),
            "revoked_coordinator_key_ids": revoked_key_ids,
            "revoked_seat_key_ids": revoked_key_ids,
        }
        if now is not None:
            values["now"] = now
        return values


@dataclass(frozen=True)
class MailboxPublishOutcome:
    record: dict[str, Any]
    disposition: str


def _dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    if __import__("os").name != "nt":
        raise AssignmentVerificationError("Windows DPAPI is required for runtime trust state")
    try:
        import win32crypt  # type: ignore[import-untyped]

        return bytes(win32crypt.CryptProtectData(data, "SelfConnect runtime root", entropy, None, None, 0))
    except Exception as exc:
        raise AssignmentVerificationError("DPAPI protection failed") from exc


def _dpapi_unprotect(data: bytes, entropy: bytes) -> bytes:
    try:
        import win32crypt  # type: ignore[import-untyped]

        return bytes(win32crypt.CryptUnprotectData(data, entropy, None, None, 0)[1])
    except Exception as exc:
        raise AssignmentVerificationError("DPAPI runtime trust state verification failed") from exc


def provision_runtime_trust_root(
    config_path: str | Path,
    state_path: str | Path,
    *,
    provisioning_identity: AgentIdentity,
    mailbox_path: str | Path,
    dispatch_path: str | Path,
    receiver_store_path: str | Path,
    revocation_store_path: str | Path,
    revocation_trust_path: str | Path,
) -> str:
    """Provision once; signer is used only here and is never retained."""
    config_target, state_target = Path(config_path).resolve(), Path(state_path).resolve()
    if config_target.exists() or state_target.exists():
        raise FileExistsError("runtime trust root is already provisioned")
    mailbox_id, dispatch_id, receiver_id = secrets.token_hex(32), secrets.token_hex(32), secrets.token_hex(32)
    body = {
        "schema": "selfconnect-assignment-runtime-root-v1",
        "key_id": key_id(provisioning_identity.public_key_hex),
        "public_key_hex": provisioning_identity.public_key_hex,
        "state_path": str(state_target),
        "mailbox_path": str(Path(mailbox_path).resolve()),
        "mailbox_database_id": mailbox_id,
        "dispatch_path": str(Path(dispatch_path).resolve()),
        "dispatch_database_id": dispatch_id,
        "receiver_store_path": str(Path(receiver_store_path).resolve()),
        "receiver_store_database_id": receiver_id,
        "revocation_store_path": str(Path(revocation_store_path).resolve()),
        "revocation_trust_path": str(Path(revocation_trust_path).resolve()),
    }
    signed = {
        **body,
        "signature_b64": base64.b64encode(provisioning_identity.sign(_canonical(body))).decode("ascii"),
    }
    for database_path, table, database_id in (
        (Path(body["mailbox_path"]), "receipt_mailbox_meta_v1", mailbox_id),
        (Path(body["dispatch_path"]), "assignment_dispatch_meta_v3", dispatch_id),
        (Path(body["receiver_store_path"]), "receiver_launch_meta_v1", receiver_id),
    ):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        if database_path.exists():
            raise FileExistsError(f"provisioned database already exists: {database_path}")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                f"CREATE TABLE {table} (singleton INTEGER PRIMARY KEY CHECK(singleton=1),database_id TEXT NOT NULL)"
            )
            connection.execute(f"INSERT INTO {table} VALUES (1,?)", (database_id,))
    config_target.parent.mkdir(parents=True, exist_ok=True)
    state_target.parent.mkdir(parents=True, exist_ok=True)
    config_target.write_bytes(_canonical(signed))
    entropy = hashlib.sha256(_canonical(body)).digest()
    initial_state = {
        "anchors": {
            "mailbox": {"database_id": mailbox_id, "heads": []},
            "dispatch": {"database_id": dispatch_id, "sequence": 0, "head_sha256": "0" * 64},
            "receiver_store": {"database_id": receiver_id, "store_path": body["receiver_store_path"]},
        },
        "revocation_high_water": None,
    }
    state_target.write_bytes(_dpapi_protect(_canonical(initial_state), entropy))
    return provisioning_identity.public_key_hex


class RuntimeTrustRoot:
    """Local integrity root whose same-user rollback threat is explicitly excluded.

    DPAPI protects the mutable state from other OS identities and accidental
    corruption.  It is not a monotonic authority against the same Windows user,
    who can decrypt, restore, or replace every local database and this state.
    High assurance therefore refuses until a separately administered monotonic
    anchor service is integrated.
    """

    def __init__(
        self,
        config_path: str | Path,
        *,
        pinned_public_key_hex: str,
        high_assurance: bool = False,
    ) -> None:
        if type(high_assurance) is not bool:
            raise TypeError("high_assurance must be an exact boolean")
        if high_assurance:
            raise AssignmentVerificationError(
                "high-assurance state authority refuses: a separately administered "
                "monotonic anchor service is not integrated"
            )
        record = _snapshot(Path(config_path).resolve().read_bytes(), "runtime trust config")
        signature_b64 = record.pop("signature_b64", None)
        if record.get("schema") != "selfconnect-assignment-runtime-root-v1":
            raise AssignmentVerificationError("runtime trust config schema is invalid")
        if record.get("public_key_hex") != pinned_public_key_hex or record.get("key_id") != key_id(
            pinned_public_key_hex
        ):
            raise AssignmentVerificationError("runtime trust root key is not pinned")
        try:
            signature = base64.b64decode(signature_b64, validate=True)
        except Exception as exc:
            raise AssignmentVerificationError("runtime trust config signature encoding is invalid") from exc
        if not AgentIdentity.verify_with_pubkey_hex(pinned_public_key_hex, _canonical(record), signature):
            raise AssignmentVerificationError("runtime trust config signature is invalid")
        self._entropy = hashlib.sha256(_canonical(record)).digest()
        self._config = MappingProxyType(copy.deepcopy(record))
        self._state_path = Path(record["state_path"]).resolve()
        if not self._state_path.exists():
            raise AssignmentVerificationError("provisioned runtime trust state is absent")
        self._read_state()

    @property
    def assurance(self) -> dict[str, Any]:
        return {
            "mode": "local_dpapi_integrity_only",
            "same_user_threat": "excluded",
            "external_monotonic_anchor": False,
        }

    def require_high_assurance(self) -> None:
        raise AssignmentVerificationError(
            "high-assurance state authority refuses: local DPAPI is not a same-user monotonic authority"
        )

    def path(self, name: str) -> Path:
        return Path(self._config[f"{name}_path"]).resolve()

    def database_id(self, name: str) -> str:
        return str(self._config[f"{name}_database_id"])

    @property
    def key_id(self) -> str:
        return str(self._config["key_id"])

    def _read_state(self) -> dict[str, Any]:
        return _snapshot(_dpapi_unprotect(self._state_path.read_bytes(), self._entropy), "runtime trust state")

    def _write_state(self, state: dict[str, Any]) -> None:
        staged = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        staged.write_bytes(_dpapi_protect(_canonical(state), self._entropy))
        staged.replace(self._state_path)

    def verify_anchor(self, name: str, actual: dict[str, Any]) -> None:
        expected = self._read_state()["anchors"].get(name)
        if expected is None or expected != _snapshot(actual, "actual protected anchor"):
            raise AssignmentVerificationError(f"protected {name} anchor mismatch")

    def initialize_anchor(self, name: str, actual: dict[str, Any]) -> None:
        state = self._read_state()
        if name in state["anchors"]:
            raise AssignmentVerificationError(f"protected {name} anchor already exists")
        state["anchors"][name] = _snapshot(actual, "initial protected anchor")
        self._write_state(state)

    def advance_anchor(self, name: str, actual: dict[str, Any]) -> None:
        state = self._read_state()
        if name not in state["anchors"]:
            raise AssignmentVerificationError(f"protected {name} anchor is absent")
        state["anchors"][name] = _snapshot(actual, "advanced protected anchor")
        self._write_state(state)

    def verify_and_advance_revocation(self, version: int, digest: str) -> None:
        state = self._read_state()
        previous = state.get("revocation_high_water")
        if previous is not None and (
            version < previous["version"] or (version == previous["version"] and digest != previous["digest"])
        ):
            raise AssignmentVerificationError("seat revocation snapshot durable rollback rejected")
        state["revocation_high_water"] = {"version": version, "digest": digest}
        self._write_state(state)


class DurableReceiptMailbox:
    """Durable untrusted receipt spool bound to one response channel.

    Records in this mailbox are not trusted because they are present.  The
    watchdog authenticates every record against the signed assignment before a
    state can be returned.
    """

    def __init__(
        self,
        *,
        channel_binding: dict[str, Any],
        trust_root: RuntimeTrustRoot,
    ) -> None:
        if type(trust_root) is not RuntimeTrustRoot:
            raise TypeError("mailbox requires the provisioned runtime trust root")
        self._trust_root = trust_root
        self.path = trust_root.path("mailbox")
        self.channel_binding = _snapshot(channel_binding, "response channel")
        self.channel_sha256 = hashlib.sha256(_canonical(self.channel_binding)).hexdigest()
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS receipt_mailbox_v1 (
                    assignment_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    record_sha256 TEXT NOT NULL,
                    record_json BLOB NOT NULL,
                    channel_sha256 TEXT NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(assignment_id, sequence),
                    UNIQUE(record_sha256)
                );
                CREATE TABLE IF NOT EXISTS receipt_mailbox_head_v1 (
                    assignment_id TEXT PRIMARY KEY,
                    high_sequence INTEGER NOT NULL CHECK(high_sequence > 0),
                    high_record_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipt_mailbox_cursor_v1 (
                    assignment_id TEXT NOT NULL,
                    consumer_id TEXT NOT NULL,
                    next_sequence INTEGER NOT NULL CHECK(next_sequence > 0),
                    last_record_sha256 TEXT,
                    PRIMARY KEY(assignment_id, consumer_id)
                );
                CREATE TABLE IF NOT EXISTS receipt_ack_mailbox_v1 (
                    receipt_sha256 TEXT PRIMARY KEY,
                    assignment_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    ack_sha256 TEXT NOT NULL UNIQUE,
                    ack_json BLOB NOT NULL,
                    channel_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipt_mailbox_meta_v1 (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    database_id TEXT NOT NULL
                );
                """
            )
            row = connection.execute("SELECT database_id FROM receipt_mailbox_meta_v1 WHERE singleton=1").fetchone()
            if row is None or row[0] != trust_root.database_id("mailbox"):
                raise AssignmentVerificationError("mailbox database ID differs from provisioned root")
        self._verify_anchor()

    def _head_document(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            database_id = connection.execute(
                "SELECT database_id FROM receipt_mailbox_meta_v1 WHERE singleton=1"
            ).fetchone()[0]
            heads = connection.execute(
                "SELECT assignment_id,high_sequence,high_record_sha256 FROM receipt_mailbox_head_v1 ORDER BY assignment_id"
            ).fetchall()
        return {"database_id": database_id, "heads": [list(row) for row in heads]}

    def _verify_anchor(self) -> None:
        self._trust_root.verify_anchor("mailbox", self._head_document())

    def _advance_anchor(self) -> None:
        self._trust_root.advance_anchor("mailbox", self._head_document())

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def require_channel(self, channel: Any) -> None:
        if not isinstance(channel, dict) or not secrets.compare_digest(
            hashlib.sha256(_canonical(channel)).hexdigest(), self.channel_sha256
        ):
            raise AssignmentVerificationError("receipt mailbox channel binding mismatch")

    def publish(self, receipt: Any, *, channel: dict[str, Any]) -> MailboxPublishOutcome:
        self._verify_anchor()
        self.require_channel(channel)
        record = _snapshot(receipt, "state receipt")
        assignment_id = record.get("assignment_id")
        sequence = record.get("sequence")
        if type(assignment_id) is not str or not assignment_id or type(sequence) is not int or sequence <= 0:
            raise AssignmentVerificationError("state receipt mailbox routing fields are invalid")
        raw = _canonical(record)
        digest = hashlib.sha256(raw).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT record_sha256,record_json,channel_sha256 FROM receipt_mailbox_v1 "
                "WHERE assignment_id=? AND sequence=?",
                (assignment_id, sequence),
            ).fetchone()
            if existing is not None:
                if existing[0] != digest or existing[1] != raw or existing[2] != self.channel_sha256:
                    connection.rollback()
                    raise AssignmentReplayError("receipt mailbox sequence fork rejected")
                connection.commit()
                return MailboxPublishOutcome(record, "duplicate_idempotent")
            head = connection.execute(
                "SELECT high_sequence,high_record_sha256 FROM receipt_mailbox_head_v1 WHERE assignment_id=?",
                (assignment_id,),
            ).fetchone()
            expected = 1 if head is None else int(head[0]) + 1
            if sequence != expected:
                connection.rollback()
                raise AssignmentReplayError("receipt mailbox append reorder or gap rejected")
            try:
                connection.execute(
                    "INSERT INTO receipt_mailbox_v1 "
                    "(assignment_id,sequence,record_sha256,record_json,channel_sha256) "
                    "VALUES (?,?,?,?,?)",
                    (assignment_id, sequence, digest, raw, self.channel_sha256),
                )
                connection.execute(
                    "INSERT INTO receipt_mailbox_head_v1 VALUES (?,?,?) "
                    "ON CONFLICT(assignment_id) DO UPDATE SET "
                    "high_sequence=excluded.high_sequence,high_record_sha256=excluded.high_record_sha256",
                    (assignment_id, sequence, digest),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise AssignmentReplayError("receipt mailbox replay rejected") from exc
        self._advance_anchor()
        return MailboxPublishOutcome(record, "inserted")

    def reader(
        self,
        assignment: Any,
        *,
        channel: dict[str, Any],
        consumer_id: str = "coordinator",
    ) -> DynamicReceiptReader:
        self._verify_anchor()
        self.require_channel(channel)
        record = _snapshot(assignment, "assignment")
        assignment_id = record.get("assignment_id")
        if type(assignment_id) is not str or not assignment_id:
            raise AssignmentVerificationError("assignment mailbox routing field is invalid")
        if type(consumer_id) is not str or not consumer_id:
            raise AssignmentVerificationError("receipt consumer_id is invalid")
        return DynamicReceiptReader(self, assignment_id, consumer_id)

    def publish_ack(
        self,
        ack: Any,
        *,
        receipt: Any,
        channel: dict[str, Any],
    ) -> MailboxPublishOutcome:
        self._verify_anchor()
        self.require_channel(channel)
        ack_record = _snapshot(ack, "receipt ACK")
        receipt_record = _snapshot(receipt, "state receipt")
        receipt_hash = hashlib.sha256(_canonical(receipt_record)).hexdigest()
        if ack_record.get("receipt_sha256") != receipt_hash:
            raise AssignmentVerificationError("receipt ACK mailbox binding mismatch")
        raw = _canonical(ack_record)
        digest = hashlib.sha256(raw).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT ack_sha256,ack_json,channel_sha256 FROM receipt_ack_mailbox_v1 WHERE receipt_sha256=?",
                (receipt_hash,),
            ).fetchone()
            if row is not None:
                if row[0] != digest or row[1] != raw or row[2] != self.channel_sha256:
                    connection.rollback()
                    raise AssignmentReplayError("receipt ACK mailbox fork rejected")
                connection.commit()
                return MailboxPublishOutcome(ack_record, "duplicate_idempotent")
            connection.execute(
                "INSERT INTO receipt_ack_mailbox_v1 VALUES (?,?,?,?,?,?)",
                (
                    receipt_hash,
                    ack_record["assignment_id"],
                    ack_record["sequence"],
                    digest,
                    raw,
                    self.channel_sha256,
                ),
            )
            connection.commit()
        return MailboxPublishOutcome(ack_record, "inserted")

    def read_ack(self, receipt: Any, *, channel: dict[str, Any]) -> dict[str, Any] | None:
        self._verify_anchor()
        self.require_channel(channel)
        receipt_hash = hashlib.sha256(_canonical(_snapshot(receipt, "state receipt"))).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT ack_sha256,ack_json,channel_sha256 FROM receipt_ack_mailbox_v1 WHERE receipt_sha256=?",
                (receipt_hash,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            raw = bytes(row[1])
            if row[2] != self.channel_sha256 or row[0] != hashlib.sha256(raw).hexdigest():
                connection.rollback()
                raise AssignmentVerificationError("receipt ACK mailbox integrity failed")
            record = _snapshot(raw, "receipt ACK")
            connection.commit()
            return record


class DynamicReceiptReader:
    """Read each newly published receipt in sequence while a watchdog polls."""

    def __init__(self, mailbox: DurableReceiptMailbox, assignment_id: str, consumer_id: str) -> None:
        self._mailbox = mailbox
        self._assignment_id = assignment_id
        self._consumer_id = consumer_id
        self._pending: tuple[int, str] | None = None
        with closing(self._mailbox._connect()) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO receipt_mailbox_cursor_v1 VALUES (?,?,1,NULL)",
                (assignment_id, consumer_id),
            )

    def __call__(self) -> dict[str, Any] | None:
        self._mailbox._verify_anchor()
        with closing(self._mailbox._connect()) as connection:
            connection.execute("BEGIN")
            cursor = connection.execute(
                "SELECT next_sequence FROM receipt_mailbox_cursor_v1 WHERE assignment_id=? AND consumer_id=?",
                (self._assignment_id, self._consumer_id),
            ).fetchone()
            if cursor is None:
                connection.rollback()
                raise AssignmentVerificationError("receipt mailbox cursor disappeared")
            next_sequence = int(cursor[0])
            row = connection.execute(
                "SELECT record_sha256,record_json,channel_sha256 FROM receipt_mailbox_v1 "
                "WHERE assignment_id=? AND sequence=?",
                (self._assignment_id, next_sequence),
            ).fetchone()
            if row is None:
                head = connection.execute(
                    "SELECT high_sequence FROM receipt_mailbox_head_v1 WHERE assignment_id=?",
                    (self._assignment_id,),
                ).fetchone()
                connection.commit()
                if head is not None and int(head[0]) >= next_sequence:
                    raise AssignmentVerificationError("receipt mailbox deletion or truncation detected")
                return None
            raw = bytes(row[1])
            digest = hashlib.sha256(raw).hexdigest()
            if row[2] != self._mailbox.channel_sha256 or row[0] != digest:
                connection.rollback()
                raise AssignmentVerificationError("receipt mailbox stored record integrity failed")
            record = _snapshot(raw, "mailbox receipt")
            if record.get("assignment_id") != self._assignment_id or record.get("sequence") != next_sequence:
                connection.rollback()
                raise AssignmentVerificationError("receipt mailbox routing integrity failed")
            connection.commit()
        self._pending = (next_sequence, digest)
        return record

    def acknowledge(self, receipt: Any) -> None:
        record = _snapshot(receipt, "acknowledged mailbox receipt")
        digest = hashlib.sha256(_canonical(record)).hexdigest()
        pending = self._pending
        if pending is None or pending != (record.get("sequence"), digest):
            raise AssignmentVerificationError("receipt mailbox cursor acknowledgement mismatch")
        with closing(self._mailbox._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE receipt_mailbox_cursor_v1 SET next_sequence=?,last_record_sha256=? "
                "WHERE assignment_id=? AND consumer_id=? AND next_sequence=?",
                (
                    int(record["sequence"]) + 1,
                    digest,
                    self._assignment_id,
                    self._consumer_id,
                    record["sequence"],
                ),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise AssignmentReplayError("receipt mailbox cursor race detected")
            connection.commit()
        self._pending = None


class DurableAssignmentJournal:
    """Hash-chained dispatch gate rooted in DPAPI-protected launch state."""

    def __init__(self, *, trust_root: RuntimeTrustRoot) -> None:
        if type(trust_root) is not RuntimeTrustRoot:
            raise TypeError("dispatch journal requires the provisioned runtime trust root")
        self._trust_root = trust_root
        self.path = trust_root.path("dispatch")
        with closing(self._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS assignment_dispatch_chain_v2 ("
                "sequence INTEGER PRIMARY KEY CHECK(sequence>0),prior_sha256 TEXT NOT NULL,"
                "entry_sha256 TEXT NOT NULL UNIQUE,entry_json BLOB NOT NULL)"
            )
            row = connection.execute("SELECT database_id FROM assignment_dispatch_meta_v3 WHERE singleton=1").fetchone()
            if row is None or row[0] != trust_root.database_id("dispatch"):
                raise AssignmentVerificationError("dispatch database ID differs from provisioned root")
        self._verify_anchor()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _identity(assignment: Any) -> tuple[str, str]:
        record = _snapshot(assignment, "dispatch assignment")
        assignment_id = record.get("assignment_id")
        if type(assignment_id) is not str or not assignment_id:
            raise AssignmentVerificationError("dispatch assignment_id is invalid")
        return assignment_id, hashlib.sha256(_canonical(record)).hexdigest()

    def _chain(self) -> tuple[int, str, dict[str, tuple[str, str]]]:
        prior, states = "0" * 64, {}
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM assignment_dispatch_chain_v2 ORDER BY sequence").fetchall()
        for expected, (sequence, previous, digest, raw) in enumerate(rows, 1):
            raw = bytes(raw)
            if sequence != expected or previous != prior or hashlib.sha256(raw).hexdigest() != digest:
                raise AssignmentVerificationError("dispatch journal chain integrity failed")
            entry = _snapshot(raw, "dispatch journal entry")
            if entry.get("sequence") != sequence or entry.get("prior_sha256") != prior:
                raise AssignmentVerificationError("dispatch journal signed fields mismatch")
            states[entry["assignment_id"]] = (entry["assignment_sha256"], entry["state"])
            prior = digest
        return len(rows), prior, states

    def _verify_anchor(self) -> None:
        sequence, head, _states = self._chain()
        actual = {"sequence": sequence, "head_sha256": head}
        self._trust_root.verify_anchor("dispatch", {"database_id": self._trust_root.database_id("dispatch"), **actual})

    def _append(self, assignment: Any, state: str, reason: str, evidence: Any) -> None:
        self._verify_anchor()
        sequence, prior, states = self._chain()
        assignment_id, assignment_hash = self._identity(assignment)
        current = states.get(assignment_id)
        if (state == "issued" and current is not None) or (
            state != "issued" and current != (assignment_hash, "issued")
        ):
            raise AssignmentReplayError("assignment dispatch transition rejected")
        entry = {
            "sequence": sequence + 1,
            "prior_sha256": prior,
            "assignment_id": assignment_id,
            "assignment_sha256": assignment_hash,
            "state": state,
            "reason": reason,
            "evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
        }
        raw = _canonical(entry)
        digest = hashlib.sha256(raw).hexdigest()
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO assignment_dispatch_chain_v2 VALUES (?,?,?,?)",
                (sequence + 1, prior, digest, raw),
            )
        self._trust_root.advance_anchor(
            "dispatch",
            {"database_id": self._trust_root.database_id("dispatch"), "sequence": sequence + 1, "head_sha256": digest},
        )

    def record_issued(self, assignment: Any) -> None:
        self._append(assignment, "issued", "pending_submit", {})

    def transition(self, assignment: Any, *, state: str, reason: str, evidence: Any) -> None:
        if state not in {"delivered", "quarantined"}:
            raise ValueError("invalid assignment dispatch transition")
        self._append(assignment, state, reason, evidence)

    def require_delivered(self, assignment: Any) -> None:
        self._verify_anchor()
        assignment_id, digest = self._identity(assignment)
        _sequence, _head, states = self._chain()
        if states.get(assignment_id) != (digest, "delivered"):
            raise AssignmentVerificationError("assignment is not delivery-authorized")

    def state(self, assignment: Any) -> str | None:
        self._verify_anchor()
        assignment_id, digest = self._identity(assignment)
        _sequence, _head, states = self._chain()
        row = states.get(assignment_id)
        return None if row is None or row[0] != digest else row[1]


def parse_assignment_ingress(raw: str | bytes) -> dict[str, Any]:
    """Strict composer boundary: one canonical JSON assignment or quarantine."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AssignmentVerificationError("assignment ingress is not UTF-8") from exc
    if type(raw) is not str or not raw or len(raw.encode("utf-8")) > 131_072:
        raise AssignmentVerificationError("assignment ingress is missing or oversized")
    if raw != raw.strip() or "\n" in raw or "\r" in raw:
        raise AssignmentVerificationError("assignment ingress must be one canonical JSON record")
    record = _snapshot(raw, "assignment ingress")
    try:
        raw_bytes = raw.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise AssignmentVerificationError("assignment ingress must be canonical ASCII JSON") from exc
    if raw_bytes != _canonical(record):
        raise AssignmentVerificationError("assignment ingress is not canonical JSON")
    return record


@dataclass(frozen=True)
class SeatAdmission:
    assignment: dict[str, Any]
    accepted_receipt: dict[str, Any]


class ReceiverLaunchContext:
    """Trusted launch-pinned receiver authorities; never parsed from a message."""

    def __init__(
        self,
        *,
        trust_root: RuntimeTrustRoot,
        assignment_journal: DurableAssignmentJournal,
        mailbox: DurableReceiptMailbox,
        revocation_resolver: SeatRevocationResolver,
        high_assurance: bool = False,
    ) -> None:
        if type(trust_root) is not RuntimeTrustRoot:
            raise TypeError("receiver context requires the provisioned runtime trust root")
        if type(high_assurance) is not bool:
            raise TypeError("high_assurance must be an exact boolean")
        if high_assurance:
            trust_root.require_high_assurance()
        if (
            assignment_journal._trust_root is not trust_root
            or mailbox._trust_root is not trust_root
            or revocation_resolver._trust_root is not trust_root
        ):
            raise AssignmentVerificationError("receiver authorities do not share the provisioned trust root")
        self._trust_root = trust_root
        self.assurance = trust_root.assurance
        self.store_path = trust_root.path("receiver_store")
        self.store = AssignmentStateStore(self.store_path)
        self.assignment_journal = assignment_journal
        self.mailbox = mailbox
        self.revocation_resolver = revocation_resolver
        with sqlite3.connect(self.store_path) as connection:
            row = connection.execute("SELECT database_id FROM receiver_launch_meta_v1 WHERE singleton=1").fetchone()
        body = {"database_id": trust_root.database_id("receiver_store"), "store_path": str(self.store_path)}
        if row is None or row[0] != body["database_id"]:
            raise AssignmentVerificationError("receiver replay store ID differs from provisioned root")
        trust_root.verify_anchor("receiver_store", body)


class SeatAssignmentBroker:
    """Receiver-side authority for assignment admission and receipt emission."""

    def __init__(
        self,
        *,
        launch_context: ReceiverLaunchContext,
        seat_identity: Any,
        bindings: AssignmentBindings,
        current_target: Callable[[], TargetIdentity],
        current_terminal_tab: Callable[[], TerminalTabIdentity],
        current_response_receiver_public_key: Callable[[], str],
        current_response_channel: Callable[[], dict[str, Any]],
        now: Callable[[], float] | None = None,
    ) -> None:
        for provider in (
            current_target,
            current_terminal_tab,
            current_response_receiver_public_key,
            current_response_channel,
        ):
            if not callable(provider):
                raise TypeError("all live binding providers are required")
        if type(launch_context) is not ReceiverLaunchContext:
            raise TypeError("an exact receiver launch context is required")
        self._launch_context = launch_context
        self._store = launch_context.store
        self._seat_identity = seat_identity
        self._bindings = bindings
        self._mailbox = launch_context.mailbox
        self._assignment_journal = launch_context.assignment_journal
        self._revocation_resolver = launch_context.revocation_resolver
        self._current_target = current_target
        self._current_terminal_tab = current_terminal_tab
        self._current_response_receiver_public_key = current_response_receiver_public_key
        self._current_response_channel = current_response_channel
        self._now = now

    def _time(self) -> float | None:
        return None if self._now is None else self._now()

    def _live_verification(self) -> dict[str, Any]:
        values = self._bindings.verification(_resolve_revocations(self._revocation_resolver), now=self._time())
        values["expected_target_identity"] = self._current_target()
        values["expected_terminal_tab_identity"] = self._current_terminal_tab()
        values["expected_response_receiver_public_key_hex"] = self._current_response_receiver_public_key()
        values["expected_response_channel"] = self._current_response_channel()
        return values

    def admit_raw(self, raw_assignment: str | bytes) -> SeatAdmission:
        """Parse one raw composer record, verify it, and emit acceptance."""
        record = parse_assignment_ingress(raw_assignment)
        self._assignment_journal.require_delivered(record)
        body = verify_consume_assignment(
            record,
            store=self._store,
            **self._live_verification(),
        )
        receipt = self.emit(
            record,
            state="accepted",
            detail={"admitted": True},
            idempotency_key=f"accepted:{body['assignment_id']}",
        )
        return SeatAdmission(copy.deepcopy(body), receipt)

    def emit(
        self,
        assignment: Any,
        *,
        state: str,
        detail: dict[str, Any],
        idempotency_key: str,
        result_sha256: str | None = None,
    ) -> dict[str, Any]:
        channel = self._current_response_channel()
        self._mailbox.require_channel(channel)
        revocations = _resolve_revocations(self._revocation_resolver)
        receipt = emit_state_receipt(
            assignment,
            seat_identity=self._seat_identity,
            authority_public_key_hex=self._bindings.authority_public_key_hex,
            current_target_identity=self._current_target(),
            current_terminal_tab_identity=self._current_terminal_tab(),
            current_response_receiver_public_key_hex=(self._current_response_receiver_public_key()),
            current_response_channel=channel,
            state=state,
            detail=detail,
            result_sha256=result_sha256,
            idempotency_key=idempotency_key,
            store=self._store,
            revoked_coordinator_key_ids=revocations,
            revoked_seat_key_ids=revocations,
            now=self._time(),
        )
        return self._mailbox.publish(receipt, channel=channel).record

    def consume_receipt_ack(self, assignment: Any, receipt: Any) -> dict[str, Any] | None:
        """Consume the coordinator ACK if delivered; absence is recoverable."""
        channel = self._current_response_channel()
        ack = self._mailbox.read_ack(receipt, channel=channel)
        if ack is None:
            return None
        revocations = _resolve_revocations(self._revocation_resolver)
        return verify_consume_ack(
            ack,
            receipt,
            assignment,
            store=self._store,
            pinned_coordinator_public_key_hex=self._bindings.coordinator_public_key_hex,
            authority_public_key_hex=self._bindings.authority_public_key_hex,
            expected_target_identity=self._current_target(),
            expected_terminal_tab_identity=self._current_terminal_tab(),
            expected_response_receiver_public_key_hex=(self._current_response_receiver_public_key()),
            expected_response_channel=channel,
            revoked_coordinator_key_ids=revocations,
            revoked_seat_key_ids=revocations,
            now=self._time(),
        )


class SelfConnectAssignmentReceiver:
    """Concrete SelfConnect-owned sidecar hook; raw bytes have one authority path."""

    def __init__(
        self,
        broker: SeatAssignmentBroker,
        *,
        read_raw_assignment: Callable[[], bytes],
        selfconnect_owned_sidecar: bool = True,
        high_assurance: bool = False,
    ) -> None:
        if type(broker) is not SeatAssignmentBroker or not callable(read_raw_assignment):
            raise TypeError("receiver requires an exact broker and raw SelfConnect reader")
        if high_assurance or not selfconnect_owned_sidecar:
            raise ValueError(
                "receiver refuses third-party TUI ingress and high-assurance mode until "
                "the SelfConnect sidecar transport has an integrated attestation verifier"
            )
        self._broker = broker
        self._read_raw_assignment = read_raw_assignment
        self.transport_assurance = "selfconnect_owned_sidecar_transport_unattested"

    def serve_once(self) -> SeatAdmission:
        raw = self._read_raw_assignment()
        if type(raw) is not bytes:
            raise AssignmentVerificationError("SelfConnect assignment ingress must return bytes")
        return self._broker.admit_raw(raw)


@dataclass(frozen=True)
class GuardedSubmitConfig:
    sender: str
    receiver: str
    keyring: AckKeyRing
    key_id: str
    ack_pipe: str
    replay_path: str | Path
    event_log_path: str | Path
    response_key_id: str | None = None
    transport: str = "auto"
    ack_timeout: float = 10.0
    max_ack_age_seconds: float = 5.0
    high_assurance: bool = False

    def __post_init__(self) -> None:
        if type(self.high_assurance) is not bool:
            raise TypeError("high_assurance must be an exact boolean")
        if self.high_assurance:
            raise ValueError(
                "high-assurance mode refuses transport_unattested; no verified pipe/SID adapter is integrated"
            )


@dataclass(frozen=True)
class AssignmentDispatch:
    assignment: dict[str, Any]
    submit_result: dict[str, Any]


class ProductionAssignmentRuntime:
    """Coordinator composition: sign inline assignment, submit it, then watch receipts."""

    def __init__(
        self,
        *,
        coordinator_identity: Any,
        coordinator_store: AssignmentStateStore,
        receiver_enrollment: dict[str, Any],
        bindings: AssignmentBindings,
        trust_root: RuntimeTrustRoot,
        mailbox: DurableReceiptMailbox,
        assignment_journal: DurableAssignmentJournal,
        revocation_resolver: SeatRevocationResolver,
        terminal_tab_guard: TerminalTabGuard,
        submit_config: GuardedSubmitConfig,
        wall_clock: Callable[[], float] = time.time,
        high_assurance: bool = False,
    ) -> None:
        if type(terminal_tab_guard) is not TerminalTabGuard:
            raise TypeError("production runtime requires an exact TerminalTabGuard")
        if type(trust_root) is not RuntimeTrustRoot:
            raise TypeError("production runtime requires the provisioned read-only trust root")
        if type(high_assurance) is not bool:
            raise TypeError("high_assurance must be an exact boolean")
        if high_assurance:
            trust_root.require_high_assurance()
        if (
            mailbox._trust_root is not trust_root
            or assignment_journal._trust_root is not trust_root
            or revocation_resolver._trust_root is not trust_root
        ):
            raise AssignmentVerificationError("runtime authorities do not share the provisioned trust root")
        if terminal_tab_guard.identity != bindings.terminal_tab_identity:
            raise ValueError("TerminalTabGuard identity differs from assignment binding")
        if key_id(str(coordinator_identity.public_key_hex)) != key_id(bindings.coordinator_public_key_hex):
            raise ValueError("coordinator signer differs from the pinned coordinator")
        mailbox.require_channel(bindings.response_channel)
        _resolve_revocations(revocation_resolver)
        self._coordinator_identity = coordinator_identity
        self._store = coordinator_store
        self._receiver_enrollment = copy.deepcopy(receiver_enrollment)
        self._bindings = bindings
        self._trust_root = trust_root
        self._mailbox = mailbox
        self._assignment_journal = assignment_journal
        self._revocation_resolver = revocation_resolver
        self._terminal_tab_guard = terminal_tab_guard
        self._submit_config = submit_config
        self._wall_clock = wall_clock
        self.assurance = trust_root.assurance

    def dispatch(
        self,
        payload: str,
        *,
        now: float | None = None,
        ttl_seconds: float = 60.0,
    ) -> AssignmentDispatch:
        revocations = _resolve_revocations(self._revocation_resolver)
        assignment = issue_assignment(
            payload,
            coordinator_identity=self._coordinator_identity,
            coordinator_birth_id=self._bindings.coordinator_birth_id,
            coordinator_generation=self._bindings.coordinator_generation,
            receiver_enrollment=self._receiver_enrollment,
            authority_public_key_hex=self._bindings.authority_public_key_hex,
            target_identity=self._bindings.target_identity,
            terminal_tab_identity=self._bindings.terminal_tab_identity,
            response_receiver_public_key_hex=(self._bindings.response_receiver_public_key_hex),
            response_channel=self._bindings.response_channel,
            store=self._store,
            revoked_coordinator_key_ids=revocations,
            revoked_seat_key_ids=revocations,
            now=now,
            ttl_seconds=ttl_seconds,
        )
        self._assignment_journal.record_issued(assignment)
        wire_assignment = _canonical(assignment).decode("ascii")
        config = self._submit_config
        try:
            result = guarded_submit(
                wire_assignment,
                target=self._bindings.target_identity,
                sender=config.sender,
                receiver=config.receiver,
                keyring=config.keyring,
                key_id=config.key_id,
                ack_pipe=config.ack_pipe,
                replay_path=config.replay_path,
                event_log_path=config.event_log_path,
                response_key_id=config.response_key_id,
                transport=config.transport,
                ack_timeout=config.ack_timeout,
                max_ack_age_seconds=config.max_ack_age_seconds,
                terminal_tab_guard=self._terminal_tab_guard,
            )
        except Exception as exc:
            self._assignment_journal.transition(
                assignment,
                state="quarantined",
                reason=f"guarded_submit_exception:{type(exc).__name__}",
                evidence={"exception_type": type(exc).__name__},
            )
            raise AssignmentDispatchError("guarded submit failed; assignment quarantined") from exc
        admitted = (
            type(result) is dict
            and result.get("ok") is True
            and result.get("state") == "acknowledged"
            and result.get("delivery_verified") is True
            and result.get("peer_acknowledged") is True
            and result.get("decision") == "accepted"
            and type(result.get("ack")) is dict
            and type(result["ack"].get("ack_sha256")) is str
            and len(result["ack"]["ack_sha256"]) == 64
        )
        if not admitted:
            reason = (
                "guarded_submit_" + str(result.get("state", "malformed"))
                if isinstance(result, dict)
                else "guarded_submit_malformed"
            )
            self._assignment_journal.transition(
                assignment,
                state="quarantined",
                reason=reason,
                evidence=result if isinstance(result, dict) else {"malformed": True},
            )
            raise AssignmentDispatchError("guarded submit did not authenticate delivery; assignment quarantined")
        try:
            durable_attestation = _verify_guarded_delivery_result(result, wire_assignment, config)
        except Exception as exc:
            self._assignment_journal.transition(
                assignment,
                state="quarantined",
                reason=f"guarded_delivery_attestation_failed:{type(exc).__name__}",
                evidence={"result": result, "exception_type": type(exc).__name__},
            )
            raise AssignmentDispatchError(
                "guarded delivery did not have a durable authenticated ACK; assignment quarantined"
            ) from exc
        self._assignment_journal.transition(
            assignment,
            state="delivered",
            reason="guarded_submit_authenticated_acceptance",
            evidence={"result": result, "durable_attestation": durable_attestation},
        )
        return AssignmentDispatch(
            copy.deepcopy(assignment),
            {
                **copy.deepcopy(result),
                "transport_assurance": "transport_unattested",
                "state_authority_assurance": self._trust_root.assurance,
            },
        )

    def watchdog(
        self,
        assignment: Any,
        *,
        read_uia: Callable[[int], str],
        read_ocr: Callable[[int], str],
        capture: Callable[[int], Any],
        alert_coordinator: Callable[[dict[str, Any]], None],
        source_guard: Callable[[Any], bool],
        target_guard: Callable[[TargetIdentity], bool],
        assignment_source: Any,
        clock: Callable[[], float] = time.monotonic,
    ) -> tuple[AssignmentWatchdog, Any]:
        record = _snapshot(assignment, "assignment")
        self._assignment_journal.require_delivered(record)
        reader = self._mailbox.reader(record, channel=self._bindings.response_channel)

        def verification_resolver() -> dict[str, Any]:
            return self._bindings.verification(
                _resolve_revocations(self._revocation_resolver),
                now=self._wall_clock(),
            )

        def receipt_acknowledger(receipt: dict[str, Any]) -> MailboxPublishOutcome:
            return self.recover_receipt_ack(receipt)

        watchdog = AssignmentWatchdog(
            store=self._store,
            receipt_reader=reader,
            read_uia=read_uia,
            read_ocr=read_ocr,
            capture=capture,
            alert_coordinator=alert_coordinator,
            source_guard=source_guard,
            target_guard=target_guard,
            verification_resolver=verification_resolver,
            receipt_acknowledger=receipt_acknowledger,
            clock=clock,
        )
        return watchdog, assignment_source

    def recover_receipt_ack(self, receipt: Any) -> MailboxPublishOutcome:
        """Idempotently recreate/publish an ACK after delivery loss."""
        revocations = _resolve_revocations(self._revocation_resolver)
        ack = acknowledge_state_receipt(
            receipt,
            coordinator_identity=self._coordinator_identity,
            store=self._store,
            revoked_coordinator_key_ids=revocations,
            now=self._wall_clock(),
        )
        return self._mailbox.publish_ack(
            ack,
            receipt=receipt,
            channel=self._bindings.response_channel,
        )


__all__ = [
    "AssignmentBindings",
    "AssignmentDispatch",
    "AssignmentDispatchError",
    "DurableAssignmentJournal",
    "DurableReceiptMailbox",
    "DynamicReceiptReader",
    "GuardedSubmitConfig",
    "MailboxPublishOutcome",
    "ProductionAssignmentRuntime",
    "ReceiverLaunchContext",
    "RuntimeTrustRoot",
    "SeatAdmission",
    "SeatAssignmentBroker",
    "SeatRevocationResolver",
    "SelfConnectAssignmentReceiver",
    "parse_assignment_ingress",
    "provision_runtime_trust_root",
]
