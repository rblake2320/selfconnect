"""Fabric V2 local host service proof.

This host keeps the Fabric V2 frame semantics in the ACK path and uses a real
Windows IOCP queue for completion dispatch. The pipe adapter uses Python's
AF_PIPE named-pipe support; the next hardening step is replacing that adapter
with direct overlapped pipe reads/writes associated with the completion port.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any

import sc_fabric_v2

try:  # pragma: no cover - import shape is platform dependent
    import pywintypes
    import win32file
    import win32pipe
    import winerror
except Exception:  # pragma: no cover
    pywintypes = None
    win32file = None
    win32pipe = None
    winerror = None


SCHEMA_VERSION = 1
STOP_KEY = 0


class FabricHostError(Exception):
    """Base class for Fabric V2 host errors."""


class IocpUnavailableError(FabricHostError):
    """Raised when Windows IOCP APIs are unavailable."""


@dataclass
class CompletionRecord:
    completion_id: int
    verified: sc_fabric_v2.VerifiedFrame
    accepted_at_ns: int
    ack_frame: bytes | None = None
    error: str = ""
    done: threading.Event = field(default_factory=threading.Event)


class IocpCompletionQueue:
    """Small wrapper over Win32 IOCP post/get used by the host worker."""

    def __init__(self) -> None:
        if win32file is None or sys.platform != "win32":
            raise IocpUnavailableError("Windows IOCP APIs are unavailable")
        self.handle = win32file.CreateIoCompletionPort(win32file.INVALID_HANDLE_VALUE, None, 0, 0)

    def post(self, *, completion_id: int, byte_count: int = 0) -> None:
        win32file.PostQueuedCompletionStatus(self.handle, int(byte_count), int(completion_id), None)

    def get(self, timeout_ms: int) -> tuple[int, int]:
        _rc, byte_count, completion_id, _overlapped = win32file.GetQueuedCompletionStatus(
            self.handle,
            int(timeout_ms),
        )
        return int(completion_id), int(byte_count)

    def close(self) -> None:
        self.handle.Close()


def _require_overlapped_win32() -> None:
    if (
        sys.platform != "win32"
        or pywintypes is None
        or win32file is None
        or win32pipe is None
        or winerror is None
    ):
        raise IocpUnavailableError("Windows overlapped named-pipe APIs are unavailable")


def _wait_completion(port: Any, *, expected_key: int, timeout_ms: int) -> int:
    try:
        _rc, byte_count, completion_key, _overlapped = win32file.GetQueuedCompletionStatus(
            port,
            int(timeout_ms),
        )
    except pywintypes.error as exc:
        raise TimeoutError(f"IOCP completion wait failed: {exc}") from exc
    if int(completion_key) != int(expected_key):
        raise FabricHostError(f"unexpected IOCP completion key: {completion_key}")
    return int(byte_count)


def _overlapped_read_exact(handle: Any, port: Any, *, size: int, key: int, timeout_ms: int) -> bytes:
    buffer = win32file.AllocateReadBuffer(int(size))
    overlapped = pywintypes.OVERLAPPED()
    try:
        win32file.ReadFile(handle, buffer, overlapped)
    except pywintypes.error as exc:
        if exc.winerror != winerror.ERROR_IO_PENDING:
            raise
    byte_count = _wait_completion(port, expected_key=key, timeout_ms=timeout_ms)
    data = bytes(buffer)[:byte_count]
    if len(data) != int(size):
        raise FabricHostError(f"short overlapped read: expected {size}, got {len(data)}")
    return data


def _overlapped_write_all(handle: Any, port: Any, *, payload: bytes, key: int, timeout_ms: int) -> int:
    overlapped = pywintypes.OVERLAPPED()
    try:
        win32file.WriteFile(handle, payload, overlapped)
    except pywintypes.error as exc:
        if exc.winerror != winerror.ERROR_IO_PENDING:
            raise
    byte_count = _wait_completion(port, expected_key=key, timeout_ms=timeout_ms)
    if byte_count != len(payload):
        raise FabricHostError(f"short overlapped write: expected {len(payload)}, wrote {byte_count}")
    return byte_count


def _frame_packet(frame: bytes) -> bytes:
    return len(frame).to_bytes(4, "little") + frame


def _read_packet_overlapped(handle: Any, port: Any, *, key: int, timeout_ms: int) -> bytes:
    header = _overlapped_read_exact(handle, port, size=4, key=key, timeout_ms=timeout_ms)
    size = int.from_bytes(header, "little")
    if size <= 0 or size > sc_fabric_v2.DEFAULT_MAX_FRAME_BYTES:
        raise FabricHostError(f"invalid Fabric frame size: {size}")
    return _overlapped_read_exact(handle, port, size=size, key=key, timeout_ms=timeout_ms)


def _write_packet_overlapped(handle: Any, port: Any, *, frame: bytes, key: int, timeout_ms: int) -> int:
    return _overlapped_write_all(handle, port, payload=_frame_packet(frame), key=key, timeout_ms=timeout_ms)


class FabricHostService:
    """Local Fabric V2 host with named-pipe ingress and IOCP dispatch."""

    def __init__(
        self,
        *,
        session: sc_fabric_v2.FabricSession,
        address: str | None = None,
        mailbox_depth: int = 100,
        request_timeout_s: float = 5.0,
    ) -> None:
        self.session = session
        self.address = sc_fabric_v2.pipe_address(address)
        self.mailbox_depth = int(mailbox_depth)
        self.request_timeout_s = float(request_timeout_s)
        self._iocp = IocpCompletionQueue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._listener_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._listener: Listener | None = None
        self._lock = threading.RLock()
        self._completion_seq = 0
        self._pending: dict[int, CompletionRecord] = {}
        self._mailboxes: dict[str, sc_fabric_v2.BoundedMailbox] = {}
        self._errors: list[str] = []
        self._completion_count = 0
        self._rejected_count = 0

    def start(self) -> None:
        self._worker_thread = threading.Thread(target=self._completion_worker, name="sc-fabric-iocp", daemon=True)
        self._listener_thread = threading.Thread(target=self._listen, name="sc-fabric-pipe", daemon=True)
        self._worker_thread.start()
        self._listener_thread.start()
        if not self._ready.wait(self.request_timeout_s):
            raise TimeoutError("Fabric host did not become ready")
        if self._errors:
            raise FabricHostError(self._errors[-1])

    def stop(self) -> None:
        self._stop.set()
        try:
            self._iocp.post(completion_id=STOP_KEY)
        except Exception:
            pass
        try:
            conn = Client(self.address, family="AF_PIPE")
            conn.close()
        except Exception:
            pass
        if self._listener_thread:
            self._listener_thread.join(self.request_timeout_s)
        if self._worker_thread:
            self._worker_thread.join(self.request_timeout_s)
        try:
            if self._listener is not None:
                self._listener.close()
        finally:
            self._iocp.close()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "address_hash": hashlib.sha256(self.address.encode("utf-8")).hexdigest(),
                "completion_count": self._completion_count,
                "rejected_count": self._rejected_count,
                "pending_count": len(self._pending),
                "mailboxes": {
                    name: {"depth": mailbox.depth(), "max_depth": mailbox.max_depth}
                    for name, mailbox in self._mailboxes.items()
                },
                "errors": list(self._errors),
            }

    def _next_completion_id(self) -> int:
        with self._lock:
            self._completion_seq += 1
            return self._completion_seq

    def _mailbox(self, receiver: str) -> sc_fabric_v2.BoundedMailbox:
        with self._lock:
            mailbox = self._mailboxes.get(receiver)
            if mailbox is None:
                mailbox = sc_fabric_v2.BoundedMailbox(receiver, max_depth=self.mailbox_depth)
                self._mailboxes[receiver] = mailbox
            return mailbox

    def _listen(self) -> None:
        try:
            self._listener = Listener(self.address, family="AF_PIPE")
            self._ready.set()
            while not self._stop.is_set():
                try:
                    conn = self._listener.accept()
                except Exception as exc:
                    if not self._stop.is_set():
                        self._errors.append(str(exc))
                    break
                threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()
        except Exception as exc:
            self._errors.append(str(exc))
            self._ready.set()

    def _handle_client(self, conn: Any) -> None:
        try:
            raw = conn.recv_bytes()
            verified = self.session.open(raw)
            completion_id = self._next_completion_id()
            record = CompletionRecord(
                completion_id=completion_id,
                verified=verified,
                accepted_at_ns=time.time_ns(),
            )
            with self._lock:
                self._pending[completion_id] = record
            self._iocp.post(completion_id=completion_id, byte_count=len(verified.payload))
            if not record.done.wait(self.request_timeout_s):
                raise TimeoutError("Fabric host completion timed out")
            if record.error:
                raise FabricHostError(record.error)
            conn.send_bytes(record.ack_frame or b"")
        except Exception as exc:
            with self._lock:
                self._rejected_count += 1
            try:
                conn.send_bytes(json.dumps({
                    "ok": False,
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                }, sort_keys=True).encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()

    def _completion_worker(self) -> None:
        while not self._stop.is_set():
            try:
                completion_id, _byte_count = self._iocp.get(500)
            except Exception:
                continue
            if completion_id == STOP_KEY:
                break
            with self._lock:
                record = self._pending.pop(completion_id, None)
            if record is None:
                continue
            try:
                mailbox = self._mailbox(record.verified.receiver)
                mailbox.put(record.verified.payload, timeout_ms=1)
                record.ack_frame = self.session.seal(
                    sender=record.verified.receiver,
                    receiver=record.verified.sender,
                    payload=f"ACK:{record.verified.sender}:{record.verified.sequence}",
                    message_type="ack",
                )
                with self._lock:
                    self._completion_count += 1
            except Exception as exc:
                record.error = str(exc)
            finally:
                record.done.set()


def host_roundtrip(
    *,
    session: sc_fabric_v2.FabricSession,
    address: str,
    sender: str,
    receiver: str,
    payload: str | bytes,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    frame = session.seal(sender=sender, receiver=receiver, payload=payload)
    conn = Client(address, family="AF_PIPE")
    start = time.perf_counter()
    try:
        conn.send_bytes(frame)
        response = conn.recv_bytes()
    finally:
        conn.close()
    elapsed_ms = (time.perf_counter() - start) * 1000
    try:
        ack = session.open(response, expected_receiver=sender)
    except Exception as exc:
        return {
            "ok": False,
            "error": exc.__class__.__name__,
            "message": str(exc),
            "elapsed_ms": round(elapsed_ms, 3),
        }
    return {
        "ok": True,
        "ack_payload": ack.payload.decode("utf-8", errors="replace"),
        "ack_sequence": ack.sequence,
        "elapsed_ms": round(elapsed_ms, 3),
        "timeout_s": timeout_s,
    }


def overlapped_named_pipe_exchange(
    *,
    session: sc_fabric_v2.FabricSession,
    request_frame: bytes,
    address: str | None = None,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Run one direct overlapped named-pipe read/write exchange.

    Both the server read path and server write path use a pipe handle created
    with FILE_FLAG_OVERLAPPED and associated with an IO completion port.
    """

    _require_overlapped_win32()
    pipe_address = sc_fabric_v2.pipe_address(address or f"SelfConnectFabricOverlapped_{uuid.uuid4().hex}")
    timeout_ms = int(timeout_s * 1000)
    server_key = 7001
    server_port = win32file.CreateIoCompletionPort(win32file.INVALID_HANDLE_VALUE, None, 0, 0)
    pipe = win32pipe.CreateNamedPipe(
        pipe_address,
        win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED,
        win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
        1,
        sc_fabric_v2.DEFAULT_MAX_FRAME_BYTES,
        sc_fabric_v2.DEFAULT_MAX_FRAME_BYTES,
        0,
        None,
    )
    win32file.CreateIoCompletionPort(pipe, server_port, server_key, 0)

    ready = threading.Event()
    server_done = threading.Event()
    server_result: dict[str, Any] = {}
    client_result: dict[str, Any] = {}

    def server() -> None:
        try:
            ready.set()
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
            except pywintypes.error as exc:
                if exc.winerror != winerror.ERROR_PIPE_CONNECTED:
                    raise
            raw_request = _read_packet_overlapped(
                pipe,
                server_port,
                key=server_key,
                timeout_ms=timeout_ms,
            )
            read_completed_at = time.perf_counter()
            try:
                verified = session.open(raw_request)
                mailbox = sc_fabric_v2.BoundedMailbox(verified.receiver, max_depth=10)
                mailbox.put(verified.payload, timeout_ms=1)
                response = session.seal(
                    sender=verified.receiver,
                    receiver=verified.sender,
                    payload=f"ACK:{verified.sender}:{verified.sequence}",
                    message_type="ack",
                )
                server_result.update({
                    "ok": True,
                    "verified_sender": verified.sender,
                    "verified_receiver": verified.receiver,
                    "verified_sequence": verified.sequence,
                    "payload_hash": verified.payload_hash,
                    "mailbox_depth": mailbox.depth(),
                })
            except Exception as exc:
                response = json.dumps({
                    "ok": False,
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                }, sort_keys=True).encode("utf-8")
                server_result.update({
                    "ok": False,
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                })
            _write_packet_overlapped(
                pipe,
                server_port,
                frame=response,
                key=server_key,
                timeout_ms=timeout_ms,
            )
            win32file.FlushFileBuffers(pipe)
            server_result["overlapped_read_write"] = True
            server_result["read_completed_ms"] = round((read_completed_at - start) * 1000, 3)
        except Exception as exc:  # pragma: no cover - surfaced through result
            server_result.update({"ok": False, "error": exc.__class__.__name__, "message": str(exc)})
        finally:
            try:
                win32pipe.DisconnectNamedPipe(pipe)
            except Exception:
                pass
            server_done.set()

    start = time.perf_counter()
    thread = threading.Thread(target=server, name="sc-fabric-overlapped-pipe", daemon=True)
    thread.start()
    if not ready.wait(timeout_s):
        raise TimeoutError("overlapped pipe server did not become ready")

    client_key = 8001
    client_port = win32file.CreateIoCompletionPort(win32file.INVALID_HANDLE_VALUE, None, 0, 0)
    client = win32file.CreateFile(
        pipe_address,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0,
        None,
        win32file.OPEN_EXISTING,
        win32file.FILE_FLAG_OVERLAPPED,
        None,
    )
    win32file.CreateIoCompletionPort(client, client_port, client_key, 0)
    try:
        _overlapped_write_all(
            client,
            client_port,
            payload=_frame_packet(request_frame),
            key=client_key,
            timeout_ms=timeout_ms,
        )
        response = _read_packet_overlapped(
            client,
            client_port,
            key=client_key,
            timeout_ms=timeout_ms,
        )
        try:
            ack = session.open(response, expected_receiver=json.loads(request_frame.decode("utf-8"))["sender"])
            client_result.update({
                "ok": True,
                "ack_payload": ack.payload.decode("utf-8", errors="replace"),
                "ack_sequence": ack.sequence,
            })
        except Exception:
            try:
                client_result.update(json.loads(response.decode("utf-8")))
            except Exception as exc:
                client_result.update({
                    "ok": False,
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                })
    finally:
        client.Close()
        client_port.Close()
    if not server_done.wait(timeout_s):
        raise TimeoutError("overlapped pipe server did not finish")
    pipe.Close()
    server_port.Close()
    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "ok": bool(server_result.get("ok")) and bool(client_result.get("ok")),
        "transport": "windows_named_pipe_overlapped",
        "completion_dispatch": "win32_iocp_overlapped_read_write",
        "overlapped_pipe_io": True,
        "server_overlapped_read": True,
        "server_overlapped_write": True,
        "client_overlapped_io": True,
        "address_hash": hashlib.sha256(pipe_address.encode("utf-8")).hexdigest(),
        "server": server_result,
        "client": client_result,
        "elapsed_ms": round(elapsed_ms, 3),
        "raw_text_included": False,
    }


