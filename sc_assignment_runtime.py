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
    acknowledge_state_receipt,
    emit_state_receipt,
    issue_assignment,
    verify_consume_ack,
    verify_consume_assignment,
)
from sc_assignment_watchdog import AssignmentWatchdog
from sc_guarded_submit import AckKeyRing, TargetIdentity, guarded_submit
from sc_seat_identity import key_id
from sc_terminal_tab import TerminalTabGuard, TerminalTabIdentity


class AssignmentDispatchError(RuntimeError):
    """A signed assignment was quarantined before it became admissible."""


@dataclass(frozen=True)
class RevocationSnapshot:
    """One mandatory, versioned live view of coordinator and seat revocations."""

    coordinator_key_ids: frozenset[str]
    seat_key_ids: frozenset[str]
    version: str
    observed_at: float

    def __post_init__(self) -> None:
        if type(self.coordinator_key_ids) is not frozenset or type(self.seat_key_ids) is not frozenset:
            raise TypeError("revocation key sets must be exact frozensets")
        if type(self.version) is not str or not self.version:
            raise ValueError("a revocation snapshot version is required")
        if isinstance(self.observed_at, bool) or not isinstance(self.observed_at, (int, float)):
            raise TypeError("revocation observed_at must be numeric")


def _resolve_revocations(resolver: Callable[[], RevocationSnapshot]) -> RevocationSnapshot:
    if not callable(resolver):
        raise TypeError("a live revocation resolver is required")
    snapshot = resolver()
    if type(snapshot) is not RevocationSnapshot:
        raise AssignmentVerificationError("revocation resolver returned an invalid snapshot")
    return snapshot


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
        revocations: RevocationSnapshot,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if type(revocations) is not RevocationSnapshot:
            raise TypeError("an exact live revocation snapshot is required")
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
            "revoked_coordinator_key_ids": revocations.coordinator_key_ids,
            "revoked_seat_key_ids": revocations.seat_key_ids,
        }
        if now is not None:
            values["now"] = now
        return values


