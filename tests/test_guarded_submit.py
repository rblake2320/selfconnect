from __future__ import annotations

import hashlib
import hmac
import contextlib
import inspect
import json
import math
import os
import struct
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import sc_guarded_submit as guarded
import sc_mesh_registry


KEY_ID = "current-2026-07"
OLD_KEY_ID = "previous-2026-06"
KEY = b"k" * 32
OLD_KEY = b"o" * 32
KEYRING = guarded.AckKeyRing({KEY_ID: KEY, OLD_KEY_ID: OLD_KEY})
TEXT = "hello snowman \u2603 rocket \U0001f680"
SENDER = "controller-a"
RECEIVER = "peer-b"
MESSAGE_ID = "11" * 16
CHALLENGE = "22" * 32
ACK_NONCE = "33" * 32
ATTEMPT_NONCE = "55" * 32
TARGET = guarded.TargetIdentity(
    hwnd=101,
    pid=202,
    exe_name="receiver.exe",
    class_name="ConsoleWindowClass",
    title="SC_GUARDED_EXACT_TITLE",
    exe_path=r"C:\receiver\receiver.exe",
    process_start_time_ns=123_456_789,
)


def _window(identity: guarded.TargetIdentity = TARGET) -> SimpleNamespace:
    return SimpleNamespace(**identity.__dict__)


def _ack(request: guarded.PeerAckRequest, *, decision="accepted", **overrides) -> bytes:
    values = {
        "keyring": KEYRING,
        "key_id": request.response_key_id,
        "message_id": request.message_id,
        "challenge": request.challenge,
        "attempt_nonce": request.attempt_nonce,
        "ack_nonce": ACK_NONCE,
        "input_sha256": request.input_sha256,
        "sender": request.receiver,
        "receiver": request.sender,
        "decision": decision,
    }
    values.update(overrides)
    return guarded.sign_peer_ack(**values)


def _submit(tmp_path: Path, **overrides):
    snapshots = overrides.pop("snapshots", None)
    calls = 0

    def snapshot(_hwnd):
        nonlocal calls
        value = snapshots[min(calls, len(snapshots) - 1)] if snapshots else TARGET
        calls += 1
        if isinstance(value, Exception):
            raise value
        return (_window(value), value) if value else (None, None)

    def receive(request, _timeout):
        value = overrides.pop("ack", None)
        if isinstance(value, Exception):
            raise value
        return value if value is not None else _ack(request)

    finalizer = guarded.DurableAckFinalizer(tmp_path / "finalizer.sqlite3")
    generated_tokens = iter((MESSAGE_ID, CHALLENGE, ATTEMPT_NONCE))

    def token_hex(byte_count: int) -> str:
        value = next(generated_tokens)
        assert len(value) == byte_count * 2
        return value

    authorities = guarded._Authorities(
        snapshot=overrides.pop("snapshot", snapshot),
        send_body=overrides.pop("send_body", lambda _window, text, _transport: {
            "ok": True, "chars_requested": len(text), "chars_accepted": len(text),
            "delivery_verified": False,
        }),
        focus=overrides.pop("focus", lambda hwnd: {"ok": True, "hwnd": hwnd}),
        enter=overrides.pop("enter", lambda target: {"ok": True, "hwnd": target.hwnd, "events_inserted": 2}),
        receive_ack=overrides.pop("receive_ack", receive),
        finalize_ack=overrides.pop("finalize_ack", finalizer.finalize),
        audit_append=overrides.pop("audit_append", sc_mesh_registry.append_event),
        token_hex=overrides.pop("token_hex", token_hex),
    )
    values = {
        "text": TEXT,
        "target": TARGET,
        "sender": SENDER,
        "receiver": RECEIVER,
        "keyring": KEYRING,
        "key_id": KEY_ID,
        "event_log_path": tmp_path / "events.jsonl",
        "authorities": authorities,
        "transport": "auto",
        "ack_timeout": 2.0,
        "max_ack_age_seconds": 300.0,
    }
    values.update(overrides)
    return guarded._guarded_submit_impl(**values)


def test_public_submit_has_no_authority_or_identifier_bypass():
    parameters = set(inspect.signature(guarded.guarded_submit).parameters)
    forbidden = {
        "resolver", "snapshot", "send_body", "body_sender", "focus", "enter",
        "audit_append", "receive_ack", "finalize_ack", "message_id", "challenge",
    }
    assert not parameters & forbidden
    assert "_guarded_submit_impl" not in guarded.__all__
    assert "_Authorities" not in guarded.__all__
    source = inspect.getsource(guarded)
    assert "multiprocessing.connection" not in source
    assert "pickle" not in source
    with pytest.raises(TypeError, match="resolver"):
        guarded.guarded_submit(
            TEXT, target=TARGET, sender=SENDER, receiver=RECEIVER,
            keyring=KEYRING, key_id=KEY_ID, ack_pipe=r"\\.\pipe\unused",
            replay_path="unused.sqlite3", event_log_path="unused.jsonl",
            resolver=lambda _hwnd: None,
        )


def test_package_manifest_contains_guarded_module():
    manifest = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '"sc_guarded_submit.py"' in manifest


def test_success_binds_generated_challenge_and_durable_events(tmp_path):
    result = _submit(tmp_path)
    assert result["ok"] is True
    assert result["delivery_verified"] is True
    assert result["message_id"] == MESSAGE_ID
    assert result["challenge"] == CHALLENGE
    assert result["ack"]["attempt_nonce"] == ATTEMPT_NONCE
    assert result["ack"]["challenge"] == CHALLENGE
    events = sc_mesh_registry.load_events(event_log_path=tmp_path / "events.jsonl")["events"]
    assert [event["event_type"] for event in events] == [
        "guarded_submit_prepared", "guarded_submit_submitted", "guarded_submit_acknowledged",
    ]
    assert {event["data"]["challenge"] for event in events} == {CHALLENGE}
    assert sc_mesh_registry.verify_events(event_log_path=tmp_path / "events.jsonl")["ok"] is True