def overlapped_selftest(*, output_dir: str | Path = "experiments/fabric_v2/results") -> dict[str, Any]:
    session = sc_fabric_v2.FabricSession.ephemeral(session_id=f"sfv2-overlapped-{uuid.uuid4().hex[:8]}")
    first_frame = session.seal(
        sender="overlapped-a",
        receiver="overlapped-b",
        payload="SC_FABRIC_OVERLAPPED_SELFTEST",
    )
    first = overlapped_named_pipe_exchange(session=session, request_frame=first_frame)
    replay_frame = session.seal(
        sender="overlapped-a",
        receiver="overlapped-b",
        payload="SC_FABRIC_OVERLAPPED_REPLAY",
        sequence=99,
    )
    replay_first = overlapped_named_pipe_exchange(session=session, request_frame=replay_frame)
    replay_second = overlapped_named_pipe_exchange(session=session, request_frame=replay_frame)
    replay_rejected = replay_second.get("client", {}).get("error") == "ReplayRejectedError"
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "suite": "fabric_v2_overlapped_pipe_selftest",
        "ok": bool(first.get("ok")) and bool(replay_first.get("ok")) and replay_rejected,
        "host_transport": "windows_named_pipe_overlapped",
        "completion_dispatch": "win32_iocp_overlapped_read_write",
        "overlapped_pipe_io": True,
        "server_overlapped_read": True,
        "server_overlapped_write": True,
        "client_overlapped_io": True,
        "first_roundtrip": first,
        "replay_first_ok": bool(replay_first.get("ok")),
        "replay_rejected": replay_rejected,
        "replay_rejection_error": replay_second.get("client", {}).get("error", ""),
        "raw_text_included": False,
        "created_at": time.time(),
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"fabric_v2_overlapped_pipe_selftest_{time.strftime('%Y%m%d_%H%M%S')}_redacted.json"
    artifact["artifact_path"] = str(path)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return artifact


