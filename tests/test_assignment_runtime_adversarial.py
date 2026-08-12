from __future__ import annotations

import base64
import copy
import json
import sqlite3

import pytest
import sc_assignment_runtime as runtime_module
from sc_assignment_protocol import (
    AssignmentStateStore,
    AssignmentVerificationError,
    verify_consume_state_receipt,
)
from sc_assignment_runtime import (
    AssignmentBindings,
    AssignmentDispatchError,
    DurableAssignmentJournal,
    DurableReceiptMailbox,
    GuardedSubmitConfig,
    ProductionAssignmentRuntime,
    RevocationSnapshot,
    SeatAssignmentBroker,
    parse_assignment_ingress,
)
from sc_assignment_watchdog import AssignmentWatchdog
from sc_guarded_submit import AckKeyRing, TargetIdentity
from sc_identity import AgentIdentity
from sc_seat_identity import create_enrollment, key_id
from sc_terminal_tab import RUNTIME_ID_SCOPE, TerminalTabGuard, TerminalTabIdentity

NOW = 2_000_000_000.0
PAYLOAD = "adversarial signed-inline assignment"
RESULT_HASH = "a" * 64


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _target():
    return TargetIdentity(
        hwnd=101,
        pid=202,
        exe_name="WindowsTerminal.exe",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        title="seat",
        exe_path=r"C:\Program Files\WindowsApps\WindowsTerminal.exe",
        process_start_time_ns=303,
    )


def _tab():
    return TerminalTabIdentity(
        window_hwnd=101,
        window_pid=202,
        window_process_start_time_ns=303,
        tab_runtime_id=(42, 7),
        term_control_runtime_id=(99, 3),
        peer_birth_id="seat-birth-1",
        runtime_id_scope=RUNTIME_ID_SCOPE,
    )


def _environment(tmp_path, monkeypatch, *, submit_result=None, submit_error=None):
    authority = AgentIdentity.generate("authority")
    coordinator = AgentIdentity.generate("coordinator")
    response_receiver = AgentIdentity.generate("response-receiver")
    seat = AgentIdentity.generate("seat")
    enrollment = create_enrollment(
        seat_identity=seat,
        authority_identity=authority,
        birth_id="seat-birth-1",
        generation=3,
        now=NOW,
        ttl_seconds=300,
    )
    target = _target()
    tab = _tab()
    channel = {
        "transport": "durable_local_receipt_mailbox_v1",
        "mailbox_id": "seat-birth-1",
        "receiver_nonce": "b" * 64,
    }
    bindings = AssignmentBindings(
        authority_public_key_hex=authority.public_key_hex,
        coordinator_public_key_hex=coordinator.public_key_hex,
        coordinator_birth_id="coordinator-birth-1",
        coordinator_generation=2,
        receiver_birth_id="seat-birth-1",
        receiver_generation=3,
        target_identity=target,
        terminal_tab_identity=tab,
        response_receiver_public_key_hex=response_receiver.public_key_hex,
        response_channel=channel,
    )
    revocations = [RevocationSnapshot(frozenset(), frozenset(), "r1", NOW)]
    wall = [NOW + 1]
    submitted = []

    def submit(text, **kwargs):
        submitted.append((text, kwargs))
        if submit_error is not None:
            raise submit_error
        return copy.deepcopy(
            submit_result
            or {
                "ok": True,
                "state": "acknowledged",
                "delivery_verified": True,
                "peer_acknowledged": True,
                "decision": "accepted",
            }
        )

    monkeypatch.setattr(runtime_module, "guarded_submit", submit)
    mailbox = DurableReceiptMailbox(tmp_path / "mailbox.sqlite3", channel_binding=channel)
    journal = DurableAssignmentJournal(tmp_path / "dispatch.sqlite3")
    coordinator_store = AssignmentStateStore(tmp_path / "coordinator.sqlite3")
    runtime = ProductionAssignmentRuntime(
        coordinator_identity=coordinator,
        coordinator_store=coordinator_store,
        receiver_enrollment=enrollment,
        bindings=bindings,
        mailbox=mailbox,
        assignment_journal=journal,
        revocation_resolver=lambda: revocations[0],
        terminal_tab_guard=TerminalTabGuard(tab, None, None, None),
        submit_config=GuardedSubmitConfig(
            sender="coordinator-birth-1",
            receiver="seat-birth-1",
            keyring=AckKeyRing({"ack-key": b"k" * 32}),
            key_id="ack-key",
            ack_pipe=r"\\.\pipe\unattested-test",
            replay_path=tmp_path / "peer-ack.sqlite3",
            event_log_path=tmp_path / "events.jsonl",
        ),
        wall_clock=lambda: wall[0],
    )
    return {
        "authority": authority,
        "coordinator": coordinator,
        "response_receiver": response_receiver,
        "seat": seat,
        "enrollment": enrollment,
        "target": target,
        "tab": tab,
        "channel": channel,
        "bindings": bindings,
        "revocations": revocations,
        "wall": wall,
        "submitted": submitted,
        "mailbox": mailbox,
        "journal": journal,
        "coordinator_store": coordinator_store,
        "runtime": runtime,
    }