@pytest.mark.parametrize(
    "field",
    ["pid", "exe_name", "class_name", "title", "exe_path", "process_start_time_ns"],
)
@pytest.mark.parametrize("changed_at", [0, 1, 2])
def test_identity_toctou_refuses_before_every_side_effect_boundary(tmp_path, field, changed_at):
    original = getattr(TARGET, field)
    changed = replace(TARGET, **{field: original + (1 if isinstance(original, int) else "-changed")})
    snapshots = [TARGET] * changed_at + [changed] * (4 - changed_at)
    entered = []
    result = _submit(tmp_path, snapshots=snapshots, enter=lambda hwnd: entered.append(hwnd) or {"ok": True})
    assert result["state"] == ("refused" if changed_at == 0 else "ambiguous")
    assert result["delivery_verified"] is False
    assert entered == []
    assert result["guard"]["mismatches"] == [field]


def test_identity_change_immediately_after_enter_is_ambiguous(tmp_path):
    changed = replace(TARGET, process_start_time_ns=TARGET.process_start_time_ns + 1)
    result = _submit(tmp_path, snapshots=[TARGET, TARGET, TARGET, changed])
    assert result["state"] == "ambiguous"
    assert result["error"] == "hardware_enter_not_confirmed"


@pytest.mark.parametrize(
    ("authority", "failure", "state", "prefix"),
    [
        ("snapshot", OSError("guard"), "refused", "target_guard_exception_before_typing"),
        ("send_body", OSError("body"), "ambiguous", "body_staged_exception"),
        ("focus", OSError("focus"), "ambiguous", "body_staged_focus_exception"),
        ("enter", OSError("enter"), "ambiguous", "hardware_enter_exception"),
        ("receive_ack", OSError("ack"), "ambiguous", "peer_ack_failed"),
        ("finalize_ack", OSError("db"), "ambiguous", "peer_ack_failed"),
    ],
)
def test_side_effect_exceptions_are_classified(tmp_path, authority, failure, state, prefix):
    def raises(*_args, **_kwargs):
        raise failure

    result = _submit(tmp_path, **{authority: raises})
    assert result["state"] == state
    assert result["delivery_verified"] is False
    assert result["error"].startswith(prefix)


@pytest.mark.parametrize(
    ("failed_event", "state", "prefix"),
    [
        ("guarded_submit_prepared", "refused", "audit_prepare_failed"),
        ("guarded_submit_submitted", "ambiguous", "audit_submitted_failed"),
        ("guarded_submit_acknowledged", "ambiguous", "peer_ack_failed"),
    ],
)
def test_audit_exceptions_are_classified_without_success(tmp_path, failed_event, state, prefix):
    def append(event_type, **kwargs):
        if event_type == failed_event:
            raise OSError("audit fault")
        return sc_mesh_registry.append_event(event_type, **kwargs)

    result = _submit(tmp_path, audit_append=append)
    assert result["state"] == state
    assert result["delivery_verified"] is False
    assert result["error"].startswith(prefix)


def test_partial_body_is_ambiguous_before_focus(tmp_path):
    focused = []
    result = _submit(
        tmp_path,
        send_body=lambda *_args: {"ok": True, "chars_requested": len(TEXT), "chars_accepted": len(TEXT) - 1},
        focus=lambda hwnd: focused.append(hwnd),
    )
    assert result["state"] == "ambiguous"
    assert focused == []


def test_direct_body_transport_denies_non_console_class():
    target = _window(replace(TARGET, class_name="OtherWindow"))
    assert guarded._production_send_body(target, TEXT, "auto")["ok"] is False


def test_keyring_requires_32_bytes_and_supports_rotation_overlap():
    with pytest.raises(ValueError):
        guarded.AckKeyRing({"short": b"x" * 31})
    assert KEYRING.resolve(KEY_ID) == KEY
    assert KEYRING.resolve(OLD_KEY_ID) == OLD_KEY
    with pytest.raises(guarded.AckVerificationError):
        KEYRING.resolve("retired")
    with pytest.raises(ValueError):
        guarded.AckKeyRing({"unsafe key id": KEY})
    original = {"stable": KEY}
    stable = guarded.AckKeyRing(original)
    original["stable"] = OLD_KEY
    assert stable.resolve("stable") == KEY


@pytest.mark.parametrize("field", ["message_id", "challenge", "input_sha256", "sender", "receiver", "key_id"])
def test_ack_exact_binding_rejects_mismatch(tmp_path, field):
    def receive(request, _timeout):
        overrides = {field: ("44" * 32 if field == "challenge" else "wrong")}
        if field == "input_sha256":
            overrides[field] = "44" * 32
        if field == "key_id":
            overrides[field] = OLD_KEY_ID
        return _ack(request, **overrides)

    result = _submit(tmp_path, receive_ack=receive)
    assert result["state"] == "ambiguous"
    assert result["error"].startswith("peer_ack_failed")


def test_unauthenticated_malformed_json_rejected_before_parse(monkeypatch):
    body = b"{not-json"
    header = b"SCACK1 " + KEY_ID.encode() + b" " + str(len(body)).encode() + b" " + (b"0" * 64)
    payload = header + b"\n" + body
    frame = struct.pack("<I", len(payload)) + payload
    called = []
    monkeypatch.setattr(json, "loads", lambda *_args: called.append(True))
    with pytest.raises(guarded.AckVerificationError, match="signature mismatch"):
        guarded._wire_decode(frame, keyring=KEYRING)
    assert called == []


