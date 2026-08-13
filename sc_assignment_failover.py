"""Durable authenticated fresh-birth failover for assignment seats.

The authorizing receipt is verified cryptographically before a durable saga is
prepared.  Exact retries resume the persisted outbox; conflicting use of the
same receipt is rejected.  Registry state remains a compare-and-set routing
mirror, never proof.  This module has no process-termination capability.
Local files and same-user Credential Manager state provide ordinary-mode
consistency only; they do not resist a same-user paired rollback.  This build
therefore refuses high-assurance execution until a separately privileged
external monotonic authority is configured.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
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
from sc_windows_credentials import read_secret, write_secret

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
FAILOVER_ROOT_SCHEMA = "selfconnect-assignment-failover-root-v1"
FAILOVER_PROTECTED_STATE_SCHEMA = "selfconnect-assignment-failover-protected-state-v1"
FAILOVER_PROTECTED_ANCHOR_SCHEMA = "selfconnect-assignment-failover-protected-anchor-v1"
FAILOVER_ROOT_CONFIG_ENV = "SELFCONNECT_FAILOVER_ROOT_CONFIG"
FAILOVER_ROOT_PUBLIC_KEY_ENV = "SELFCONNECT_FAILOVER_ROOT_PUBLIC_KEY_HEX"
_LAUNCH_CONFIG_PATH = os.environ.get(FAILOVER_ROOT_CONFIG_ENV, "")
_LAUNCH_PUBLIC_KEY_HEX = os.environ.get(FAILOVER_ROOT_PUBLIC_KEY_ENV, "")
class AssignmentFailoverError(AssignmentVerificationError):
    """A failover authorization, saga, or high-assurance boundary failed."""


class FailoverConflictError(AssignmentFailoverError):
    """The authorizing receipt was reused with a different failover intent."""


@dataclass(frozen=True, slots=True, init=False)
class _FailoverLaunchContext:
    """Verified immutable launch root; never accepted by an action API."""

    assignment_store: AssignmentStateStore
    assignment_store_path: Path
    saga_store: _FailoverSagaStore
    saga_store_path: Path
    registry_path: Path
    event_log_path: Path
    protected_state_path: Path
    claim_store_path: Path
    claim_store_database_id: str
    saga_store_database_id: str
    root_id: str
    credential_target: str
    config_sha256: str
    _config_path: Path

    def __init__(
        self,
        *,
        config_path: str | Path,
        pinned_public_key_hex: str,
    ) -> None:
        target = Path(config_path).resolve(strict=True)
        record, body = _verified_body(
            target.read_bytes(), pinned_public_key_hex, "failover root config"
        )
        expected_fields = {
            "schema",
            "root_id",
            "credential_target",
            "assignment_store_path",
            "assignment_store_database_id",
            "saga_store_path",
            "saga_store_database_id",
            "registry_path",
            "event_log_path",
            "protected_state_path",
            "claim_store_path",
            "claim_store_database_id",
            "external_monotonic_authority",
        }
        if set(body) != expected_fields or body.get("schema") != FAILOVER_ROOT_SCHEMA:
            raise AssignmentFailoverError("failover root config fields are invalid")
        if body.get("external_monotonic_authority") is not None:
            raise AssignmentFailoverError(
                "unsupported external monotonic authority configuration"
            )
        for field in (
            "root_id",
            "assignment_store_database_id",
            "saga_store_database_id",
            "claim_store_database_id",
        ):
            _require_hex64(body.get(field), f"failover root {field}")
        root_id = str(body["root_id"])
        credential_target = body.get("credential_target")
        if (
            type(credential_target) is not str
            or credential_target != f"SelfConnect/Failover/{root_id}"
        ):
            raise AssignmentFailoverError("failover protected-state target is invalid")
        paths = {
            name: _canonical_absolute_path(body.get(f"{name}_path"), name)
            for name in (
                "assignment_store",
                "saga_store",
                "registry",
                "event_log",
                "claim_store",
                "protected_state",
            )
        }
        if len(set(paths.values())) != len(paths):
            raise AssignmentFailoverError("failover root paths must be distinct")
        expected_event = sc_mesh_registry.default_event_log_path(paths["registry"]).resolve(
            strict=False
        )
        if paths["event_log"] != expected_event:
            raise AssignmentFailoverError("failover event-log path is not derived from the registry")
        assignment_store = AssignmentStateStore(paths["assignment_store"])
        _verify_database_id(
            paths["assignment_store"],
            "failover_assignment_store_meta_v1",
            str(body["assignment_store_database_id"]),
        )
        saga_store = _FailoverSagaStore(
            paths["saga_store"], database_id=str(body["saga_store_database_id"])
        )
        object.__setattr__(self, "saga_store", saga_store)
        object.__setattr__(self, "saga_store_path", saga_store.path)
        object.__setattr__(self, "assignment_store", assignment_store)
        object.__setattr__(self, "assignment_store_path", assignment_store.path.resolve())
        object.__setattr__(self, "registry_path", paths["registry"])
        object.__setattr__(self, "event_log_path", paths["event_log"])
        object.__setattr__(self, "protected_state_path", paths["protected_state"])
        object.__setattr__(self, "claim_store_path", paths["claim_store"])
        object.__setattr__(
            self, "claim_store_database_id", str(body["claim_store_database_id"])
        )
        object.__setattr__(
            self, "saga_store_database_id", str(body["saga_store_database_id"])
        )
        object.__setattr__(self, "root_id", root_id)
        object.__setattr__(self, "credential_target", credential_target)
        object.__setattr__(self, "config_sha256", _digest(record))
        object.__setattr__(self, "_config_path", target)
        _SeatDeliveryClaimStore(
            self,
            database_id=str(body["claim_store_database_id"]),
        )
        self.verify_protected_state()

    def require_assurance(self, high_assurance: bool) -> dict[str, Any]:
        if type(high_assurance) is not bool:
            raise AssignmentFailoverError("high_assurance must be an exact bool")
        if high_assurance:
            raise AssignmentFailoverError(
                "high-assurance failover refused: no separately privileged "
                "external monotonic authority is configured"
            )
        # Keep construction local and literal.  A module attribute must never
        # be able to redefine the assurance asserted by signed artifacts.
        return {
            "mode": "ordinary",
            "same_user_threat": "excluded",
            "external_monotonic_authority": "absent",
            "monotonicity": "local_best_effort",
            "high_assurance": False,
        }

    def require_delivery_claim(
        self,
        delivery: Mapping[str, Any],
        replacement_assignment: Mapping[str, Any],
    ) -> None:
        """Require the receipt to exist in the launch-pinned completed claim row.

        Receipt construction helpers and signatures are not claim authority.
        In ordinary mode this is a same-user-local consistency check only.
        """
        receipt = _snapshot(
            delivery,
            "delivery receipt",
            require_canonical_wire=False,
        )
        assignment = _snapshot(
            replacement_assignment,
            "replacement assignment",
            require_canonical_wire=False,
        )
        assignment_hash = _digest(assignment)
        state = self.verify_protected_state()
        protected = state["seat_claims"].get(assignment_hash)
        if (
            type(protected) is not dict
            or protected.get("status") != "completed"
            or protected.get("operation_id") != receipt.get("operation_id")
            or protected.get("claim_id") != receipt.get("delivery_claim_id")
            or protected.get("idempotency_key")
            != receipt.get("delivery_idempotency_key")
            or protected.get("claim_store_database_id") != self.claim_store_database_id
            or receipt.get("failover_root_id") != self.root_id
            or receipt.get("claim_store_database_id") != self.claim_store_database_id
            or not secrets.compare_digest(
                str(protected.get("receipt_sha256", "")),
                _digest(receipt),
            )
        ):
            raise AssignmentFailoverError(
                "delivery receipt has no launch-pinned completed seat claim authority"
            )

    def _read_state(self) -> dict[str, Any]:
        raw = read_secret(self.credential_target)
        if raw is None:
            raise AssignmentFailoverError("failover protected anchor is absent")
        anchor = _snapshot(raw, "failover protected anchor")
        if (
            set(anchor)
            != {"schema", "root_id", "config_sha256", "revision", "state_sha256"}
            or anchor.get("schema") != FAILOVER_PROTECTED_ANCHOR_SCHEMA
            or anchor.get("root_id") != self.root_id
            or anchor.get("config_sha256") != self.config_sha256
            or type(anchor.get("revision")) is not int
            or int(anchor["revision"]) < 1
        ):
            raise AssignmentFailoverError("failover protected anchor is invalid")
        _require_hex64(anchor.get("state_sha256"), "failover protected state hash")
        try:
            state_raw = self.protected_state_path.read_bytes()
        except OSError as exc:
            raise AssignmentFailoverError("failover protected state is absent") from exc
        if not secrets.compare_digest(
            hashlib.sha256(state_raw).hexdigest(), str(anchor["state_sha256"])
        ):
            raise AssignmentFailoverError("failover protected state rolled back or diverged")
        state = _snapshot(state_raw, "failover protected state")
        if (
            set(state) != {"schema", "root_id", "config_sha256", "revision", "event_anchor", "operations", "seat_claims"}
            or state.get("schema") != FAILOVER_PROTECTED_STATE_SCHEMA
            or state.get("root_id") != self.root_id
            or state.get("config_sha256") != self.config_sha256
            or type(state.get("revision")) is not int
            or int(state["revision"]) < 1
            or state.get("revision") != anchor.get("revision")
            or type(state.get("operations")) is not dict
            or type(state.get("seat_claims")) is not dict
        ):
            raise AssignmentFailoverError("failover protected state is invalid")
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        current = self._read_state()
        expected = int(current["revision"]) + 1
        if state.get("revision") != expected:
            raise AssignmentFailoverError("failover protected-state revision conflicted")
        raw = _canonical(state)
        staged = self.protected_state_path.with_suffix(
            self.protected_state_path.suffix + ".tmp"
        )
        with staged.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(self.protected_state_path)
        anchor = {
            "schema": FAILOVER_PROTECTED_ANCHOR_SCHEMA,
            "root_id": self.root_id,
            "config_sha256": self.config_sha256,
            "revision": state["revision"],
            "state_sha256": hashlib.sha256(raw).hexdigest(),
        }
        write_secret(self.credential_target, _canonical(anchor))
        if self._read_state() != state:
            raise AssignmentFailoverError("failover protected-state write did not verify")

    def verify_protected_state(self) -> dict[str, Any]:
        state = self._read_state()
        config = _snapshot(self._config_path.read_bytes(), "failover root config")
        _verify_database_id(
            self.assignment_store_path,
            "failover_assignment_store_meta_v1",
            str(config["assignment_store_database_id"]),
        )
        _verify_database_id(
            self.saga_store_path,
            "failover_saga_store_meta_v1",
            self.saga_store_database_id,
        )
        _verify_event_anchor(self.event_log_path, state["event_anchor"])
        for operation_id, protected in state["operations"].items():
            _require_hex64(operation_id, "protected operation id")
            if type(protected) is not dict:
                raise AssignmentFailoverError("protected failover operation is invalid")
            operation = self.saga_store.find_by_receipt(str(protected.get("receipt_sha256", "")))
            if operation is None:
                raise AssignmentFailoverError("protected failover operation is absent from the canonical saga")
            if (
                operation["operation_id"] != operation_id
                or not secrets.compare_digest(
                    _digest(operation["spec"]), str(protected.get("spec_sha256", ""))
                )
            ):
                raise AssignmentFailoverError("protected failover operation binding mismatch")
            protected_stage = protected.get("stage")
            if protected_stage not in _STAGE_INDEX or operation["stage"] != protected_stage:
                raise AssignmentFailoverError("canonical failover saga differs from protected high-water")
            protected_saga = protected.get("saga_sha256")
            if protected_saga is not None and not secrets.compare_digest(
                str(protected_saga), _operation_anchor(operation)
            ):
                raise AssignmentFailoverError("canonical failover saga provenance mismatch")
            for event in protected.get("events", {}).values():
                _verify_event_anchor(self.event_log_path, event)
        _verify_database_id(
            self.claim_store_path,
            "failover_claim_store_meta_v1",
            self.claim_store_database_id,
        )
        with sqlite3.connect(self.claim_store_path) as connection:
            for assignment_sha256, protected in state["seat_claims"].items():
                _require_hex64(assignment_sha256, "protected seat assignment hash")
                if (
                    type(protected) is not dict
                    or protected.get("claim_store_database_id")
                    != self.claim_store_database_id
                    or protected.get("status") not in {"reserved", "claimed", "completed"}
                ):
                    raise AssignmentFailoverError("protected seat claim is invalid")
                row = connection.execute(
                    "SELECT idempotency_key,claim_id,state,receipt_json "
                    "FROM delivery_claim_v1 WHERE assignment_sha256=?",
                    (assignment_sha256,),
                ).fetchone()
                if row is None:
                    raise AssignmentFailoverError(
                        "seat delivery claim store rolled back below protected high-water"
                    )
                if (
                    row[0] != protected.get("idempotency_key")
                    or row[1] != protected.get("claim_id")
                ):
                    raise AssignmentFailoverError("protected seat claim database binding mismatch")
                if protected["status"] == "completed":
                    if row[2] != "completed" or row[3] is None:
                        raise AssignmentFailoverError(
                            "seat delivery completion rolled back below protected high-water"
                        )
                    receipt = _snapshot(bytes(row[3]), "stored seat delivery receipt")
                    if not secrets.compare_digest(
                        _digest(receipt), str(protected.get("receipt_sha256", ""))
                    ):
                        raise AssignmentFailoverError("protected seat receipt integrity failed")
                elif protected["status"] == "claimed" and row[2] not in {"claimed", "completed"}:
                    raise AssignmentFailoverError("protected seat claim state is invalid")
        return state

    def reserve_operation(self, operation_id: str, receipt_sha256: str, spec_sha256: str) -> bool:
        state = self._read_state()
        existing = state["operations"].get(operation_id)
        exact = {
            "receipt_sha256": receipt_sha256,
            "spec_sha256": spec_sha256,
            "stage": "prepared",
            "saga_sha256": None,
            "events": {},
        }
        if existing is not None:
            if existing.get("receipt_sha256") != receipt_sha256 or existing.get("spec_sha256") != spec_sha256:
                raise FailoverConflictError("protected failover operation conflicts")
            return False
        if any(item.get("receipt_sha256") == receipt_sha256 for item in state["operations"].values()):
            raise FailoverConflictError("protected authorizing receipt was reused")
        state["operations"][operation_id] = exact
        state["revision"] += 1
        self._write_state(state)
        return True

    def advance_operation(self, operation_id: str, saga_operation: Mapping[str, Any]) -> None:
        state = self._read_state()
        operation = state["operations"].get(operation_id)
        stage = saga_operation.get("stage")
        if type(operation) is not dict or stage not in _STAGE_INDEX:
            raise AssignmentFailoverError("protected failover operation is invalid")
        prior = operation.get("stage")
        if prior not in _STAGE_INDEX or _STAGE_INDEX[stage] < _STAGE_INDEX[prior]:
            raise AssignmentFailoverError("protected failover high-water cannot move backward")
        saga_sha256 = _operation_anchor(saga_operation)
        if stage == prior and operation.get("saga_sha256") == saga_sha256:
            return
        operation["stage"] = stage
        operation["saga_sha256"] = saga_sha256
        state["revision"] += 1
        self._write_state(state)

    def record_event(self, operation_id: str, name: str, result: Mapping[str, Any]) -> None:
        event = result.get("event") if isinstance(result, Mapping) else None
        event_data = event.get("data") if type(event) is dict else None
        expected_type = {
            "intent": "assignment_failover_intent",
            "off_rails": "assignment_failover_off_rails",
        }.get(name)
        if (
            expected_type is None
            or type(event) is not dict
            or event.get("event_type") != expected_type
            or type(event_data) is not dict
            or event_data.get("operation_id") != operation_id
        ):
            raise AssignmentFailoverError("failover audit did not return the exact durable event")
        anchor = {
            "events_checked": _event_position(self.event_log_path, str(event.get("event_hash", ""))),
            "head_hash": str(event.get("event_hash", "")),
        }
        _verify_event_anchor(self.event_log_path, anchor)
        state = self._read_state()
        operation = state["operations"].get(operation_id)
        if type(operation) is not dict:
            raise AssignmentFailoverError("protected failover operation is absent")
        prior = operation["events"].get(name)
        if prior is not None and prior != anchor:
            raise AssignmentFailoverError("protected failover event provenance conflicted")
        if prior == anchor:
            return
        operation["events"][name] = anchor
        state["revision"] += 1
        self._write_state(state)

    def reserve_seat_claim(
        self,
        assignment_sha256: str,
        *,
        operation_id: str,
        idempotency_key: str,
        claim_id: str,
    ) -> tuple[dict[str, Any], bool]:
        state = self._read_state()
        exact = {
            "operation_id": operation_id,
            "idempotency_key": idempotency_key,
            "claim_id": claim_id,
            "claim_store_database_id": self.claim_store_database_id,
            "status": "reserved",
            "receipt_sha256": None,
        }
        existing = state["seat_claims"].get(assignment_sha256)
        if existing is not None:
            for field in ("operation_id", "idempotency_key", "claim_store_database_id"):
                if existing.get(field) != exact[field]:
                    raise FailoverConflictError("protected seat claim conflicts")
            return existing, False
        state["seat_claims"][assignment_sha256] = exact
        state["revision"] += 1
        self._write_state(state)
        return exact, True

    def advance_seat_claim(
        self,
        assignment_sha256: str,
        *,
        claim_id: str,
        status: str,
        receipt_sha256: str | None = None,
    ) -> None:
        if status not in {"claimed", "completed"}:
            raise AssignmentFailoverError("protected seat-claim status is invalid")
        state = self._read_state()
        claim = state["seat_claims"].get(assignment_sha256)
        if type(claim) is not dict or claim.get("claim_id") != claim_id:
            raise AssignmentFailoverError("protected seat claim binding mismatch")
        current = claim.get("status")
        order = {"reserved": 0, "claimed": 1, "completed": 2}
        if current not in order or order[status] < order[current]:
            raise AssignmentFailoverError("protected seat-claim high-water moved backward")
        if status == "completed":
            _require_hex64(receipt_sha256, "protected seat receipt hash")
        if current == status:
            if status == "completed" and claim.get("receipt_sha256") != receipt_sha256:
                raise AssignmentFailoverError("protected seat receipt hash conflicts")
            return
        claim["status"] = status
        claim["receipt_sha256"] = receipt_sha256
        state["revision"] += 1
        self._write_state(state)

def provision_failover_trust_root(
    *,
    config_path: str | Path,
    provisioning_identity: Any,
    assignment_store_path: str | Path,
    saga_path: str | Path,
    registry_path: str | Path,
    claim_store_path: str | Path,
    protected_state_path: str | Path,
) -> str:
    """Provision one signed path root and OS-protected failover high-water."""
    target = Path(config_path).resolve(strict=False)
    if target.exists():
        raise FileExistsError("failover trust root is already provisioned")
    paths = {
        "assignment_store": Path(assignment_store_path).resolve(strict=True),
        "saga_store": Path(saga_path).resolve(strict=False),
        "registry": Path(registry_path).resolve(strict=True),
        "claim_store": Path(claim_store_path).resolve(strict=False),
        "protected_state": Path(protected_state_path).resolve(strict=False),
    }
    paths["event_log"] = sc_mesh_registry.default_event_log_path(paths["registry"]).resolve(
        strict=False
    )
    if len(set(paths.values())) != len(paths):
        raise ValueError("failover trust-root paths must be distinct")
    if paths["protected_state"].exists():
        raise FileExistsError("failover protected state is already provisioned")
    for name in ("saga_store", "claim_store"):
        if paths[name].exists():
            raise FileExistsError(f"failover {name} is already provisioned")
    verified = sc_mesh_registry.verify_events(event_log_path=paths["event_log"])
    if verified.get("ok") is not True:
        raise AssignmentFailoverError("event log is invalid at failover provisioning")
    root_id = secrets.token_hex(32)
    database_ids = {name: secrets.token_hex(32) for name in ("assignment_store", "saga_store", "claim_store")}
    _initialize_database_id(paths["assignment_store"], "failover_assignment_store_meta_v1", database_ids["assignment_store"])
    _FailoverSagaStore(paths["saga_store"], database_id=database_ids["saga_store"])
    _initialize_claim_database(paths["claim_store"], database_ids["claim_store"])
    body = {
        "schema": FAILOVER_ROOT_SCHEMA,
        "root_id": root_id,
        "credential_target": f"SelfConnect/Failover/{root_id}",
        **{f"{name}_path": str(path) for name, path in paths.items()},
        **{f"{name}_database_id": value for name, value in database_ids.items()},
        "external_monotonic_authority": None,
    }
    signed = _signed(body, provisioning_identity)
    config_hash = _digest(signed)
    credential_target = str(body["credential_target"])
    if read_secret(credential_target) is not None:
        raise FileExistsError("failover protected state already exists")
    state = {
        "schema": FAILOVER_PROTECTED_STATE_SCHEMA,
        "root_id": root_id,
        "config_sha256": config_hash,
        "revision": 1,
        "event_anchor": {
            "events_checked": int(verified["events_checked"]),
            "head_hash": str(verified["head_hash"]),
        },
        "operations": {},
        "seat_claims": {},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(_canonical(signed))
        handle.flush()
        os.fsync(handle.fileno())
    state_path = paths["protected_state"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("xb") as handle:
        state_raw = _canonical(state)
        handle.write(state_raw)
        handle.flush()
        os.fsync(handle.fileno())
    anchor = {
        "schema": FAILOVER_PROTECTED_ANCHOR_SCHEMA,
        "root_id": root_id,
        "config_sha256": config_hash,
        "revision": state["revision"],
        "state_sha256": hashlib.sha256(state_raw).hexdigest(),
    }
    write_secret(credential_target, _canonical(anchor))
    return str(provisioning_identity.public_key_hex)


def initialize_failover_runtime() -> None:
    """Verify the launch-pinned root; no in-process object is an authority."""
    if not _LAUNCH_CONFIG_PATH or not _LAUNCH_PUBLIC_KEY_HEX:
        raise AssignmentFailoverError("failover launch root is not pinned by the process environment")
    _FailoverLaunchContext(
        config_path=_LAUNCH_CONFIG_PATH,
        pinned_public_key_hex=_LAUNCH_PUBLIC_KEY_HEX,
    )


def _require_launch_context() -> _FailoverLaunchContext:
    if not _LAUNCH_CONFIG_PATH or not _LAUNCH_PUBLIC_KEY_HEX:
        raise AssignmentFailoverError(
            "failover launch root is not pinned by the process environment"
        )
    context = _FailoverLaunchContext(
        config_path=_LAUNCH_CONFIG_PATH,
        pinned_public_key_hex=_LAUNCH_PUBLIC_KEY_HEX,
    )
    if context.assignment_store.path.resolve() != context.assignment_store_path:
        raise AssignmentFailoverError("launch assignment store path drifted")
    if context.saga_store.path.resolve() != context.saga_store_path:
        raise AssignmentFailoverError("launch saga path drifted")
    try:
        live_config = _snapshot(context._config_path.read_bytes(), "failover root config")
    except OSError as exc:
        raise AssignmentFailoverError("failover root config is unavailable") from exc
    if not secrets.compare_digest(_digest(live_config), context.config_sha256):
        raise AssignmentFailoverError("failover root config changed after launch")
    context.verify_protected_state()
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


def _operation_anchor(operation: Mapping[str, Any]) -> str:
    fields = (
        "operation_id",
        "receipt_sha256",
        "spec",
        "stage",
        "receipt_commit_sha256",
        "continuity",
        "replacement_assignment",
        "delivery_receipt",
        "created_at",
    )
    if not all(field in operation for field in fields):
        raise AssignmentFailoverError("canonical failover saga record is incomplete")
    return _digest({field: operation[field] for field in fields})


def _require_hex64(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise AssignmentFailoverError(f"{label} is invalid")
    return value


def _canonical_absolute_path(value: Any, label: str) -> Path:
    if type(value) is not str or not value:
        raise AssignmentFailoverError(f"failover {label} path is invalid")
    candidate = Path(value)
    resolved = candidate.resolve(strict=False)
    if not candidate.is_absolute() or str(resolved) != value:
        raise AssignmentFailoverError(f"failover {label} path is not canonical absolute")
    return resolved


def _initialize_database_id(path: Path, table: str, database_id: str) -> None:
    _require_hex64(database_id, f"{table} database id")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {table} "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), database_id TEXT NOT NULL)"
        )
        row = connection.execute(
            f"SELECT database_id FROM {table} WHERE singleton=1"
        ).fetchone()
        if row is None:
            connection.execute(f"INSERT INTO {table} VALUES(1,?)", (database_id,))
        elif not secrets.compare_digest(str(row[0]), database_id):
            raise AssignmentFailoverError(f"{table} database id conflicts")


def _verify_database_id(path: Path, table: str, expected: str) -> None:
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                f"SELECT database_id FROM {table} WHERE singleton=1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise AssignmentFailoverError(f"{table} database identity is absent") from exc
    if row is None or not secrets.compare_digest(str(row[0]), expected):
        raise AssignmentFailoverError(f"{table} database identity mismatch")


def _initialize_claim_database(path: Path, database_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_claim_v1 (
                assignment_sha256 TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                claim_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(state IN ('claimed','completed')),
                receipt_json BLOB,
                created_at REAL NOT NULL,
                completed_at REAL
            )
            """
        )
    _initialize_database_id(path, "failover_claim_store_meta_v1", database_id)


