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
    ReceiverLaunchContext,
    RuntimeTrustRoot,
    SeatAssignmentBroker,
    SeatRevocationResolver,
    SelfConnectAssignmentReceiver,
    parse_assignment_ingress,
    provision_runtime_trust_root,
)
from sc_assignment_watchdog import AssignmentWatchdog
from sc_authority_trust import bootstrap_authority_trust
from sc_guarded_submit import AckKeyRing, TargetIdentity
from sc_identity import AgentIdentity
from sc_seat_identity import create_enrollment, key_id
from sc_seat_revocation import apply_revocation_snapshot, create_revocation_snapshot, sign_revocation_snapshot
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


def _environment(tmp_path, monkeypatch, *, submit_result=None, submit_error=None, attest_result=True):
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
                "ack": {"ack_sha256": "a" * 64},
            }
        )

    monkeypatch.setattr(runtime_module, "guarded_submit", submit)
    if attest_result:
        monkeypatch.setattr(
            runtime_module,
            "_verify_guarded_delivery_result",
            lambda _result, _wire, _config: {"ack_sha256": "a" * 64},
        )
    launch = AgentIdentity.generate("launch-provisioner")
    trust_path, revocation_path = tmp_path / "trust.json", tmp_path / "revocations.json"
    bootstrap_authority_trust(
        trust_path,
        root_public_keys=[authority.public_key_hex],
        quorum=1,
        recovery_public_keys=[authority.public_key_hex],
        recovery_quorum=1,
    )
    snapshot = create_revocation_snapshot(revocation_path, trust_path, [], now=NOW, ttl_seconds=300)
    apply_revocation_snapshot(
        revocation_path,
        trust_path,
        snapshot,
        [sign_revocation_snapshot(snapshot, authority)],
        now=NOW,
    )
    root_key = provision_runtime_trust_root(
        tmp_path / "runtime-root.json",
        tmp_path / "runtime-state.dpapi",
        provisioning_identity=launch,
        mailbox_path=tmp_path / "mailbox.sqlite3",
        dispatch_path=tmp_path / "dispatch.sqlite3",
        receiver_store_path=tmp_path / "seat.sqlite3",
        revocation_store_path=revocation_path,
        revocation_trust_path=trust_path,
    )
    trust_root = RuntimeTrustRoot(tmp_path / "runtime-root.json", pinned_public_key_hex=root_key)
    mailbox = DurableReceiptMailbox(channel_binding=channel, trust_root=trust_root)
    journal = DurableAssignmentJournal(trust_root=trust_root)
    resolver = SeatRevocationResolver(trust_root, clock=lambda: wall[0])
    coordinator_store = AssignmentStateStore(tmp_path / "coordinator.sqlite3")
    runtime = ProductionAssignmentRuntime(
        coordinator_identity=coordinator,
        coordinator_store=coordinator_store,
        receiver_enrollment=enrollment,
        bindings=bindings,
        trust_root=trust_root,
        mailbox=mailbox,
        assignment_journal=journal,
        revocation_resolver=resolver,
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
        "revocation_resolver": resolver,
        "revocation_path": revocation_path,
        "trust_path": trust_path,
        "wall": wall,
        "submitted": submitted,
        "mailbox": mailbox,
        "journal": journal,
        "coordinator_store": coordinator_store,
        "runtime": runtime,
        "launch": launch,
        "root_key": root_key,
        "trust_root": trust_root,
        "root_config_path": tmp_path / "runtime-root.json",
        "root_state_path": tmp_path / "runtime-state.dpapi",
    }


def _broker(env, path):
    return SeatAssignmentBroker(
        launch_context=ReceiverLaunchContext(
            trust_root=env["trust_root"],
            assignment_journal=env["journal"],
            mailbox=env["mailbox"],
            revocation_resolver=env["revocation_resolver"],
        ),
        seat_identity=env["seat"],
        bindings=env["bindings"],
        current_target=lambda: env["target"],
        current_terminal_tab=lambda: env["tab"],
        current_response_receiver_public_key=lambda: env["response_receiver"].public_key_hex,
        current_response_channel=lambda: env["channel"],
        now=lambda: env["wall"][0],
    )


def _dispatch(env):
    return env["runtime"].dispatch(PAYLOAD, now=NOW, ttl_seconds=60)


