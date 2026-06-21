"""Fabric V2 frame, mailbox, and named-pipe primitives.

This module is the first production-shaped Fabric V2 slice. It keeps the
cryptographic/session layer independent from the transport so the benchmark can
measure current transport and Fabric V2 with the same harness.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_MAX_FRAME_BYTES = 256 * 1024
DEFAULT_DEADLINE_MS = 30_000


class FabricError(Exception):
    """Base class for Fabric V2 errors."""


class FrameVerificationError(FabricError):
    """Raised when a frame fails MAC, schema, or target verification."""


class ReplayRejectedError(FrameVerificationError):
    """Raised when an already accepted sender/receiver/sequence tuple repeats."""


class DeadlineExpiredError(FrameVerificationError):
    """Raised when a frame arrives after its deadline."""


class MailboxFullError(FabricError):
    """Raised when a bounded mailbox refuses more traffic."""


@dataclass(frozen=True)
class VerifiedFrame:
    sender: str
    receiver: str
    sequence: int
    payload: bytes
    payload_hash: str
    message_type: str
    created_at_ns: int
    deadline_at_ns: int
    frame_hash: str


@dataclass
class FabricSession:
    """Shared session state for sign-once/MAC-many Fabric V2 frames."""

    session_id: str
    session_key: bytes
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES
    _next_sequences: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _accepted_sequences: set[tuple[str, str, int]] = field(default_factory=set, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    @classmethod
    def from_secret(
        cls,
        secret: str | bytes,
        *,
        session_id: str | None = None,
        context: str = "selfconnect-fabric-v2",
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
    ) -> FabricSession:
        if isinstance(secret, str):
            secret_bytes = secret.encode("utf-8")
        else:
            secret_bytes = bytes(secret)
        if not secret_bytes:
            raise ValueError("secret must not be empty")
        key = hmac.new(
            context.encode("utf-8"),
            secret_bytes,
            hashlib.sha256,
        ).digest()
        return cls(
            session_id=session_id or f"sfv2-{uuid.uuid4().hex[:16]}",
            session_key=key,
            max_frame_bytes=max_frame_bytes,
        )

    @classmethod
    def ephemeral(cls, *, session_id: str | None = None) -> FabricSession:
        return cls.from_secret(os.urandom(32), session_id=session_id)

    def next_sequence(self, sender: str) -> int:
        with self._lock:
            current = self._next_sequences.get(sender, 0) + 1
            self._next_sequences[sender] = current
            return current

    def seal(
        self,
        *,
        sender: str,
        receiver: str,
        payload: str | bytes,
        message_type: str = "data",
        deadline_ms: int = DEFAULT_DEADLINE_MS,
        sequence: int | None = None,
        created_at_ns: int | None = None,
    ) -> bytes:
        if not sender or not receiver:
            raise ValueError("sender and receiver are required")
        payload_bytes = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        now_ns = time.time_ns() if created_at_ns is None else int(created_at_ns)
        seq = self.next_sequence(sender) if sequence is None else int(sequence)
        record: dict[str, Any] = {
            "version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "message_type": message_type,
            "sender": sender,
            "receiver": receiver,
            "sequence": seq,
            "created_at_ns": now_ns,
            "deadline_at_ns": now_ns + int(deadline_ms) * 1_000_000,
            "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
            "payload_hash": hashlib.sha256(payload_bytes).hexdigest(),
        }
        record["mac"] = self._mac(record)
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(encoded) > self.max_frame_bytes:
            raise ValueError("frame exceeds max_frame_bytes")
        return encoded

    def open(
        self,
        encoded: bytes,
        *,
        expected_receiver: str = "",
        now_ns: int | None = None,
        allow_replay: bool = False,
    ) -> VerifiedFrame:
        if len(encoded) > self.max_frame_bytes:
            raise FrameVerificationError("frame exceeds max_frame_bytes")
        try:
            record = json.loads(encoded.decode("utf-8"))
        except Exception as exc:
            raise FrameVerificationError(f"invalid frame JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise FrameVerificationError("frame must decode to an object")
        self._validate_schema(record, expected_receiver=expected_receiver)
        expected_mac = self._mac(record)
        if not hmac.compare_digest(str(record.get("mac", "")), expected_mac):
            raise FrameVerificationError("frame MAC mismatch")

        current_ns = time.time_ns() if now_ns is None else int(now_ns)
        if current_ns > int(record["deadline_at_ns"]):
            raise DeadlineExpiredError("frame deadline expired")

        payload = base64.b64decode(str(record["payload_b64"]).encode("ascii"), validate=True)
        payload_hash = hashlib.sha256(payload).hexdigest()
        if payload_hash != record["payload_hash"]:
            raise FrameVerificationError("payload hash mismatch")

        replay_key = (str(record["sender"]), str(record["receiver"]), int(record["sequence"]))
        with self._lock:
            if replay_key in self._accepted_sequences and not allow_replay:
                raise ReplayRejectedError("frame sequence replay rejected")
            self._accepted_sequences.add(replay_key)

        frame_hash = hashlib.sha256(encoded).hexdigest()
        return VerifiedFrame(
            sender=str(record["sender"]),
            receiver=str(record["receiver"]),
            sequence=int(record["sequence"]),
            payload=payload,
            payload_hash=payload_hash,
            message_type=str(record["message_type"]),
            created_at_ns=int(record["created_at_ns"]),
            deadline_at_ns=int(record["deadline_at_ns"]),
            frame_hash=frame_hash,
        )

    def _mac(self, record: dict[str, Any]) -> str:
        payload = dict(record)
        payload.pop("mac", None)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return hmac.new(self.session_key, canonical, hashlib.sha256).hexdigest()

    def _validate_schema(self, record: dict[str, Any], *, expected_receiver: str = "") -> None:
        required = {
            "version",
            "session_id",
            "message_type",
            "sender",
            "receiver",
            "sequence",
            "created_at_ns",
            "deadline_at_ns",
            "payload_b64",
            "payload_hash",
            "mac",
        }
        missing = sorted(required - set(record))
        if missing:
            raise FrameVerificationError(f"frame missing field(s): {', '.join(missing)}")
        if int(record["version"]) != SCHEMA_VERSION:
            raise FrameVerificationError("unsupported frame version")
        if str(record["session_id"]) != self.session_id:
            raise FrameVerificationError("session_id mismatch")
        if expected_receiver and str(record["receiver"]) != expected_receiver:
            raise FrameVerificationError("receiver mismatch")
        if int(record["sequence"]) < 1:
            raise FrameVerificationError("sequence must be positive")


class BoundedMailbox:
    """Thread-safe bounded mailbox with explicit backpressure."""

    def __init__(self, name: str, *, max_depth: int = 100) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        self.name = name
        self.max_depth = int(max_depth)
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=self.max_depth)

    def put(self, frame: bytes, *, timeout_ms: int = 0) -> None:
        try:
            self._queue.put(frame, block=timeout_ms > 0, timeout=max(0, timeout_ms) / 1000)
        except queue.Full as exc:
            raise MailboxFullError(f"mailbox {self.name} is full") from exc

    def get(self, *, timeout_ms: int | None = None) -> bytes:
        try:
            if timeout_ms is None:
                return self._queue.get(block=False)
            return self._queue.get(block=True, timeout=max(0, timeout_ms) / 1000)
        except queue.Empty as exc:
            raise TimeoutError(f"mailbox {self.name} has no frame") from exc

    def depth(self) -> int:
        return self._queue.qsize()


def pipe_address(name: str | None = None) -> str:
    if sys.platform != "win32":
        raise RuntimeError("Windows named-pipe transport is only available on Windows")
    value = name or f"SelfConnectFabricV2_{uuid.uuid4().hex}"
    if value.startswith("\\\\.\\pipe\\"):
        return value
    clean = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
    return f"\\\\.\\pipe\\{clean}"


def named_pipe_roundtrip(
    *,
    session: FabricSession,
    sender: str,
    receiver: str,
    payload: str | bytes,
    address: str | None = None,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Run one real Windows named-pipe request/ACK exchange."""

    target = pipe_address(address)
    ready = threading.Event()
    errors: list[str] = []
    server_result: dict[str, Any] = {}

    def server() -> None:
        listener = None
        conn = None
        try:
            listener = Listener(target, family="AF_PIPE")
            ready.set()
            conn = listener.accept()
            raw = conn.recv_bytes()
            verified = session.open(raw, expected_receiver=receiver)
            ack_payload = f"ACK:{verified.sender}:{verified.sequence}".encode()
            ack = session.seal(
                sender=receiver,
                receiver=sender,
                payload=ack_payload,
                message_type="ack",
            )
            conn.send_bytes(ack)
            server_result.update({
                "verified_sender": verified.sender,
                "verified_receiver": verified.receiver,
                "verified_sequence": verified.sequence,
                "payload_hash": verified.payload_hash,
            })
        except Exception as exc:  # pragma: no cover - surfaced through artifact
            errors.append(str(exc))
            ready.set()
        finally:
            if conn is not None:
                conn.close()
            if listener is not None:
                listener.close()

    thread = threading.Thread(target=server, name="selfconnect-fabric-v2-pipe", daemon=True)
    start = time.perf_counter()
    thread.start()
    if not ready.wait(timeout_s):
        raise TimeoutError("named-pipe server did not become ready")
    if errors:
        raise FabricError(errors[0])

    request = session.seal(sender=sender, receiver=receiver, payload=payload)
    conn = None
    try:
        conn = Client(target, family="AF_PIPE")
        conn.send_bytes(request)
        response = conn.recv_bytes()
    finally:
        if conn is not None:
            conn.close()
    thread.join(timeout_s)
    if thread.is_alive():
        raise TimeoutError("named-pipe server did not finish")
    if errors:
        raise FabricError(errors[0])
    ack = session.open(response, expected_receiver=sender)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "ok": True,
        "transport": "windows_named_pipe",
        "address_hash": hashlib.sha256(target.encode("utf-8")).hexdigest(),
        "sender": sender,
        "receiver": receiver,
        "request_sequence": server_result.get("verified_sequence"),
        "ack_sequence": ack.sequence,
        "ack_payload": ack.payload.decode("utf-8", errors="replace"),
        "payload_hash": server_result.get("payload_hash"),
        "elapsed_ms": round(elapsed_ms, 3),
        "raw_text_included": False,
    }


