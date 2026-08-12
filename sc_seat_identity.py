"""Per-seat cryptographic identity; Windows Terminal handles are routing only."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ENROLLMENT_SCHEMA = "selfconnect-seat-enrollment-v1"
CHALLENGE_SCHEMA = "selfconnect-seat-challenge-v1"
DELIVERY_SCHEMA = "selfconnect-seat-delivery-v1"
PROOF_SCHEMA = "selfconnect-seat-proof-v1"
CHANNEL_SCHEMA = "selfconnect-seat-channel-v1"
RECEIVER_TRUST_SCHEMA = "selfconnect-seat-receiver-trust-v1"
MAX_TTL_SECONDS = 60.0
MAX_ENROLLMENT_TTL_SECONDS = 600.0
MAX_CLOCK_SKEW_SECONDS = 5.0
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def tab_snapshot_digest(snapshot: dict[str, Any]) -> str:
    stable = {key: value for key, value in snapshot.items() if key not in ("stage", "timestamp")}
    if stable.get("ok") is not True:
        raise ValueError("live tab snapshot is not verified")
    return _sha256(_canonical(stable))


def _record_issue(challenge: dict[str, Any], issue_store: str | Path) -> None:
    path = Path(issue_store)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS issued(challenge TEXT PRIMARY KEY, record_sha256 TEXT UNIQUE NOT NULL, issued_at REAL NOT NULL)")
        db.execute("INSERT INTO issued VALUES(?,?,?)", (challenge["challenge"], _sha256(_canonical(challenge)), challenge["issued_at"]))
        db.commit()
    finally:
        db.close()


def _require_issued(challenge: dict[str, Any], issue_store: str | Path) -> None:
    path = Path(issue_store)
    if not path.exists():
        raise ValueError("seat challenge was not durably issued")
    db = sqlite3.connect(path)
    try:
        row = db.execute("SELECT record_sha256 FROM issued WHERE challenge=?", (challenge.get("challenge"),)).fetchone()
    finally:
        db.close()
    if row is None or row[0] != _sha256(_canonical(challenge)):
        raise ValueError("seat challenge was not durably issued")


def _hex(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"invalid {name}")
    return value


def key_id(public_key_hex: str) -> str:
    raw = bytes.fromhex(public_key_hex)
    if len(raw) != 32:
        raise ValueError("seat public key must be a full 32-byte Ed25519 key")
    return _sha256(raw)


def enroll_receiver_key(identity: Any, path: str | Path) -> Path:
    """Explicitly pin a response-pipe receiver key; never auto-enroll."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    public_key_hex = str(identity.public_key_hex)
    receiver_id = key_id(public_key_hex)
    data = {"schema": RECEIVER_TRUST_SCHEMA, "receivers": []}
    if target.exists():
        data = json.loads(target.read_text(encoding="utf-8"))
        if data.get("schema") != RECEIVER_TRUST_SCHEMA or not isinstance(data.get("receivers"), list):
            raise ValueError("seat receiver trust store is malformed")
    matches = [item for item in data["receivers"] if item.get("key_id") == receiver_id]
    if matches and matches[0].get("public_key_hex") != public_key_hex:
        raise ValueError("seat receiver key-id collision")
    if not matches:
        data["receivers"].append({"key_id": receiver_id, "public_key_hex": public_key_hex})
    staged = target.with_suffix(target.suffix + ".tmp")
    staged.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(staged, target)
    return target.resolve()


def trusted_receiver_public_key(receiver_id: str, path: str | Path) -> str:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != RECEIVER_TRUST_SCHEMA or not isinstance(data.get("receivers"), list):
        raise ValueError("seat receiver trust store is malformed")
    for item in data["receivers"]:
        public_key_hex = item.get("public_key_hex")
        if item.get("key_id") == receiver_id and isinstance(public_key_hex, str) and key_id(public_key_hex) == receiver_id:
            return public_key_hex
    raise ValueError("seat response receiver is not independently trusted")


def _signed(body: dict[str, Any], identity: Any, field: str) -> dict[str, Any]:
    return {**body, field: base64.b64encode(identity.sign(_canonical(body))).decode()}


def _verify_signed(record: dict[str, Any], public_key_hex: str, field: str, label: str) -> dict[str, Any]:
    body = dict(record)
    signature = body.pop(field, None)
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(
            base64.b64decode(signature, validate=True), _canonical(body)
        )
    except Exception as exc:
        raise ValueError(f"{label} signature is invalid") from exc
    return body