def _update_revocations(env, revoked):
    snapshot = create_revocation_snapshot(
        env["revocation_path"], env["trust_path"], revoked, now=env["wall"][0], ttl_seconds=300
    )
    apply_revocation_snapshot(
        env["revocation_path"],
        env["trust_path"],
        snapshot,
        [sign_revocation_snapshot(snapshot, env["authority"])],
        now=env["wall"][0],
    )


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


def test_forged_success_shape_without_durable_authenticated_ack_cannot_promote(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch, attest_result=False)
    with pytest.raises(AssignmentDispatchError, match="durable authenticated ACK"):
        _dispatch(env)
    record = parse_assignment_ingress(env["submitted"][0][0])
    assert env["journal"].state(record) == "quarantined"
    with pytest.raises(AssignmentVerificationError, match="delivery-authorized"):
        _broker(env, tmp_path).admit_raw(_canonical(record))


def test_live_revocation_snapshots_gate_issue_admit_emit_and_watchdog(tmp_path, monkeypatch):
    issue_env = _environment(tmp_path / "issue", monkeypatch)
    coordinator_id = key_id(issue_env["coordinator"].public_key_hex)
    _update_revocations(issue_env, [coordinator_id])
    with pytest.raises(AssignmentVerificationError, match="revoked"):
        _dispatch(issue_env)
    assert not issue_env["submitted"]

    env = _environment(tmp_path / "lifecycle", monkeypatch)
    dispatch = _dispatch(env)
    raw = _canonical(dispatch.assignment)
    coordinator_id = key_id(env["coordinator"].public_key_hex)
    seat_id = key_id(env["seat"].public_key_hex)
    _update_revocations(env, [seat_id])
    with pytest.raises(AssignmentVerificationError, match="revoked"):
        _broker(env, tmp_path / "revoked-admit").admit_raw(raw)

    env["wall"][0] = NOW + 2
    _update_revocations(env, [])
    broker = _broker(env, tmp_path / "active")
    broker.admit_raw(raw)
    env["wall"][0] = NOW + 3
    _update_revocations(env, [coordinator_id])
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
        verification_resolver=lambda: env["bindings"].verification(frozenset(), now=NOW + 2),
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
        **env["bindings"].verification(frozenset(), now=NOW + 2),
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
        verification_resolver=lambda: env["bindings"].verification(frozenset(), now=NOW + 52),
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
        **env["bindings"].verification(frozenset(), now=NOW + 2),
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
        **env["bindings"].verification(frozenset(), now=NOW + 2),
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