def _broker(env, path):
    return SeatAssignmentBroker(
        store=AssignmentStateStore(path / "seat.sqlite3"),
        seat_identity=env["seat"],
        bindings=env["bindings"],
        mailbox=env["mailbox"],
        assignment_journal=env["journal"],
        revocation_resolver=lambda: env["revocations"][0],
        current_target=lambda: env["target"],
        current_terminal_tab=lambda: env["tab"],
        current_response_receiver_public_key=lambda: env["response_receiver"].public_key_hex,
        current_response_channel=lambda: env["channel"],
        now=lambda: env["wall"][0],
    )


def _dispatch(env):
    return env["runtime"].dispatch(PAYLOAD, now=NOW, ttl_seconds=60)


@pytest.mark.parametrize(
    "result",
    (
        {"ok": False, "state": "refused", "delivery_verified": False},
        {"ok": False, "state": "ambiguous", "delivery_verified": False},
        {"ok": True, "state": "acknowledged", "delivery_verified": True},
    ),
)
def test_nonaccepted_guarded_submit_quarantines_and_cannot_be_admitted(tmp_path, monkeypatch, result):
    env = _environment(tmp_path, monkeypatch, submit_result=result)
    with pytest.raises(AssignmentDispatchError, match="quarantined"):
        _dispatch(env)
    raw = env["submitted"][0][0]
    record = parse_assignment_ingress(raw)
    assert env["journal"].state(record) == "quarantined"
    with pytest.raises(AssignmentVerificationError, match="delivery-authorized"):
        _broker(env, tmp_path).admit_raw(raw)


def test_guarded_submit_exception_quarantines_without_returning_assignment(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch, submit_error=RuntimeError("pipe failed"))
    with pytest.raises(AssignmentDispatchError, match="quarantined"):
        _dispatch(env)
    assert env["journal"].state(parse_assignment_ingress(env["submitted"][0][0])) == "quarantined"