@dataclass(frozen=True)
class MailboxPublishOutcome:
    record: dict[str, Any]
    disposition: str


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

    def publish(self, receipt: Any, *, channel: dict[str, Any]) -> MailboxPublishOutcome:
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
        return MailboxPublishOutcome(record, "inserted")

    def reader(
        self,
        assignment: Any,
        *,
        channel: dict[str, Any],
        consumer_id: str = "coordinator",
    ) -> DynamicReceiptReader:
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
    """Local dispatch gate: only acknowledged assignments become admissible."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS assignment_dispatch_v1 (
                    assignment_id TEXT PRIMARY KEY,
                    assignment_sha256 TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL CHECK(state IN ('issued','delivered','quarantined')),
                    reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

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

    def record_issued(self, assignment: Any) -> None:
        assignment_id, digest = self._identity(assignment)
        with closing(self._connect()) as connection:
            try:
                connection.execute(
                    "INSERT INTO assignment_dispatch_v1 "
                    "(assignment_id,assignment_sha256,state,reason) VALUES (?,?,'issued','pending_submit')",
                    (assignment_id, digest),
                )
            except sqlite3.IntegrityError as exc:
                raise AssignmentReplayError("assignment dispatch replay rejected") from exc

    def transition(self, assignment: Any, *, state: str, reason: str) -> None:
        if state not in {"delivered", "quarantined"}:
            raise ValueError("invalid assignment dispatch transition")
        assignment_id, digest = self._identity(assignment)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT assignment_sha256,state FROM assignment_dispatch_v1 WHERE assignment_id=?",
                (assignment_id,),
            ).fetchone()
            if row is None or row[0] != digest or row[1] != "issued":
                connection.rollback()
                raise AssignmentReplayError("assignment dispatch transition rejected")
            connection.execute(
                "UPDATE assignment_dispatch_v1 SET state=?,reason=?,updated_at=CURRENT_TIMESTAMP WHERE assignment_id=?",
                (state, reason, assignment_id),
            )
            connection.commit()

    def require_delivered(self, assignment: Any) -> None:
        assignment_id, digest = self._identity(assignment)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT assignment_sha256,state FROM assignment_dispatch_v1 WHERE assignment_id=?",
                (assignment_id,),
            ).fetchone()
        if row is None or row[0] != digest or row[1] != "delivered":
            raise AssignmentVerificationError("assignment is not delivery-authorized")

    def state(self, assignment: Any) -> str | None:
        assignment_id, digest = self._identity(assignment)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT assignment_sha256,state FROM assignment_dispatch_v1 WHERE assignment_id=?",
                (assignment_id,),
            ).fetchone()
        return None if row is None or row[0] != digest else str(row[1])


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


class SeatAssignmentBroker:
    """Receiver-side authority for assignment admission and receipt emission."""

    def __init__(
        self,
        *,
        store: AssignmentStateStore,
        seat_identity: Any,
        bindings: AssignmentBindings,
        mailbox: DurableReceiptMailbox,
        assignment_journal: DurableAssignmentJournal,
        revocation_resolver: Callable[[], RevocationSnapshot],
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
        self._assignment_journal = assignment_journal
        self._revocation_resolver = revocation_resolver
        self._current_target = current_target
        self._current_terminal_tab = current_terminal_tab
        self._current_response_receiver_public_key = current_response_receiver_public_key
        self._current_response_channel = current_response_channel
        self._now = now

    def _time(self) -> float | None:
        return None if self._now is None else self._now()

    def _live_verification(self) -> dict[str, Any]:
        values = self._bindings.verification(
            _resolve_revocations(self._revocation_resolver),
            now=self._time(),
        )
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
            revoked_coordinator_key_ids=revocations.coordinator_key_ids,
            revoked_seat_key_ids=revocations.seat_key_ids,
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
            revoked_coordinator_key_ids=revocations.coordinator_key_ids,
            revoked_seat_key_ids=revocations.seat_key_ids,
            now=self._time(),
        )


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
        mailbox: DurableReceiptMailbox,
        assignment_journal: DurableAssignmentJournal,
        revocation_resolver: Callable[[], RevocationSnapshot],
        terminal_tab_guard: TerminalTabGuard,
        submit_config: GuardedSubmitConfig,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if type(terminal_tab_guard) is not TerminalTabGuard:
            raise TypeError("production runtime requires an exact TerminalTabGuard")
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
        self._mailbox = mailbox
        self._assignment_journal = assignment_journal
        self._revocation_resolver = revocation_resolver
        self._terminal_tab_guard = terminal_tab_guard
        self._submit_config = submit_config
        self._wall_clock = wall_clock

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
            revoked_coordinator_key_ids=revocations.coordinator_key_ids,
            revoked_seat_key_ids=revocations.seat_key_ids,
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
            )
            raise AssignmentDispatchError("guarded submit failed; assignment quarantined") from exc
        admitted = (
            type(result) is dict
            and result.get("ok") is True
            and result.get("state") == "acknowledged"
            and result.get("delivery_verified") is True
            and result.get("peer_acknowledged") is True
            and result.get("decision") == "accepted"
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
            )
            raise AssignmentDispatchError("guarded submit did not authenticate delivery; assignment quarantined")
        self._assignment_journal.transition(
            assignment,
            state="delivered",
            reason="guarded_submit_authenticated_acceptance",
        )
        return AssignmentDispatch(
            copy.deepcopy(assignment),
            {**copy.deepcopy(result), "transport_assurance": "transport_unattested"},
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
            revoked_coordinator_key_ids=revocations.coordinator_key_ids,
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
    "RevocationSnapshot",
    "SeatAdmission",
    "SeatAssignmentBroker",
    "parse_assignment_ingress",
]