def test_sqlite_mutation_cannot_promote_quarantined_dispatch(tmp_path, monkeypatch):
    env = _environment(
        tmp_path, monkeypatch, submit_result={"ok": False, "state": "refused", "delivery_verified": False}
    )
    with pytest.raises(AssignmentDispatchError):
        _dispatch(env)
    record = parse_assignment_ingress(env["submitted"][0][0])
    with sqlite3.connect(env["journal"].path) as connection:
        row = connection.execute(
            "SELECT sequence,entry_json FROM assignment_dispatch_chain_v2 ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        forged = json.loads(bytes(row[1]))
        forged["state"] = "delivered"
        connection.execute(
            "UPDATE assignment_dispatch_chain_v2 SET entry_json=? WHERE sequence=?",
            (_canonical(forged), row[0]),
        )
    with pytest.raises(AssignmentVerificationError, match=r"integrity|signature"):
        env["journal"].require_delivered(record)


def test_mailbox_wholesale_replacement_fails_external_launch_anchor(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    _broker(env, tmp_path).admit_raw(_canonical(assignment))
    with sqlite3.connect(env["mailbox"].path) as connection:
        connection.execute("DELETE FROM receipt_mailbox_v1")
        connection.execute("DELETE FROM receipt_mailbox_head_v1")
        connection.execute("UPDATE receipt_mailbox_meta_v1 SET database_id='replacement-database'")
    with pytest.raises(AssignmentVerificationError, match=r"database ID|anchor"):
        DurableReceiptMailbox(channel_binding=env["channel"], trust_root=env["trust_root"])


def test_selfconnect_receiver_hook_routes_raw_only_through_broker(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    broker = _broker(env, tmp_path)
    receiver = SelfConnectAssignmentReceiver(broker, read_raw_assignment=lambda: _canonical(assignment))
    admission = receiver.serve_once()
    assert admission.assignment["assignment_id"] == assignment["assignment_id"]
    assert receiver.transport_assurance == "selfconnect_owned_sidecar_transport_unattested"
    with pytest.raises(ValueError, match="third-party TUI"):
        SelfConnectAssignmentReceiver(
            broker,
            read_raw_assignment=lambda: _canonical(assignment),
            selfconnect_owned_sidecar=False,
        )
    with pytest.raises(ValueError, match="attestation verifier"):
        SelfConnectAssignmentReceiver(
            broker,
            read_raw_assignment=lambda: _canonical(assignment),
            high_assurance=True,
        )


def test_replay_store_is_launch_pinned_and_fresh_store_cannot_readmit(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    broker = _broker(env, tmp_path)
    broker.admit_raw(_canonical(assignment))
    with pytest.raises(Exception, match="replay"):
        broker.admit_raw(_canonical(assignment))
    assert "store" not in __import__("inspect").signature(broker.admit_raw).parameters
    assert "store_path" not in __import__("inspect").signature(ReceiverLaunchContext).parameters
    fresh = tmp_path / "fresh.sqlite3"
    AssignmentStateStore(fresh)
    with pytest.raises(TypeError):
        env["trust_root"]._config["receiver_store_path"] = str(fresh)
    context = ReceiverLaunchContext(
        trust_root=env["trust_root"],
        assignment_journal=env["journal"],
        mailbox=env["mailbox"],
        revocation_resolver=env["revocation_resolver"],
    )
    assert context.store_path == env["trust_root"].path("receiver_store")


def test_ack_failure_keeps_cursor_for_restart_auto_recovery(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    _broker(env, tmp_path).admit_raw(_canonical(assignment))
    original = env["runtime"].recover_receipt_ack
    calls = []

    def fail_once(receipt):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("ACK outbox unavailable")
        return original(receipt)

    monkeypatch.setattr(env["runtime"], "recover_receipt_ack", fail_once)
    alerts = []
    watchdog, source = env["runtime"].watchdog(
        assignment,
        read_uia=lambda _hwnd: "working",
        read_ocr=lambda _hwnd: "",
        capture=lambda _hwnd: None,
        alert_coordinator=alerts.append,
        source_guard=lambda _source: True,
        target_guard=lambda _target: True,
        assignment_source="source",
        clock=lambda: 0.0,
    )
    first = watchdog.monitor(
        hwnd=env["target"].hwnd,
        assignment=assignment,
        assignment_source=source,
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert first.state == "blocked"
    restarted, source = env["runtime"].watchdog(
        assignment,
        read_uia=lambda _hwnd: "working",
        read_ocr=lambda _hwnd: "",
        capture=lambda _hwnd: None,
        alert_coordinator=lambda _event: None,
        source_guard=lambda _source: True,
        target_guard=lambda _target: True,
        assignment_source="source",
        clock=lambda: 0.0,
    )
    ticks = [0.0]
    restarted._clock = lambda: ticks[0]
    second = restarted.monitor(
        hwnd=env["target"].hwnd,
        assignment=assignment,
        assignment_source=source,
        timeout_seconds=0.2,
        poll_seconds=0.1,
        sleep=lambda value: ticks.__setitem__(0, ticks[0] + value),
    )
    assert second.state == "blocked" and len(calls) >= 2


def test_guard_after_ack_blocks_authenticated_return_and_preserves_cursor(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    broker = _broker(env, tmp_path)
    broker.admit_raw(_canonical(assignment))
    env["wall"][0] = NOW + 2
    broker.emit(assignment, state="working", detail={}, idempotency_key="working-after-guard")
    env["wall"][0] = NOW + 3
    broker.emit(
        assignment,
        state="completed",
        detail={},
        result_sha256=RESULT_HASH,
        idempotency_key="completed-after-guard",
    )
    checks = []
    watchdog, source = env["runtime"].watchdog(
        assignment,
        read_uia=lambda _hwnd: "completed",
        read_ocr=lambda _hwnd: "",
        capture=lambda _hwnd: None,
        alert_coordinator=lambda _event: None,
        source_guard=lambda _source: checks.append(True) or len(checks) < 5,
        target_guard=lambda _target: True,
        assignment_source="source",
        clock=lambda: 0.0,
    )
    result = watchdog.monitor(
        hwnd=env["target"].hwnd,
        assignment=assignment,
        assignment_source=source,
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert result.state == "blocked" and result.authenticated is False
    reader = env["mailbox"].reader(assignment, channel=env["channel"])
    assert reader()["sequence"] == 1
    restarted, source = env["runtime"].watchdog(
        assignment,
        read_uia=lambda _hwnd: "advisory only",
        read_ocr=lambda _hwnd: "",
        capture=lambda _hwnd: None,
        alert_coordinator=lambda _event: None,
        source_guard=lambda _source: True,
        target_guard=lambda _target: True,
        assignment_source="source",
        clock=lambda: 0.0,
    )
    recovered = restarted.monitor(
        hwnd=env["target"].hwnd,
        assignment=assignment,
        assignment_source=source,
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert recovered.state == "completed" and recovered.authenticated is True


def test_signed_revocation_resolver_rejects_nan_stale_and_replay(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    old = env["revocation_path"].read_bytes()
    env["wall"][0] = float("nan")
    with pytest.raises(AssignmentVerificationError, match="time"):
        env["revocation_resolver"]()
    env["wall"][0] = NOW + 301
    with pytest.raises(AssignmentVerificationError, match="stale"):
        env["revocation_resolver"]()
    env["wall"][0] = NOW + 2
    _update_revocations(env, [])
    env["revocation_resolver"]()
    env["revocation_path"].write_bytes(old)
    restarted = SeatRevocationResolver(env["trust_root"], clock=lambda: env["wall"][0])
    with pytest.raises(AssignmentVerificationError, match=r"replay|rollback"):
        restarted()


def test_runtime_root_consumer_has_no_signer_and_rejects_attacker_key(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    root = env["trust_root"]
    assert not hasattr(root, "identity")
    assert not hasattr(root, "sign")
    assert root.key_id == key_id(env["root_key"])
    attacker = AgentIdentity.generate("attacker-launch-root")
    with pytest.raises(AssignmentVerificationError, match="not pinned"):
        RuntimeTrustRoot(env["root_config_path"], pinned_public_key_hex=attacker.public_key_hex)


def test_runtime_root_is_provision_once_and_authorities_cannot_be_mixed(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    with pytest.raises(FileExistsError, match="already provisioned"):
        provision_runtime_trust_root(
            env["root_config_path"],
            env["root_state_path"],
            provisioning_identity=AgentIdentity.generate("attacker"),
            mailbox_path=tmp_path / "attacker-mailbox.sqlite3",
            dispatch_path=tmp_path / "attacker-dispatch.sqlite3",
            receiver_store_path=tmp_path / "attacker-seat.sqlite3",
            revocation_store_path=env["revocation_path"],
            revocation_trust_path=env["trust_path"],
        )

    other = _environment(tmp_path / "other", monkeypatch)
    with pytest.raises(AssignmentVerificationError, match="do not share"):
        ReceiverLaunchContext(
            trust_root=env["trust_root"],
            assignment_journal=other["journal"],
            mailbox=env["mailbox"],
            revocation_resolver=env["revocation_resolver"],
        )


def test_mailbox_and_protected_root_reset_cannot_reinitialize(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch)
    assignment = _dispatch(env).assignment
    _broker(env, tmp_path).admit_raw(_canonical(assignment))
    with sqlite3.connect(env["mailbox"].path) as connection:
        connection.execute("DELETE FROM receipt_mailbox_v1")
        connection.execute("DELETE FROM receipt_mailbox_head_v1")
        connection.execute("UPDATE receipt_mailbox_meta_v1 SET database_id='reset-database'")
    with pytest.raises(AssignmentVerificationError, match=r"database ID|anchor"):
        DurableReceiptMailbox(channel_binding=env["channel"], trust_root=env["trust_root"])
    env["root_state_path"].unlink()
    with pytest.raises(AssignmentVerificationError, match="state is absent"):
        RuntimeTrustRoot(env["root_config_path"], pinned_public_key_hex=env["root_key"])