def test_authenticated_duplicate_json_fields_are_rejected():
    raw_body = b'{"schema":"one","schema":"two"}'
    digest = hmac.new(KEY, raw_body, hashlib.sha256).hexdigest().encode()
    header = b"SCACK1 " + KEY_ID.encode() + b" " + str(len(raw_body)).encode() + b" " + digest
    payload = header + b"\n" + raw_body
    frame = struct.pack("<I", len(payload)) + payload
    with pytest.raises(guarded.AckVerificationError, match="duplicate"):
        guarded._wire_decode(frame, keyring=KEYRING)


@pytest.mark.skipif(os.name != "nt", reason="real named-pipe deadline requires Windows")
def test_named_pipe_connect_uses_total_deadline():
    pipe = guarded.make_private_pipe_address()
    client = guarded.RawJsonNamedPipeClient(pipe, KEYRING)
    request = guarded.PeerAckRequest(
        guarded.REQUEST_SCHEMA, KEY_ID, MESSAGE_ID, CHALLENGE,
        hashlib.sha256(TEXT.encode()).hexdigest(), SENDER, RECEIVER, time.time(), KEY_ID, ATTEMPT_NONCE,
    )
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        client.receive(request, 0.05)
    assert time.monotonic() - started < 0.5


@pytest.mark.skipif(os.name != "nt", reason="Win32 ABI attestation requires Windows")
def test_security_api_uses_pointer_width_handles_and_explicit_prototypes():
    from ctypes import wintypes

    guarded._configure_security_api()
    kernel32 = guarded.ctypes.windll.kernel32
    advapi32 = guarded.ctypes.windll.advapi32
    assert guarded.ctypes.sizeof(wintypes.HANDLE) == guarded.ctypes.sizeof(guarded.ctypes.c_void_p)
    assert kernel32.GetCurrentProcess.restype is wintypes.HANDLE
    assert kernel32.GetCurrentThread.restype is wintypes.HANDLE
    assert advapi32.ImpersonateNamedPipeClient.argtypes == [wintypes.HANDLE]
    assert advapi32.RevertToSelf.argtypes == []
    assert advapi32.SetFileSecurityW.argtypes == [wintypes.LPCWSTR, wintypes.DWORD, guarded.ctypes.c_void_p]


@pytest.mark.skipif(os.name != "nt", reason="Win32 OVERLAPPED lifetime requires Windows")
def test_cancelled_overlapped_io_retains_closure_and_buffers_until_completion(monkeypatch):
    kernel32 = guarded.ctypes.windll.kernel32
    entered = threading.Event()
    release = threading.Event()
    closed = []

    def wait(_event, _timeout):
        entered.set()
        release.wait(2)
        return 0

    monkeypatch.setattr(kernel32, "WaitForSingleObject", wait)
    monkeypatch.setattr(kernel32, "CloseHandle", lambda handle: closed.append(handle) or 1)
    buffer = guarded.ctypes.create_string_buffer(b"retained")

    def closure(*_args):
        return buffer.raw

    overlapped = guarded._OVERLAPPED()
    guarded._retain_cancelled_io(0x1_0000_1234, overlapped, 0x1_0000_5678, closure, (buffer,))
    assert entered.wait(1)
    with guarded._PENDING_IO_LOCK:
        assert any(item[0] == 0x1_0000_1234 and item[3] is closure and item[4] == (buffer,) for item in guarded._PENDING_IO)
    release.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        with guarded._PENDING_IO_LOCK:
            if not guarded._PENDING_IO:
                break
        time.sleep(0.01)
    assert closed == [0x1_0000_5678]


@pytest.mark.parametrize("timestamp", [True, False, math.nan, math.inf, -math.inf])
def test_bool_or_nonfinite_ack_timestamp_rejected(timestamp):
    body = {
        "schema": guarded.ACK_SCHEMA, "key_id": KEY_ID, "message_id": MESSAGE_ID,
        "challenge": CHALLENGE, "ack_nonce": ACK_NONCE,
        "attempt_nonce": ATTEMPT_NONCE,
        "input_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
        "processed_input_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
        "sender": RECEIVER, "receiver": SENDER, "decision": "accepted", "issued_at": timestamp,
    }
    raw = guarded._wire_encode(body, key_id=KEY_ID, key=KEY)
    with pytest.raises(guarded.AckVerificationError, match="finite"):
        guarded.verify_peer_ack(
            raw, keyring=KEYRING, key_id=KEY_ID, message_id=MESSAGE_ID,
            challenge=CHALLENGE, attempt_nonce=ATTEMPT_NONCE, input_sha256=body["input_sha256"],
            sender=RECEIVER, receiver=SENDER,
        )


@pytest.mark.parametrize("value", [True, False, 0, -1, math.nan, math.inf, 901])
def test_invalid_timeout_and_max_age_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        _submit(tmp_path, ack_timeout=value)
    with pytest.raises(ValueError):
        _submit(tmp_path, max_ack_age_seconds=value)


@pytest.mark.parametrize(
    "text",
    ["", "a\n", "a\r", "a\t", "a\x80", "a\x9f", "a\u2028b", "a\u2029b", "a\u202eb", "a\u2066b", "a\ud800b"],
)
def test_explicit_control_bidi_line_separator_and_invalid_unicode_policy(tmp_path, text):
    with pytest.raises(ValueError):
        _submit(tmp_path, text=text)


