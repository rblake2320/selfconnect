from __future__ import annotations

import inspect
import json

import pytest
import sc_assignment_runtime as runtime_module
from sc_assignment_protocol import (
    AssignmentReplayError,
    AssignmentStateStore,
    AssignmentVerificationError,
)
from sc_assignment_runtime import (
    AssignmentBindings,
    DurableAssignmentJournal,
    DurableReceiptMailbox,
    GuardedSubmitConfig,
    ProductionAssignmentRuntime,
    ReceiverLaunchContext,
    RuntimeTrustRoot,
    SeatAssignmentBroker,
    SeatRevocationResolver,
    provision_runtime_trust_root,
)
from sc_authority_trust import bootstrap_authority_trust
from sc_guarded_submit import AckKeyRing, TargetIdentity
from sc_identity import AgentIdentity
from sc_seat_identity import create_enrollment
from sc_seat_revocation import apply_revocation_snapshot, create_revocation_snapshot, sign_revocation_snapshot
from sc_terminal_tab import RUNTIME_ID_SCOPE, TerminalTabGuard, TerminalTabIdentity

NOW = 2_000_000_000.0
PAYLOAD = "execute only the signed inline assignment"
RESULT_HASH = "a" * 64


def _target(*, hwnd=101):
    return TargetIdentity(
        hwnd=hwnd,
        pid=202,
        exe_name="WindowsTerminal.exe",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        title="seat",
        exe_path=r"C:\Program Files\WindowsApps\WindowsTerminal.exe",
        process_start_time_ns=303,
    )


def _tab(*, hwnd=101, runtime=(42, 7)):
    return TerminalTabIdentity(
        window_hwnd=hwnd,
        window_pid=202,
        window_process_start_time_ns=303,
        tab_runtime_id=runtime,
        term_control_runtime_id=(99, 3),
        peer_birth_id="seat-birth-1",
        runtime_id_scope=RUNTIME_ID_SCOPE,
    )


def _case(tmp_path, monkeypatch):
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
        coordinator_birth_id="codex-12-4abf6b40",
        coordinator_generation=7,
        receiver_birth_id="seat-birth-1",
        receiver_generation=3,
        target_identity=target,
        terminal_tab_identity=tab,
        response_receiver_public_key_hex=response_receiver.public_key_hex,
        response_channel=channel,
    )
    launch = AgentIdentity.generate("launch")
    submitted = []

    def fake_guarded_submit(text, **kwargs):
        submitted.append((text, kwargs))
        return {
            "ok": True,
            "state": "acknowledged",
            "delivery_verified": True,
            "peer_acknowledged": True,
            "decision": "accepted",
            "ack": {"ack_sha256": "a" * 64},
        }

    monkeypatch.setattr(runtime_module, "guarded_submit", fake_guarded_submit)
    monkeypatch.setattr(
        runtime_module,
        "_verify_guarded_delivery_result",
        lambda _result, _wire, _config: {"ack_sha256": "a" * 64},
    )
    guard = TerminalTabGuard(tab, None, None, None)
    coordinator_store = AssignmentStateStore(tmp_path / "coordinator.sqlite3")
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
        mailbox_path=tmp_path / "receipts.sqlite3",
        dispatch_path=tmp_path / "dispatch.sqlite3",
        receiver_store_path=tmp_path / "seat.sqlite3",
        revocation_store_path=revocation_path,
        revocation_trust_path=trust_path,
    )
    trust_root = RuntimeTrustRoot(tmp_path / "runtime-root.json", pinned_public_key_hex=root_key)
    mailbox = DurableReceiptMailbox(channel_binding=channel, trust_root=trust_root)
    assignment_journal = DurableAssignmentJournal(trust_root=trust_root)
    revocation_resolver = SeatRevocationResolver(trust_root, clock=lambda: NOW + 4)
    coordinator_runtime = ProductionAssignmentRuntime(
        coordinator_identity=coordinator,
        coordinator_store=coordinator_store,
        receiver_enrollment=enrollment,
        bindings=bindings,
        trust_root=trust_root,
        mailbox=mailbox,
        assignment_journal=assignment_journal,
        revocation_resolver=revocation_resolver,
        terminal_tab_guard=guard,
        submit_config=GuardedSubmitConfig(
            sender="codex-12-4abf6b40",
            receiver="seat-birth-1",
            keyring=AckKeyRing({"ack-key": b"k" * 32}),
            key_id="ack-key",
            ack_pipe=r"\\.\pipe\selfconnect-test",
            replay_path=tmp_path / "ack.sqlite3",
            event_log_path=tmp_path / "events.jsonl",
        ),
        wall_clock=lambda: NOW + 4,
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
        "mailbox": mailbox,
        "submitted": submitted,
        "coordinator_store": coordinator_store,
        "assignment_journal": assignment_journal,
        "revocation_resolver": revocation_resolver,
        "launch": launch,
        "trust_root": trust_root,
        "runtime": coordinator_runtime,
    }


def _broker(case, tmp_path, wall, **providers):
    return SeatAssignmentBroker(
        launch_context=ReceiverLaunchContext(
            trust_root=case["trust_root"],
            assignment_journal=case["assignment_journal"],
            mailbox=case["mailbox"],
            revocation_resolver=case["revocation_resolver"],
        ),
        seat_identity=case["seat"],
        bindings=case["bindings"],
        current_target=providers.get("current_target", lambda: case["target"]),
        current_terminal_tab=providers.get("current_terminal_tab", lambda: case["tab"]),
        current_response_receiver_public_key=providers.get(
            "current_response_receiver_public_key",
            lambda: case["response_receiver"].public_key_hex,
        ),
        current_response_channel=providers.get("current_response_channel", lambda: case["channel"]),
        now=lambda: wall[0],
    )


