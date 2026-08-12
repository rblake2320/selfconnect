"""Fail-closed orchestration harness for the final Windows live ceremony.

The harness is evidence plumbing, not an authorization boundary.  Its ports
must be bound to the reviewed runtime, seat-pipe/trust, and failover APIs.  It
never reads terminal text and refuses to run until every component worktree is
clean and pinned to an exact reviewed commit.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_TRIGGERS = ("blocked", "rejected")
_NEGATIVE_PROBES = (
    "wrong_seat",
    "assignment_replay",
    "receipt_replay",
    "target_drift",
    "tab_drift",
    "revoked_key",
    "forged_composer_text",
)


class CeremonyError(RuntimeError):
    """The live ceremony could not produce trustworthy evidence."""


@dataclass(frozen=True)
class ReviewedCheckout:
    name: str
    worktree: Path
    commit_sha: str

    def __post_init__(self) -> None:
        if not self.name.strip() or _SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("reviewed checkout requires a name and full lowercase commit SHA")
        object.__setattr__(self, "worktree", Path(self.worktree).resolve())


@dataclass(frozen=True)
class SeatIdentity:
    birth_id: str
    generation: int
    key_id: str
    seat_epoch: str

    def __post_init__(self) -> None:
        if not self.birth_id or type(self.generation) is not int or self.generation <= 0:
            raise ValueError("seat birth and generation are required")
        for value, label in ((self.key_id, "key_id"), (self.seat_epoch, "seat_epoch")):
            if not isinstance(value, str) or not value:
                raise ValueError(f"seat {label} is required")


@dataclass(frozen=True)
class ExactRoute:
    hwnd: int
    pid: int
    process_start_time_ns: int
    tab_runtime_id: tuple[int, ...]
    term_control_runtime_id: tuple[int, ...]
    seat: SeatIdentity

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0 for value in (self.hwnd, self.pid, self.process_start_time_ns)):
            raise ValueError("exact positive target identity is required")
        for runtime_id in (self.tab_runtime_id, self.term_control_runtime_id):
            if not runtime_id or any(type(item) is not int for item in runtime_id):
                raise ValueError("exact terminal RuntimeIds are required")


@dataclass(frozen=True)
class CeremonyPorts:
    """Reviewed API bindings supplied only after final branches are approved."""

    dispatch: Callable[[str, str], Mapping[str, Any]]
    seat_transition: Callable[[Mapping[str, Any], str], Mapping[str, Any]]
    verify_receipt: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    acknowledge_receipt: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    failover: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    replacement_transition: Callable[[Mapping[str, Any], str], Mapping[str, Any]]
    negative_probe: Callable[[str], None]

    def __post_init__(self) -> None:
        if any(not callable(value) for value in self.__dict__.values()):
            raise TypeError("every ceremony port must be callable")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise CeremonyError("ceremony evidence is not canonical JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _git(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise CeremonyError(f"cannot verify reviewed checkout {worktree.name}")
    return result.stdout.strip()


def verify_reviewed_checkouts(checkouts: Sequence[ReviewedCheckout]) -> list[dict[str, str]]:
    """Require Windows and clean exact-SHA worktrees before any live boundary."""
    if os.name != "nt":
        raise CeremonyError("live ceremony is Windows-only")
    if len(checkouts) < 3 or len({item.name for item in checkouts}) != len(checkouts):
        raise CeremonyError("runtime, pipe/trust, and failover reviewed checkouts are required")
    evidence = []
    for checkout in checkouts:
        if not checkout.worktree.is_dir():
            raise CeremonyError(f"reviewed worktree is missing: {checkout.name}")
        actual = _git(checkout.worktree, "rev-parse", "HEAD")
        if actual != checkout.commit_sha:
            raise CeremonyError(f"reviewed checkout SHA mismatch: {checkout.name}")
        if _git(checkout.worktree, "status", "--porcelain"):
            raise CeremonyError(f"reviewed checkout is dirty: {checkout.name}")
        evidence.append({"name": checkout.name, "worktree": str(checkout.worktree), "commit_sha": actual})
    return evidence


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CeremonyError(f"{label} did not return structured evidence")
    return dict(value)


def _assignment(dispatch: Mapping[str, Any], route: ExactRoute) -> dict[str, Any]:
    result = _mapping(dispatch.get("submit_result"), "guarded submit")
    if not (
        result.get("ok") is True
        and result.get("state") == "acknowledged"
        and result.get("delivery_verified") is True
        and result.get("peer_acknowledged") is True
        and result.get("decision") == "accepted"
    ):
        raise CeremonyError("guarded submit did not authenticate assignment processing")
    assignment = _mapping(dispatch.get("assignment"), "assignment dispatch")
    exact = {
        "receiver_birth_id": route.seat.birth_id,
        "receiver_generation": route.seat.generation,
        "receiver_key_id": route.seat.key_id,
        "receiver_seat_epoch": route.seat.seat_epoch,
    }
    if any(assignment.get(field) != value for field, value in exact.items()):
        raise CeremonyError("assignment does not bind the exact intended seat")
    if not isinstance(assignment.get("payload"), str) or not assignment["payload"]:
        raise CeremonyError("assignment lacks its signed inline payload")
    return assignment


def _verified_transition(
    ports: CeremonyPorts,
    assignment: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    state: str,
    sequence: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    verified = _mapping(ports.verify_receipt(assignment, receipt), "receipt verifier")
    if (
        verified.get("assignment_id") != assignment.get("assignment_id")
        or verified.get("state") != state
        or verified.get("sequence") != sequence
    ):
        raise CeremonyError("verified receipt state/sequence binding mismatch")
    ack = _mapping(ports.acknowledge_receipt(receipt), "coordinator receipt ACK")
    if (
        ack.get("assignment_id") != assignment.get("assignment_id")
        or ack.get("sequence") != sequence
        or ack.get("receipt_sha256") != _digest(receipt)
    ):
        raise CeremonyError("coordinator ACK does not bind the exact verified receipt")
    return verified, ack


def _fresh_replacement(failover: Mapping[str, Any], old: SeatIdentity) -> tuple[dict[str, Any], SeatIdentity]:
    if failover.get("ok") is not True or failover.get("stage") != "delivered":
        raise CeremonyError("authenticated failover did not reach delivered")
    if failover.get("process_action") != "none":
        raise CeremonyError("ceremony forbids automatic process termination")
    replacement = _mapping(failover.get("replacement_seat"), "replacement seat")
    new = SeatIdentity(
        birth_id=replacement.get("birth_id"),
        generation=replacement.get("generation"),
        key_id=replacement.get("seat_key_id"),
        seat_epoch=replacement.get("seat_epoch"),
    )
    if (
        new.birth_id == old.birth_id
        or new.generation <= old.generation
        or new.key_id == old.key_id
        or new.seat_epoch == old.seat_epoch
    ):
        raise CeremonyError("failover replacement is not a fresh seat identity")
    assignment = _mapping(failover.get("replacement_assignment"), "replacement assignment")
    exact = {
        "receiver_birth_id": new.birth_id,
        "receiver_generation": new.generation,
        "receiver_key_id": new.key_id,
        "receiver_seat_epoch": new.seat_epoch,
    }
    if any(assignment.get(field) != value for field, value in exact.items()):
        raise CeremonyError("replacement assignment does not bind the fresh seat")
    if not isinstance(failover.get("delivery_receipt"), Mapping):
        raise CeremonyError("failover lacks the replacement seat delivery receipt")
    return assignment, new


def _run_trigger_case(ports: CeremonyPorts, route: ExactRoute, trigger: str) -> dict[str, Any]:
    case_id = f"live-{trigger}"
    dispatch = _mapping(ports.dispatch(case_id, trigger), "assignment runtime")
    assignment = _assignment(dispatch, route)
    receipts: list[dict[str, Any]] = []
    acks: list[dict[str, Any]] = []
    for sequence, state in enumerate(("accepted", "working", trigger), 1):
        receipt = _mapping(ports.seat_transition(assignment, state), "seat transition")
        _verified, ack = _verified_transition(
            ports, assignment, receipt, state=state, sequence=sequence
        )
        receipts.append(receipt)
        acks.append(ack)
    failover = _mapping(ports.failover(assignment, receipts[-1]), "assignment failover")
    replacement_assignment, replacement = _fresh_replacement(failover, route.seat)
    replacement_receipts = []
    replacement_acks = []
    for sequence, state in enumerate(("accepted", "working", "completed"), 1):
        receipt = _mapping(
            ports.replacement_transition(replacement_assignment, state),
            "replacement transition",
        )
        _verified, ack = _verified_transition(
            ports, replacement_assignment, receipt, state=state, sequence=sequence
        )
        replacement_receipts.append(receipt)
        replacement_acks.append(ack)
    return {
        "case_id": case_id,
        "trigger": trigger,
        "predecessor_assignment_sha256": _digest(assignment),
        "predecessor_receipt_sha256": [_digest(item) for item in receipts],
        "predecessor_ack_sha256": [_digest(item) for item in acks],
        "failover_operation_id": failover.get("operation_id"),
        "replacement_assignment_sha256": _digest(replacement_assignment),
        "replacement_birth_id": replacement.birth_id,
        "replacement_generation": replacement.generation,
        "replacement_receipt_sha256": [_digest(item) for item in replacement_receipts],
        "replacement_ack_sha256": [_digest(item) for item in replacement_acks],
    }


def run_live_ceremony(
    *,
    checkouts: Sequence[ReviewedCheckout],
    route: ExactRoute,
    ports: CeremonyPorts,
) -> dict[str, Any]:
    """Run both terminal-failure paths and fixed adversarial probes.

    The caller must supply ports bound to reviewed production APIs.  UIA, OCR,
    composer text, HWND/PID/title observations, and PostMessage queue acceptance
    are intentionally absent from the authority surface.
    """
    checkout_evidence = verify_reviewed_checkouts(checkouts)
    cases = [_run_trigger_case(ports, route, trigger) for trigger in _TRIGGERS]
    rejected = []
    for probe in _NEGATIVE_PROBES:
        try:
            ports.negative_probe(probe)
        except Exception as exc:
            rejected.append({"probe": probe, "exception": type(exc).__name__})
        else:
            raise CeremonyError(f"negative probe did not fail closed: {probe}")
    evidence = {
        "schema": "selfconnect-live-assignment-ceremony-v1",
        "claim": "cryptographic API composition evidence; screen evidence is non-authoritative",
        "reviewed_checkouts": checkout_evidence,
        "route": {
            "hwnd": route.hwnd,
            "pid": route.pid,
            "process_start_time_ns": route.process_start_time_ns,
            "tab_runtime_id": list(route.tab_runtime_id),
            "term_control_runtime_id": list(route.term_control_runtime_id),
            "seat_birth_id": route.seat.birth_id,
            "seat_generation": route.seat.generation,
            "seat_key_id": route.seat.key_id,
        },
        "cases": cases,
        "negative_rejections": rejected,
    }
    evidence["evidence_sha256"] = _digest(evidence)
    if _HEX64.fullmatch(evidence["evidence_sha256"]) is None:
        raise AssertionError("unreachable evidence digest failure")
    return evidence


def missing_live_prerequisites() -> tuple[str, ...]:
    """Exact external prerequisites intentionally not fabricated by this branch."""
    return (
        "reviewed merged runtime SHA exposing production dispatch/receiver/watchdog APIs",
        "reviewed Windows seat-pipe/trust SHA with OS-derived SID/process/pipe evidence",
        "reviewed failover SHA with fresh-birth delivery receipt and no process kill",
        "pinned coordinator/authority/seat/response-receiver public keys and signed enrollments",
        "fresh signed durable revocation resolver used at every verification boundary",
        "two live Windows Terminal seats with exact TargetIdentity and TerminalTabIdentity",
        "DACL-protected state, mailbox, pipe, registry, event, and evidence paths",
        "operator authorization to inject only the reviewed signed assignment wire",
    )


__all__ = [
    "CeremonyError",
    "CeremonyPorts",
    "ExactRoute",
    "ReviewedCheckout",
    "SeatIdentity",
    "missing_live_prerequisites",
    "run_live_ceremony",
    "verify_reviewed_checkouts",
]
