"""Signed, durable, freshness-bounded seat-key revocation snapshots."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sc_authority_trust import load_authority_trust, verify_authority_signatures
from sc_seat_identity import (
    _canonical,
    _duration_ms,
    _hex,
    _sha256,
    _time_ms,
    canonical_json_loads,
    key_id,
)
from sc_trust_anchor import advance_monotonic_anchor, verify_monotonic_anchor

SNAPSHOT_SCHEMA = "selfconnect-seat-revocations-v1"
STORE_SCHEMA = "selfconnect-seat-revocation-store-v1"
MAX_REVOCATION_TTL_SECONDS = 3600.0
MAX_REVOCATION_KEYS = 100_000


def _load_store(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = canonical_json_loads(path.read_bytes())
    if set(data) != {"schema", "latest_sha256", "snapshot", "signatures"}:
        raise ValueError("seat revocation store is malformed")
    if data["schema"] != STORE_SCHEMA:
        raise ValueError("seat revocation store schema is invalid")
    if data["latest_sha256"] != _sha256(_canonical(data["snapshot"])):
        raise ValueError("seat revocation store digest is invalid")
    verify_monotonic_anchor(
        path,
        "seat-revocation-store",
        data["snapshot"]["epoch"],
        data["snapshot"]["version"],
        _sha256(_canonical(data)),
    )
    return data


def create_revocation_snapshot(
    store_path: str | Path,
    trust_path: str | Path,
    revoked_key_ids: Iterable[str],
    *,
    now: float | None = None,
    ttl_seconds: float = 300.0,
) -> dict[str, Any]:
    if not 0 < ttl_seconds <= MAX_REVOCATION_TTL_SECONDS:
        raise ValueError("seat revocation snapshot TTL is invalid")
    issued = _time_ms(now)
    ttl_ms = _duration_ms(ttl_seconds, "seat revocation snapshot TTL")
    revoked = sorted({_hex(value, "revoked seat key ID") for value in revoked_key_ids})
    if len(revoked) > MAX_REVOCATION_KEYS:
        raise ValueError("seat revocation snapshot is too large")
    trust = load_authority_trust(trust_path)
    previous = _load_store(Path(store_path))
    if previous is None:
        version = 1
        previous_sha256 = "0" * 64
    else:
        prior = previous["snapshot"]
        version = prior["version"] + 1
        previous_sha256 = previous["latest_sha256"]
    return {
        "schema": SNAPSHOT_SCHEMA,
        "epoch": trust["epoch"],
        "version": version,
        "trust_version": trust["version"],
        "issued_at": issued,
        "expires_at": issued + ttl_ms,
        "previous_sha256": previous_sha256,
        "revoked_key_ids": revoked,
    }


def sign_revocation_snapshot(snapshot: dict[str, Any], identity: Any) -> dict[str, str]:
    return {
        "key_id": key_id(str(identity.public_key_hex)),
        "signature_b64": base64.b64encode(identity.sign(_canonical(snapshot))).decode("ascii"),
    }


def _validate_snapshot(
    snapshot: dict[str, Any],
    signatures: Iterable[dict[str, str]],
    trust_path: str | Path,
    *,
    now: float,
) -> None:
    required = {
        "schema",
        "epoch",
        "version",
        "trust_version",
        "issued_at",
        "expires_at",
        "previous_sha256",
        "revoked_key_ids",
    }
    if set(snapshot) != required or snapshot["schema"] != SNAPSHOT_SCHEMA:
        raise ValueError("seat revocation snapshot is malformed")
    trust = load_authority_trust(trust_path)
    if snapshot["epoch"] != trust["epoch"] or snapshot["trust_version"] != trust["version"]:
        raise ValueError("seat revocation snapshot does not use current authority trust")
    if type(snapshot["version"]) is not int or snapshot["version"] < 1:
        raise ValueError("seat revocation snapshot version is invalid")
    issued, expires = snapshot["issued_at"], snapshot["expires_at"]
    if type(issued) is not int or type(expires) is not int or type(now) is not int:
        raise ValueError("seat revocation snapshot time is invalid")
    if expires <= issued or expires - issued > MAX_REVOCATION_TTL_SECONDS * 1000:
        raise ValueError("seat revocation snapshot validity is invalid")
    if issued > now + 5000 or now > expires:
        raise ValueError("seat revocation snapshot is stale")
    revoked = snapshot["revoked_key_ids"]
    if not isinstance(revoked, list) or len(revoked) > MAX_REVOCATION_KEYS:
        raise ValueError("seat revocation snapshot keys are invalid")
    normalized = sorted({_hex(value, "revoked seat key ID") for value in revoked})
    if revoked != normalized:
        raise ValueError("seat revocation snapshot keys are not unique and sorted")
    _hex(snapshot["previous_sha256"], "previous revocation snapshot digest")
    verify_authority_signatures(snapshot, signatures, trust_path)


def apply_revocation_snapshot(
    store_path: str | Path,
    trust_path: str | Path,
    snapshot: dict[str, Any],
    signatures: Iterable[dict[str, str]],
    *,
    now: float | None = None,
) -> Path:
    current = _time_ms(now)
    signed = list(signatures)
    _validate_snapshot(snapshot, signed, trust_path, now=current)
    target = Path(store_path)
    previous = _load_store(target)
    if previous is None:
        if snapshot["version"] != 1 or snapshot["previous_sha256"] != "0" * 64:
            raise ValueError("initial seat revocation snapshot has invalid ancestry")
    else:
        prior = previous["snapshot"]
        if snapshot["epoch"] < prior["epoch"] or snapshot["version"] <= prior["version"]:
            raise ValueError("seat revocation snapshot replay or rollback rejected")
        if snapshot["version"] != prior["version"] + 1:
            raise ValueError("seat revocation snapshot version must be contiguous")
        if snapshot["previous_sha256"] != previous["latest_sha256"]:
            raise ValueError("seat revocation snapshot chain is invalid")
    document = {
        "schema": STORE_SCHEMA,
        "latest_sha256": _sha256(_canonical(snapshot)),
        "snapshot": snapshot,
        "signatures": signed,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_suffix(target.suffix + ".tmp")
    staged.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(staged, 0o600)
    os.replace(staged, target)
    if os.name == "nt":
        from sc_guarded_submit import _protect_evidence_path

        _protect_evidence_path(target)
    advance_monotonic_anchor(
        target,
        "seat-revocation-store",
        snapshot["epoch"],
        snapshot["version"],
        _sha256(_canonical(document)),
    )
    return target.resolve()


def resolve_revoked_key_ids(
    store_path: str | Path,
    trust_path: str | Path,
    *,
    now: float | None = None,
) -> frozenset[str]:
    """Production resolver: absence, bad signature, rollback, or staleness fails closed."""
    current = _time_ms(now)
    stored = _load_store(Path(store_path))
    if stored is None:
        raise ValueError("required seat revocation snapshot is absent")
    _validate_snapshot(stored["snapshot"], stored["signatures"], trust_path, now=current)
    return frozenset(stored["snapshot"]["revoked_key_ids"])
