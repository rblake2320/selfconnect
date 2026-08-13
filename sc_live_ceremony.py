"""Fail-closed orchestration harness for the final Windows live ceremony.

The harness is evidence plumbing, not an authorization boundary.  Its ports
must be bound to the reviewed runtime, seat-pipe/trust, and failover APIs.  It
never reads terminal text and refuses to run until every component worktree is
clean and pinned to an exact reviewed commit.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import re
import secrets
import subprocess
import sys
from base64 import b64decode, b64encode
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_TTL_SECONDS = 60
_EVIDENCE_CLOCK_SKEW_SECONDS = 5
_REQUIRED_COMPONENTS = {
    "runtime": "sc_assignment_runtime.py",
    "trust_pipe": "sc_seat_pipe.py",
    "failover": "sc_assignment_failover.py",
}
_PORT_NAMES = (
    "dispatch",
    "seat_transition",
    "verify_receipt",
    "acknowledge_receipt",
    "failover",
    "replacement_transition",
    "negative_probe",
)
_PORT_ENTRYPOINTS = {
    "dispatch": ("runtime", "live_ceremony_dispatch"),
    "seat_transition": ("runtime", "live_ceremony_seat_transition"),
    "verify_receipt": ("trust_pipe", "live_ceremony_verify_receipt"),
    "acknowledge_receipt": ("runtime", "live_ceremony_acknowledge_receipt"),
    "failover": ("failover", "live_ceremony_failover"),
    "replacement_transition": ("runtime", "live_ceremony_replacement_transition"),
    "negative_probe": ("trust_pipe", "live_ceremony_negative_probe"),
}
_PROTOCOL_ERROR_ENTRYPOINTS = {
    "runtime": "LiveCeremonyProtocolError",
    "trust_pipe": "LiveCeremonyProtocolError",
    "failover": "LiveCeremonyProtocolError",
}
_TRIGGERS = ("blocked", "rejected")
_NEGATIVE_PROBES = (
    "wrong_seat",
    "assignment_replay",
    "receipt_replay",
    "target_drift",
    "tab_drift",
    "revoked_key",
    "forged_composer_text",
    "stub_or_dry_run",
)


class CeremonyError(RuntimeError):
    """The live ceremony could not produce trustworthy evidence."""


@dataclass(frozen=True)
class ReviewedCheckout:
    component: str
    worktree: Path
    commit_sha: str

    def __post_init__(self) -> None:
        if self.component not in _REQUIRED_COMPONENTS or _SHA.fullmatch(self.commit_sha) is None:
            raise ValueError("reviewed checkout requires a canonical component and full lowercase commit SHA")
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
class EvidenceTrust:
    """Pinned coordinator key and exact operator authorization expectation."""

    coordinator_id: str
    key_id: str
    public_key: bytes
    operator_id: str
    operator_authorization_id: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.coordinator_id, "coordinator_id"),
            (self.key_id, "key_id"),
            (self.operator_id, "operator_id"),
            (self.operator_authorization_id, "operator_authorization_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"evidence {label} is required")
        if not isinstance(self.public_key, bytes) or len(self.public_key) != 32:
            raise ValueError("evidence coordinator Ed25519 public key must be 32 bytes")


@dataclass(frozen=True)
class EvidenceSigner:
    trust: EvidenceTrust
    sign: Callable[[bytes], bytes]

    def __post_init__(self) -> None:
        if not isinstance(self.trust, EvidenceTrust) or not callable(self.sign):
            raise TypeError("pinned evidence trust and coordinator signer are required")


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
    protocol_errors: tuple[type[Exception], ...]
    bindings: Mapping[str, str]

    def __post_init__(self) -> None:
        callables = tuple(getattr(self, name) for name in _PORT_NAMES)
        if any(not callable(value) for value in callables):
            raise TypeError("every ceremony port must be callable")
        if (
            type(self.protocol_errors) is not tuple
            or not self.protocol_errors
            or any(
                not isinstance(error, type)
                or not issubclass(error, Exception)
                or error in {Exception, RuntimeError, NotImplementedError, AttributeError}
                for error in self.protocol_errors
            )
        ):
            raise TypeError("specific protocol verification error classes are required")
        if (
            not isinstance(self.bindings, Mapping)
            or set(self.bindings) != set(_PORT_NAMES)
            or any(component not in _REQUIRED_COMPONENTS for component in self.bindings.values())
        ):
            raise TypeError("every ceremony port requires a canonical component binding")
        object.__setattr__(self, "bindings", MappingProxyType(dict(self.bindings)))


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode(
            "ascii"
        )
    except (TypeError, ValueError) as exc:
        raise CeremonyError("ceremony evidence is not canonical JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise CeremonyError("evidence timestamp must be UTC")
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CeremonyError("evidence timestamp is missing")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CeremonyError("evidence timestamp is not canonical UTC") from exc
    return parsed.replace(tzinfo=UTC)


def verify_evidence_bundle(
    bundle: Mapping[str, Any],
    *,
    trust: EvidenceTrust,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a final bundle against independently pinned signer/operator trust."""
    envelope = _mapping(bundle, "final evidence bundle")
    body = _mapping(envelope.get("body"), "signed evidence body")
    signature_record = _mapping(envelope.get("signature"), "evidence signature")
    expected_identity = {
        "coordinator_id": trust.coordinator_id,
        "key_id": trust.key_id,
        "operator_id": trust.operator_id,
        "operator_authorization_id": trust.operator_authorization_id,
    }
    if any(body.get(field) != value for field, value in expected_identity.items()):
        raise CeremonyError("evidence signer/operator binding mismatch")
    issued_at = _parse_utc(body.get("issued_at_utc"))
    expires_at = _parse_utc(body.get("expires_at_utc"))
    if (expires_at - issued_at).total_seconds() != _EVIDENCE_TTL_SECONDS:
        raise CeremonyError("evidence validity window is not bounded")
    observed = _utc_now() if now is None else now
    if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
        raise CeremonyError("evidence verification clock must be UTC")
    skew = timedelta(seconds=_EVIDENCE_CLOCK_SKEW_SECONDS)
    if issued_at > observed + skew or expires_at < observed - skew:
        raise CeremonyError("evidence timestamp is outside its validity window")
    pinned_shas = body.get("pinned_shas")
    reviewed = body.get("reviewed_checkouts")
    if not isinstance(pinned_shas, Mapping) or not isinstance(reviewed, list):
        raise CeremonyError("evidence lacks exact pinned checkout SHAs")
    recorded = {item.get("component"): item.get("commit_sha") for item in reviewed if isinstance(item, Mapping)}
    if (
        len(reviewed) != len(_REQUIRED_COMPONENTS)
        or dict(pinned_shas) != recorded
        or set(recorded) != set(_REQUIRED_COMPONENTS)
        or any(not isinstance(sha, str) or _SHA.fullmatch(sha) is None for sha in recorded.values())
    ):
        raise CeremonyError("evidence pinned SHA binding mismatch")
    if not isinstance(body.get("evidence_nonce"), str) or _HEX64.fullmatch(body["evidence_nonce"]) is None:
        raise CeremonyError("evidence nonce is invalid")
    module_files = {item.get("component"): item.get("module_file") for item in reviewed if isinstance(item, Mapping)}
    port_bindings = body.get("port_bindings")
    if not isinstance(port_bindings, list) or len(port_bindings) != len(_PORT_NAMES):
        raise CeremonyError("evidence port bindings are incomplete")
    bound = {item.get("port"): item for item in port_bindings if isinstance(item, Mapping)}
    if set(bound) != set(_PORT_NAMES) or any(
        item.get("component") not in _REQUIRED_COMPONENTS
        or (item.get("component"), item.get("symbol")) != _PORT_ENTRYPOINTS[port]
        or item.get("module_file") != module_files.get(item.get("component"))
        for port, item in bound.items()
    ):
        raise CeremonyError("evidence port binding mismatch")
    if body.get("execution_mode") != "live" or body.get("dry_run") is not False or body.get("stub") is not False:
        raise CeremonyError("stub or dry-run evidence is not a live ceremony")
    storage = body.get("storage")
    if (
        not isinstance(storage, Mapping)
        or storage.get("protection") != "windows-owner-and-system-dacl"
        or not isinstance(storage.get("path"), str)
        or not Path(storage["path"]).is_absolute()
    ):
        raise CeremonyError("evidence storage binding is invalid")
    if signature_record.get("algorithm") != "Ed25519" or signature_record.get("key_id") != trust.key_id:
        raise CeremonyError("evidence signature metadata mismatch")
    payload = _canonical(body)
    if envelope.get("body_sha256") != hashlib.sha256(payload).hexdigest():
        raise CeremonyError("evidence body digest mismatch")
    try:
        signature = b64decode(signature_record.get("signature_b64", ""), validate=True)
        Ed25519PublicKey.from_public_bytes(trust.public_key).verify(signature, payload)
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise CeremonyError("evidence coordinator signature is invalid") from exc
    return body


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


