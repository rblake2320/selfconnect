"""Authenticated, one-shot SelfConnect role-migration manifests.

Terminal text is only a notification channel.  A successor accepts a role only
after this module verifies an independently enrolled Ed25519 signer, the exact
checkpoint bytes, the live successor window binding, freshness, and replay.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import sqlite3
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MANIFEST_SCHEMA = "selfconnect-migration-manifest-v2"
TRUST_SCHEMA = "selfconnect-migration-trust-v1"
RECEIPT_SCHEMA = "selfconnect-migration-receipt-v1"
MAX_TTL_SECONDS = 300


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def signer_key_id(public_key_hex: str) -> str:
    return _sha256_bytes(bytes.fromhex(public_key_hex))


def default_state_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise RuntimeError("LOCALAPPDATA is required for migration trust state")
    return Path(root) / "SelfConnect" / "migration"


def default_trust_store() -> Path:
    return default_state_dir() / "trusted_signers.json"


def default_replay_store() -> Path:
    return default_state_dir() / "accepted_manifests.sqlite3"


def enroll_migration_signer(identity: Any, path: str | Path | None = None) -> Path:
    """Explicitly enroll an authority; migration never auto-enrolls a key."""
    target = Path(path) if path else default_trust_store()
    target.parent.mkdir(parents=True, exist_ok=True)
    public_key_hex = str(identity.public_key_hex)
    key_id = signer_key_id(public_key_hex)
    data = {"schema": TRUST_SCHEMA, "signers": []}
    if target.exists():
        data = json.loads(target.read_text(encoding="utf-8"))
        if data.get("schema") != TRUST_SCHEMA or not isinstance(data.get("signers"), list):
            raise ValueError("migration trust store is malformed")
    matches = [entry for entry in data["signers"] if entry.get("key_id") == key_id]
    if matches and matches[0].get("public_key_hex") != public_key_hex:
        raise ValueError("migration signer key-id collision")
    if not matches:
        data["signers"].append({
            "key_id": key_id,
            "public_key_hex": public_key_hex,
            "label": str(getattr(identity, "label", "")),
        })
    staged = target.with_suffix(target.suffix + ".tmp")
    staged.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(staged, target)
    return target.resolve()


def _trusted_public_key(key_id: str, trust_store: str | Path) -> str:
    data = json.loads(Path(trust_store).read_text(encoding="utf-8"))
    if data.get("schema") != TRUST_SCHEMA or not isinstance(data.get("signers"), list):
        raise ValueError("migration trust store is malformed")
    for entry in data["signers"]:
        if entry.get("key_id") == key_id:
            public_key_hex = entry.get("public_key_hex")
            if not isinstance(public_key_hex, str) or signer_key_id(public_key_hex) != key_id:
                raise ValueError("migration trust entry is malformed")
            return public_key_hex
    raise ValueError("migration signer is not independently trusted")


def require_enrolled_signer(identity: Any, trust_store: str | Path | None = None) -> str:
    public_key_hex = str(identity.public_key_hex)
    key_id = signer_key_id(public_key_hex)
    if _trusted_public_key(key_id, trust_store or default_trust_store()) != public_key_hex:
        raise ValueError("migration signer does not match the enrolled trust key")
    return key_id


def resolve_window_binding(hwnd: int) -> dict[str, Any]:
    """Resolve an HWND to a short-lived, PID-reuse-resistant target binding."""
    import ctypes

    import psutil
    from self_connect import list_windows

    if type(hwnd) is not int or hwnd <= 0:
        raise ValueError("successor HWND must be a positive integer")
    target = next((item for item in list_windows() if item.hwnd == hwnd), None)
    if target is None:
        raise ValueError("successor window is not live")
    class_buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
    process = psutil.Process(int(target.pid))
    core = {
        "hwnd": hwnd,
        "pid": int(target.pid),
        "exe_name": str(target.exe_name or "").lower(),
        "class_name": class_buf.value,
        "process_started_at": float(process.create_time()),
    }
    core["binding_sha256"] = _sha256_bytes(_canonical(core))
    return core


def resolve_live_tab_snapshot(hwnd: int, peer_birth_id: str) -> dict[str, Any]:
    """Capture the active seat for routing; cryptographic proof supplies identity."""
    from sc_guarded_submit import TargetIdentity
    from sc_terminal_tab import capture_active_terminal_tab
    from self_connect import list_windows

    window = next((item for item in list_windows() if item.hwnd == hwnd), None)
    if window is None:
        raise ValueError("successor routing window is not live")
    guard = capture_active_terminal_tab(TargetIdentity.from_window(window), peer_birth_id=peer_birth_id)
    return guard.checkpoint("migration_seat_verify", select=False, deadline=time.monotonic() + 5.0)


def create_signed_manifest(
    *,
    identity: Any,
    checkpoint_path: str | Path,
    role: str,
    source_hwnd: int,
    successor_binding: dict[str, Any],
    output_path: str | Path | None = None,
    now: float | None = None,
    ttl_seconds: int = 120,
) -> Path:
    if not 1 <= int(ttl_seconds) <= MAX_TTL_SECONDS:
        raise ValueError("migration manifest TTL must be between 1 and 300 seconds")
    issued_at = time.time() if now is None else float(now)
    if not math.isfinite(issued_at):
        raise ValueError("migration manifest time is invalid")
    checkpoint = Path(checkpoint_path).resolve()
    checkpoint_bytes = checkpoint.read_bytes()
    public_key_hex = str(identity.public_key_hex)
    body = {
        "schema": MANIFEST_SCHEMA,
        "manifest_id": str(uuid.uuid4()),
        "issuer_key_id": signer_key_id(public_key_hex),
        "role": str(role),
        "source_hwnd": int(source_hwnd),
        "successor": dict(successor_binding),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _sha256_bytes(checkpoint_bytes),
        "issued_at": issued_at,
        "expires_at": issued_at + int(ttl_seconds),
        "nonce": uuid.uuid4().hex,
    }
    signed = {**body, "signature_b64": base64.b64encode(identity.sign(_canonical(body))).decode()}
    target = Path(output_path) if output_path else checkpoint.with_suffix(checkpoint.suffix + ".migration.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(signed, indent=2) + "\n", encoding="utf-8")
    return target.resolve()


def _consume_once(receipt: dict[str, Any], replay_store: str | Path) -> None:
    target = Path(replay_store)
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS accepted (manifest_id TEXT PRIMARY KEY, accepted_at REAL NOT NULL)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS accepted_receipts "
            "(manifest_id TEXT PRIMARY KEY, receipt_json TEXT NOT NULL)"
        )
        try:
            manifest_id = str(receipt["manifest_id"])
            accepted_at = float(receipt["accepted_at"])
            db.execute(
                "INSERT INTO accepted(manifest_id, accepted_at) VALUES (?, ?)",
                (manifest_id, accepted_at),
            )
            db.execute(
                "INSERT INTO accepted_receipts(manifest_id, receipt_json) VALUES (?, ?)",
                (manifest_id, json.dumps(receipt, sort_keys=True, separators=(",", ":"))),
            )
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise ValueError("migration manifest has already been consumed") from exc
    finally:
        db.close()


def manifest_consumed(
    manifest_id: str,
    replay_store: str | Path | None = None,
    *,
    manifest_path: str | Path | None = None,
) -> bool:
    """Return true only for a durable receipt matching the supplied manifest."""
    target = Path(replay_store) if replay_store else default_replay_store()
    if not target.exists():
        return False
    db = sqlite3.connect(target)
    try:
        if manifest_path is None:
            row = db.execute(
                "SELECT 1 FROM accepted WHERE manifest_id = ?", (manifest_id,)
            ).fetchone()
            return row is not None
        row = db.execute(
            "SELECT receipt_json FROM accepted_receipts WHERE manifest_id = ?",
            (manifest_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    finally:
        db.close()
    if row is None:
        return False
    try:
        receipt = json.loads(row[0])
        signed_manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        successor = signed_manifest["successor"]
        return (
            receipt.get("schema") == RECEIPT_SCHEMA
            and receipt.get("manifest_id") == manifest_id
            and receipt.get("manifest_sha256") == _sha256_bytes(_canonical(signed_manifest))
            and receipt.get("successor_binding_sha256") == successor.get("binding_sha256")
            and receipt.get("issuer_key_id") == signed_manifest.get("issuer_key_id")
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return False


def verify_migration_manifest(
    manifest_path: str | Path,
    *,
    expected_hwnd: int,
    trust_store: str | Path | None = None,
    replay_store: str | Path | None = None,
    consume: bool = False,
    now: float | None = None,
    target_resolver: Callable[[int], dict[str, Any]] = resolve_window_binding,
    seat_bundle: dict[str, Any] | None = None,
    seat_replay_store: str | Path | None = None,
    seat_issue_store: str | Path | None = None,
    seat_tab_snapshot_resolver: Callable[[int, str], dict[str, Any]] | None = None,
    seat_receiver_trust_store: str | Path | None = None,
) -> dict[str, Any]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("migration manifest schema is invalid")
    signed_manifest = dict(manifest)
    signature_text = manifest.pop("signature_b64", None)
    required_ints = ("source_hwnd",)
    if any(type(manifest.get(field)) is not int for field in required_ints):
        raise ValueError("migration manifest integer field is invalid")
    if manifest["source_hwnd"] <= 0:
        raise ValueError("migration manifest source HWND is invalid")
    try:
        uuid.UUID(str(manifest.get("manifest_id")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("migration manifest ID is invalid") from exc
    role = manifest.get("role")
    nonce = manifest.get("nonce")
    if not isinstance(role, str) or not role.strip():
        raise ValueError("migration manifest role is invalid")
    if not isinstance(nonce, str) or len(nonce) != 32:
        raise ValueError("migration manifest nonce is invalid")
    try:
        bytes.fromhex(nonce)
    except ValueError as exc:
        raise ValueError("migration manifest nonce is invalid") from exc
    for field in ("issued_at", "expires_at"):
        if type(manifest.get(field)) not in (int, float) or not math.isfinite(float(manifest[field])):
            raise ValueError("migration manifest time field is invalid")
    check_time = time.time() if now is None else float(now)
    if not math.isfinite(check_time):
        raise ValueError("migration verification time is invalid")
    if manifest["expires_at"] <= manifest["issued_at"]:
        raise ValueError("migration manifest validity window is inverted or empty")
    if manifest["issued_at"] > check_time + 5 or check_time > manifest["expires_at"]:
        raise ValueError("migration manifest is not currently valid")
    if manifest["expires_at"] - manifest["issued_at"] > MAX_TTL_SECONDS:
        raise ValueError("migration manifest validity window is too broad")
    key_id = manifest.get("issuer_key_id")
    if not isinstance(key_id, str) or not isinstance(signature_text, str):
        raise ValueError("migration manifest signature fields are invalid")
    public_key_hex = _trusted_public_key(key_id, trust_store or default_trust_store())
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(
            base64.b64decode(signature_text, validate=True), _canonical(manifest)
        )
    except Exception as exc:
        raise ValueError("migration manifest signature is invalid") from exc
    successor = manifest.get("successor")
    if not isinstance(successor, dict) or successor.get("hwnd") != expected_hwnd:
        raise ValueError("migration manifest targets a different successor HWND")
    live_binding = target_resolver(expected_hwnd)
    routing_binding_matches = live_binding == successor
    checkpoint_path = Path(str(manifest.get("checkpoint_path", ""))).resolve()
    checkpoint_bytes = checkpoint_path.read_bytes()
    if _sha256_bytes(checkpoint_bytes) != manifest.get("checkpoint_sha256"):
        raise ValueError("migration checkpoint bytes do not match the signed manifest")
    checkpoint = json.loads(checkpoint_bytes)
    if checkpoint.get("schema") != "selfconnect-checkpoint-v1":
        raise ValueError("migration checkpoint schema is invalid")
    stable = {key: value for key, value in checkpoint.items() if key != "digest"}
    if checkpoint.get("digest") != _sha256_bytes(_canonical(stable)):
        raise ValueError("migration checkpoint digest is invalid")
    if checkpoint.get("role") != manifest.get("role") or checkpoint.get("own_hwnd") != manifest["source_hwnd"]:
        raise ValueError("migration checkpoint role/source binding is invalid")
    if consume:
        if not isinstance(seat_bundle, dict):
            raise ValueError("authenticated per-seat channel proof is required")
        from sc_seat_identity import (
            trusted_receiver_public_key,
            verify_enrollment,
            verify_proof,
        )
        enrollment = seat_bundle.get("enrollment")
        challenge = seat_bundle.get("challenge")
        delivery = seat_bundle.get("delivery")
        proof = seat_bundle.get("proof")
        channel = seat_bundle.get("channel_evidence")
        if not all(isinstance(item, dict) for item in (enrollment, challenge, delivery, proof, channel)):
            raise ValueError("authenticated per-seat channel proof is malformed")
        if seat_issue_store is None or seat_tab_snapshot_resolver is None or seat_receiver_trust_store is None:
            raise ValueError("trusted seat issuance, receiver, and live tab configuration are required")
        verified_enrollment = verify_enrollment(
            enrollment, authority_public_key_hex=public_key_hex, now=check_time
        )
        receiver_key_id = channel.get("receiver_key_id")
        if not isinstance(receiver_key_id, str):
            raise ValueError("secure response receiver key ID is required")
        receiver_public_key_hex = trusted_receiver_public_key(
            receiver_key_id, seat_receiver_trust_store
        )
        if receiver_public_key_hex == public_key_hex:
            raise ValueError("seat response receiver key must be distinct from migration authority")
        if challenge.get("operation_sha256") != _sha256_bytes(_canonical(signed_manifest)):
            raise ValueError("seat challenge does not bind the signed migration operation")
        verify_proof(
            proof, challenge=challenge, delivery=delivery, enrollment=enrollment,
            channel_evidence=channel,
            authority_public_key_hex=public_key_hex,
            receiver_public_key_hex=receiver_public_key_hex,
            expected_operation_sha256=_sha256_bytes(_canonical(signed_manifest)),
            expected_tab_snapshot_sha256=__import__("sc_seat_identity").tab_snapshot_digest(
                seat_tab_snapshot_resolver(expected_hwnd, verified_enrollment["birth_id"])
            ),
            expected_target_hwnd=expected_hwnd,
            replay_store=seat_replay_store or replay_store or default_replay_store(),
            issue_store=seat_issue_store,
            now=check_time, consume=True,
        )
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": _sha256_bytes(_canonical(signed_manifest)),
        "successor_binding_sha256": successor.get("binding_sha256"),
        "issuer_key_id": key_id,
        "accepted_at": check_time,
    }
    if consume:
        _consume_once(receipt, replay_store or default_replay_store())
    return {
        "ok": True,
        "status": "ACCEPTED" if consume else "VERIFIED",
        "manifest_id": manifest["manifest_id"],
        "role": manifest["role"],
        "source_hwnd": manifest["source_hwnd"],
        "successor_hwnd": expected_hwnd,
        "checkpoint_path": str(checkpoint_path),
        "issuer_key_id": key_id,
        "receipt": receipt,
        "routing_binding_matches": routing_binding_matches,
    }


def _authorized_cli_store(value: str | None, env_name: str, label: str) -> str | None:
    """Require custom CLI state paths to be pinned by the trusted launch context."""
    if value is None:
        return None
    authorized = os.environ.get(env_name)
    if not authorized or Path(authorized).resolve() != Path(value).resolve():
        raise ValueError(f"custom migration {label} is not authorized by successor launch")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify authenticated SelfConnect migration handoffs")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--expected-hwnd", required=True, type=int)
    verify.add_argument("--trust-store")
    verify.add_argument("--replay-store")
    verify.add_argument("--seat-bundle")
    verify.add_argument("--seat-replay-store")
    verify.add_argument("--seat-issue-store")
    verify.add_argument("--seat-receiver-trust-store")
    verify.add_argument("--consume", action="store_true")
    args = parser.parse_args()
    try:
        inherited_hwnd = os.environ.get("SELFCONNECT_MIGRATION_SUCCESSOR_HWND", "")
        try:
            own_hwnd = int(inherited_hwnd)
        except ValueError as exc:
            raise ValueError(
                "trusted successor launch binding is absent; do not accept this migration"
            ) from exc
        if own_hwnd != args.expected_hwnd:
            raise ValueError(
                f"verification command targets hwnd={args.expected_hwnd}, "
                f"but this successor was launched for hwnd={own_hwnd}"
            )
        trust_store = _authorized_cli_store(
            args.trust_store, "SELFCONNECT_MIGRATION_TRUST_STORE", "trust store"
        )
        replay_store = _authorized_cli_store(
            args.replay_store, "SELFCONNECT_MIGRATION_REPLAY_STORE", "replay store"
        )
        seat_bundle_path = _authorized_cli_store(
            args.seat_bundle, "SELFCONNECT_MIGRATION_SEAT_BUNDLE", "seat bundle"
        )
        seat_replay_store = _authorized_cli_store(
            args.seat_replay_store, "SELFCONNECT_MIGRATION_SEAT_REPLAY_STORE", "seat replay store"
        )
        seat_issue_store = _authorized_cli_store(
            args.seat_issue_store, "SELFCONNECT_MIGRATION_SEAT_ISSUE_STORE", "seat issue store"
        )
        seat_receiver_trust_store = _authorized_cli_store(
            args.seat_receiver_trust_store,
            "SELFCONNECT_MIGRATION_SEAT_RECEIVER_TRUST_STORE",
            "seat receiver trust store",
        )
        seat_bundle = None
        if seat_bundle_path:
            seat_bundle = json.loads(Path(seat_bundle_path).read_text(encoding="utf-8"))
        result = verify_migration_manifest(
            args.manifest,
            expected_hwnd=args.expected_hwnd,
            trust_store=trust_store,
            replay_store=replay_store,
            consume=args.consume,
            seat_bundle=seat_bundle,
            seat_replay_store=seat_replay_store,
            seat_issue_store=seat_issue_store,
            seat_tab_snapshot_resolver=resolve_live_tab_snapshot if seat_bundle else None,
            seat_receiver_trust_store=seat_receiver_trust_store,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "REJECTED", "reason": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
