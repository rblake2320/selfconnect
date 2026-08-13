"""Versioned seat-authority trust with explicit bootstrap and signed change control."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sc_seat_identity import _canonical, _sha256, canonical_json_loads, key_id
from sc_trust_anchor import (
    advance_local_integrity_anchor,
    require_high_assurance_anchor,
    verify_local_integrity_anchor,
)

TRUST_SCHEMA = "selfconnect-seat-authority-trust-v1"
TRANSITION_SCHEMA = "selfconnect-seat-authority-transition-v1"


def _key_map(public_keys: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for public_key in public_keys:
        identifier = key_id(public_key)
        if identifier in result:
            raise ValueError("authority trust contains a duplicate key")
        result[identifier] = public_key
    if not result:
        raise ValueError("authority trust requires at least one key")
    return dict(sorted(result.items()))


def _validate_quorum(quorum: int, keys: dict[str, str], label: str) -> int:
    if type(quorum) is not int or quorum < 1 or quorum > len(keys):
        raise ValueError(f"{label} quorum is invalid")
    return quorum


def _state_body(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "history"}


def _state_digest(state: dict[str, Any]) -> str:
    return _sha256(_canonical(_state_body(state)))


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(staged, 0o600)
    os.replace(staged, path)
    if os.name == "nt":
        from sc_guarded_submit import _protect_evidence_path

        _protect_evidence_path(path)


def _bootstrap_local_authority_trust_for_test(
    path: str | Path,
    *,
    root_public_keys: Iterable[str],
    quorum: int,
    recovery_public_keys: Iterable[str],
    recovery_quorum: int,
) -> Path:
    """Create non-authoritative local fixture state for tests and development.

    This helper is intentionally private.  The production package exposes no
    unsigned trust-root mint/reset API.  Files created here remain local
    integrity fixtures and cannot satisfy the high-assurance runtime gate.
    """
    target = Path(path)
    if target.exists():
        raise FileExistsError("authority trust is already bootstrapped")
    roots = _key_map(root_public_keys)
    recovery = _key_map(recovery_public_keys)
    bootstrap = {
        "epoch": 1,
        "version": 1,
        "roots": roots,
        "quorum": _validate_quorum(quorum, roots, "authority"),
        "recovery": recovery,
        "recovery_quorum": _validate_quorum(recovery_quorum, recovery, "recovery"),
    }
    state = {
        "schema": TRUST_SCHEMA,
        **bootstrap,
        "bootstrap": bootstrap,
        "history": [],
    }
    _atomic_write(target, state)
    advance_local_integrity_anchor(
        target,
        "seat-authority-trust",
        state["epoch"],
        state["version"],
        _sha256(_canonical(state)),
    )
    return target.resolve()


def load_local_authority_trust(path: str | Path) -> dict[str, Any]:
    """Validate signed structure plus the same-user local integrity journal."""
    state = canonical_json_loads(Path(path).read_bytes())
    required = {
        "schema",
        "epoch",
        "version",
        "roots",
        "quorum",
        "recovery",
        "recovery_quorum",
        "bootstrap",
        "history",
    }
    if set(state) != required or state["schema"] != TRUST_SCHEMA:
        raise ValueError("authority trust store is malformed")
    roots = _key_map(state["roots"].values())
    recovery = _key_map(state["recovery"].values())
    if roots != state["roots"] or recovery != state["recovery"]:
        raise ValueError("authority trust key IDs are invalid")
    _validate_quorum(state["quorum"], roots, "authority")
    _validate_quorum(state["recovery_quorum"], recovery, "recovery")
    if type(state["epoch"]) is not int or type(state["version"]) is not int:
        raise ValueError("authority trust counters are invalid")
    if state["epoch"] < 1 or state["version"] < 1 or not isinstance(state["history"], list):
        raise ValueError("authority trust counters are invalid")
    genesis = state["bootstrap"]
    if not isinstance(genesis, dict) or set(genesis) != {
        "epoch",
        "version",
        "roots",
        "quorum",
        "recovery",
        "recovery_quorum",
    }:
        raise ValueError("authority trust bootstrap is malformed")
    current = {"schema": TRUST_SCHEMA, **genesis, "bootstrap": genesis, "history": []}
    if genesis["epoch"] != 1 or genesis["version"] != 1:
        raise ValueError("authority trust bootstrap counters are invalid")
    current["roots"] = _key_map(genesis["roots"].values())
    current["recovery"] = _key_map(genesis["recovery"].values())
    _validate_quorum(genesis["quorum"], current["roots"], "bootstrap authority")
    _validate_quorum(genesis["recovery_quorum"], current["recovery"], "bootstrap recovery")
    for record in state["history"]:
        if not isinstance(record, dict) or set(record) != {"body", "signatures"}:
            raise ValueError("authority trust history is malformed")
        body, signatures = record["body"], record["signatures"]
        if body.get("previous_state_sha256") != _state_digest(current):
            raise ValueError("authority trust history chain is invalid")
        kind = body.get("kind")
        signing_keys = current["roots"] if kind == "rotation" else current["recovery"]
        threshold = current["quorum"] if kind == "rotation" else current["recovery_quorum"]
        _verify_quorum(body, signatures, signing_keys, threshold)
        if body.get("previous_epoch") != current["epoch"] or body.get("previous_version") != current["version"]:
            raise ValueError("authority trust history counters are invalid")
        if body.get("version") != current["version"] + 1:
            raise ValueError("authority trust history version is invalid")
        expected_epoch = current["epoch"] if kind == "rotation" else current["epoch"] + 1
        if body.get("epoch") != expected_epoch:
            raise ValueError("authority trust history epoch is invalid")
        next_roots = _key_map(body.get("new_roots", {}).values())
        next_quorum = _validate_quorum(body.get("new_quorum"), next_roots, "history authority")
        current = {
            **current,
            "epoch": body["epoch"],
            "version": body["version"],
            "roots": next_roots,
            "quorum": next_quorum,
            "history": [*current["history"], record],
        }
    for field in ("epoch", "version", "roots", "quorum", "recovery", "recovery_quorum", "bootstrap"):
        if state[field] != current[field]:
            raise ValueError("authority trust current state does not match signed history")
    verify_local_integrity_anchor(
        path,
        "seat-authority-trust",
        state["epoch"],
        state["version"],
        _sha256(_canonical(state)),
    )
    return state


def authority_public_key(path: str | Path, authority_key_id: str) -> str:
    """Resolve an authority key only through the high-assurance boundary."""
    require_high_assurance_anchor()
    return _local_authority_public_key(path, authority_key_id)


def _local_authority_public_key(path: str | Path, authority_key_id: str) -> str:
    """Resolve fixture state without making an authority claim."""
    state = load_local_authority_trust(path)
    try:
        return state["roots"][authority_key_id]
    except KeyError as exc:
        raise ValueError("authority key is not trusted at the current epoch") from exc


def build_authority_transition(
    path: str | Path,
    *,
    new_root_public_keys: Iterable[str],
    new_quorum: int,
    recovery: bool = False,
) -> dict[str, Any]:
    state = load_local_authority_trust(path)
    roots = _key_map(new_root_public_keys)
    threshold = _validate_quorum(new_quorum, roots, "new authority")
    if roots == state["roots"] and threshold == state["quorum"]:
        raise ValueError("authority transition makes no change")
    return {
        "schema": TRANSITION_SCHEMA,
        "kind": "recovery" if recovery else "rotation",
        "previous_state_sha256": _state_digest(state),
        "previous_epoch": state["epoch"],
        "previous_version": state["version"],
        "epoch": state["epoch"] + (1 if recovery else 0),
        "version": state["version"] + 1,
        "new_roots": roots,
        "new_quorum": threshold,
    }


def sign_authority_transition(transition: dict[str, Any], identity: Any) -> dict[str, str]:
    return {
        "key_id": key_id(str(identity.public_key_hex)),
        "signature_b64": base64.b64encode(identity.sign(_canonical(transition))).decode("ascii"),
    }


def _verify_quorum(
    body: dict[str, Any],
    signatures: Iterable[dict[str, str]],
    keys: dict[str, str],
    quorum: int,
) -> None:
    valid: set[str] = set()
    for signed in signatures:
        identifier = signed.get("key_id")
        if identifier in valid or identifier not in keys:
            continue
        try:
            signature = base64.b64decode(signed.get("signature_b64"), validate=True)
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(keys[identifier])).verify(signature, _canonical(body))
        except Exception:
            continue
        valid.add(identifier)
    if len(valid) < quorum:
        raise ValueError("authority transition signature quorum is not satisfied")


def apply_authority_transition(
    path: str | Path,
    transition: dict[str, Any],
    signatures: Iterable[dict[str, str]],
) -> dict[str, Any]:
    target = Path(path)
    state = load_local_authority_trust(target)
    signatures = list(signatures)
    required = {
        "schema",
        "kind",
        "previous_state_sha256",
        "previous_epoch",
        "previous_version",
        "epoch",
        "version",
        "new_roots",
        "new_quorum",
    }
    if set(transition) != required or transition["schema"] != TRANSITION_SCHEMA:
        raise ValueError("authority transition is malformed")
    if transition["previous_state_sha256"] != _state_digest(state):
        raise ValueError("authority transition is stale or targets a different trust state")
    if transition["previous_epoch"] != state["epoch"] or transition["previous_version"] != state["version"]:
        raise ValueError("authority transition counters do not advance current state")
    if transition["version"] != state["version"] + 1:
        raise ValueError("authority transition version is not monotonic")
    kind = transition["kind"]
    if kind == "rotation":
        if transition["epoch"] != state["epoch"]:
            raise ValueError("authority rotation cannot change the recovery epoch")
        signing_keys, quorum = state["roots"], state["quorum"]
    elif kind == "recovery":
        if transition["epoch"] != state["epoch"] + 1:
            raise ValueError("authority recovery epoch is not monotonic")
        signing_keys, quorum = state["recovery"], state["recovery_quorum"]
    else:
        raise ValueError("authority transition kind is invalid")
    roots = _key_map(transition["new_roots"].values())
    if roots != transition["new_roots"]:
        raise ValueError("authority transition key IDs are invalid")
    threshold = _validate_quorum(transition["new_quorum"], roots, "new authority")
    _verify_quorum(transition, signatures, signing_keys, quorum)
    signed_record = {"body": transition, "signatures": signatures}
    next_state = {
        **state,
        "epoch": transition["epoch"],
        "version": transition["version"],
        "roots": roots,
        "quorum": threshold,
        "history": [*state["history"], signed_record],
    }
    _atomic_write(target, next_state)
    advance_local_integrity_anchor(
        target,
        "seat-authority-trust",
        next_state["epoch"],
        next_state["version"],
        _sha256(_canonical(next_state)),
    )
    return next_state


def verify_authority_signatures(
    payload: dict[str, Any],
    signatures: Iterable[dict[str, str]],
    trust_path: str | Path,
) -> None:
    state = load_local_authority_trust(trust_path)
    _verify_quorum(payload, signatures, state["roots"], state["quorum"])