def create_enrollment(*, seat_identity: Any, authority_identity: Any, birth_id: str,
                      generation: int, now: float | None = None,
                      ttl_seconds: float = 300.0) -> dict[str, Any]:
    if not _SAFE_ID.fullmatch(birth_id) or type(generation) is not int or generation <= 0:
        raise ValueError("invalid seat birth identity")
    if not 0 < ttl_seconds <= MAX_ENROLLMENT_TTL_SECONDS:
        raise ValueError("seat enrollment TTL is invalid")
    issued = time.time() if now is None else float(now)
    public_key = str(seat_identity.public_key_hex)
    body = {"schema": ENROLLMENT_SCHEMA, "enrollment_id": str(uuid.uuid4()),
            "seat_key_id": key_id(public_key), "seat_public_key_hex": public_key,
            "birth_id": birth_id, "generation": generation, "seat_epoch": secrets.token_hex(32),
            "issued_at": issued, "expires_at": issued + ttl_seconds,
            "authority_key_id": key_id(str(authority_identity.public_key_hex))}
    return _signed(body, authority_identity, "authority_signature_b64")


def verify_enrollment(enrollment: dict[str, Any], *, authority_public_key_hex: str,
                      revoked_key_ids: frozenset[str] = frozenset(),
                      now: float | None = None) -> dict[str, Any]:
    data = _verify_signed(enrollment, authority_public_key_hex, "authority_signature_b64", "seat enrollment")
    required = {"schema", "enrollment_id", "seat_key_id", "seat_public_key_hex", "birth_id",
                "generation", "seat_epoch", "issued_at", "expires_at", "authority_key_id"}
    if set(data) != required or data["schema"] != ENROLLMENT_SCHEMA:
        raise ValueError("seat enrollment fields are invalid")
    if data["seat_key_id"] != key_id(data["seat_public_key_hex"]):
        raise ValueError("seat enrollment key binding is invalid")
    if data["authority_key_id"] != key_id(authority_public_key_hex):
        raise ValueError("seat enrollment authority is not trusted")
    if data["seat_key_id"] in revoked_key_ids:
        raise ValueError("seat enrollment key is revoked")
    current = time.time() if now is None else float(now)
    issued, expires = float(data["issued_at"]), float(data["expires_at"])
    if not all(math.isfinite(v) for v in (current, issued, expires)) or expires <= issued:
        raise ValueError("seat enrollment time is invalid")
    if expires - issued > MAX_ENROLLMENT_TTL_SECONDS or issued > current + MAX_CLOCK_SKEW_SECONDS or current > expires:
        raise ValueError("seat enrollment is not currently valid")
    return data


def create_challenge(*, enrollment: dict[str, Any], operation_sha256: str,
                     tab_snapshot_sha256: str, response_address_sha256: str,
                     server_nonce: str, authority_identity: Any,
                     expected_peer_sid: str, expected_pipe_instance: str,
                     issue_store: str | Path, now: float | None = None,
                     ttl_seconds: float = 15.0) -> dict[str, Any]:
    if not 0 < ttl_seconds <= MAX_TTL_SECONDS:
        raise ValueError("seat challenge TTL is invalid")
    for value, name in ((operation_sha256, "operation"), (tab_snapshot_sha256, "tab snapshot"),
                        (response_address_sha256, "response address"), (server_nonce, "server nonce")):
        _hex(value, name)
    issued = time.time() if now is None else float(now)
    body = {"schema": CHALLENGE_SCHEMA, "challenge": secrets.token_hex(32),
            "operation_sha256": operation_sha256, "seat_key_id": enrollment["seat_key_id"],
            "birth_id": enrollment["birth_id"], "generation": enrollment["generation"],
            "seat_epoch": enrollment["seat_epoch"], "tab_snapshot_sha256": tab_snapshot_sha256,
            "response_address_sha256": response_address_sha256, "server_nonce": server_nonce,
            "expected_peer_sid": expected_peer_sid, "expected_pipe_instance": expected_pipe_instance,
            "issued_at": issued, "expires_at": issued + ttl_seconds,
            "authority_key_id": key_id(str(authority_identity.public_key_hex))}
    challenge = _signed(body, authority_identity, "authority_signature_b64")
    _record_issue(challenge, issue_store)
    return challenge


