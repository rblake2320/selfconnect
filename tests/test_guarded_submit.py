from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import math
import os
import struct
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
        "key_id": request.key_id,
        "message_id": request.message_id,
        "challenge": request.challenge,
        "ack_nonce": ACK_NONCE,
        "input_sha256": request.input_sha256,
        "sender": request.receiver,
        "receiver": request.sender,
        "decision": decision,
    }
    values.update(overrides)
    return guarded.sign_peer_ack(**values)


def _tokens(byte_count: int) -> str:
    return ("11" if byte_count == 16 else "22") * byte_count


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
    authorities = guarded._Authorities(
        snapshot=overrides.pop("snapshot", snapshot),
        send_body=overrides.pop("send_body", lambda _window, text, _transport: {
            "ok": True, "chars_requested": len(text), "chars_accepted": len(text),
            "delivery_verified": False,
        }),
        focus=overrides.pop("focus", lambda hwnd: {"ok": True, "hwnd": hwnd}),
        enter=overrides.pop("enter", lambda hwnd: {"ok": True, "hwnd": hwnd, "events_inserted": 2}),
        receive_ack=overrides.pop("receive_ack", receive),
        finalize_ack=overrides.pop("finalize_ack", finalizer.finalize),
        audit_append=overrides.pop("audit_append", sc_mesh_registry.append_event),
        token_hex=overrides.pop("token_hex", _tokens),
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
    assert result["state"] == "refused"
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
        ("send_body", OSError("body"), "refused", "body_transport_exception"),
        ("focus", OSError("focus"), "refused", "focus_exception"),
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


def test_partial_body_is_refused_before_focus(tmp_path):
    focused = []
    result = _submit(
        tmp_path,
        send_body=lambda *_args: {"ok": True, "chars_requested": len(TEXT), "chars_accepted": len(TEXT) - 1},
        focus=lambda hwnd: focused.append(hwnd),
    )
    assert result["state"] == "refused"
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
    pipe = rf"\\.\pipe\selfconnect_missing_{os.getpid()}_{time.time_ns()}"
    client = guarded.RawJsonNamedPipeClient(pipe, KEYRING)
    request = guarded.PeerAckRequest(
        guarded.REQUEST_SCHEMA, KEY_ID, MESSAGE_ID, CHALLENGE,
        hashlib.sha256(TEXT.encode()).hexdigest(), SENDER, RECEIVER, time.time(),
    )
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        client.receive(request, 0.05)
    assert time.monotonic() - started < 0.5


@pytest.mark.parametrize("timestamp", [True, False, math.nan, math.inf, -math.inf])
def test_bool_or_nonfinite_ack_timestamp_rejected(timestamp):
    body = {
        "schema": guarded.ACK_SCHEMA, "key_id": KEY_ID, "message_id": MESSAGE_ID,
        "challenge": CHALLENGE, "ack_nonce": ACK_NONCE,
        "input_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
        "sender": RECEIVER, "receiver": SENDER, "decision": "accepted", "issued_at": timestamp,
    }
    raw = guarded._wire_encode(body, key_id=KEY_ID, key=KEY)
    with pytest.raises(guarded.AckVerificationError, match="finite"):
        guarded.verify_peer_ack(
            raw, keyring=KEYRING, key_id=KEY_ID, message_id=MESSAGE_ID,
            challenge=CHALLENGE, input_sha256=body["input_sha256"],
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
        hashlib.sha256(TEXT.encode()).hexdigest(), SENDER, RECEIVER, 1.0,
    )
    raw = _ack(request, issued_at=1.0)
    ack = guarded.verify_peer_ack(
        raw, keyring=KEYRING, key_id=KEY_ID, message_id=MESSAGE_ID,
        challenge=CHALLENGE, input_sha256=request.input_sha256,
        sender=RECEIVER, receiver=SENDER, now=1.0,
    )
    with pytest.raises(OSError):
        store.finalize(ack, raw, lambda _digest: (_ for _ in ()).throw(OSError("audit down")))
    calls = []
    store.finalize(ack, raw, lambda digest: calls.append(digest))
    assert len(calls) == 1
    with pytest.raises(guarded.AckReplayError):
        store.finalize(ack, raw, lambda _digest: None)


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
