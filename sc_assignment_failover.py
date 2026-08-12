"""Durable authenticated fresh-birth failover for assignment seats.

The authorizing receipt is verified cryptographically before a durable saga is
prepared.  Exact retries resume the persisted outbox; conflicting use of the
same receipt is rejected.  Registry state remains a compare-and-set routing
mirror, never proof.  This module has no process-termination capability.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

import sc_mesh_registry
from sc_assignment_protocol import (
    AssignmentStateStore,
    AssignmentVerificationError,
    build_assignment,
    verify_durably_consumed_state_receipt,
    verify_state_receipt_authorization,
)
from sc_identity import AgentIdentity
from sc_seat_identity import key_id, verify_enrollment
from sc_tasks import FileLock

FAILOVER_STATES = frozenset({"blocked", "rejected"})
CONTINUITY_SCHEMA = "selfconnect-assignment-continuity-v1"
CONTINUATION_PAYLOAD_SCHEMA = "selfconnect-assignment-continuation-v1"
DELIVERY_RECEIPT_SCHEMA = "selfconnect-assignment-failover-delivery-v1"
ACTUATION_PROOF_SCHEMA = "selfconnect-seat-delivery-actuation-v1"
MAX_DELIVERY_CLOCK_SKEW_SECONDS = 5.0
_STAGES = (
    "prepared",
    "authorized",
    "intent_audited",
    "off_rails",
    "off_rails_audited",
    "assignment_built",
    "assignment_issued",
    "delivered",
)
_STAGE_INDEX = {stage: index for index, stage in enumerate(_STAGES)}
_RECEIPT_VERIFICATION_FIELDS = frozenset(
    {
        "pinned_coordinator_public_key_hex",
        "expected_coordinator_birth_id",
        "expected_coordinator_generation",
        "expected_receiver_birth_id",
        "expected_receiver_generation",
        "expected_target_identity",
        "expected_terminal_tab_identity",
        "expected_response_receiver_public_key_hex",
        "expected_response_channel",
    }
)
_CONTEXT_SEAL = object()
_SEAT_CLAIM_SEAL = object()


class AssignmentFailoverError(AssignmentVerificationError):
    """A failover authorization, saga, or high-assurance boundary failed."""


class FailoverConflictError(AssignmentFailoverError):
    """The authorizing receipt was reused with a different failover intent."""


@dataclass(frozen=True, slots=True, init=False)
class FailoverLaunchContext:
    """Trusted immutable durability capability created once at runtime launch."""

    assignment_store: AssignmentStateStore
    assignment_store_path: Path
    saga_store: FailoverSagaStore
    saga_store_path: Path
    registry_path: Path
    event_log_path: Path
    event_head_anchor: str
    _seal: object

    def __init__(
        self,
        *,
        assignment_store: AssignmentStateStore,
        saga_path: str | Path,
        registry_path: str | Path,
        seal: object,
    ) -> None:
        if seal is not _CONTEXT_SEAL or type(assignment_store) is not AssignmentStateStore:
            raise TypeError("FailoverLaunchContext is runtime-owned")
        resolved_registry = Path(registry_path).resolve(strict=True)
        resolved_saga = Path(saga_path).resolve(strict=False)
        if resolved_saga == resolved_registry:
            raise ValueError("saga and registry paths must be distinct")
        verified = sc_mesh_registry.verify_events(registry_path=resolved_registry)
        if verified.get("ok") is not True:
            raise AssignmentFailoverError("event log is not valid at launch")
        object.__setattr__(self, "assignment_store", assignment_store)
        object.__setattr__(self, "assignment_store_path", assignment_store.path.resolve())
        saga_store = FailoverSagaStore(resolved_saga)
        object.__setattr__(self, "saga_store", saga_store)
        object.__setattr__(self, "saga_store_path", saga_store.path)
        object.__setattr__(self, "registry_path", resolved_registry)
        object.__setattr__(
            self,
            "event_log_path",
            sc_mesh_registry.default_event_log_path(resolved_registry).resolve(strict=False),
        )
        object.__setattr__(self, "event_head_anchor", str(verified["head_hash"]))
        object.__setattr__(self, "_seal", seal)


def _create_launch_context(
    *,
    assignment_store: AssignmentStateStore,
    saga_path: str | Path,
    registry_path: str | Path,
) -> FailoverLaunchContext:
    """Trusted launcher hook; action APIs accept only the resulting capability."""
    return FailoverLaunchContext(
        assignment_store=assignment_store,
        saga_path=saga_path,
        registry_path=registry_path,
        seal=_CONTEXT_SEAL,
    )


def _require_launch_context(context: Any) -> FailoverLaunchContext:
    if type(context) is not FailoverLaunchContext or context._seal is not _CONTEXT_SEAL:
        raise AssignmentFailoverError("trusted immutable failover launch context is required")
    if context.assignment_store.path.resolve() != context.assignment_store_path:
        raise AssignmentFailoverError("launch assignment store path drifted")
    if context.saga_store.path.resolve() != context.saga_store_path:
        raise AssignmentFailoverError("launch saga path drifted")
    verified = sc_mesh_registry.verify_events(event_log_path=context.event_log_path)
    if verified.get("ok") is not True:
        raise AssignmentFailoverError("event log failed launch-anchor verification")
    anchor = context.event_head_anchor
    if anchor != sc_mesh_registry.EVENT_GENESIS_HASH:
        events = sc_mesh_registry.load_events(
            event_log_path=context.event_log_path,
            limit=max(1, int(verified["events_checked"])),
        )["events"]
        if not any(secrets.compare_digest(str(item.get("event_hash", "")), anchor) for item in events):
            raise AssignmentFailoverError("launch event-head anchor is absent")
    return context


def _reject_constant(value: str) -> None:
    raise AssignmentFailoverError(f"non-finite JSON number {value!r} is forbidden")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise AssignmentFailoverError(f"duplicate JSON member {name!r}")
        result[name] = value
    return result


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
        raise AssignmentFailoverError("record is not stable canonical JSON") from exc


def _snapshot(value: Any, label: str, *, require_canonical_wire: bool = True) -> dict[str, Any]:
    raw: bytes | None = None
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise AssignmentFailoverError(f"{label} is not UTF-8 JSON") from exc
    elif is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    try:
        if raw is None:
            raw = _canonical(value)
        result = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except AssignmentFailoverError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AssignmentFailoverError(f"{label} JSON is invalid") from exc
    if type(result) is not dict:
        raise AssignmentFailoverError(f"{label} must be a JSON object")
    if require_canonical_wire and isinstance(value, (bytes, str)) and raw != _canonical(result):
        raise AssignmentFailoverError(f"{label} is not canonical JSON")
    return result


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite(value: float | None, name: str) -> float:
    resolved = time.time() if value is None else value
    if isinstance(resolved, bool) or not isinstance(resolved, (int, float)):
        raise AssignmentFailoverError(f"invalid {name}")
    result = float(resolved)
    if not math.isfinite(result):
        raise AssignmentFailoverError(f"invalid {name}")
    return result


def _signed(body: dict[str, Any], identity: Any) -> dict[str, Any]:
    frozen = _snapshot(body, "signed body", require_canonical_wire=False)
    return {
        **frozen,
        "signature_b64": base64.b64encode(identity.sign(_canonical(frozen))).decode("ascii"),
    }


def _verified_body(record: Any, public_key_hex: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    snap = _snapshot(record, label)
    body = dict(snap)
    signature = body.pop("signature_b64", None)
    if type(signature) is not str:
        raise AssignmentFailoverError(f"{label} is unsigned")
    try:
        raw_signature = base64.b64decode(signature, validate=True)
    except (TypeError, ValueError) as exc:
        raise AssignmentFailoverError(f"{label} signature is invalid") from exc
    if not AgentIdentity.verify_with_pubkey_hex(public_key_hex, _canonical(body), raw_signature):
        raise AssignmentFailoverError(f"{label} signature is invalid")
    return snap, body


def _create_seat_actuation_proof(
    claim: Mapping[str, Any],
    *,
    seat_identity: Any,
    result_sha256: str,
    acted_at: float | None = None,
) -> dict[str, Any]:
    """Create the seat's signed assertion that an exact durable claim acted."""
    frozen_claim = _snapshot(claim, "delivery claim", require_canonical_wire=False)
    body = {
        "schema": ACTUATION_PROOF_SCHEMA,
        "claim": frozen_claim,
        "result_sha256": result_sha256,
        "actuation_nonce": secrets.token_hex(32),
        "acted_at": _finite(acted_at, "actuation time"),
    }
    return _signed(body, seat_identity)