def test_pending_ack_can_reconcile_once_then_replay_is_rejected(tmp_path):
    store = guarded.DurableAckFinalizer(tmp_path / "finalize.sqlite3")
    request = guarded.PeerAckRequest(
        guarded.REQUEST_SCHEMA, KEY_ID, MESSAGE_ID, CHALLENGE,
        hashlib.sha256(TEXT.encode()).hexdigest(), SENDER, RECEIVER, 1.0, KEY_ID, ATTEMPT_NONCE,
    )
    raw = _ack(request, issued_at=1.0)
    ack = guarded.verify_peer_ack(
        raw, keyring=KEYRING, key_id=KEY_ID, message_id=MESSAGE_ID,
        challenge=CHALLENGE, attempt_nonce=ATTEMPT_NONCE, input_sha256=request.input_sha256,
        sender=RECEIVER, receiver=SENDER, now=1.0,
    )
    event = {
        "event_type": "guarded_submit_acknowledged", "status": "accepted", "data": {"digest": "a"},
        "repo_snapshot": {}, "strict_idempotency_key": "guarded-submit:test",
    }
    with pytest.raises(OSError):
        store.finalize(ack, raw, lambda _event: (_ for _ in ()).throw(OSError("audit down")), event)
    calls = []
    store.finalize(ack, raw, lambda value: calls.append(value), event)
    assert len(calls) == 1
    with pytest.raises(guarded.AckReplayError):
        store.finalize(ack, raw, lambda _event: None, event)


def test_strict_idempotent_audit_append_does_not_duplicate(tmp_path):
    path = tmp_path / "events.jsonl"
    first = sc_mesh_registry.append_event(
        "ack", event_log_path=path, strict=True, strict_idempotency_key="ack:1", repo_snapshot={},
    )
    second = sc_mesh_registry.append_event(
        "ack", event_log_path=path, strict=True, strict_idempotency_key="ack:1", repo_snapshot={},
    )
    assert second["idempotent_replay"] is True
    assert first["event"]["event_hash"] == second["event"]["event_hash"]
    assert sc_mesh_registry.verify_events(event_log_path=path)["events_checked"] == 1


def test_strict_idempotency_key_rejects_conflicting_intended_event(tmp_path):
    path = tmp_path / "events.jsonl"
    sc_mesh_registry.append_event(
        "ack", status="accepted", data={"digest": "a"}, event_log_path=path,
        strict=True, strict_idempotency_key="ack:1", repo_snapshot={},
    )
    with pytest.raises(sc_mesh_registry.EventLogIntegrityError, match="different event"):
        sc_mesh_registry.append_event(
            "ack", status="rejected", data={"digest": "b"}, event_log_path=path,
            strict=True, strict_idempotency_key="ack:1", repo_snapshot={},
        )


def _request_wire(
    *, issued_at: float = 1.0, attempt_nonce: str = ATTEMPT_NONCE,
    input_sha256: str | None = None, request_key_id: str = KEY_ID, response_key_id: str = KEY_ID,
) -> tuple[guarded.PeerAckRequest, bytes]:
    request = guarded.PeerAckRequest(
        guarded.REQUEST_SCHEMA, request_key_id, MESSAGE_ID, CHALLENGE,
        input_sha256 or hashlib.sha256(TEXT.encode()).hexdigest(), SENDER, RECEIVER, issued_at,
        response_key_id, attempt_nonce,
    )
    return request, guarded._wire_encode(
        guarded._request_body(request), key_id=request_key_id, key=KEYRING.resolve(request_key_id),
    )


