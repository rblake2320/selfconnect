"""Versioned numeric event kinds for the SelfConnect mesh log.

Kinds are the compatibility contract.  Human-readable ``event_type`` remains in
the envelope for operators, but consumers dispatch on ``kind`` and fail closed
for unknown governance or actuation kinds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

KIND_VERSION = 1
OBSERVATIONAL = "observational"
GOVERNANCE = "governance"
ACTUATION = "actuation"
VALID_CLASSES = {OBSERVATIONAL, GOVERNANCE, ACTUATION}
CUSTOM_OBSERVATIONAL_KIND = 9_000


@dataclass(frozen=True)
class EventKind:
    kind: int
    event_type: str
    kind_class: str


_DEFINITIONS = (
    EventKind(100, "role_registered", GOVERNANCE),
    EventKind(101, "role_updated", GOVERNANCE),
    EventKind(102, "virtual_role_registered", GOVERNANCE),
    EventKind(103, "virtual_role_updated", GOVERNANCE),
    EventKind(104, "role_status_updated", GOVERNANCE),
    EventKind(105, "role_handoff", GOVERNANCE),
    EventKind(106, "role_removed", GOVERNANCE),
    EventKind(107, "fleet_agent_registered", GOVERNANCE),
    EventKind(108, "role_reconciled", GOVERNANCE),
    EventKind(109, "task_assigned", GOVERNANCE),
    EventKind(110, "task_completed", GOVERNANCE),
    EventKind(111, "task_complete", GOVERNANCE),
    EventKind(112, "approval", GOVERNANCE),
    EventKind(113, "blocked", GOVERNANCE),
    EventKind(114, "role_migrated", GOVERNANCE),
    EventKind(115, "assignment_failover_intent", GOVERNANCE),
    EventKind(116, "assignment_failover_off_rails", GOVERNANCE),
    EventKind(1_000, "role_heartbeat", OBSERVATIONAL),
    EventKind(1_001, "fleet_agent_heartbeat", OBSERVATIONAL),
    EventKind(1_002, "fleet_agent_done", OBSERVATIONAL),
    EventKind(1_003, "local_model_message_processed", OBSERVATIONAL),
    EventKind(1_004, "tamper_seed", OBSERVATIONAL),
    EventKind(10_000, "fabric_v0_message", ACTUATION),
    EventKind(10_001, "guarded_submit_prepared", ACTUATION),
    EventKind(10_002, "guarded_submit_submitted", ACTUATION),
    EventKind(10_003, "guarded_submit_acknowledged", ACTUATION),
    EventKind(10_004, "guarded_submit_refused", ACTUATION),
    EventKind(10_005, "guarded_submit_ambiguous", ACTUATION),
)

BY_TYPE = {item.event_type: item for item in _DEFINITIONS}
BY_KIND = {item.kind: item for item in _DEFINITIONS}


class UnknownProtectedEventKind(ValueError):
    """An unknown governance/actuation kind cannot be safely ignored."""


def envelope_for(
    event_type: str,
    *,
    kind: int | None = None,
    kind_class: str | None = None,
) -> dict[str, Any]:
    """Resolve and validate a v1 kind envelope.

    Unregistered events are permitted only as explicitly observational events.
    They share the custom observational kind and retain their event_type label.
    """
    known = BY_TYPE.get(event_type)
    if known is not None:
        if kind is not None and int(kind) != known.kind:
            raise ValueError(f"event type {event_type!r} requires kind {known.kind}")
        if kind_class is not None and kind_class != known.kind_class:
            raise ValueError(
                f"event type {event_type!r} requires kind class {known.kind_class!r}"
            )
        return {
            "kind": known.kind,
            "kind_version": KIND_VERSION,
            "kind_class": known.kind_class,
        }

    if kind is None or kind_class is None:
        raise UnknownProtectedEventKind(
            f"unregistered event type {event_type!r} requires an explicit kind and kind_class"
        )
    resolved_class = kind_class
    resolved_kind = int(kind)
    if resolved_class not in VALID_CLASSES:
        raise ValueError(f"unknown event kind class {resolved_class!r}")
    if resolved_class != OBSERVATIONAL:
        raise UnknownProtectedEventKind(
            f"unregistered {resolved_class} event type {event_type!r} is fail-closed"
        )
    if resolved_kind in BY_KIND:
        raise ValueError(f"kind {resolved_kind} is reserved for {BY_KIND[resolved_kind].event_type!r}")
    if _class_for_kind(resolved_kind) != OBSERVATIONAL:
        raise UnknownProtectedEventKind(
            f"unknown kind {resolved_kind} is in the {_class_for_kind(resolved_kind)} range"
        )
    return {
        "kind": resolved_kind,
        "kind_version": KIND_VERSION,
        "kind_class": OBSERVATIONAL,
    }


def _class_for_kind(kind: int) -> str:
    if 1 <= kind < 1_000:
        return GOVERNANCE
    if 1_000 <= kind < 10_000:
        return OBSERVATIONAL
    if kind >= 10_000:
        return ACTUATION
    raise ValueError("event kinds must be positive integers")


def validate_envelope(record: Mapping[str, Any]) -> str | None:
    """Return an envelope error, or ``None`` for a compatible event.

    Legacy v1 event-log records have no numeric kind and remain verifiable.
    """
    raw_record_version = record.get("version", 1)
    if type(raw_record_version) is not int or raw_record_version not in {1, 2}:
        return "invalid_event_version"
    record_version = raw_record_version
    if record_version < 2:
        return None
    try:
        kind = record["kind"]
        version = record["kind_version"]
        kind_class = record["kind_class"]
    except KeyError:
        return "invalid_kind_envelope"
    if type(kind) is not int or type(version) is not int or type(kind_class) is not str:
        return "invalid_kind_envelope"
    if version != KIND_VERSION:
        return "unsupported_kind_version"
    if kind_class not in VALID_CLASSES:
        return "invalid_kind_class"
    try:
        derived_class = _class_for_kind(kind)
    except ValueError:
        return "invalid_kind"
    if kind_class != derived_class:
        return "kind_class_range_mismatch"
    known = BY_KIND.get(kind)
    if known is None:
        if derived_class == OBSERVATIONAL:
            return None
        return "unknown_protected_kind"
    if known.kind_class != kind_class or known.event_type != record.get("event_type"):
        return "kind_mapping_mismatch"
    return None


def dispatch_event(
    record: Mapping[str, Any],
    handlers: Mapping[int, Callable[[Mapping[str, Any]], Any]],
) -> dict[str, Any]:
    """Dispatch a known kind, ignore unknown observations, fail closed otherwise."""
    error = validate_envelope(record)
    if error:
        raise UnknownProtectedEventKind(error)
    record_version = record.get("version", 1)
    if type(record_version) is not int or record_version not in {1, 2}:
        raise UnknownProtectedEventKind("invalid_event_version")
    if record_version < 2:
        raise UnknownProtectedEventKind("legacy event requires an explicit legacy adapter")
    kind = record["kind"]
    handler = handlers.get(kind)
    if handler is not None:
        return {"handled": True, "ignored": False, "result": handler(record)}
    if str(record["kind_class"]) == OBSERVATIONAL:
        return {"handled": False, "ignored": True, "result": None}
    raise UnknownProtectedEventKind(f"no handler for protected event kind {kind}")


__all__ = [
    "ACTUATION", "BY_KIND", "BY_TYPE", "CUSTOM_OBSERVATIONAL_KIND",
    "GOVERNANCE", "KIND_VERSION", "OBSERVATIONAL", "UnknownProtectedEventKind",
    "dispatch_event", "envelope_for", "validate_envelope",
]