def _event_position(path: Path, event_hash: str) -> int:
    _require_hex64(event_hash, "event anchor hash")
    try:
        events = sc_mesh_registry.load_events(
            event_log_path=path,
            limit=max(1, int(sc_mesh_registry.verify_events(event_log_path=path)["events_checked"])),
        )["events"]
    except Exception as exc:
        raise AssignmentFailoverError("event log could not be read for provenance") from exc
    for index, event in enumerate(events, start=1):
        if secrets.compare_digest(str(event.get("event_hash", "")), event_hash):
            return index
    raise AssignmentFailoverError("protected event anchor is absent")


def _verify_event_anchor(path: Path, anchor: Any) -> None:
    if type(anchor) is not dict or set(anchor) != {"events_checked", "head_hash"}:
        raise AssignmentFailoverError("protected event anchor is invalid")
    count = anchor.get("events_checked")
    head = anchor.get("head_hash")
    if type(count) is not int or count < 0:
        raise AssignmentFailoverError("protected event anchor count is invalid")
    _require_hex64(head, "protected event anchor hash")
    verified = sc_mesh_registry.verify_events(event_log_path=path)
    if verified.get("ok") is not True or int(verified.get("events_checked", -1)) < count:
        raise AssignmentFailoverError("event log rolled back below protected provenance")
    if count == 0:
        if head != sc_mesh_registry.EVENT_GENESIS_HASH:
            raise AssignmentFailoverError("event genesis anchor is invalid")
        return
    if _event_position(path, str(head)) != count:
        raise AssignmentFailoverError("protected event provenance is absent or reordered")