def test_receiver_admission_is_single_claimant_and_recovers_completed_result_after_freshness(tmp_path, monkeypatch):
    store = guarded.DurableReceiverAdmissionStore(tmp_path / "receiver.sqlite3")
    attempts = [_request_wire(attempt_nonce=f"{index + 1:064x}") for index in range(8)]
    monkeypatch.setattr(guarded.time, "time", lambda: 1.0)
    assert guarded._validate_request(attempts[0][1], keyring=KEYRING, max_age_seconds=0.5) == attempts[0][0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        admissions = list(pool.map(lambda item: store.admit(*item), attempts))
    assert len({item[0] for item in admissions}) == 1
    admission_id = admissions[0][0]
    deadline = time.monotonic() + 2
    with ThreadPoolExecutor(max_workers=8) as pool:
        owners = list(pool.map(lambda _index: store.claim(admission_id, deadline), range(8)))
    owner = next(item for item in owners if item is not None)
    assert sum(item is not None for item in owners) == 1
    request = attempts[0][0]
    store.complete(admission_id, owner, "accepted", request.input_sha256)
    fresh_request, fresh_raw = _request_wire(
        attempt_nonce="99" * 32, issued_at=10.0, response_key_id=OLD_KEY_ID,
    )
    monkeypatch.setattr(guarded.time, "time", lambda: 10.0)
    with pytest.raises(guarded.AckVerificationError, match="freshness"):
        guarded._validate_request(attempts[0][1], keyring=KEYRING, max_age_seconds=0.5)
    assert guarded._validate_request(fresh_raw, keyring=KEYRING, max_age_seconds=0.5) == fresh_request
    assert store.admit(fresh_request, fresh_raw) == (
        admission_id, "completed", ("accepted", request.input_sha256),
    )


def test_receiver_rejects_exact_attempt_replay(tmp_path):
    store = guarded.DurableReceiverAdmissionStore(tmp_path / "receiver.sqlite3")
    request, raw = _request_wire()
    store.admit(request, raw)
    with pytest.raises(guarded.AckReplayError, match="attempt replay"):
        store.admit(request, raw)


def test_receiver_processing_lease_takeover_requires_recovery_path(tmp_path):
    store = guarded.DurableReceiverAdmissionStore(tmp_path / "receiver.sqlite3")
    request, raw = _request_wire()
    admission_id, state, _ = store.admit(request, raw)
    assert state == "admitted"
    owner = store.claim(admission_id, time.monotonic() + 0.05)
    assert owner is not None
    with contextlib.closing(sqlite3.connect(store.path)) as connection:
        connection.execute("UPDATE receiver_admission SET lease_expires_tick=-1 WHERE admission_id=?", (admission_id,))
        connection.commit()
    fresh, fresh_raw = _request_wire(attempt_nonce="66" * 32, issued_at=2.0)
    admission_id2, state2, _ = store.admit(fresh, fresh_raw)
    assert (admission_id2, state2) == (admission_id, "processing")
    assert store.claim(admission_id, time.monotonic() + 1) is not None


def test_receiver_rejects_same_admission_binding_with_changed_authenticated_body(tmp_path):
    store = guarded.DurableReceiverAdmissionStore(tmp_path / "receiver.sqlite3")
    request, raw = _request_wire(issued_at=1.0)
    store.admit(request, raw)
    changed, changed_raw = _request_wire(
        issued_at=2.0, attempt_nonce="66" * 32, input_sha256="77" * 32,
    )
    with pytest.raises(guarded.AckReplayError, match="binding conflict"):
        store.admit(changed, changed_raw)
    with pytest.raises(guarded.AckReplayError, match="attempt replay"):
        store.admit(changed, changed_raw)


def test_finalizer_rejects_unattested_legacy_schema_without_mutation(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE peer_ack_finalization (sender TEXT, receiver TEXT, message_id TEXT, challenge TEXT, "
            "ack_nonce TEXT, input_sha256 TEXT, ack_sha256 TEXT, state TEXT, created_at REAL, audited_at REAL)"
        )
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(guarded.AckReplayError, match="constraints are not authoritative"):
        guarded.DurableAckFinalizer(path)
    assert path.read_bytes() == before


def test_finalizer_schema_version_and_constraints_are_attested(tmp_path):
    path = tmp_path / "finalize.sqlite3"
    guarded.DurableAckFinalizer(path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(peer_ack_finalization)")}
        assert {"raw_ack", "key_id", "decision", "issued_at", "audit_event_json"} <= columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == guarded.FINALIZER_SCHEMA_VERSION
    replacement = tmp_path / "replacement.sqlite3"
    os.replace(path, replacement)
    assert replacement.exists()


@pytest.mark.parametrize(
    ("constructor", "version"),
    [
        (guarded.DurableAckFinalizer, guarded.FINALIZER_SCHEMA_VERSION + 1),
        (guarded.DurableReceiverAdmissionStore, guarded.RECEIVER_SCHEMA_VERSION + 1),
    ],
)
def test_future_sqlite_schema_is_rejected_without_database_mutation(tmp_path, constructor, version):
    path = tmp_path / f"future-{version}.sqlite3"
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('preserve-exactly')")
        connection.execute(f"PRAGMA user_version={version}")
        connection.commit()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(guarded.AckReplayError, match="newer than"):
        constructor(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_current_finalizer_catalog_missing_global_nonce_constraint_is_rejected(tmp_path):
    path = tmp_path / "malformed.sqlite3"
    malformed = guarded.FINALIZER_CREATE_SQL.replace(", UNIQUE (ack_nonce)", "")
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(malformed)
        connection.execute(f"PRAGMA user_version={guarded.FINALIZER_SCHEMA_VERSION}")
        connection.commit()
    with pytest.raises(guarded.AckReplayError, match="UNIQUE attestation"):
        guarded.DurableAckFinalizer(path)


def test_current_receiver_catalog_missing_attempt_uniqueness_is_rejected(tmp_path):
    path = tmp_path / "malformed-receiver.sqlite3"
    malformed = guarded.RECEIVER_CREATE_SQL.replace("request_sha256 TEXT NOT NULL UNIQUE", "request_sha256 TEXT NOT NULL")
    with contextlib.closing(sqlite3.connect(path)) as connection:
        for statement in malformed.split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute(f"PRAGMA user_version={guarded.RECEIVER_SCHEMA_VERSION}")
        connection.commit()
    with pytest.raises(guarded.AckReplayError, match="UNIQUE attestation"):
        guarded.DurableReceiverAdmissionStore(path)


def test_exact_empty_v1_receiver_catalog_migrates_and_malformed_v1_is_rejected(tmp_path):
    legacy_sql = """
    CREATE TABLE receiver_admission (
        sender TEXT NOT NULL, key_id TEXT NOT NULL, message_id TEXT NOT NULL,
        challenge TEXT NOT NULL, request_sha256 TEXT NOT NULL, request_body BLOB NOT NULL,
        admission_id TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK (state IN ('admitted', 'processing', 'completed')),
        lease_owner TEXT, lease_boot_id INTEGER, lease_expires_tick REAL,
        decision TEXT, processed_input_sha256 TEXT, response BLOB,
        created_at REAL NOT NULL, completed_at REAL,
        PRIMARY KEY (sender, key_id, message_id, challenge)
    )
    """
    valid = tmp_path / "receiver-v1.sqlite3"
    with contextlib.closing(sqlite3.connect(valid)) as connection:
        connection.execute(legacy_sql)
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    guarded.DurableReceiverAdmissionStore(valid)
    with contextlib.closing(sqlite3.connect(valid)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == guarded.RECEIVER_SCHEMA_VERSION

    malformed = tmp_path / "receiver-v1-malformed.sqlite3"
    with contextlib.closing(sqlite3.connect(malformed)) as connection:
        connection.execute(legacy_sql.replace("admission_id TEXT NOT NULL UNIQUE", "admission_id TEXT NOT NULL"))
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    with pytest.raises(guarded.AckReplayError, match="UNIQUE attestation"):
        guarded.DurableReceiverAdmissionStore(malformed)


def test_finalizer_ack_nonce_is_globally_single_use(tmp_path):
    store = guarded.DurableAckFinalizer(tmp_path / "finalize.sqlite3")
    request1, _ = _request_wire(issued_at=1.0)
    raw1 = _ack(request1, issued_at=1.0)
    ack1 = guarded.verify_peer_ack(
        raw1, keyring=KEYRING, key_id=KEY_ID, message_id=request1.message_id,
        challenge=request1.challenge, attempt_nonce=request1.attempt_nonce,
        input_sha256=request1.input_sha256, sender=RECEIVER, receiver=SENDER, now=1.0,
    )
    event1 = {"event_type": "ack", "data": {"operation": 1}, "repo_snapshot": {}}
    store.finalize(ack1, raw1, lambda _event: None, event1)

    request2 = replace(
        request1, message_id="88" * 16, challenge="99" * 32, attempt_nonce="aa" * 32,
    )
    raw2 = _ack(request2, issued_at=1.0)
    ack2 = guarded.verify_peer_ack(
        raw2, keyring=KEYRING, key_id=KEY_ID, message_id=request2.message_id,
        challenge=request2.challenge, attempt_nonce=request2.attempt_nonce,
        input_sha256=request2.input_sha256, sender=RECEIVER, receiver=SENDER, now=1.0,
    )
    with pytest.raises(guarded.AckReplayError, match="nonce replay"):
        store.finalize(ack2, raw2, lambda _event: None, {"event_type": "ack", "data": {"operation": 2}})


def test_audit_append_then_failure_recovers_exact_persisted_event(tmp_path):
    store = guarded.DurableAckFinalizer(tmp_path / "finalize.sqlite3")
    request, _ = _request_wire(issued_at=1.0)
    raw = _ack(request, issued_at=1.0)
    ack = guarded.verify_peer_ack(
        raw, keyring=KEYRING, key_id=KEY_ID, message_id=MESSAGE_ID,
        challenge=CHALLENGE, attempt_nonce=ATTEMPT_NONCE, input_sha256=request.input_sha256,
        sender=RECEIVER, receiver=SENDER, now=1.0,
    )
    event = {
        "event_type": "guarded_submit_acknowledged", "status": "acknowledged",
        "summary": "guarded submit acknowledged", "data": {"decision": "accepted", "nonce": ACK_NONCE},
        "strict_idempotency_key": "guarded-ack:exact-recovery", "repo_snapshot": {},
    }
    event_path = tmp_path / "events.jsonl"

    def append_then_fail(value):
        sc_mesh_registry.append_event(event_log_path=event_path, strict=True, **value)
        raise OSError("crash after durable audit append")

    with pytest.raises(OSError, match="crash after"):
        store.finalize(ack, raw, append_then_fail, event)
    pending = guarded.list_pending_acks(store.path)
    assert pending[0]["audit_event"] == event
    assert guarded.reconcile_pending_acks(store.path, event_path) == 1
    assert guarded.reconcile_pending_acks(store.path, event_path) == 0
    loaded = sc_mesh_registry.load_events(event_log_path=event_path)["events"]
    assert len(loaded) == 1
    assert loaded[0]["event_type"] == event["event_type"]
    assert {key: loaded[0]["data"][key] for key in event["data"]} == event["data"]


def test_public_pending_reconciliation_is_audit_only_and_idempotent(tmp_path):
    store = guarded.DurableAckFinalizer(tmp_path / "finalize.sqlite3")
    request, _ = _request_wire(issued_at=1.0)
    raw = _ack(request, issued_at=1.0)
    ack = guarded.verify_peer_ack(
        raw, keyring=KEYRING, key_id=KEY_ID, message_id=MESSAGE_ID, challenge=CHALLENGE,
        attempt_nonce=ATTEMPT_NONCE, input_sha256=request.input_sha256,
        sender=RECEIVER, receiver=SENDER, now=1.0,
    )
    event = {
        "event_type": "guarded_submit_acknowledged", "status": "accepted",
        "data": {"recovery": "audit_only_no_physical_submit"}, "repo_snapshot": {},
        "strict_idempotency_key": "guarded-submit:pending",
    }
    with pytest.raises(OSError):
        store.finalize(ack, raw, lambda _event: (_ for _ in ()).throw(OSError("audit unavailable")), event)
    assert len(guarded.list_pending_acks(tmp_path / "finalize.sqlite3")) == 1
    assert guarded.reconcile_pending_acks(tmp_path / "finalize.sqlite3", tmp_path / "events.jsonl") == 1
    assert guarded.reconcile_pending_acks(tmp_path / "finalize.sqlite3", tmp_path / "events.jsonl") == 0
    events = sc_mesh_registry.load_events(event_log_path=tmp_path / "events.jsonl")["events"]
    assert events[0]["data"]["recovery"] == "audit_only_no_physical_submit"


def test_rotation_active_overlap_expiry_and_revocation():
    ring = guarded.AckKeyRing({
        "old": guarded.AckKey(OLD_KEY, not_before=10, expires_at=30),
        "new": guarded.AckKey(KEY, not_before=20, expires_at=40),
        "revoked": guarded.AckKey(b"r" * 32, not_before=0, expires_at=40, revoked=True),
    })
    assert ring.active_key_ids(now=25) == ("old", "new")
    with pytest.raises(guarded.AckVerificationError, match="inactive"):
        ring.resolve("old", now=30)
    with pytest.raises(guarded.AckVerificationError, match="inactive"):
        ring.resolve("revoked", now=25)


@pytest.mark.skipif(os.name != "nt", reason="real private named pipe requires Windows")
@pytest.mark.parametrize(
    "response_key",
    [
        guarded.AckKey(b"r" * 32, revoked=True),
        guarded.AckKey(b"e" * 32, expires_at=1.0),
    ],
)
def test_receiver_rejects_revoked_or_expired_response_key_before_admission(tmp_path, response_key):
    ring = guarded.AckKeyRing({KEY_ID: KEY, "denied": response_key})
    pipe = guarded.make_private_pipe_address()
    request = guarded.PeerAckRequest(
        guarded.REQUEST_SCHEMA, KEY_ID, MESSAGE_ID, CHALLENGE,
        hashlib.sha256(TEXT.encode()).hexdigest(), SENDER, RECEIVER, time.time(), "denied", ATTEMPT_NONCE,
    )
    server = guarded.ProcessingAckServer(pipe, ring, KEY_ID, tmp_path / "admission.sqlite3")
    errors = []

    def serve():
        try:
            server.serve_once(guarded.SubprocessAckProcessor(["definitely-must-not-run.exe"]), timeout=2)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve)
    thread.start()
    with pytest.raises(OSError):
        guarded.RawJsonNamedPipeClient(pipe, ring).receive(request, 2)
    thread.join(timeout=3)
    assert len(errors) == 1 and isinstance(errors[0], guarded.AckVerificationError)
    with contextlib.closing(sqlite3.connect(tmp_path / "admission.sqlite3")) as connection:
        assert connection.execute("SELECT count(*) FROM receiver_attempt").fetchone()[0] == 0


def test_request_rotation_authenticates_old_key_and_binds_new_response_key():
    request = guarded.PeerAckRequest(
        guarded.REQUEST_SCHEMA, OLD_KEY_ID, MESSAGE_ID, CHALLENGE,
        hashlib.sha256(TEXT.encode()).hexdigest(), SENDER, RECEIVER, time.time(), KEY_ID, ATTEMPT_NONCE,
    )
    raw = guarded._wire_encode(guarded._request_body(request), key_id=OLD_KEY_ID, key=OLD_KEY)
    assert guarded._validate_request(raw, keyring=KEYRING, max_age_seconds=300) == request


@pytest.mark.parametrize("stage", ["body", "focus", "enter"])
def test_submit_uses_one_total_deadline_across_all_physical_stages(tmp_path, stage):
    calls = []

    def body(_window, text, _transport):
        calls.append("body")
        if stage == "body":
            time.sleep(0.2)
        return {"ok": True, "chars_requested": len(text), "chars_accepted": len(text)}

    def focus(hwnd):
        calls.append("focus")
        if stage == "focus":
            time.sleep(0.2)
        return {"ok": True, "hwnd": hwnd}

    def enter(target):
        calls.append("enter")
        if stage == "enter":
            time.sleep(0.2)
        return {"ok": True, "hwnd": target.hwnd, "events_inserted": 2}

    result = _submit(
        tmp_path, ack_timeout=0.1, send_body=body, focus=focus, enter=enter,
        receive_ack=lambda *_args: calls.append("ack") or b"",
    )
    assert result["state"] == "ambiguous"
    if stage == "body":
        assert calls == ["body"]
    elif stage == "focus":
        assert calls == ["body", "focus"]
    else:
        assert calls == ["body", "focus", "enter"]


def test_signed_rejection_binds_adapter_attested_digest_separately():
    requested = hashlib.sha256(TEXT.encode()).hexdigest()
    processed = "44" * 32
    raw = guarded.sign_peer_ack(
        keyring=KEYRING, key_id=KEY_ID, message_id=MESSAGE_ID, challenge=CHALLENGE,
        attempt_nonce=ATTEMPT_NONCE, ack_nonce=ACK_NONCE,
        input_sha256=requested, processed_input_sha256=processed,
        sender=RECEIVER, receiver=SENDER, decision="rejected",
    )
    ack = guarded.verify_peer_ack(
        raw, keyring=KEYRING, key_id=KEY_ID, message_id=MESSAGE_ID, challenge=CHALLENGE,
        attempt_nonce=ATTEMPT_NONCE, input_sha256=requested, sender=RECEIVER, receiver=SENDER,
    )
    assert ack.decision == "rejected" and ack.processed_input_sha256 == processed


def test_governed_subprocess_is_killed_at_total_deadline(tmp_path):
    adapter = tmp_path / "hung.py"
    adapter.write_text("import time\ntime.sleep(60)\n", encoding="ascii")
    processor = guarded.SubprocessAckProcessor([sys.executable, str(adapter)])
    request, _ = _request_wire(issued_at=time.time())
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="killed"):
        processor.process(request, "aa" * 32, time.monotonic() + 0.1)
    assert time.monotonic() - started < 1


@pytest.mark.skipif(os.name != "nt", reason="real private named pipe requires Windows")
def test_private_overlapped_pipe_loopback_and_governed_processor(tmp_path):
    pipe = guarded.make_private_pipe_address()
    integration_timeout = 5
    request = guarded.PeerAckRequest(
        guarded.REQUEST_SCHEMA, OLD_KEY_ID, MESSAGE_ID, CHALLENGE,
        hashlib.sha256(TEXT.encode()).hexdigest(), SENDER, RECEIVER, time.time(), KEY_ID, ATTEMPT_NONCE,
    )
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "import json,sys\np=json.load(sys.stdin)\nr=p['request']\n"
        "print(json.dumps({'admission_id':p['admission_id'],'mode':p['mode'],"
        "'decision':'accepted','input_sha256':r['input_sha256']}))\n",
        encoding="ascii",
    )
    processor = guarded.SubprocessAckProcessor([sys.executable, str(adapter)])

    server = guarded.ProcessingAckServer(pipe, KEYRING, KEY_ID, tmp_path / "admission.sqlite3")
    errors = []

    def serve():
        try:
            server.serve_once(processor, timeout=integration_timeout)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        raw_ack = guarded.RawJsonNamedPipeClient(pipe, KEYRING).receive(request, integration_timeout)
    except OSError as exc:
        thread.join(timeout=integration_timeout + 1)
        raise AssertionError(f"initial server failed: {errors}") from exc
    thread.join(timeout=integration_timeout + 1)
    assert not thread.is_alive() and errors == []
    ack = guarded.verify_peer_ack(
        raw_ack, keyring=KEYRING, key_id=KEY_ID, message_id=MESSAGE_ID, challenge=CHALLENGE,
        attempt_nonce=ATTEMPT_NONCE, input_sha256=request.input_sha256,
        sender=RECEIVER, receiver=SENDER,
    )
    assert ack.decision == "accepted"

    # The all-new leg is a separate operation through the same real endpoint.
    new_request = replace(
        request, key_id=KEY_ID, response_key_id=KEY_ID, message_id="77" * 16,
        challenge="88" * 32, attempt_nonce="99" * 32, issued_at=time.time(),
    )
    new_errors = []
    new_server = guarded.ProcessingAckServer(pipe, KEYRING, KEY_ID, tmp_path / "admission.sqlite3")

    def new_serve():
        try:
            new_server.serve_once(processor, timeout=integration_timeout)
        except Exception as exc:
            new_errors.append(exc)

    new_thread = threading.Thread(target=new_serve)
    new_thread.start()
    new_raw_ack = guarded.RawJsonNamedPipeClient(pipe, KEYRING).receive(new_request, integration_timeout)
    new_thread.join(timeout=integration_timeout + 1)
    assert new_errors == []
    guarded.verify_peer_ack(
        new_raw_ack, keyring=KEYRING, key_id=KEY_ID, message_id=new_request.message_id,
        challenge=new_request.challenge, attempt_nonce=new_request.attempt_nonce,
        input_sha256=new_request.input_sha256, sender=RECEIVER, receiver=SENDER,
    )

    # A lost first response redelivery returns the durable signed result and
    # never launches a second physical processor.
    replay_errors = []
    replay_server = guarded.ProcessingAckServer(pipe, KEYRING, KEY_ID, tmp_path / "admission.sqlite3")

    def replay_serve():
        try:
            replay_server.serve_once(
                guarded.SubprocessAckProcessor(["definitely-must-not-run.exe"]), timeout=integration_timeout,
            )
        except Exception as exc:
            replay_errors.append(exc)

    replay_thread = threading.Thread(target=replay_serve)
    replay_thread.start()
    retry = replace(
        request, issued_at=time.time(), attempt_nonce="66" * 32, response_key_id=OLD_KEY_ID,
    )
    try:
        replay_ack = guarded.RawJsonNamedPipeClient(pipe, KEYRING).receive(retry, integration_timeout)
    except OSError as exc:
        replay_thread.join(timeout=integration_timeout + 1)
        raise AssertionError(f"recovery server failed: {replay_errors}") from exc
    replay_thread.join(timeout=integration_timeout + 1)
    assert replay_errors == [] and replay_ack != raw_ack
    replay = guarded.verify_peer_ack(
        replay_ack, keyring=KEYRING, key_id=OLD_KEY_ID, message_id=MESSAGE_ID, challenge=CHALLENGE,
        attempt_nonce=retry.attempt_nonce, input_sha256=request.input_sha256,
        sender=RECEIVER, receiver=SENDER,
    )
    assert replay.decision == ack.decision

    # Reusing the exact authenticated attempt is denied before any processor
    # could run, even though the stable operation already has a decision.
    exact_errors = []
    exact_server = guarded.ProcessingAckServer(pipe, KEYRING, KEY_ID, tmp_path / "admission.sqlite3")

    def exact_serve():
        try:
            exact_server.serve_once(
                guarded.SubprocessAckProcessor(["definitely-must-not-run.exe"]), timeout=integration_timeout,
            )
        except Exception as exc:
            exact_errors.append(exc)

    exact_thread = threading.Thread(target=exact_serve)
    exact_thread.start()
    with pytest.raises(OSError):
        guarded.RawJsonNamedPipeClient(pipe, KEYRING).receive(retry, integration_timeout)
    exact_thread.join(timeout=integration_timeout + 1)
    assert len(exact_errors) == 1
    assert isinstance(exact_errors[0], guarded.AckReplayError)


def test_strict_event_append_serializes_concurrent_writers(tmp_path):
    path = tmp_path / "events.jsonl"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: sc_mesh_registry.append_event(
            f"event-{index}", event_log_path=path, strict=True, repo_snapshot={},
        ), range(20)))
    verified = sc_mesh_registry.verify_events(event_log_path=path)
    assert verified["ok"] is True
    assert verified["events_checked"] == 20


def test_strict_event_append_fsyncs_and_rejects_tamper(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    calls = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    sc_mesh_registry.append_event("first", event_log_path=path, strict=True, repo_snapshot={})
    assert len(calls) == 1
    record = json.loads(path.read_text(encoding="utf-8"))
    record["event_type"] = "tampered"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(sc_mesh_registry.EventLogIntegrityError):
        sc_mesh_registry.append_event("second", event_log_path=path, strict=True, repo_snapshot={})