def _verify_seat_actuation_proof(
    proof: Any,
    *,
    claim: Mapping[str, Any],
    seat_public_key_hex: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    snap, body = _verified_body(proof, seat_public_key_hex, "seat actuation proof")
    exact = {
        "schema": ACTUATION_PROOF_SCHEMA,
        "claim": _snapshot(claim, "delivery claim", require_canonical_wire=False),
    }
    expected_fields = set(exact) | {
        "result_sha256",
        "actuation_nonce",
        "acted_at",
    }
    if set(body) != expected_fields or any(
        body.get(field) != value for field, value in exact.items()
    ):
        raise AssignmentFailoverError("seat actuation proof exact claim binding mismatch")
    for field in ("result_sha256", "actuation_nonce"):
        value = body.get(field)
        if type(value) is not str or len(value) != 64 or any(
            ch not in "0123456789abcdef" for ch in value
        ):
            raise AssignmentFailoverError(f"seat actuation proof {field} is invalid")
    _finite(body.get("acted_at"), "actuation time")
    return snap, body


class FailoverSagaStore:
    """SQLite saga/outbox keyed by the authorizing receipt and exact intent."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS failover_saga_v1 (
                    operation_id TEXT PRIMARY KEY,
                    receipt_sha256 TEXT NOT NULL UNIQUE,
                    spec_sha256 TEXT NOT NULL UNIQUE,
                    spec_json BLOB NOT NULL,
                    stage TEXT NOT NULL CHECK(stage IN (
                        'prepared','authorized','intent_audited','off_rails',
                        'off_rails_audited','assignment_built',
                        'assignment_issued','delivered')),
                    receipt_commit_sha256 TEXT,
                    continuity_json BLOB,
                    replacement_assignment_json BLOB,
                    delivery_receipt_json BLOB,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _decode(value: Any, label: str) -> dict[str, Any] | None:
        if value is None:
            return None
        raw = bytes(value) if not isinstance(value, bytes) else value
        return _snapshot(raw, label)

    def prepare(self, spec: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        frozen = _snapshot(spec, "failover operation spec", require_canonical_wire=False)
        spec_hash = _digest(frozen)
        operation_id = spec_hash
        receipt_hash = str(frozen["receipt_sha256"])
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT operation_id,spec_sha256,spec_json FROM failover_saga_v1 "
                "WHERE receipt_sha256=?",
                (receipt_hash,),
            ).fetchone()
            if existing is not None:
                if existing[0] != operation_id or existing[1] != spec_hash:
                    connection.rollback()
                    raise FailoverConflictError("authorizing receipt has a conflicting failover intent")
                stored = self._decode(existing[2], "stored failover spec")
                if stored != frozen:
                    connection.rollback()
                    raise FailoverConflictError("stored failover intent integrity failed")
                connection.commit()
                return self.load(operation_id), False
            try:
                connection.execute(
                    "INSERT INTO failover_saga_v1 "
                    "(operation_id,receipt_sha256,spec_sha256,spec_json,stage,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (operation_id, receipt_hash, spec_hash, _canonical(frozen), "prepared", now, now),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise FailoverConflictError("failover operation conflicts with durable state") from exc
        return self.load(operation_id), True

    def load(self, operation_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT operation_id,receipt_sha256,spec_sha256,spec_json,stage,"
                "receipt_commit_sha256,continuity_json,replacement_assignment_json,"
                "delivery_receipt_json,last_error,created_at,updated_at "
                "FROM failover_saga_v1 WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise AssignmentFailoverError("failover operation is not durable")
        spec = self._decode(row[3], "stored failover spec")
        if spec is None or _digest(spec) != row[2] or row[0] != row[2]:
            raise AssignmentFailoverError("failover operation integrity failed")
        return {
            "operation_id": row[0],
            "receipt_sha256": row[1],
            "spec": spec,
            "stage": row[4],
            "receipt_commit_sha256": row[5],
            "continuity": self._decode(row[6], "stored continuity artifact"),
            "replacement_assignment": self._decode(row[7], "stored replacement assignment"),
            "delivery_receipt": self._decode(row[8], "stored delivery receipt"),
            "last_error": row[9],
            "created_at": row[10],
            "updated_at": row[11],
        }

    def find_by_receipt(self, receipt_sha256: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT operation_id FROM failover_saga_v1 WHERE receipt_sha256=?",
                (receipt_sha256,),
            ).fetchone()
        return None if row is None else self.load(str(row[0]))

    def advance(self, operation_id: str, expected_stage: str, next_stage: str, **values: Any) -> dict[str, Any]:
        if _STAGE_INDEX[next_stage] != _STAGE_INDEX[expected_stage] + 1:
            raise AssignmentFailoverError("invalid failover saga stage transition")
        allowed = {
            "receipt_commit_sha256",
            "continuity_json",
            "replacement_assignment_json",
            "delivery_receipt_json",
        }
        if not set(values) <= allowed:
            raise AssignmentFailoverError("invalid failover saga update")
        assignments = ["stage=?", "updated_at=?", "last_error=NULL"]
        parameters: list[Any] = [next_stage, time.time()]
        for field, value in values.items():
            assignments.append(f"{field}=?")
            parameters.append(_canonical(value) if field.endswith("_json") else value)
        parameters.extend((operation_id, expected_stage))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"UPDATE failover_saga_v1 SET {','.join(assignments)} "
                "WHERE operation_id=? AND stage=?",
                parameters,
            )
            if cursor.rowcount != 1:
                connection.rollback()
                current = self.load(operation_id)
                if _STAGE_INDEX[current["stage"]] >= _STAGE_INDEX[next_stage]:
                    return current
                raise AssignmentFailoverError("failover saga stage changed concurrently")
            connection.commit()
        return self.load(operation_id)

    def record_error(self, operation_id: str, exc: BaseException) -> None:
        message = f"{type(exc).__name__}:{exc}"[:2048]
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE failover_saga_v1 SET last_error=?,updated_at=? WHERE operation_id=?",
                (message, time.time(), operation_id),
            )

    def pending(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT operation_id FROM failover_saga_v1 WHERE stage!='delivered' "
                "ORDER BY created_at"
            ).fetchall()
        return [self.load(str(row[0])) for row in rows]


def list_pending_failovers(path: str | Path) -> list[dict[str, Any]]:
    """List durable intents which an owner can reconcile after restart."""
    return FailoverSagaStore(path).pending()


class SeatDeliveryClaimStore:
    """Seat-side claim/dedupe authority committed before remote actuation."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS delivery_claim_v1 (
                    assignment_sha256 TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    claim_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL CHECK(state IN ('claimed','completed')),
                    receipt_json BLOB,
                    created_at REAL NOT NULL,
                    completed_at REAL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def deliver_exact(
        self,
        *,
        operation_id: str,
        continuity: Mapping[str, Any],
        replacement_assignment: Mapping[str, Any],
        seat_identity: Any,
        actuator: Callable[[dict[str, Any]], Mapping[str, Any]],
        delivered_at: float | None = None,
        after_commit: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Claim once, actuate once, persist proof, and replay exact proof.

        A process failure after the claim but before durable completion leaves
        the claim ambiguous and retries fail closed without a second actuation.
        A transport failure after completion returns the stored signed receipt
        on retry without invoking ``actuator`` again.
        """
        assignment = _snapshot(
            replacement_assignment,
            "seat delivery assignment",
            require_canonical_wire=False,
        )
        assignment_hash = _digest(assignment)
        idempotency_key = f"failover-delivery:{operation_id}"
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT idempotency_key,claim_id,state,receipt_json "
                "FROM delivery_claim_v1 WHERE assignment_sha256=?",
                (assignment_hash,),
            ).fetchone()
            if row is not None:
                if row[0] != idempotency_key:
                    connection.rollback()
                    raise FailoverConflictError("delivery assignment has a conflicting idempotency key")
                if row[2] == "completed" and row[3] is not None:
                    receipt = _snapshot(bytes(row[3]), "stored seat delivery receipt")
                    connection.commit()
                    return receipt
                connection.rollback()
                raise AssignmentFailoverError(
                    "seat delivery claim is ambiguous; second actuation refused"
                )
            claim_id = secrets.token_hex(32)
            connection.execute(
                "INSERT INTO delivery_claim_v1 VALUES(?,?,?,'claimed',NULL,?,NULL)",
                (assignment_hash, idempotency_key, claim_id, time.time()),
            )
            connection.commit()

        claim_body = {
            "schema": "selfconnect-seat-delivery-claim-v1",
            "claim_id": claim_id,
            "idempotency_key": idempotency_key,
            "operation_id": operation_id,
            "replacement_assignment_sha256": assignment_hash,
            "continuity_sha256": _digest(continuity),
        }
        claim = {
            **claim_body,
            "claim_commit_sha256": _digest(claim_body),
        }
        try:
            proof = actuator(dict(claim))
        except Exception as exc:
            raise AssignmentFailoverError("seat delivery actuator failed after durable claim") from exc
        verified_proof, proof_body = _verify_seat_actuation_proof(
            proof,
            claim=claim,
            seat_public_key_hex=str(seat_identity.public_key_hex),
        )
        result_hash = str(proof_body["result_sha256"])
        receipt = _create_delivery_receipt(
            operation_id=operation_id,
            continuity=continuity,
            replacement_assignment=assignment,
            seat_identity=seat_identity,
            delivery_claim_id=claim_id,
            delivery_idempotency_key=idempotency_key,
            delivery_claim_commit_sha256=claim["claim_commit_sha256"],
            actuation_proof=verified_proof,
            result_sha256=result_hash,
            delivered_at=(
                float(proof_body["acted_at"])
                if delivered_at is None
                else delivered_at
            ),
            _claim_seal=_SEAT_CLAIM_SEAL,
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE delivery_claim_v1 SET state='completed',receipt_json=?,completed_at=? "
                "WHERE assignment_sha256=? AND claim_id=? AND state='claimed'",
                (_canonical(receipt), time.time(), assignment_hash, claim_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise AssignmentFailoverError("seat delivery claim completion conflicted")
            connection.commit()
        if after_commit is not None:
            after_commit()
        return receipt


def _create_delivery_receipt(
    *,
    operation_id: str,
    continuity: Mapping[str, Any],
    replacement_assignment: Mapping[str, Any],
    seat_identity: Any,
    delivery_claim_id: str,
    delivery_idempotency_key: str,
    delivery_claim_commit_sha256: str,
    actuation_proof: Mapping[str, Any],
    result_sha256: str,
    delivered_at: float | None = None,
    _claim_seal: object | None = None,
) -> dict[str, Any]:
    """Seat-side helper: sign proof that the exact replacement was received.

    The failover coordinator never calls this helper.  A guarded delivery
    implementation must obtain this proof from the enrolled replacement seat.
    """
    if _claim_seal is not _SEAT_CLAIM_SEAL:
        raise AssignmentFailoverError(
            "delivery receipts require a durable seat-side claim store"
        )
    assignment = _snapshot(replacement_assignment, "replacement assignment", require_canonical_wire=False)
    if key_id(str(seat_identity.public_key_hex)) != assignment.get("receiver_key_id"):
        raise AssignmentFailoverError("delivery receipt signer is not the replacement seat")
    body = {
        "schema": DELIVERY_RECEIPT_SCHEMA,
        "operation_id": operation_id,
        "continuity_sha256": _digest(continuity),
        "replacement_assignment_sha256": _digest(assignment),
        "replacement_assignment_id": assignment["assignment_id"],
        "receiver_birth_id": assignment["receiver_birth_id"],
        "receiver_generation": assignment["receiver_generation"],
        "receiver_key_id": assignment["receiver_key_id"],
        "receiver_seat_epoch": assignment["receiver_seat_epoch"],
        "target_identity_sha256": assignment["target_identity_sha256"],
        "terminal_tab_identity_sha256": assignment["terminal_tab_identity_sha256"],
        "response_channel_sha256": assignment["response_channel_sha256"],
        "delivery_claim_id": delivery_claim_id,
        "delivery_idempotency_key": delivery_idempotency_key,
        "delivery_claim_commit_sha256": delivery_claim_commit_sha256,
        "actuation_proof": _snapshot(
            actuation_proof,
            "seat actuation proof",
            require_canonical_wire=False,
        ),
        "result_sha256": result_sha256,
        "delivery_nonce": secrets.token_hex(32),
        "delivered_at": _finite(delivered_at, "delivery time"),
    }
    return _signed(body, seat_identity)


def _verify_delivery_receipt(
    receipt: Any,
    *,
    operation_id: str,
    continuity: Mapping[str, Any],
    assignment: Mapping[str, Any],
    replacement: Mapping[str, Any],
    now: float,
    require_fresh: bool,
) -> dict[str, Any]:
    snap, body = _verified_body(receipt, str(replacement["seat_public_key_hex"]), "delivery receipt")
    exact = {
        "schema": DELIVERY_RECEIPT_SCHEMA,
        "operation_id": operation_id,
        "continuity_sha256": _digest(continuity),
        "replacement_assignment_sha256": _digest(assignment),
        "replacement_assignment_id": assignment["assignment_id"],
        "receiver_birth_id": assignment["receiver_birth_id"],
        "receiver_generation": assignment["receiver_generation"],
        "receiver_key_id": assignment["receiver_key_id"],
        "receiver_seat_epoch": assignment["receiver_seat_epoch"],
        "target_identity_sha256": assignment["target_identity_sha256"],
        "terminal_tab_identity_sha256": assignment["terminal_tab_identity_sha256"],
        "response_channel_sha256": assignment["response_channel_sha256"],
        "delivery_idempotency_key": f"failover-delivery:{operation_id}",
    }
    expected_fields = set(exact) | {
        "actuation_proof",
        "delivery_claim_id",
        "delivery_claim_commit_sha256",
        "delivery_nonce",
        "delivered_at",
        "result_sha256",
    }
    if set(body) != expected_fields or any(body.get(field) != value for field, value in exact.items()):
        raise AssignmentFailoverError("delivery receipt exact binding mismatch")
    nonce = body.get("delivery_nonce")
    if type(nonce) is not str or len(nonce) != 64 or any(ch not in "0123456789abcdef" for ch in nonce):
        raise AssignmentFailoverError("delivery receipt nonce is invalid")
    for field in (
        "delivery_claim_id",
        "delivery_claim_commit_sha256",
        "result_sha256",
    ):
        value = body.get(field)
        if type(value) is not str or len(value) != 64 or any(
            ch not in "0123456789abcdef" for ch in value
        ):
            raise AssignmentFailoverError(f"delivery receipt {field} is invalid")
    claim = {
        "schema": "selfconnect-seat-delivery-claim-v1",
        "claim_id": body["delivery_claim_id"],
        "idempotency_key": body["delivery_idempotency_key"],
        "operation_id": operation_id,
        "replacement_assignment_sha256": body["replacement_assignment_sha256"],
        "continuity_sha256": body["continuity_sha256"],
    }
    claim["claim_commit_sha256"] = _digest(claim)
    if not secrets.compare_digest(
        claim["claim_commit_sha256"],
        str(body["delivery_claim_commit_sha256"]),
    ):
        raise AssignmentFailoverError("delivery receipt claim commit is invalid")
    _proof, proof_body = _verify_seat_actuation_proof(
        body.get("actuation_proof"),
        claim=claim,
        seat_public_key_hex=str(replacement["seat_public_key_hex"]),
    )
    if not secrets.compare_digest(
        str(proof_body["result_sha256"]),
        str(body["result_sha256"]),
    ):
        raise AssignmentFailoverError("delivery receipt actuation result mismatch")
    delivered = _finite(body.get("delivered_at"), "delivery receipt time")
    if _finite(proof_body.get("acted_at"), "actuation time") != delivered:
        raise AssignmentFailoverError("delivery receipt actuation time mismatch")
    if delivered < float(assignment["issued_at"]) or delivered > float(assignment["expires_at"]):
        raise AssignmentFailoverError("delivery receipt is outside replacement assignment validity")
    if require_fresh and abs(delivered - now) > MAX_DELIVERY_CLOCK_SKEW_SECONDS:
        raise AssignmentFailoverError("delivery receipt is not fresh")
    return snap


def _guard(
    resolver: Callable[..., bool],
    *,
    target: dict[str, Any],
    tab: dict[str, Any],
    channel: dict[str, Any],
    operation_id: str,
) -> None:
    try:
        result = resolver(
            target_identity=target,
            terminal_tab_identity=tab,
            response_channel=channel,
            operation_id=operation_id,
            stage="before_delivery",
        )
    except Exception as exc:
        raise AssignmentFailoverError("high-assurance target resolver failed") from exc
    if result is not True:
        raise AssignmentFailoverError("high-assurance target resolver refused")


def _checkpoint(hook: Callable[[str, dict[str, Any]], None] | None, name: str, operation: dict[str, Any]) -> None:
    if hook is not None:
        hook(name, operation)


def failover_assignment(
    assignment: Any,
    receipt: Any,
    *,
    context: FailoverLaunchContext,
    role: str,
    mesh: str,
    coordinator_identity: Any,
    authority_public_key_hex: str,
    replacement_enrollment: Any,
    replacement_target_identity: Any,
    replacement_terminal_tab_identity: Any,
    replacement_response_receiver_public_key_hex: str,
    replacement_response_channel: Mapping[str, Any],
    high_assurance_target_resolver: Callable[..., bool],
    guarded_delivery: Callable[..., Any],
    audit_append: Callable[..., Mapping[str, Any]] = sc_mesh_registry.append_event,
    registry_transition: Callable[..., Mapping[str, Any]] = sc_mesh_registry.transition_agent_off_rails_exact,
    revoked_coordinator_key_ids: frozenset[str] = frozenset(),
    revoked_seat_key_ids: frozenset[str] = frozenset(),
    now: float | None = None,
    replacement_ttl_seconds: float = 300.0,
    stage_hook: Callable[[str, dict[str, Any]], None] | None = None,
    **receipt_verification: Any,
) -> dict[str, Any]:
    """Run or resume one exact receipt-authorized failover saga."""
    context = _require_launch_context(context)
    store = context.assignment_store
    saga = context.saga_store
    registry_path = context.registry_path
    current = _finite(now, "failover time")
    if not role.strip() or not mesh.strip():
        raise AssignmentFailoverError("role and mesh are required")
    for boundary in (
        high_assurance_target_resolver,
        guarded_delivery,
        audit_append,
        registry_transition,
    ):
        if not callable(boundary):
            raise AssignmentFailoverError("failover high-assurance boundaries are required")

    assignment_snap = _snapshot(assignment, "predecessor assignment")
    receipt_snap = _snapshot(receipt, "authorizing receipt")
    replacement_snap = _snapshot(replacement_enrollment, "replacement enrollment")
    target = _snapshot(replacement_target_identity, "replacement target", require_canonical_wire=False)
    tab = _snapshot(replacement_terminal_tab_identity, "replacement TerminalTab", require_canonical_wire=False)
    channel = _snapshot(replacement_response_channel, "replacement response channel", require_canonical_wire=False)
    if set(receipt_verification) != _RECEIPT_VERIFICATION_FIELDS:
        raise AssignmentFailoverError("exact receipt verification context is required")
    recovery_verification = {
        "pinned_coordinator_public_key_hex": receipt_verification[
            "pinned_coordinator_public_key_hex"
        ],
        "expected_coordinator_birth_id": receipt_verification[
            "expected_coordinator_birth_id"
        ],
        "expected_coordinator_generation": receipt_verification[
            "expected_coordinator_generation"
        ],
        "expected_receiver_birth_id": receipt_verification["expected_receiver_birth_id"],
        "expected_receiver_generation": receipt_verification["expected_receiver_generation"],
        "expected_target_identity": _snapshot(
            receipt_verification["expected_target_identity"],
            "predecessor target recovery context",
            require_canonical_wire=False,
        ),
        "expected_terminal_tab_identity": _snapshot(
            receipt_verification["expected_terminal_tab_identity"],
            "predecessor TerminalTab recovery context",
            require_canonical_wire=False,
        ),
        "expected_response_receiver_public_key_hex": receipt_verification[
            "expected_response_receiver_public_key_hex"
        ],
        "expected_response_channel": _snapshot(
            receipt_verification["expected_response_channel"],
            "predecessor response-channel recovery context",
            require_canonical_wire=False,
        ),
    }
    try:
        replacement = verify_enrollment(
            replacement_snap,
            authority_public_key_hex=authority_public_key_hex,
            revoked_key_ids=revoked_seat_key_ids,
            now=current,
        )
    except (TypeError, ValueError) as exc:
        raise AssignmentFailoverError(f"replacement enrollment verification failed: {exc}") from exc

    receipt_hash = _digest(receipt_snap)
    raw_old = {
        "birth_id": receipt_snap.get("receiver_birth_id"),
        "generation": receipt_snap.get("receiver_generation"),
        "seat_key_id": receipt_snap.get("receiver_key_id"),
        "seat_epoch": receipt_snap.get("receiver_seat_epoch"),
    }
    new = {
        "birth_id": replacement["birth_id"],
        "generation": replacement["generation"],
        "seat_key_id": replacement["seat_key_id"],
        "seat_epoch": replacement["seat_epoch"],
    }
    if new["birth_id"] == raw_old["birth_id"]:
        raise AssignmentFailoverError("replacement birth_id must be fresh")
    if type(raw_old["generation"]) is not int or new["generation"] <= raw_old["generation"]:
        raise AssignmentFailoverError("replacement generation must be higher")
    if new["seat_key_id"] == raw_old["seat_key_id"]:
        raise AssignmentFailoverError("replacement seat key must be distinct")
    if new["seat_epoch"] == raw_old["seat_epoch"]:
        raise AssignmentFailoverError("replacement seat epoch must be distinct")
    coordinator_key_id = key_id(str(coordinator_identity.public_key_hex))
    if coordinator_key_id in revoked_coordinator_key_ids:
        raise AssignmentFailoverError("failover coordinator key is revoked")

    spec = {
        "schema": "selfconnect-assignment-failover-operation-v1",
        "receipt_sha256": receipt_hash,
        "predecessor_assignment_sha256": _digest(assignment_snap),
        "role": role,
        "mesh": mesh,
        "old_seat": raw_old,
        "replacement_seat": new,
        "replacement_enrollment_sha256": _digest(replacement_snap),
        "target_identity_sha256": _digest(target),
        "terminal_tab_identity_sha256": _digest(tab),
        "response_channel_sha256": _digest(channel),
        "response_receiver_key_id": key_id(replacement_response_receiver_public_key_hex),
        "coordinator_key_id": coordinator_key_id,
        "predecessor_assignment": assignment_snap,
        "authorizing_receipt": receipt_snap,
        "replacement_enrollment": replacement_snap,
        "replacement_target_identity": target,
        "replacement_terminal_tab_identity": tab,
        "replacement_response_channel": channel,
        "replacement_response_receiver_public_key_hex": (
            replacement_response_receiver_public_key_hex
        ),
        "receipt_verification": recovery_verification,
        "launch_event_head_anchor": context.event_head_anchor,
        "launch_event_log_path_sha256": hashlib.sha256(
            str(context.event_log_path).encode("utf-8")
        ).hexdigest(),
    }
    lock_path = saga.path.with_name(f"{saga.path.name}.run.lock")
    with FileLock(lock_path):
        preverified: dict[str, Any] | None = None
        if saga.find_by_receipt(receipt_hash) is None:
            try:
                preverified = verify_state_receipt_authorization(
                    receipt_snap,
                    assignment_snap,
                    store=store,
                    authority_public_key_hex=authority_public_key_hex,
                    revoked_coordinator_key_ids=revoked_coordinator_key_ids,
                    revoked_seat_key_ids=revoked_seat_key_ids,
                    now=current,
                    **receipt_verification,
                )
            except AssignmentVerificationError as exc:
                raise AssignmentFailoverError(str(exc)) from exc
            try:
                store.require_receipt("consumed", receipt_snap)
            except AssignmentVerificationError:
                pass
            else:
                raise FailoverConflictError(
                    "receipt was consumed outside a durable failover saga"
                )
        operation, created = saga.prepare(spec)
        operation_id = operation["operation_id"]
        try:
            if created:
                _checkpoint(stage_hook, "after_saga_prepare", operation)
            if operation["stage"] != "prepared":
                recovered = verify_durably_consumed_state_receipt(
                    receipt_snap,
                    assignment_snap,
                    store=store,
                    authority_public_key_hex=authority_public_key_hex,
                    revoked_coordinator_key_ids=revoked_coordinator_key_ids,
                    revoked_seat_key_ids=revoked_seat_key_ids,
                    now=current,
                    **receipt_verification,
                )
                recovered_old = {
                    "birth_id": recovered["receiver_birth_id"],
                    "generation": recovered["receiver_generation"],
                    "seat_key_id": recovered["receiver_key_id"],
                    "seat_epoch": recovered["receiver_seat_epoch"],
                }
                if (
                    recovered["state"] not in FAILOVER_STATES
                    or recovered_old != raw_old
                    or recovered["coordinator_key_id"] != coordinator_key_id
                ):
                    raise AssignmentFailoverError("durable failover authorization binding mismatch")
            if operation["stage"] == "prepared":
                try:
                    verified = preverified or verify_state_receipt_authorization(
                            receipt_snap,
                            assignment_snap,
                            store=store,
                            authority_public_key_hex=authority_public_key_hex,
                            revoked_coordinator_key_ids=revoked_coordinator_key_ids,
                            revoked_seat_key_ids=revoked_seat_key_ids,
                            now=current,
                            **receipt_verification,
                        )
                    newly_committed, commit_hash = store.consume_receipt(receipt_snap)
                    if created and not newly_committed:
                        raise FailoverConflictError("receipt was consumed outside this failover saga")
                except AssignmentVerificationError:
                    if created:
                        raise
                    try:
                        verified = verify_durably_consumed_state_receipt(
                            receipt_snap,
                            assignment_snap,
                            store=store,
                            authority_public_key_hex=authority_public_key_hex,
                            revoked_coordinator_key_ids=revoked_coordinator_key_ids,
                            revoked_seat_key_ids=revoked_seat_key_ids,
                            now=current,
                            **receipt_verification,
                        )
                        commit_hash = str(verified["commit_sha256"])
                    except AssignmentVerificationError:
                        receipt_time = _finite(
                            receipt_snap.get("issued_at"), "prepared receipt recovery time"
                        )
                        verified = verify_state_receipt_authorization(
                            receipt_snap,
                            assignment_snap,
                            store=store,
                            authority_public_key_hex=authority_public_key_hex,
                            revoked_coordinator_key_ids=revoked_coordinator_key_ids,
                            revoked_seat_key_ids=revoked_seat_key_ids,
                            now=receipt_time,
                            **receipt_verification,
                        )
                        newly_committed, commit_hash = store.consume_receipt(receipt_snap)
                        if not newly_committed:
                            raise FailoverConflictError(
                                "prepared receipt recovery conflicted with durable consumption"
                            )
                if verified["state"] not in FAILOVER_STATES:
                    raise AssignmentFailoverError("only blocked or rejected receipt authorizes failover")
                exact_old = {
                    "birth_id": verified["receiver_birth_id"],
                    "generation": verified["receiver_generation"],
                    "seat_key_id": verified["receiver_key_id"],
                    "seat_epoch": verified["receiver_seat_epoch"],
                }
                if exact_old != raw_old or verified["coordinator_key_id"] != coordinator_key_id:
                    raise AssignmentFailoverError("verified failover identity binding mismatch")
                operation = saga.advance(
                    operation_id,
                    "prepared",
                    "authorized",
                    receipt_commit_sha256=commit_hash,
                )

            common_audit = {
                "operation_id": operation_id,
                "assignment_id": receipt_snap["assignment_id"],
                "authorizing_receipt_sha256": receipt_hash,
                "predecessor_assignment_sha256": spec["predecessor_assignment_sha256"],
                "old_seat_key_id": raw_old["seat_key_id"],
                "old_seat_epoch": raw_old["seat_epoch"],
                "replacement_seat": new,
                "process_action": "none",
            }
            if operation["stage"] == "authorized":
                result = audit_append(
                    "assignment_failover_intent",
                    role=role,
                    mesh=mesh,
                    birth_id=str(raw_old["birth_id"]),
                    generation=int(raw_old["generation"]),
                    status="pending",
                    summary="authenticated failover intent pending exact registry CAS",
                    data=common_audit,
                    registry_path=registry_path,
                    strict=True,
                    strict_idempotency_key=f"assignment-failover-intent:{operation_id}",
                )
                if not isinstance(result, Mapping) or result.get("ok") is not True:
                    raise AssignmentFailoverError("strict failover intent audit failed")
                _checkpoint(stage_hook, "after_intent_audit", operation)
                operation = saga.advance(operation_id, "authorized", "intent_audited")

            if operation["stage"] == "intent_audited":
                transition = registry_transition(
                    role,
                    mesh=mesh,
                    expected_birth_id=str(raw_old["birth_id"]),
                    expected_generation=int(raw_old["generation"]),
                    expected_seat_key_id=str(raw_old["seat_key_id"]),
                    expected_seat_epoch=str(raw_old["seat_epoch"]),
                    registry_path=registry_path,
                )
                if not isinstance(transition, Mapping) or transition.get("ok") is not True:
                    error = transition.get("error", "registry CAS refused") if isinstance(transition, Mapping) else "invalid registry CAS result"
                    raise AssignmentFailoverError(f"exact old-seat registry CAS failed: {error}")
                _checkpoint(stage_hook, "after_registry_cas", operation)
                operation = saga.advance(operation_id, "intent_audited", "off_rails")

            if operation["stage"] == "off_rails":
                result = audit_append(
                    "assignment_failover_off_rails",
                    role=role,
                    mesh=mesh,
                    birth_id=str(raw_old["birth_id"]),
                    generation=int(raw_old["generation"]),
                    status="off_rails",
                    summary="exact authenticated assignment seat transitioned off rails",
                    data=common_audit,
                    registry_path=registry_path,
                    strict=True,
                    strict_idempotency_key=f"assignment-failover-off-rails:{operation_id}",
                )
                if not isinstance(result, Mapping) or result.get("ok") is not True:
                    raise AssignmentFailoverError("strict off-rails audit failed")
                _checkpoint(stage_hook, "after_off_rails_audit", operation)
                operation = saga.advance(operation_id, "off_rails", "off_rails_audited")

            if operation["stage"] == "off_rails_audited":
                continuity_body = {
                    "schema": CONTINUITY_SCHEMA,
                    "operation_id": operation_id,
                    "predecessor_assignment_id": assignment_snap["assignment_id"],
                    "predecessor_assignment_sha256": spec["predecessor_assignment_sha256"],
                    "authorizing_receipt_sha256": receipt_hash,
                    "authorizing_receipt_commit_sha256": operation["receipt_commit_sha256"],
                    "old_seat": raw_old,
                    "replacement_seat": new,
                    "target_identity_sha256": spec["target_identity_sha256"],
                    "terminal_tab_identity_sha256": spec["terminal_tab_identity_sha256"],
                    "response_channel_sha256": spec["response_channel_sha256"],
                    "coordinator_key_id": coordinator_key_id,
                    "issued_at": current,
                }
                continuity = _signed(continuity_body, coordinator_identity)
                continuation_payload = _canonical(
                    {
                        "schema": CONTINUATION_PAYLOAD_SCHEMA,
                        "predecessor_payload": assignment_snap["payload"],
                        "continuity": continuity,
                    }
                ).decode("ascii")
                requested_ttl = _finite(replacement_ttl_seconds, "replacement assignment TTL")
                remaining_enrollment = float(replacement["expires_at"]) - current
                effective_ttl = min(requested_ttl, remaining_enrollment)
                if effective_ttl <= 0:
                    raise AssignmentFailoverError("replacement enrollment expired before assignment issue")
                replacement_assignment = build_assignment(
                    continuation_payload,
                    coordinator_identity=coordinator_identity,
                    coordinator_birth_id=receipt_snap["coordinator_birth_id"],
                    coordinator_generation=receipt_snap["coordinator_generation"],
                    receiver_enrollment=replacement_snap,
                    authority_public_key_hex=authority_public_key_hex,
                    target_identity=target,
                    terminal_tab_identity=tab,
                    response_receiver_public_key_hex=replacement_response_receiver_public_key_hex,
                    response_channel=channel,
                    revoked_coordinator_key_ids=revoked_coordinator_key_ids,
                    revoked_seat_key_ids=revoked_seat_key_ids,
                    now=current,
                    ttl_seconds=effective_ttl,
                )
                operation = saga.advance(
                    operation_id,
                    "off_rails_audited",
                    "assignment_built",
                    continuity_json=continuity,
                    replacement_assignment_json=replacement_assignment,
                )
                _checkpoint(stage_hook, "after_assignment_build", operation)

            if operation["stage"] == "assignment_built":
                replacement_assignment = operation["replacement_assignment"]
                if replacement_assignment is None:
                    raise AssignmentFailoverError("replacement assignment outbox is missing")
                store.ensure_assignment("issued", replacement_assignment)
                _checkpoint(stage_hook, "after_assignment_issue", operation)
                operation = saga.advance(operation_id, "assignment_built", "assignment_issued")

            if operation["stage"] == "assignment_issued":
                continuity = operation["continuity"]
                replacement_assignment = operation["replacement_assignment"]
                if continuity is None or replacement_assignment is None:
                    raise AssignmentFailoverError("failover outbox is incomplete")
                _guard(
                    high_assurance_target_resolver,
                    target=target,
                    tab=tab,
                    channel=channel,
                    operation_id=operation_id,
                )
                raw_delivery = guarded_delivery(
                    operation_id=operation_id,
                    continuity=continuity,
                    assignment=replacement_assignment,
                    target_identity=target,
                    terminal_tab_identity=tab,
                    response_channel=channel,
                )
                delivery = _verify_delivery_receipt(
                    raw_delivery,
                    operation_id=operation_id,
                    continuity=continuity,
                    assignment=replacement_assignment,
                    replacement=replacement,
                    now=current,
                    require_fresh=True,
                )
                operation = saga.advance(
                    operation_id,
                    "assignment_issued",
                    "delivered",
                    delivery_receipt_json=delivery,
                )
                _checkpoint(stage_hook, "after_delivery", operation)

            if operation["stage"] != "delivered":
                raise AssignmentFailoverError("failover saga did not reach delivery")
            continuity = operation["continuity"]
            replacement_assignment = operation["replacement_assignment"]
            delivery = operation["delivery_receipt"]
            if continuity is None or replacement_assignment is None or delivery is None:
                raise AssignmentFailoverError("completed failover outbox is incomplete")
            _verify_delivery_receipt(
                delivery,
                operation_id=operation_id,
                continuity=continuity,
                assignment=replacement_assignment,
                replacement=replacement,
                now=current,
                require_fresh=False,
            )
            return {
                "ok": True,
                "stage": "delivered",
                "operation_id": operation_id,
                "old_seat": raw_old,
                "replacement_seat": new,
                "continuity": continuity,
                "replacement_assignment": replacement_assignment,
                "delivery_receipt": delivery,
                "process_action": "none",
                "resumed": not created,
            }
        except Exception as exc:
            saga.record_error(operation_id, exc)
            if isinstance(exc, AssignmentFailoverError):
                raise
            if isinstance(exc, AssignmentVerificationError):
                raise AssignmentFailoverError(str(exc)) from exc
            raise AssignmentFailoverError(f"failover saga boundary failed: {type(exc).__name__}:{exc}") from exc


__all__ = [
    "CONTINUITY_SCHEMA",
    "DELIVERY_RECEIPT_SCHEMA",
    "FAILOVER_STATES",
    "AssignmentFailoverError",
    "FailoverConflictError",
    "FailoverLaunchContext",
    "FailoverSagaStore",
    "SeatDeliveryClaimStore",
    "failover_assignment",
    "list_pending_failovers",
]