def test_live_revocation_snapshots_gate_issue_admit_emit_and_watchdog(tmp_path, monkeypatch):
    issue_env = _environment(tmp_path / "issue", monkeypatch)
    coordinator_id = key_id(issue_env["coordinator"].public_key_hex)
    issue_env["revocations"][0] = RevocationSnapshot(frozenset({coordinator_id}), frozenset(), "r2", NOW + 1)
    with pytest.raises(AssignmentVerificationError, match="revoked"):
        _dispatch(issue_env)
    assert not issue_env["submitted"]

    env = _environment(tmp_path / "lifecycle", monkeypatch)
    dispatch = _dispatch(env)
    raw = _canonical(dispatch.assignment)
    coordinator_id = key_id(env["coordinator"].public_key_hex)
    seat_id = key_id(env["seat"].public_key_hex)
    env["revocations"][0] = RevocationSnapshot(frozenset(), frozenset({seat_id}), "r3", NOW + 1)
    with pytest.raises(AssignmentVerificationError, match="revoked"):
        _broker(env, tmp_path / "revoked-admit").admit_raw(raw)

    env["revocations"][0] = RevocationSnapshot(frozenset(), frozenset(), "r4", NOW + 1)
    broker = _broker(env, tmp_path / "active")
    broker.admit_raw(raw)
    env["revocations"][0] = RevocationSnapshot(frozenset({coordinator_id}), frozenset(), "r5", NOW + 2)
    with pytest.raises(AssignmentVerificationError, match="revoked"):
        broker.emit(
            dispatch.assignment,
            state="working",
            detail={},
            idempotency_key="working-revoked",
        )

    alerts = []
    watchdog, source = env["runtime"].watchdog(
        dispatch.assignment,
        read_uia=lambda _hwnd: "working",
        read_ocr=lambda _hwnd: "",
        capture=lambda _hwnd: None,
        alert_coordinator=alerts.append,
        source_guard=lambda _source: True,
        target_guard=lambda target: target == env["target"],
        assignment_source="source",
        clock=lambda: 0.0,
    )
    result = watchdog.monitor(
        hwnd=env["target"].hwnd,
        assignment=dispatch.assignment,
        assignment_source=source,
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert result.state == "blocked" and "revoked" in result.evidence


def test_post_read_guard_failure_does_not_commit_or_advance_cursor(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    receipt = _broker(env, tmp_path).admit_raw(_canonical(assignment)).accepted_receipt
    checks = []
    reader = env["mailbox"].reader(assignment, channel=env["channel"], consumer_id="guard-test")
    watchdog = AssignmentWatchdog(
        store=env["coordinator_store"],
        receipt_reader=reader,
        read_uia=lambda _hwnd: "completed",
        read_ocr=lambda _hwnd: "",
        capture=lambda _hwnd: None,
        alert_coordinator=lambda _event: None,
        source_guard=lambda _source: checks.append(True) or len(checks) == 1,
        target_guard=lambda _target: True,
        verification_resolver=lambda: env["bindings"].verification(env["revocations"][0], now=NOW + 2),
        clock=lambda: 0.0,
    )
    result = watchdog.monitor(
        hwnd=env["target"].hwnd,
        assignment=assignment,
        assignment_source="source",
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert result.state == "blocked"
    restarted = env["mailbox"].reader(assignment, channel=env["channel"], consumer_id="guard-test")
    assert restarted() == receipt


def test_watchdog_reentrancy_is_rejected_without_second_consumer(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    broker = _broker(env, tmp_path)
    broker.admit_raw(_canonical(assignment))
    broker.emit(assignment, state="working", detail={}, idempotency_key="working")
    broker.emit(
        assignment,
        state="completed",
        detail={},
        result_sha256=RESULT_HASH,
        idempotency_key="completed",
    )
    nested = []
    watchdog = None

    def source_guard(_source):
        if not nested:
            nested.append(
                watchdog.monitor(
                    hwnd=env["target"].hwnd,
                    assignment=assignment,
                    assignment_source="source",
                    timeout_seconds=1,
                    poll_seconds=0.1,
                    sleep=lambda _seconds: None,
                )
            )
        return True

    watchdog, source = env["runtime"].watchdog(
        assignment,
        read_uia=lambda _hwnd: "working",
        read_ocr=lambda _hwnd: "",
        capture=lambda _hwnd: None,
        alert_coordinator=lambda _event: None,
        source_guard=source_guard,
        target_guard=lambda target: target == env["target"],
        assignment_source="source",
        clock=lambda: 0.0,
    )
    outer = watchdog.monitor(
        hwnd=env["target"].hwnd,
        assignment=assignment,
        assignment_source=source,
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert nested[0].state == "blocked" and "already active" in nested[0].evidence
    assert outer.state == "completed"


def test_durable_cursor_restart_skips_already_committed_expired_receipt(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    broker = _broker(env, tmp_path)
    accepted = broker.admit_raw(_canonical(assignment)).accepted_receipt
    reader = env["mailbox"].reader(assignment, channel=env["channel"], consumer_id="restart")
    observed = reader()
    verify_consume_state_receipt(
        observed,
        assignment,
        store=env["coordinator_store"],
        **env["bindings"].verification(env["revocations"][0], now=NOW + 2),
    )
    reader.acknowledge(observed)
    assert observed == accepted

    env["wall"][0] = NOW + 50
    broker.emit(assignment, state="working", detail={}, idempotency_key="late-working")
    env["wall"][0] = NOW + 51
    broker.emit(
        assignment,
        state="completed",
        detail={},
        result_sha256=RESULT_HASH,
        idempotency_key="late-completed",
    )
    env["wall"][0] = NOW + 52
    restarted = env["mailbox"].reader(assignment, channel=env["channel"], consumer_id="restart")
    ticks = [0.0]
    watchdog = AssignmentWatchdog(
        store=env["coordinator_store"],
        receipt_reader=restarted,
        read_uia=lambda _hwnd: "old receipt expired",
        read_ocr=lambda _hwnd: "",
        capture=lambda _hwnd: None,
        alert_coordinator=lambda _event: None,
        source_guard=lambda _source: True,
        target_guard=lambda target: target == env["target"],
        verification_resolver=lambda: env["bindings"].verification(env["revocations"][0], now=NOW + 52),
        clock=lambda: ticks[0],
    )
    completed = watchdog.monitor(
        hwnd=env["target"].hwnd,
        assignment=assignment,
        assignment_source="source",
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda seconds: ticks.__setitem__(0, ticks[0] + seconds),
    )
    assert completed.state == "completed" and completed.receipt["sequence"] == 3


def test_exact_receipt_replay_is_explicitly_idempotent(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    receipt = _broker(env, tmp_path).admit_raw(_canonical(assignment)).accepted_receipt
    outcome = env["mailbox"].publish(receipt, channel=env["channel"])
    assert outcome.disposition == "duplicate_idempotent" and outcome.record == receipt


@pytest.mark.parametrize("tamper", ("delete", "truncate", "record", "reorder"))
def test_mailbox_detects_delete_truncate_record_tamper_and_reorder(tmp_path, monkeypatch, tamper):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    receipt = _broker(env, tmp_path).admit_raw(_canonical(assignment)).accepted_receipt
    if tamper == "reorder":
        forged = copy.deepcopy(receipt)
        forged["sequence"] = 3
        with pytest.raises(Exception, match=r"reorder|gap|fork"):
            env["mailbox"].publish(forged, channel=env["channel"])
        return
    with sqlite3.connect(env["mailbox"].path) as connection:
        if tamper in {"delete", "truncate"}:
            connection.execute("DELETE FROM receipt_mailbox_v1")
        else:
            connection.execute(
                "UPDATE receipt_mailbox_v1 SET record_json=? WHERE assignment_id=? AND sequence=1",
                (b"{}", assignment["assignment_id"]),
            )
    reader = env["mailbox"].reader(assignment, channel=env["channel"], consumer_id=tamper)
    with pytest.raises(AssignmentVerificationError, match=r"deletion|truncation|integrity"):
        reader()


def test_mailbox_read_is_an_immutable_toctou_snapshot(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    receipt = _broker(env, tmp_path).admit_raw(_canonical(assignment)).accepted_receipt
    reader = env["mailbox"].reader(assignment, channel=env["channel"], consumer_id="snapshot")
    snapshot = reader()
    with sqlite3.connect(env["mailbox"].path) as connection:
        connection.execute(
            "UPDATE receipt_mailbox_v1 SET record_json=? WHERE assignment_id=? AND sequence=1",
            (b'{"forged":true}', assignment["assignment_id"]),
        )
    assert snapshot == receipt
    verify_consume_state_receipt(
        snapshot,
        assignment,
        store=env["coordinator_store"],
        **env["bindings"].verification(env["revocations"][0], now=NOW + 2),
    )
    reader.acknowledge(snapshot)
    fresh = env["mailbox"].reader(assignment, channel=env["channel"], consumer_id="fresh")
    with pytest.raises(AssignmentVerificationError, match="integrity"):
        fresh()


def test_ack_loss_recovery_is_durable_and_idempotent(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    broker = _broker(env, tmp_path)
    receipt = broker.admit_raw(_canonical(assignment)).accepted_receipt
    verify_consume_state_receipt(
        receipt,
        assignment,
        store=env["coordinator_store"],
        **env["bindings"].verification(env["revocations"][0], now=NOW + 2),
    )
    original_publish = env["mailbox"].publish_ack
    calls = []

    def lose_once(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("simulated ACK delivery loss")
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(env["mailbox"], "publish_ack", lose_once)
    with pytest.raises(OSError, match="delivery loss"):
        env["runtime"].recover_receipt_ack(receipt)
    recovered = env["runtime"].recover_receipt_ack(receipt)
    duplicate = env["runtime"].recover_receipt_ack(receipt)
    assert recovered.disposition == "inserted"
    assert duplicate.disposition == "duplicate_idempotent"
    env["wall"][0] = NOW + 3
    ack = broker.consume_receipt_ack(assignment, receipt)
    assert ack is not None and ack["receipt_sha256"] == recovered.record["receipt_sha256"]


def test_raw_ingress_quarantines_unsigned_wrong_key_and_noncanonical_text(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    broker = _broker(env, tmp_path)
    unsigned = copy.deepcopy(assignment)
    unsigned.pop("signature_b64")
    with pytest.raises(AssignmentVerificationError, match=r"signature|delivery-authorized"):
        broker.admit_raw(_canonical(unsigned))

    wrong = AgentIdentity.generate("wrong")
    body = copy.deepcopy(assignment)
    body.pop("signature_b64")
    wrong_signed = {
        **body,
        "signature_b64": base64.b64encode(wrong.sign(_canonical(body))).decode("ascii"),
    }
    with pytest.raises(AssignmentVerificationError, match=r"signature|delivery-authorized"):
        broker.admit_raw(_canonical(wrong_signed))
    for raw in (b"prefix " + _canonical(assignment), _canonical(assignment) + b"\ncompleted", b"completed"):
        with pytest.raises(AssignmentVerificationError):
            broker.admit_raw(raw)


def test_unattested_transport_is_labeled_and_high_assurance_refuses_it(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    dispatch = _dispatch(env)
    assert dispatch.submit_result["transport_assurance"] == "transport_unattested"
    with pytest.raises(ValueError, match="transport_unattested"):
        GuardedSubmitConfig(
            sender="sender",
            receiver="receiver",
            keyring=AckKeyRing({"key": b"k" * 32}),
            key_id="key",
            ack_pipe=r"\\.\pipe\unattested",
            replay_path=tmp_path / "replay.sqlite3",
            event_log_path=tmp_path / "events.jsonl",
            high_assurance=True,
        )