def selftest(*, output_dir: str | Path = "experiments/fabric_v2/results") -> dict[str, Any]:
    session = FabricSession.ephemeral(session_id=f"sfv2-selftest-{uuid.uuid4().hex[:8]}")
    frame = session.seal(sender="selftest-a", receiver="selftest-b", payload="SC_FABRIC_V2_SELFTEST")
    verified = session.open(frame, expected_receiver="selftest-b")
    replay_rejected = False
    try:
        session.open(frame, expected_receiver="selftest-b")
    except ReplayRejectedError:
        replay_rejected = True

    pipe_result: dict[str, Any]
    if sys.platform == "win32":
        pipe_session = FabricSession.ephemeral(session_id=f"sfv2-pipe-{uuid.uuid4().hex[:8]}")
        pipe_result = named_pipe_roundtrip(
            session=pipe_session,
            sender="selftest-a",
            receiver="selftest-b",
            payload="SC_FABRIC_V2_PIPE",
        )
    else:
        pipe_result = {"ok": False, "status": "na", "reason": "not_windows"}

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "ok": verified.payload == b"SC_FABRIC_V2_SELFTEST" and replay_rejected and pipe_result.get("ok", False),
        "suite": "fabric_v2_selftest",
        "session_id_hash": hashlib.sha256(session.session_id.encode("utf-8")).hexdigest(),
        "frame_payload_hash": verified.payload_hash,
        "replay_rejected": replay_rejected,
        "pipe": pipe_result,
        "raw_text_included": False,
        "created_at": time.time(),
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"fabric_v2_selftest_{time.strftime('%Y%m%d_%H%M%S')}_redacted.json"
    artifact["artifact_path"] = str(path)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return artifact


def _print_json(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok", False) else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="selfconnect-fabric", description="SelfConnect Fabric V2 primitives")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("selftest")
    p.add_argument("--output-dir", default="experiments/fabric_v2/results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "selftest":
        return _print_json(selftest(output_dir=args.output_dir))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