def deliver_challenge_postmessage(*, challenge: dict[str, Any], target_hwnd: int,
                                  authority_identity: Any, sender: Callable[[int, str], dict[str, Any]],
                                  tab_checkpoint: Callable[[str], dict[str, Any]],
                                  now: float | None = None) -> dict[str, Any]:
    """Send the authority-issued nonce through exact H and sign the observed delivery."""
    before = tab_checkpoint("before_seat_challenge")
    before_digest = tab_snapshot_digest(before)
    if before_digest != challenge["tab_snapshot_sha256"]:
        raise ValueError("live tab snapshot changed before challenge delivery")
    wire = "[SELFCONNECT_SEAT_CHALLENGE] " + base64.urlsafe_b64encode(_canonical(challenge)).decode()
    result = sender(target_hwnd, wire)
    after = tab_checkpoint("after_seat_challenge")
    if before.get("ok") is not True or after.get("ok") is not True:
        raise ValueError("exact tab challenge checkpoint failed")
    if tab_snapshot_digest(after) != before_digest:
        raise ValueError("live tab snapshot changed during challenge delivery")
    if result.get("ok") is not True or result.get("transport") != "postmessage_wm_char" or result.get("chars_accepted") != len(wire):
        raise ValueError("exact HWND PostMessage challenge delivery failed")
    body = {"schema": DELIVERY_SCHEMA, "challenge_sha256": _sha256(_canonical(challenge)),
            "target_hwnd": int(target_hwnd), "transport": "postmessage_wm_char",
            "tab_snapshot_sha256": challenge["tab_snapshot_sha256"],
            "delivered_at": time.time() if now is None else float(now)}
    return _signed(body, authority_identity, "authority_signature_b64")


def create_proof(*, seat_identity: Any, challenge: dict[str, Any], delivery: dict[str, Any],
                 authority_public_key_hex: str, issue_store: str | Path) -> dict[str, Any]:
    _require_issued(challenge, issue_store)
    challenge_body = _verify_signed(challenge, authority_public_key_hex, "authority_signature_b64", "seat challenge")
    delivery_body = _verify_signed(delivery, authority_public_key_hex, "authority_signature_b64", "seat delivery")
    if delivery_body.get("challenge_sha256") != _sha256(_canonical(challenge)) or delivery_body.get("transport") != "postmessage_wm_char":
        raise ValueError("seat challenge was not delivered through the authorized tab channel")
    if key_id(str(seat_identity.public_key_hex)) != challenge_body.get("seat_key_id"):
        raise ValueError("seat broker key does not match challenge")
    body = {"schema": PROOF_SCHEMA, "challenge_sha256": _sha256(_canonical(challenge)),
            "delivery_sha256": _sha256(_canonical(delivery)), "challenge": challenge_body["challenge"],
            "operation_sha256": challenge_body["operation_sha256"],
            "tab_snapshot_sha256": challenge_body["tab_snapshot_sha256"],
            "seat_key_id": challenge_body["seat_key_id"], "birth_id": challenge_body["birth_id"],
            "generation": challenge_body["generation"], "seat_epoch": challenge_body["seat_epoch"],
            "proof_nonce": secrets.token_hex(32)}
    return _signed(body, seat_identity, "signature_b64")


def secure_channel_evidence(challenge: dict[str, Any], *, receiver_identity: Any,
                            peer_sid: str, pipe_instance: str,
                            now: float | None = None) -> dict[str, Any]:
    body = {"schema": CHANNEL_SCHEMA, "transport": "private_named_pipe_v1",
            "response_address_sha256": challenge["response_address_sha256"],
            "server_nonce": challenge["server_nonce"], "peer_sid": peer_sid,
            "pipe_instance": pipe_instance, "observed_at": time.time() if now is None else float(now)}
    signed = _signed(body, receiver_identity, "receiver_signature_b64")
    return {**signed, "receiver_key_id": key_id(receiver_identity.public_key_hex)}