def _finite(value: float | None, name: str) -> float:
    resolved = time.time() if value is None else value
    if isinstance(resolved, bool) or not isinstance(resolved, (int, float)):
        raise AssignmentFailoverError(f"invalid {name}")
    result = float(resolved)
    if not math.isfinite(result):
        raise AssignmentFailoverError(f"invalid {name}")
    return result


def _require_ordinary_assurance(value: Any) -> dict[str, Any]:
    assurance = _snapshot(
        value,
        "failover assurance",
        require_canonical_wire=False,
    )
    # This independent literal check intentionally does not call the builder
    # or read a module-level policy object.  Rebinding either cannot make a
    # false assurance label pass verification.
    if (
        set(assurance)
        != {
            "mode",
            "same_user_threat",
            "external_monotonic_authority",
            "monotonicity",
            "high_assurance",
        }
        or assurance.get("mode") != "ordinary"
        or assurance.get("same_user_threat") != "excluded"
        or assurance.get("external_monotonic_authority") != "absent"
        or assurance.get("monotonicity") != "local_best_effort"
        or assurance.get("high_assurance") is not False
    ):
        raise AssignmentFailoverError("failover assurance label is invalid")
    return assurance


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


class _FailoverSagaStore:
    """SQLite saga/outbox keyed by the authorizing receipt and exact intent."""

    def __init__(self, path: str | Path, *, database_id: str | None = None) -> None:
        self.path = Path(path).resolve(strict=False)
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
        if database_id is not None:
            _initialize_database_id(
                self.path, "failover_saga_store_meta_v1", database_id
            )
            _verify_database_id(
                self.path, "failover_saga_store_meta_v1", database_id
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


def list_pending_failovers() -> list[dict[str, Any]]:
    """List intents only from the launch-pinned canonical saga."""
    return _require_launch_context().saga_store.pending()


class _SeatDeliveryClaimStore:
    """Seat-side claim/dedupe authority committed before remote actuation."""

    def __init__(
        self,
        context: _FailoverLaunchContext,
        *,
        database_id: str | None = None,
    ) -> None:
        if type(context) is not _FailoverLaunchContext:
            raise TypeError("seat delivery claims require the verified failover launch root")
        self._context = context
        self.path = context.claim_store_path
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
        if database_id is not None:
            _initialize_database_id(
                self.path, "failover_claim_store_meta_v1", database_id
            )
        expected = context.claim_store_database_id
        _verify_database_id(self.path, "failover_claim_store_meta_v1", expected)
        self.database_id = expected

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
        proposed_claim_id = secrets.token_hex(32)
        protected_claim, newly_reserved = self._context.reserve_seat_claim(
            assignment_hash,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            claim_id=proposed_claim_id,
        )
        claim_id = str(protected_claim["claim_id"])
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT idempotency_key,claim_id,state,receipt_json "
                "FROM delivery_claim_v1 WHERE assignment_sha256=?",
                (assignment_hash,),
            ).fetchone()
            if row is not None:
                if row[0] != idempotency_key or row[1] != claim_id:
                    connection.rollback()
                    raise FailoverConflictError("delivery assignment has a conflicting idempotency key")
                if row[2] == "completed" and row[3] is not None:
                    receipt = _snapshot(bytes(row[3]), "stored seat delivery receipt")
                    connection.commit()
                    receipt_hash = _digest(receipt)
                    if protected_claim.get("status") == "completed" and not secrets.compare_digest(
                        str(protected_claim.get("receipt_sha256", "")), receipt_hash
                    ):
                        raise AssignmentFailoverError("protected completed seat receipt conflicts")
                    self._context.advance_seat_claim(
                        assignment_hash,
                        claim_id=claim_id,
                        status="completed",
                        receipt_sha256=receipt_hash,
                    )
                    return receipt
                connection.rollback()
                raise AssignmentFailoverError(
                    "seat delivery claim is ambiguous; second actuation refused"
                )
            if not newly_reserved:
                connection.rollback()
                raise AssignmentFailoverError(
                    "seat delivery claim store rolled back below protected high-water"
                )
            connection.execute(
                "INSERT INTO delivery_claim_v1 VALUES(?,?,?,'claimed',NULL,?,NULL)",
                (assignment_hash, idempotency_key, claim_id, time.time()),
            )
            connection.commit()
        self._context.advance_seat_claim(
            assignment_hash,
            claim_id=claim_id,
            status="claimed",
        )

        claim_body = {
            "schema": "selfconnect-seat-delivery-claim-v1",
            "claim_id": claim_id,
            "idempotency_key": idempotency_key,
            "operation_id": operation_id,
            "replacement_assignment_sha256": assignment_hash,
            "continuity_sha256": _digest(continuity),
            "failover_root_id": self._context.root_id,
            "claim_store_database_id": self.database_id,
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
            failover_root_id=self._context.root_id,
            claim_store_database_id=self.database_id,
            actuation_proof=verified_proof,
            result_sha256=result_hash,
            assurance=_require_ordinary_assurance(
                _snapshot(
                    continuity,
                    "continuity artifact",
                    require_canonical_wire=False,
                ).get("assurance")
            ),
            delivered_at=(
                float(proof_body["acted_at"])
                if delivered_at is None
                else delivered_at
            ),
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
        self._context.advance_seat_claim(
            assignment_hash,
            claim_id=claim_id,
            status="completed",
            receipt_sha256=_digest(receipt),
        )
        if after_commit is not None:
            after_commit()
        return receipt


def open_seat_delivery_claim_store() -> _SeatDeliveryClaimStore:
    """Open only the claim store pinned by the verified process launch root."""
    return _SeatDeliveryClaimStore(_require_launch_context())


def _create_delivery_receipt(
    *,
    operation_id: str,
    continuity: Mapping[str, Any],
    replacement_assignment: Mapping[str, Any],
    seat_identity: Any,
    delivery_claim_id: str,
    delivery_idempotency_key: str,
    delivery_claim_commit_sha256: str,
    failover_root_id: str,
    claim_store_database_id: str,
    actuation_proof: Mapping[str, Any],
    result_sha256: str,
    assurance: Mapping[str, Any],
    delivered_at: float | None = None,
) -> dict[str, Any]:
    """Format and sign a seat delivery receipt.

    This helper is intentionally not an authorization or durable-claim
    boundary.  The coordinator separately verifies the receipt against the
    launch-pinned completed claim row.
    """
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
        "failover_root_id": failover_root_id,
        "claim_store_database_id": claim_store_database_id,
        "assurance": _require_ordinary_assurance(assurance),
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
    failover_root_id: str,
    claim_store_database_id: str,
    assurance: Mapping[str, Any],
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
        "failover_root_id": failover_root_id,
        "claim_store_database_id": claim_store_database_id,
        "assurance": _require_ordinary_assurance(assurance),
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
        "failover_root_id": body["failover_root_id"],
        "claim_store_database_id": body["claim_store_database_id"],
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
        raise AssignmentFailoverError("operational target resolver failed") from exc
    if result is not True:
        raise AssignmentFailoverError("operational target resolver refused")


def _require_registry_off_rails(
    registry_path: Path,
    *,
    role: str,
    mesh: str,
    old_seat: Mapping[str, Any],
) -> None:
    """Check the routing mirror postcondition; the registry is not authority."""
    try:
        registry = sc_mesh_registry.load_registry_strict(registry_path)
    except Exception as exc:
        raise AssignmentFailoverError(
            "ordinary/no_external_anchor registry postcondition is unreadable"
        ) from exc
    rows = [
        row
        for row in registry["agents"]
        if row.get("role") == role and row.get("mesh") == mesh
    ]
    expected_identity = {
        "birth_id": old_seat["birth_id"],
        "generation": old_seat["generation"],
        "seat_key_id": old_seat["seat_key_id"],
        "seat_epoch": old_seat["seat_epoch"],
    }
    if (
        len(rows) != 1
        or rows[0].get("status") != "off_rails"
        or rows[0].get("birth_id") != old_seat["birth_id"]
        or rows[0].get("generation") != old_seat["generation"]
        or rows[0].get("off_rails_identity") != expected_identity
    ):
        raise AssignmentFailoverError(
            "ordinary/no_external_anchor registry postcondition mismatch"
        )


def _checkpoint(hook: Callable[[str, dict[str, Any]], None] | None, name: str, operation: dict[str, Any]) -> None:
    if hook is not None:
        hook(name, operation)


def failover_assignment(
    assignment: Any,
    receipt: Any,
    *,
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
    high_assurance: bool = False,
    revoked_coordinator_key_ids: frozenset[str] = frozenset(),
    revoked_seat_key_ids: frozenset[str] = frozenset(),
    now: float | None = None,
    replacement_ttl_seconds: float = 300.0,
    stage_hook: Callable[[str, dict[str, Any]], None] | None = None,
    **receipt_verification: Any,
) -> dict[str, Any]:
    """Run or resume one exact receipt-authorized ordinary-mode failover saga.

    Operational callbacks are not security boundaries.  This build has no
    separately privileged external monotonic authority, so high assurance is
    refused before any callback can run.
    """
    context = _require_launch_context()
    assurance = context.require_assurance(high_assurance)
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
            raise AssignmentFailoverError(
                "failover operational callbacks are required and are not authority"
            )

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
        "assurance": assurance,
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
        "failover_root_id": context.root_id,
        "saga_store_database_id": context.saga_store_database_id,
        "launch_event_log_path_sha256": hashlib.sha256(
            str(context.event_log_path).encode("utf-8")
        ).hexdigest(),
    }
    operation_id = _digest(spec)
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
        protected_created = context.reserve_operation(
            operation_id,
            receipt_hash,
            operation_id,
        )
        operation, created = saga.prepare(spec)
        if created is not protected_created:
            raise AssignmentFailoverError("protected failover reservation and canonical saga differ")
        operation_id = operation["operation_id"]
        context.advance_operation(operation_id, operation)
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
                context.advance_operation(operation_id, operation)

            common_audit = {
                "operation_id": operation_id,
                "assignment_id": receipt_snap["assignment_id"],
                "authorizing_receipt_sha256": receipt_hash,
                "predecessor_assignment_sha256": spec["predecessor_assignment_sha256"],
                "old_seat_key_id": raw_old["seat_key_id"],
                "old_seat_epoch": raw_old["seat_epoch"],
                "replacement_seat": new,
                "process_action": "none",
                "assurance": assurance,
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
                context.record_event(operation_id, "intent", result)
                _checkpoint(stage_hook, "after_intent_audit", operation)
                operation = saga.advance(operation_id, "authorized", "intent_audited")
                context.advance_operation(operation_id, operation)

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
                context.advance_operation(operation_id, operation)
                _require_registry_off_rails(
                    registry_path,
                    role=role,
                    mesh=mesh,
                    old_seat=raw_old,
                )

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
                context.record_event(operation_id, "off_rails", result)
                _checkpoint(stage_hook, "after_off_rails_audit", operation)
                operation = saga.advance(operation_id, "off_rails", "off_rails_audited")
                context.advance_operation(operation_id, operation)

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
                    "assurance": assurance,
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
                context.advance_operation(operation_id, operation)
                _checkpoint(stage_hook, "after_assignment_build", operation)

            if operation["stage"] == "assignment_built":
                replacement_assignment = operation["replacement_assignment"]
                if replacement_assignment is None:
                    raise AssignmentFailoverError("replacement assignment outbox is missing")
                store.ensure_assignment("issued", replacement_assignment)
                _checkpoint(stage_hook, "after_assignment_issue", operation)
                operation = saga.advance(operation_id, "assignment_built", "assignment_issued")
                context.advance_operation(operation_id, operation)

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
                    failover_root_id=context.root_id,
                    claim_store_database_id=context.claim_store_database_id,
                    assurance=assurance,
                    now=current,
                    require_fresh=True,
                )
                context.require_delivery_claim(delivery, replacement_assignment)
                operation = saga.advance(
                    operation_id,
                    "assignment_issued",
                    "delivered",
                    delivery_receipt_json=delivery,
                )
                context.advance_operation(operation_id, operation)
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
                failover_root_id=context.root_id,
                claim_store_database_id=context.claim_store_database_id,
                assurance=assurance,
                now=current,
                require_fresh=False,
            )
            context.require_delivery_claim(delivery, replacement_assignment)
            _require_registry_off_rails(
                registry_path,
                role=role,
                mesh=mesh,
                old_seat=raw_old,
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
                "assurance": assurance,
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
    "FAILOVER_ROOT_CONFIG_ENV",
    "FAILOVER_ROOT_PUBLIC_KEY_ENV",
    "FAILOVER_STATES",
    "AssignmentFailoverError",
    "FailoverConflictError",
    "failover_assignment",
    "initialize_failover_runtime",
    "list_pending_failovers",
    "open_seat_delivery_claim_store",
    "provision_failover_trust_root",
]