def selftest(*, output_dir: str | Path = "experiments/fabric_v2/results") -> dict[str, Any]:
    session = sc_fabric_v2.FabricSession.ephemeral(session_id=f"sfv2-host-{uuid.uuid4().hex[:8]}")
    host = FabricHostService(
        session=session,
        address=f"SelfConnectFabricHost_{uuid.uuid4().hex}",
        mailbox_depth=10,
    )
    start = time.perf_counter()
    replay_rejected = False
    try:
        host.start()
        first = host_roundtrip(
            session=session,
            address=host.address,
            sender="host-selftest-a",
            receiver="host-selftest-b",
            payload="SC_FABRIC_HOST_SELFTEST",
        )
        replay = session.seal(
            sender="host-selftest-a",
            receiver="host-selftest-b",
            payload="SC_FABRIC_HOST_REPLAY",
            sequence=99,
        )
        conn = Client(host.address, family="AF_PIPE")
        try:
            conn.send_bytes(replay)
            _ = conn.recv_bytes()
        finally:
            conn.close()
        conn = Client(host.address, family="AF_PIPE")
        try:
            conn.send_bytes(replay)
            rejected_response = conn.recv_bytes()
            replay_rejected = b"ReplayRejectedError" in rejected_response
        finally:
            conn.close()
        stats = host.stats()
    finally:
        host.stop()
    elapsed_ms = (time.perf_counter() - start) * 1000
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "suite": "fabric_v2_host_selftest",
        "ok": bool(first.get("ok")) and replay_rejected and stats["completion_count"] >= 2,
        "host_transport": "windows_named_pipe_af_pipe",
        "completion_dispatch": "win32_iocp_post_get",
        "overlapped_pipe_io": False,
        "boundary": "IOCP dispatch is in the ACK path; direct overlapped named-pipe IO remains next hardening step.",
        "first_roundtrip": first,
        "replay_rejected": replay_rejected,
        "stats": stats,
        "elapsed_ms": round(elapsed_ms, 3),
        "raw_text_included": False,
        "created_at": time.time(),
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"fabric_v2_host_selftest_{time.strftime('%Y%m%d_%H%M%S')}_redacted.json"
    artifact["artifact_path"] = str(path)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return artifact


def _print_json(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok", False) else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="selfconnect-fabric-host", description="SelfConnect Fabric V2 host service proof")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("selftest")
    p.add_argument("--output-dir", default="experiments/fabric_v2/results")
    p = sub.add_parser("overlapped-selftest")
    p.add_argument("--output-dir", default="experiments/fabric_v2/results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "selftest":
        return _print_json(selftest(output_dir=args.output_dir))
    if args.command == "overlapped-selftest":
        return _print_json(overlapped_selftest(output_dir=args.output_dir))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