def verify_proof(proof: dict[str, Any], *, challenge: dict[str, Any], delivery: dict[str, Any],
                 enrollment: dict[str, Any], channel_evidence: dict[str, Any] | None,
                 authority_public_key_hex: str, receiver_public_key_hex: str,
                 expected_operation_sha256: str, expected_tab_snapshot_sha256: str,
                 expected_target_hwnd: int,
                 replay_store: str | Path, issue_store: str | Path,
                 revoked_key_ids: frozenset[str] = frozenset(),
                 now: float | None = None, consume: bool = True) -> dict[str, Any]:
    current = time.time() if now is None else float(now)
    _require_issued(challenge, issue_store)
    enrolled = verify_enrollment(enrollment, authority_public_key_hex=authority_public_key_hex,
                                 revoked_key_ids=revoked_key_ids, now=current)
    challenge_body = _verify_signed(challenge, authority_public_key_hex, "authority_signature_b64", "seat challenge")
    delivery_body = _verify_signed(delivery, authority_public_key_hex, "authority_signature_b64", "seat delivery")
    if challenge_body["authority_key_id"] != key_id(authority_public_key_hex):
        raise ValueError("seat challenge authority is not trusted")
    if challenge_body["operation_sha256"] != expected_operation_sha256 or challenge_body["tab_snapshot_sha256"] != expected_tab_snapshot_sha256:
        raise ValueError("seat challenge expected digest binding is invalid")
    if delivery_body.get("challenge_sha256") != _sha256(_canonical(challenge)) or delivery_body.get("transport") != "postmessage_wm_char" or delivery_body.get("tab_snapshot_sha256") != expected_tab_snapshot_sha256:
        raise ValueError("seat challenge exact-HWND delivery proof is invalid")
    if delivery_body.get("target_hwnd") != expected_target_hwnd:
        raise ValueError("seat challenge delivery targets a different HWND")
    issued, expires = float(challenge_body["issued_at"]), float(challenge_body["expires_at"])
    if expires <= issued or expires - issued > MAX_TTL_SECONDS or issued > current + MAX_CLOCK_SKEW_SECONDS or current > expires:
        raise ValueError("seat challenge is not currently valid")
    if channel_evidence is None:
        raise ValueError("secure response channel proof is required")
    channel = dict(channel_evidence)
    receiver_id = channel.pop("receiver_key_id", None)
    channel_body = _verify_signed(channel, receiver_public_key_hex, "receiver_signature_b64", "secure response channel")
    if receiver_id != key_id(receiver_public_key_hex) or channel_body.get("transport") != "private_named_pipe_v1" or channel_body.get("response_address_sha256") != challenge_body["response_address_sha256"] or channel_body.get("server_nonce") != challenge_body["server_nonce"]:
        raise ValueError("secure response channel proof is invalid")
    if channel_body.get("peer_sid") != challenge_body["expected_peer_sid"] or channel_body.get("pipe_instance") != challenge_body["expected_pipe_instance"]:
        raise ValueError("secure response channel identity is invalid")
    observed_at = float(channel_body.get("observed_at"))
    if not math.isfinite(observed_at) or abs(observed_at - current) > MAX_CLOCK_SKEW_SECONDS:
        raise ValueError("secure response channel evidence is stale")
    proof_body = _verify_signed(proof, enrolled["seat_public_key_hex"], "signature_b64", "seat proof")
    expected = {"challenge_sha256": _sha256(_canonical(challenge)), "delivery_sha256": _sha256(_canonical(delivery)),
                "challenge": challenge_body["challenge"], "operation_sha256": expected_operation_sha256,
                "tab_snapshot_sha256": expected_tab_snapshot_sha256, "seat_key_id": enrolled["seat_key_id"],
                "birth_id": enrolled["birth_id"], "generation": enrolled["generation"], "seat_epoch": enrolled["seat_epoch"]}
    if proof_body.get("schema") != PROOF_SCHEMA or any(proof_body.get(k) != v for k, v in expected.items()):
        raise ValueError("seat proof operation binding is invalid")
    proof_nonce = _hex(proof_body.get("proof_nonce"), "proof nonce")
    if consume:
        path = Path(replay_store)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS consumed(challenge TEXT PRIMARY KEY, proof_nonce TEXT UNIQUE NOT NULL, consumed_at REAL NOT NULL)")
            try:
                db.execute("INSERT INTO consumed VALUES(?,?,?)", (challenge_body["challenge"], proof_nonce, current))
                db.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("seat challenge or proof has already been consumed") from exc
        finally:
            db.close()
    return {"ok": True, "status": "ACCEPTED" if consume else "VERIFIED",
            "seat_key_id": enrolled["seat_key_id"], "challenge": challenge_body["challenge"]}