def _load_reviewed_module(checkout: ReviewedCheckout) -> tuple[Any, Path]:
    module_path = (checkout.worktree / _REQUIRED_COMPONENTS[checkout.component]).resolve()
    try:
        module_path.relative_to(checkout.worktree)
    except ValueError as exc:
        raise CeremonyError(f"reviewed module escapes worktree: {checkout.component}") from exc
    if not module_path.is_file():
        raise CeremonyError(f"reviewed module is missing: {checkout.component}")
    import_name = f"_selfconnect_ceremony_{checkout.component}_{checkout.commit_sha}"
    spec = importlib.util.spec_from_file_location(import_name, module_path)
    if spec is None or spec.loader is None:
        raise CeremonyError(f"cannot load reviewed module: {checkout.component}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[import_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(import_name, None)
        raise CeremonyError(f"reviewed module import failed: {checkout.component}:{type(exc).__name__}") from exc
    actual = Path(str(getattr(module, "__file__", ""))).resolve()
    if actual != module_path:
        raise CeremonyError(f"reviewed module origin mismatch: {checkout.component}")
    return module, actual


def _direct_function(module: Any, symbol_name: str, module_file: Path) -> Callable[..., Any]:
    try:
        symbol = getattr(module, symbol_name)
    except AttributeError as exc:
        raise CeremonyError(f"reviewed module lacks production entry point: {symbol_name}") from exc
    if (
        not inspect.isfunction(symbol)
        or getattr(symbol, "__module__", None) != module.__name__
        or hasattr(symbol, "__wrapped__")
        or Path(symbol.__code__.co_filename).resolve() != module_file
    ):
        raise CeremonyError(f"production entry point is not a direct pinned-module function: {symbol_name}")
    return symbol


def _build_ceremony_ports(
    modules: Mapping[str, Any],
    checkout_evidence: Sequence[Mapping[str, str]],
) -> tuple[CeremonyPorts, list[dict[str, str]]]:
    """Fetch fixed production symbols from freshly imported pinned modules."""
    module_files = {item["component"]: Path(item["module_file"]).resolve() for item in checkout_evidence}
    callbacks: dict[str, Callable[..., Any]] = {}
    bindings: dict[str, str] = {}
    evidence = []
    for port, (component, symbol_name) in _PORT_ENTRYPOINTS.items():
        callback = _direct_function(modules[component], symbol_name, module_files[component])
        callbacks[port] = callback
        bindings[port] = component
        evidence.append(
            {
                "port": port,
                "component": component,
                "symbol": symbol_name,
                "module_file": str(module_files[component]),
            }
        )
    errors = []
    for component, symbol_name in _PROTOCOL_ERROR_ENTRYPOINTS.items():
        try:
            error = getattr(modules[component], symbol_name)
        except AttributeError as exc:
            raise CeremonyError(f"reviewed module lacks protocol error: {component}.{symbol_name}") from exc
        if (
            not isinstance(error, type)
            or not issubclass(error, Exception)
            or error in {Exception, RuntimeError, NotImplementedError, AttributeError}
            or error.__module__ != modules[component].__name__
            or not isinstance(inspect.getsourcefile(error), str)
            or Path(inspect.getsourcefile(error)).resolve() != module_files[component]
        ):
            raise CeremonyError(f"reviewed module protocol error is invalid: {component}.{symbol_name}")
        errors.append(error)
    return CeremonyPorts(**callbacks, protocol_errors=tuple(errors), bindings=bindings), evidence


def _apply_private_dacl(path: Path) -> None:
    from sc_guarded_submit import _protect_evidence_path

    _protect_evidence_path(path)


def persist_evidence_bundle(bundle: Mapping[str, Any], path: Path) -> Path:
    """Atomically persist canonical evidence under an owner/SYSTEM Windows DACL."""
    if os.name != "nt":
        raise CeremonyError("DACL-protected ceremony evidence requires Windows")
    destination = Path(path).resolve()
    if not destination.parent.is_dir() or destination.exists():
        raise CeremonyError("ceremony evidence destination is unsafe")
    payload = _canonical(bundle) + b"\n"
    temporary = destination.parent / f".{destination.name}.{secrets.token_hex(16)}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        _apply_private_dacl(temporary)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, destination)
        _apply_private_dacl(destination)
        if destination.read_bytes() != payload:
            raise CeremonyError("persisted ceremony evidence verification failed")
    except CeremonyError:
        raise
    except Exception as exc:
        raise CeremonyError("cannot persist DACL-protected ceremony evidence") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
    return destination


def _verified_reviewed_components(
    checkouts: Sequence[ReviewedCheckout],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if os.name != "nt":
        raise CeremonyError("live ceremony is Windows-only")
    by_component = {item.component: item for item in checkouts}
    if len(checkouts) != len(_REQUIRED_COMPONENTS) or set(by_component) != set(_REQUIRED_COMPONENTS):
        raise CeremonyError("runtime, pipe/trust, and failover reviewed checkouts are required")
    evidence = []
    modules = {}
    for component in _REQUIRED_COMPONENTS:
        checkout = by_component[component]
        if not checkout.worktree.is_dir():
            raise CeremonyError(f"reviewed worktree is missing: {component}")
        actual = _git(checkout.worktree, "rev-parse", "HEAD")
        if actual != checkout.commit_sha:
            raise CeremonyError(f"reviewed checkout SHA mismatch: {component}")
        if _git(checkout.worktree, "status", "--porcelain"):
            raise CeremonyError(f"reviewed checkout is dirty: {component}")
        module, module_file = _load_reviewed_module(checkout)
        modules[component] = module
        evidence.append(
            {
                "component": component,
                "worktree": str(checkout.worktree),
                "commit_sha": actual,
                "module_file": str(module_file),
            }
        )
    return evidence, modules


def verify_reviewed_checkouts(checkouts: Sequence[ReviewedCheckout]) -> list[dict[str, str]]:
    """Require Windows, canonical components, clean SHAs, and imported origins."""
    evidence, _modules = _verified_reviewed_components(checkouts)
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
        and result.get("execution_mode") == "live"
        and result.get("dry_run") is False
        and result.get("stub") is False
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
        _verified, ack = _verified_transition(ports, assignment, receipt, state=state, sequence=sequence)
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
        _verified, ack = _verified_transition(ports, replacement_assignment, receipt, state=state, sequence=sequence)
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


def _run_verified_ceremony(
    *,
    checkout_evidence: Sequence[Mapping[str, str]],
    port_evidence: Sequence[Mapping[str, str]],
    route: ExactRoute,
    ports: CeremonyPorts,
    evidence_signer: EvidenceSigner,
    evidence_path: Path,
) -> dict[str, Any]:
    """Execute after pinned modules and their direct symbols are verified."""
    if not isinstance(evidence_signer, EvidenceSigner):
        raise TypeError("a pinned coordinator evidence signer is required")
    cases = [_run_trigger_case(ports, route, trigger) for trigger in _TRIGGERS]
    rejected = []
    for probe in _NEGATIVE_PROBES:
        try:
            ports.negative_probe(probe)
        except ports.protocol_errors as exc:
            rejected.append({"probe": probe, "exception": type(exc).__name__})
        except Exception as exc:
            raise CeremonyError(f"negative probe raised unrelated error: {probe}:{type(exc).__name__}") from exc
        else:
            raise CeremonyError(f"negative probe did not fail closed: {probe}")
    issued_at = _utc_now()
    trust = evidence_signer.trust
    body = {
        "schema": "selfconnect-live-assignment-ceremony-v2",
        "claim": "cryptographic API composition evidence; screen evidence is non-authoritative",
        "issued_at_utc": _utc_text(issued_at),
        "expires_at_utc": _utc_text(issued_at + timedelta(seconds=_EVIDENCE_TTL_SECONDS)),
        "evidence_nonce": secrets.token_hex(32),
        "coordinator_id": trust.coordinator_id,
        "key_id": trust.key_id,
        "operator_id": trust.operator_id,
        "operator_authorization_id": trust.operator_authorization_id,
        "pinned_shas": {item["component"]: item["commit_sha"] for item in checkout_evidence},
        "reviewed_checkouts": checkout_evidence,
        "port_bindings": port_evidence,
        "execution_mode": "live",
        "dry_run": False,
        "stub": False,
        "storage": {
            "path": str(Path(evidence_path).resolve()),
            "protection": "windows-owner-and-system-dacl",
        },
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
    payload = _canonical(body)
    try:
        signature = evidence_signer.sign(payload)
    except Exception as exc:
        raise CeremonyError("coordinator evidence signing failed") from exc
    if not isinstance(signature, bytes):
        raise CeremonyError("coordinator evidence signer returned an invalid signature")
    bundle = {
        "body": body,
        "body_sha256": hashlib.sha256(payload).hexdigest(),
        "signature": {
            "algorithm": "Ed25519",
            "key_id": trust.key_id,
            "signature_b64": b64encode(signature).decode("ascii"),
        },
    }
    verify_evidence_bundle(bundle, trust=trust, now=issued_at)
    if _HEX64.fullmatch(bundle["body_sha256"]) is None:
        raise AssertionError("unreachable evidence digest failure")
    persisted = persist_evidence_bundle(bundle, evidence_path)
    if persisted != Path(body["storage"]["path"]):
        raise CeremonyError("persisted evidence path binding mismatch")
    return bundle


def run_live_ceremony(
    *,
    checkouts: Sequence[ReviewedCheckout],
    route: ExactRoute,
    evidence_signer: EvidenceSigner,
    evidence_path: Path,
) -> dict[str, Any]:
    """Load pinned modules, fetch fixed production symbols, and run the ceremony.

    No caller-supplied execution callback is accepted. UIA, OCR, composer text,
    HWND/PID/title observations, and PostMessage queue acceptance remain absent
    from the authority surface.
    """
    checkout_evidence, modules = _verified_reviewed_components(checkouts)
    ports, port_evidence = _build_ceremony_ports(modules, checkout_evidence)
    return _run_verified_ceremony(
        checkout_evidence=checkout_evidence,
        port_evidence=port_evidence,
        route=route,
        ports=ports,
        evidence_signer=evidence_signer,
        evidence_path=evidence_path,
    )


def missing_live_prerequisites() -> tuple[str, ...]:
    """Exact external prerequisites intentionally not fabricated by this branch."""
    return (
        "reviewed runtime SHA exporting the four fixed live_ceremony runtime entry points and protocol error",
        "reviewed seat-pipe/trust SHA exporting fixed receipt/negative entry points with OS-derived evidence",
        "reviewed failover SHA exporting live_ceremony_failover with fresh-birth delivery and no process kill",
        "pinned coordinator/authority/seat/response-receiver public keys and signed enrollments",
        "fresh signed durable revocation resolver used at every verification boundary",
        "two live Windows Terminal seats with exact TargetIdentity and TerminalTabIdentity",
        "DACL-protected state, mailbox, pipe, registry, event, and evidence paths",
        "operator authorization to inject only the reviewed signed assignment wire",
    )


__all__ = [
    "CeremonyError",
    "EvidenceSigner",
    "EvidenceTrust",
    "ExactRoute",
    "ReviewedCheckout",
    "SeatIdentity",
    "missing_live_prerequisites",
    "persist_evidence_bundle",
    "run_live_ceremony",
    "verify_evidence_bundle",
    "verify_reviewed_checkouts",
]
