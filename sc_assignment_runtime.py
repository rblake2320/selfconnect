"""Production composition for signed-inline assignments and state receipts.

The terminal receives the complete coordinator-signed assignment record.  No
caller-supplied payload copy is accepted by the receiver or watchdog paths.
Screen observations remain diagnostic only; state is driven by seat-signed
receipts consumed through :mod:`sc_assignment_protocol`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sc_assignment_protocol import (
    AssignmentReplayError,
    AssignmentStateStore,
    AssignmentVerificationError,
    emit_state_receipt,
    issue_assignment,
    verify_consume_assignment,
)
from sc_assignment_watchdog import AssignmentWatchdog
from sc_guarded_submit import AckKeyRing, TargetIdentity, guarded_submit
from sc_seat_identity import key_id
from sc_terminal_tab import TerminalTabGuard, TerminalTabIdentity


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

    def verification(self, *, now: float | None = None) -> dict[str, Any]:
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
        }
        if now is not None:
            values["now"] = now
        return values


class DurableReceiptMailbox:
    """Durable untrusted receipt spool bound to one response channel.

    Records in this mailbox are not trusted because they are present.  The
    watchdog authenticates every record against the signed assignment before a
    state can be returned.
    """

    def __init__(self, path: str | Path, *, channel_binding: dict[str, Any]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
                """
            )

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

    def publish(self, receipt: Any, *, channel: dict[str, Any]) -> dict[str, Any]:
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
                return record
            try:
                connection.execute(
                    "INSERT INTO receipt_mailbox_v1 "
                    "(assignment_id,sequence,record_sha256,record_json,channel_sha256) "
                    "VALUES (?,?,?,?,?)",
                    (assignment_id, sequence, digest, raw, self.channel_sha256),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise AssignmentReplayError("receipt mailbox replay rejected") from exc
        return record

    def reader(self, assignment: Any, *, channel: dict[str, Any]) -> DynamicReceiptReader:
        self.require_channel(channel)
        record = _snapshot(assignment, "assignment")
        assignment_id = record.get("assignment_id")
        if type(assignment_id) is not str or not assignment_id:
            raise AssignmentVerificationError("assignment mailbox routing field is invalid")
        return DynamicReceiptReader(self, assignment_id)


class DynamicReceiptReader:
    """Read each newly published receipt in sequence while a watchdog polls."""

    def __init__(self, mailbox: DurableReceiptMailbox, assignment_id: str) -> None:
        self._mailbox = mailbox
        self._assignment_id = assignment_id
        self._next_sequence = 1

    def __call__(self) -> dict[str, Any] | None:
        with closing(self._mailbox._connect()) as connection:
            row = connection.execute(
                "SELECT record_json,channel_sha256 FROM receipt_mailbox_v1 WHERE assignment_id=? AND sequence=?",
                (self._assignment_id, self._next_sequence),
            ).fetchone()
        if row is None:
            return None
        if row[1] != self._mailbox.channel_sha256:
            raise AssignmentVerificationError("receipt mailbox stored channel binding mismatch")
        record = _snapshot(bytes(row[0]).decode("ascii"), "mailbox receipt")
        self._next_sequence += 1
        return record


@dataclass(frozen=True)
class SeatAdmission:
    assignment: dict[str, Any]
    accepted_receipt: dict[str, Any]


class SeatAssignmentBroker:
    """Receiver-side authority for assignment admission and receipt emission."""

    def __init__(
        self,
        *,
        store: AssignmentStateStore,
        seat_identity: Any,
        bindings: AssignmentBindings,
        mailbox: DurableReceiptMailbox,
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
        self._store = store
        self._seat_identity = seat_identity
        self._bindings = bindings
        self._mailbox = mailbox
        self._current_target = current_target
        self._current_terminal_tab = current_terminal_tab
        self._current_response_receiver_public_key = current_response_receiver_public_key
        self._current_response_channel = current_response_channel
        self._now = now

    def _time(self) -> float | None:
        return None if self._now is None else self._now()

    def _live_verification(self) -> dict[str, Any]:
        values = self._bindings.verification(now=self._time())
        values["expected_target_identity"] = self._current_target()
        values["expected_terminal_tab_identity"] = self._current_terminal_tab()
        values["expected_response_receiver_public_key_hex"] = self._current_response_receiver_public_key()
        values["expected_response_channel"] = self._current_response_channel()
        return values

    def admit(self, assignment: Any) -> SeatAdmission:
        """Consume only the signed assignment, then publish an accepted receipt."""
        record = _snapshot(assignment, "assignment")
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
            now=self._time(),
        )
        return self._mailbox.publish(receipt, channel=channel)


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
        mailbox: DurableReceiptMailbox,
        terminal_tab_guard: TerminalTabGuard,
        submit_config: GuardedSubmitConfig,
    ) -> None:
        if type(terminal_tab_guard) is not TerminalTabGuard:
            raise TypeError("production runtime requires an exact TerminalTabGuard")
        if terminal_tab_guard.identity != bindings.terminal_tab_identity:
            raise ValueError("TerminalTabGuard identity differs from assignment binding")
        if key_id(str(coordinator_identity.public_key_hex)) != key_id(bindings.coordinator_public_key_hex):
            raise ValueError("coordinator signer differs from the pinned coordinator")
        mailbox.require_channel(bindings.response_channel)
        self._coordinator_identity = coordinator_identity
        self._store = coordinator_store
        self._receiver_enrollment = copy.deepcopy(receiver_enrollment)
        self._bindings = bindings
        self._mailbox = mailbox
        self._terminal_tab_guard = terminal_tab_guard
        self._submit_config = submit_config

    def dispatch(
        self,
        payload: str,
        *,
        now: float | None = None,
        ttl_seconds: float = 60.0,
    ) -> AssignmentDispatch:
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
            now=now,
            ttl_seconds=ttl_seconds,
        )
        wire_assignment = _canonical(assignment).decode("ascii")
        config = self._submit_config
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
        return AssignmentDispatch(copy.deepcopy(assignment), copy.deepcopy(result))

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
        verification_now: float | None = None,
    ) -> tuple[AssignmentWatchdog, Any]:
        record = _snapshot(assignment, "assignment")
        reader = self._mailbox.reader(record, channel=self._bindings.response_channel)
        watchdog = AssignmentWatchdog(
            store=self._store,
            receipt_reader=reader,
            read_uia=read_uia,
            read_ocr=read_ocr,
            capture=capture,
            alert_coordinator=alert_coordinator,
            source_guard=source_guard,
            target_guard=target_guard,
            verification=self._bindings.verification(now=verification_now),
            clock=clock,
        )
        return watchdog, assignment_source


__all__ = [
    "AssignmentBindings",
    "AssignmentDispatch",
    "DurableReceiptMailbox",
    "DynamicReceiptReader",
    "GuardedSubmitConfig",
    "ProductionAssignmentRuntime",
    "SeatAdmission",
    "SeatAssignmentBroker",
]
