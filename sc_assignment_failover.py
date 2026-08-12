"""Authenticated fresh-birth failover for blocked assignment seats.

The signed assignment receipt is the authority for failover.  Mesh registry
state is checked only as a compare-and-set routing mirror and is never accepted
as evidence that a seat was blocked or rejected.  This module does not expose
or invoke any process-termination operation.
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import sc_mesh_registry
from sc_assignment_protocol import (
    AssignmentStateStore,
    AssignmentVerificationError,
    issue_assignment,
    verify_consume_state_receipt,
)
from sc_seat_identity import key_id, verify_enrollment

FAILOVER_STATES = frozenset({"blocked", "rejected"})


class AssignmentFailoverError(AssignmentVerificationError):
    """A fresh-birth failover authorization or boundary failed closed."""


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


def _canonical_snapshot(value: Any, label: str) -> dict[str, Any]:
    """Freeze a JSON object and require canonical bytes when bytes are supplied."""
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
        if raw is not None:
            decoded = raw.decode("utf-8")
            snapshot = json.loads(
                decoded,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        else:
            snapshot = json.loads(
                _canonical(value).decode("ascii"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
    except AssignmentFailoverError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AssignmentFailoverError(f"{label} JSON is invalid") from exc
    if type(snapshot) is not dict:
        raise AssignmentFailoverError(f"{label} must be a JSON object")
    if raw is not None and not secrets.compare_digest(raw, _canonical(snapshot)):
        raise AssignmentFailoverError(f"{label} is not canonical JSON")
    return snapshot


def _finite(value: float | None, name: str) -> float:
    resolved = time.time() if value is None else value
    if isinstance(resolved, bool) or not isinstance(resolved, (int, float)):
        raise AssignmentFailoverError(f"invalid {name}")
    result = float(resolved)
    if not math.isfinite(result):
        raise AssignmentFailoverError(f"invalid {name}")
    return result


def _identity_document(value: Any, label: str) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    return _canonical_snapshot(value, label)


def _guard_exact_target(
    guard: Callable[..., bool],
    *,
    target_identity: Any,
    terminal_tab_identity: Any,
    response_channel: Mapping[str, Any],
    stage: str,
) -> None:
    if not callable(guard):
        raise AssignmentFailoverError("exact target guard is required")
    try:
        allowed = guard(
            target_identity=target_identity,
            terminal_tab_identity=terminal_tab_identity,
            response_channel=response_channel,
            stage=stage,
        )
    except Exception as exc:
        raise AssignmentFailoverError(f"exact target guard failed at {stage}") from exc
    if allowed is not True:
        raise AssignmentFailoverError(f"exact target guard refused at {stage}")


def _require_boundary_result(result: Any, assignment: Mapping[str, Any]) -> dict[str, Any]:
    if type(result) is not dict:
        raise AssignmentFailoverError("guarded delivery boundary returned an invalid receipt")
    exact = {
        "assignment_id": assignment["assignment_id"],
        "receiver_birth_id": assignment["receiver_birth_id"],
        "receiver_generation": assignment["receiver_generation"],
        "receiver_key_id": assignment["receiver_key_id"],
        "receiver_seat_epoch": assignment["receiver_seat_epoch"],
        "target_identity_sha256": assignment["target_identity_sha256"],
        "terminal_tab_identity_sha256": assignment["terminal_tab_identity_sha256"],
        "response_channel_sha256": assignment["response_channel_sha256"],
    }
    if result.get("ok") is not True or result.get("state") != "delivered":
        raise AssignmentFailoverError("guarded delivery boundary failed closed")
    if any(result.get(field) != value for field, value in exact.items()):
        raise AssignmentFailoverError("guarded delivery receipt exact binding mismatch")
    return dict(result)


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
    store: AssignmentStateStore,
    registry_path: str | Path,
    exact_target_guard: Callable[..., bool],
    guarded_delivery: Callable[..., Mapping[str, Any]],
    audit_append: Callable[..., Mapping[str, Any]] = sc_mesh_registry.append_event,
    registry_transition: Callable[..., Mapping[str, Any]] = (
        sc_mesh_registry.transition_agent_off_rails_exact
    ),
    revoked_coordinator_key_ids: frozenset[str] = frozenset(),
    revoked_seat_key_ids: frozenset[str] = frozenset(),
    now: float | None = None,
    replacement_ttl_seconds: float = 60.0,
    **receipt_verification: Any,
) -> dict[str, Any]:
    """Replace a blocked/rejected seat through authenticated, guarded boundaries.

    ``receipt_verification`` is the exact verification context accepted by
    :func:`verify_consume_state_receipt`.  The receipt is durably consumed before
    any audit or registry mutation.  Byte-identical receipt retries are rejected
    here even though the lower-level receipt protocol permits idempotent reads.
    """
    current = _finite(now, "failover time")
    if not isinstance(role, str) or not role.strip() or not isinstance(mesh, str) or not mesh.strip():
        raise AssignmentFailoverError("role and mesh are required")
    if not callable(audit_append) or not callable(registry_transition) or not callable(guarded_delivery):
        raise AssignmentFailoverError("failover boundaries are required")

    canonical_receipt = _canonical_snapshot(receipt, "assignment failover receipt")
    try:
        verified = verify_consume_state_receipt(
            canonical_receipt,
            assignment,
            store=store,
            authority_public_key_hex=authority_public_key_hex,
            revoked_coordinator_key_ids=revoked_coordinator_key_ids,
            revoked_seat_key_ids=revoked_seat_key_ids,
            now=current,
            **receipt_verification,
        )
    except AssignmentFailoverError:
        raise
    except (AssignmentVerificationError, TypeError, ValueError) as exc:
        raise AssignmentFailoverError(f"assignment failover receipt verification failed: {exc}") from exc
    if verified.get("newly_committed") is not True:
        raise AssignmentFailoverError("assignment failover receipt replay rejected")
    if verified.get("state") not in FAILOVER_STATES:
        raise AssignmentFailoverError("only blocked or rejected receipt authorizes failover")

    old = {
        "birth_id": verified["receiver_birth_id"],
        "generation": verified["receiver_generation"],
        "seat_key_id": verified["receiver_key_id"],
        "seat_epoch": verified["receiver_seat_epoch"],
    }
    coordinator_key_id = key_id(str(coordinator_identity.public_key_hex))
    if coordinator_key_id != verified["coordinator_key_id"]:
        raise AssignmentFailoverError("failover coordinator is not the assignment coordinator")

    replacement_snapshot = _canonical_snapshot(
        replacement_enrollment, "replacement enrollment"
    )
    try:
        replacement = verify_enrollment(
            replacement_snapshot,
            authority_public_key_hex=authority_public_key_hex,
            revoked_key_ids=revoked_seat_key_ids,
            now=current,
        )
    except (TypeError, ValueError) as exc:
        raise AssignmentFailoverError(f"replacement enrollment verification failed: {exc}") from exc
    if type(replacement.get("generation")) is not int or replacement["generation"] <= old["generation"]:
        raise AssignmentFailoverError("replacement generation must be higher")
    if replacement.get("birth_id") == old["birth_id"]:
        raise AssignmentFailoverError("replacement birth_id must be fresh")
    if replacement.get("seat_key_id") == old["seat_key_id"]:
        raise AssignmentFailoverError("replacement seat key must be distinct")
    if replacement.get("seat_epoch") == old["seat_epoch"]:
        raise AssignmentFailoverError("replacement seat epoch must be distinct")

    target_document = _identity_document(replacement_target_identity, "replacement target")
    tab_document = _identity_document(
        replacement_terminal_tab_identity, "replacement TerminalTab"
    )
    channel_document = _canonical_snapshot(
        replacement_response_channel, "replacement response channel"
    )
    _guard_exact_target(
        exact_target_guard,
        target_identity=target_document,
        terminal_tab_identity=tab_document,
        response_channel=channel_document,
        stage="before_authorization",
    )

    receipt_sha256 = hashlib.sha256(_canonical(canonical_receipt)).hexdigest()
    audit_data = {
        "action": "authenticated_fresh_birth_failover",
        "assignment_id": verified["assignment_id"],
        "receipt_sha256": receipt_sha256,
        "receipt_state": verified["state"],
        "old_seat_key_id": old["seat_key_id"],
        "old_seat_epoch": old["seat_epoch"],
        "replacement_birth_id": replacement["birth_id"],
        "replacement_generation": replacement["generation"],
        "replacement_seat_key_id": replacement["seat_key_id"],
        "replacement_seat_epoch": replacement["seat_epoch"],
        "process_action": "none",
    }
    try:
        audit_result = audit_append(
            "blocked",
            role=role,
            mesh=mesh,
            birth_id=old["birth_id"],
            generation=old["generation"],
            status="off_rails",
            summary="authenticated assignment receipt authorized fresh-birth failover",
            data=audit_data,
            registry_path=registry_path,
            strict=True,
            strict_idempotency_key=f"assignment-failover:{receipt_sha256}",
        )
    except Exception as exc:
        raise AssignmentFailoverError("strict failover audit append failed") from exc
    if not isinstance(audit_result, Mapping) or audit_result.get("ok") is not True:
        raise AssignmentFailoverError("strict failover audit append failed")

    try:
        transition = registry_transition(
            role,
            mesh=mesh,
            expected_birth_id=old["birth_id"],
            expected_generation=old["generation"],
            expected_seat_key_id=old["seat_key_id"],
            expected_seat_epoch=old["seat_epoch"],
            registry_path=registry_path,
        )
    except Exception as exc:
        raise AssignmentFailoverError("exact old-seat registry transition failed") from exc
    if not isinstance(transition, Mapping) or transition.get("ok") is not True:
        error = transition.get("error", "registry transition refused") if isinstance(transition, Mapping) else "invalid registry transition result"
        raise AssignmentFailoverError(f"exact old-seat registry transition failed: {error}")

    _guard_exact_target(
        exact_target_guard,
        target_identity=target_document,
        terminal_tab_identity=tab_document,
        response_channel=channel_document,
        stage="before_delivery",
    )
    replacement_assignment = issue_assignment(
        verified["payload"] if "payload" in verified else _canonical_snapshot(assignment, "assignment")["payload"],
        coordinator_identity=coordinator_identity,
        coordinator_birth_id=verified["coordinator_birth_id"],
        coordinator_generation=verified["coordinator_generation"],
        receiver_enrollment=replacement_snapshot,
        authority_public_key_hex=authority_public_key_hex,
        target_identity=target_document,
        terminal_tab_identity=tab_document,
        response_receiver_public_key_hex=replacement_response_receiver_public_key_hex,
        response_channel=channel_document,
        store=store,
        revoked_coordinator_key_ids=revoked_coordinator_key_ids,
        revoked_seat_key_ids=revoked_seat_key_ids,
        now=current,
        ttl_seconds=replacement_ttl_seconds,
    )
    try:
        delivery_result = guarded_delivery(
            assignment=replacement_assignment,
            target_identity=target_document,
            terminal_tab_identity=tab_document,
            response_channel=channel_document,
        )
    except Exception as exc:
        raise AssignmentFailoverError("guarded replacement delivery failed") from exc
    delivery = _require_boundary_result(delivery_result, replacement_assignment)
    return {
        "ok": True,
        "old_seat": old,
        "replacement_seat": {
            "birth_id": replacement["birth_id"],
            "generation": replacement["generation"],
            "seat_key_id": replacement["seat_key_id"],
            "seat_epoch": replacement["seat_epoch"],
        },
        "replacement_assignment": replacement_assignment,
        "delivery": delivery,
        "audit": dict(audit_result),
        "registry_transition": dict(transition),
        "process_action": "none",
    }


__all__ = [
    "FAILOVER_STATES",
    "AssignmentFailoverError",
    "failover_assignment",
]