def test_signed_inline_dispatch_broker_and_dynamic_watchdog_end_to_end(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    dispatch = case["runtime"].dispatch(PAYLOAD, now=NOW, ttl_seconds=60)
    assert dispatch.submit_result["delivery_verified"] is True
    assert len(case["submitted"]) == 1
    submitted_record = json.loads(case["submitted"][0][0])
    assert submitted_record == dispatch.assignment
    assert case["submitted"][0][0] != PAYLOAD
    assert submitted_record["payload"] == PAYLOAD
    assert case["submitted"][0][1]["target"] == case["target"]
    assert case["submitted"][0][1]["terminal_tab_guard"].identity == case["tab"]

    wall = [NOW + 1]
    broker = _broker(case, tmp_path, wall)
    admission = broker.admit_raw(json.dumps(dispatch.assignment, sort_keys=True, separators=(",", ":")))
    assert admission.assignment["payload"] == PAYLOAD
    assert admission.accepted_receipt["state"] == "accepted"
    wall[0] = NOW + 2
    broker.emit(
        dispatch.assignment,
        state="working",
        detail={"phase": "running"},
        idempotency_key="working-1",
    )
    wall[0] = NOW + 3
    broker.emit(
        dispatch.assignment,
        state="completed",
        detail={"phase": "done"},
        result_sha256=RESULT_HASH,
        idempotency_key="completed-1",
    )

    target_checks = []
    watchdog, source = case["runtime"].watchdog(
        dispatch.assignment,
        read_uia=lambda _hwnd: "Worked for 1m 02s\n✓ • 277ms",
        read_ocr=lambda _hwnd: "",
        capture=lambda _hwnd: None,
        alert_coordinator=lambda _event: pytest.fail("unexpected watchdog alert"),
        source_guard=lambda value: value == "signed-terminal-channel",
        target_guard=lambda value: target_checks.append(value) or value == case["target"],
        assignment_source="signed-terminal-channel",
        clock=lambda: 0.0,
    )
    result = watchdog.monitor(
        hwnd=case["target"].hwnd,
        assignment=dispatch.assignment,
        assignment_source=source,
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert result.state == "completed" and result.authenticated is True
    assert result.receipt["result_sha256"] == RESULT_HASH
    assert len(target_checks) == 15 and all(item == case["target"] for item in target_checks)
    ack = broker.consume_receipt_ack(dispatch.assignment, admission.accepted_receipt)
    assert ack is not None and ack["state"] == "accepted"


def test_dynamic_reader_observes_receipt_published_after_initial_empty_read(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    assignment = case["runtime"].dispatch(PAYLOAD, now=NOW).assignment
    reader = case["mailbox"].reader(assignment, channel=case["channel"])
    assert reader() is None
    admission = _broker(case, tmp_path, [NOW + 1]).admit_raw(
        json.dumps(assignment, sort_keys=True, separators=(",", ":"))
    )
    receipt = reader()
    assert receipt == admission.accepted_receipt
    reader.acknowledge(receipt)
    assert reader() is None


def test_live_target_response_key_and_channel_mismatches_fail_before_admission(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    assignment = case["runtime"].dispatch(PAYLOAD, now=NOW).assignment
    wrong_response = AgentIdentity.generate("wrong-response")
    cases = (
        {"current_target": lambda: _target(hwnd=909)},
        {"current_terminal_tab": lambda: _tab(runtime=(42, 8))},
        {"current_response_receiver_public_key": lambda: wrong_response.public_key_hex},
        {
            "current_response_channel": lambda: {
                **case["channel"],
                "receiver_nonce": "c" * 64,
            }
        },
    )
    for index, providers in enumerate(cases):
        with pytest.raises(AssignmentVerificationError):
            _broker(case, tmp_path / str(index), [NOW + 1], **providers).admit_raw(
                json.dumps(assignment, sort_keys=True, separators=(",", ":"))
            )


def test_mailbox_fork_and_forged_receipt_never_authenticate(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    assignment = case["runtime"].dispatch(PAYLOAD, now=NOW).assignment
    admission = _broker(case, tmp_path, [NOW + 1]).admit_raw(
        json.dumps(assignment, sort_keys=True, separators=(",", ":"))
    )
    fork = json.loads(json.dumps(admission.accepted_receipt))
    fork["detail"] = {"forged": True}
    with pytest.raises(AssignmentReplayError, match="fork"):
        case["mailbox"].publish(fork, channel=case["channel"])

    forged = json.loads(json.dumps(admission.accepted_receipt))
    forged["signature_b64"] = "Zm9yZ2Vk"
    alerts = []
    watchdog = runtime_module.AssignmentWatchdog(
        store=case["coordinator_store"],
        receipt_reader=lambda: forged,
        read_uia=lambda _hwnd: "completed",
        read_ocr=lambda _hwnd: "",
        capture=lambda _hwnd: None,
        alert_coordinator=alerts.append,
        source_guard=lambda _source: True,
        target_guard=lambda target: target == case["target"],
        verification_resolver=lambda: case["bindings"].verification(frozenset(), now=NOW + 2),
        clock=lambda: 0.0,
    )
    result = watchdog.monitor(
        hwnd=case["target"].hwnd,
        assignment=assignment,
        assignment_source="channel",
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert result.state == "blocked" and result.authenticated is False
    assert alerts[0]["reason"].startswith("protocol_failure")


def test_runtime_surface_has_no_external_receiver_payload_authority():
    assert "payload" not in inspect.signature(SeatAssignmentBroker.admit_raw).parameters
    assert "payload" not in inspect.signature(runtime_module.AssignmentWatchdog.monitor).parameters
