from __future__ import annotations

import time

from sc_assignment_protocol import (
    AssignmentStateStore,
    admit_assignment,
    create_assignment,
    create_state_receipt,
)
from sc_assignment_watchdog import AssignmentWatchdog
from sc_guarded_submit import TargetIdentity
from sc_identity import AgentIdentity
from sc_seat_identity import create_enrollment
from sc_terminal_tab import RUNTIME_ID_SCOPE, TerminalTabIdentity

PAYLOAD = "canonical watchdog assignment"
NOW = 2_000_000_000.0


def _case(tmp_path):
    authority = AgentIdentity.generate("authority")
    coordinator = AgentIdentity.generate("coordinator")
    seat = AgentIdentity.generate("seat")
    response_receiver = AgentIdentity.generate("response-receiver")
    enrollment = create_enrollment(
        seat_identity=seat, authority_identity=authority, birth_id="seat-birth-1", generation=3, now=NOW
    )
    coordinator_store = AssignmentStateStore(tmp_path / "coordinator.sqlite3")
    seat_store = AssignmentStateStore(tmp_path / "seat.sqlite3")
    target = TargetIdentity(
        hwnd=123,
        pid=456,
        exe_name="WindowsTerminal.exe",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        title="seat",
        exe_path=r"C:\WindowsTerminal.exe",
        process_start_time_ns=789,
    )
    tab = TerminalTabIdentity(
        window_hwnd=123,
        window_pid=456,
        window_process_start_time_ns=789,
        tab_runtime_id=(1, 2),
        term_control_runtime_id=(1, 3),
        peer_birth_id="seat-birth-1",
        runtime_id_scope=RUNTIME_ID_SCOPE,
    )
    channel = {
        "transport": "private_named_pipe_v1",
        "pipe_instance": r"\\.\pipe\watchdog-test",
        "server_nonce": "b" * 64,
    }
    assignment = create_assignment(
        PAYLOAD,
        coordinator_identity=coordinator,
        coordinator_birth_id="codex-12",
        coordinator_generation=2,
        receiver_enrollment=enrollment,
        authority_public_key_hex=authority.public_key_hex,
        target_identity=target,
        terminal_tab_identity=tab,
        response_receiver_public_key_hex=response_receiver.public_key_hex,
        response_channel=channel,
        store=coordinator_store,
        now=NOW,
    )
    verification = {
        "pinned_coordinator_public_key_hex": coordinator.public_key_hex,
        "authority_public_key_hex": authority.public_key_hex,
        "expected_coordinator_birth_id": "codex-12",
        "expected_coordinator_generation": 2,
        "expected_receiver_birth_id": "seat-birth-1",
        "expected_receiver_generation": 3,
        "expected_target_identity": target,
        "expected_terminal_tab_identity": tab,
        "expected_response_receiver_public_key_hex": response_receiver.public_key_hex,
        "expected_response_channel": channel,
        "now": NOW + 1,
    }
    seat._test_receipt_kwargs = {
        "authority_public_key_hex": authority.public_key_hex,
        "current_target_identity": target,
        "current_terminal_tab_identity": tab,
        "current_response_receiver_public_key_hex": response_receiver.public_key_hex,
        "current_response_channel": channel,
    }
    admit_assignment(assignment, store=seat_store, **verification)
    return seat, coordinator_store, seat_store, assignment, verification


def _watch(tmp_path, receipts, *, uia="Working", source=True, target=True, capture=None, alerts=None, clock=None):
    seat, coordinator_store, seat_store, assignment, verification = _case(tmp_path)
    queue = list(receipts)
    alerts = [] if alerts is None else alerts
    watch = AssignmentWatchdog(
        store=coordinator_store,
        receipt_reader=lambda _hwnd: queue.pop(0) if queue else None,
        read_uia=lambda _hwnd: uia,
        read_ocr=lambda _hwnd: "",
        capture=(capture or (lambda hwnd: {"hwnd": hwnd, "proof": "capture"})),
        alert_coordinator=alerts.append,
        source_guard=lambda _source: source,
        target_guard=lambda _hwnd: target,
        verification=verification,
        clock=clock or time.monotonic,
    )
    return seat, seat_store, assignment, watch, alerts, queue


def _receipts(seat, store, assignment, states):
    return [
        create_state_receipt(
            assignment,
            seat_identity=seat,
            state=state,
            detail={"state": state},
            result_sha256="a" * 64 if state == "completed" else None,
            idempotency_key=f"{state}-{index}",
            store=store,
            now=NOW + index,
            **seat._test_receipt_kwargs,
        )
        for index, state in enumerate(states, 1)
    ]


def test_end_to_end_canonical_receipts_drive_watchdog(tmp_path):
    seat, seat_store, assignment, watch, alerts, queue = _watch(tmp_path, [])
    queue.extend(_receipts(seat, seat_store, assignment, ("accepted", "working", "completed")))
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert result.state == "completed" and result.authenticated and not alerts


def test_screen_refusal_is_advisory_until_authenticated_completion(tmp_path):
    seat, seat_store, assignment, watch, alerts, queue = _watch(tmp_path, [], uia="status: refused")
    queue.extend(_receipts(seat, seat_store, assignment, ("accepted", "working", "completed")))
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert result.state == "completed" and not alerts


def test_blocked_receipt_captures_and_alerts_before_return(tmp_path):
    seat, seat_store, assignment, watch, alerts, queue = _watch(tmp_path, [])
    queue.extend(_receipts(seat, seat_store, assignment, ("accepted", "blocked")))
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
    )
    assert result.state == "blocked" and alerts and alerts[0]["state"] == "blocked"


def test_rejected_receipt_captures_and_alerts_before_return(tmp_path):
    seat, seat_store, assignment, watch, alerts, queue = _watch(tmp_path, [])
    queue.extend(_receipts(seat, seat_store, assignment, ("accepted", "rejected")))
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
    )
    assert result.state == "refused" and alerts and alerts[0]["state"] == "refused"


def test_malformed_receipt_fails_closed_and_alerts(tmp_path):
    _, _, assignment, watch, alerts, queue = _watch(tmp_path, [])
    queue.append(["not-a-receipt"])
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
    )
    assert result.state == "blocked" and alerts[0]["reason"].startswith("protocol_failure")


def test_capture_error_still_alerts_on_timeout(tmp_path):
    now = [0.0]
    _, _, assignment, watch, alerts, _ = _watch(
        tmp_path, [], capture=lambda _hwnd: (_ for _ in ()).throw(RuntimeError("capture down")), clock=lambda: now[0]
    )
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: now.__setitem__(0, 2.0),
    )
    assert result.state == "blocked" and alerts[0]["state"] == "blocked"
    assert alerts[0]["reason"] == "timeout" and "capture_error" in alerts[0]


def test_timeout_alert_is_blocked_timeout(tmp_path):
    now = [0.0]
    _, _, assignment, watch, alerts, _ = _watch(tmp_path, [], clock=lambda: now[0])
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: now.__setitem__(0, 2.0),
    )
    assert result.state == "blocked" and alerts[0]["state"] == "blocked"
    assert alerts[0]["reason"] == "timeout"


def test_source_and_target_guard_run_every_poll_and_fail_closed(tmp_path):
    seat, seat_store, assignment, watch, alerts, queue = _watch(tmp_path, [])
    queue.extend(_receipts(seat, seat_store, assignment, ("accepted", "working", "completed")))
    counts = {"source": 0, "target": 0}
    watch._source_guard = lambda _source: counts.__setitem__("source", counts["source"] + 1) or True
    watch._target_guard = lambda _hwnd: counts.__setitem__("target", counts["target"] + 1) or True
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: None,
    )
    assert result.state == "completed" and counts["source"] >= 3 and counts["target"] >= 3

    watch._target_guard = lambda _hwnd: False
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
    )
    assert result.state == "blocked" and alerts


def test_receipt_replay_and_order_are_durable_per_assignment(tmp_path):
    seat, seat_store, assignment, watch, alerts, queue = _watch(tmp_path, [])
    accepted = _receipts(seat, seat_store, assignment, ("accepted",))[0]
    queue.append(accepted)
    queue.append(accepted)
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
    )
    assert result.state == "blocked" and alerts


def test_no_static_receipt_or_screen_can_complete(tmp_path):
    _, _, assignment, watch, alerts, _ = _watch(tmp_path, [], uia="completed")
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=0.01,
        poll_seconds=0.005,
    )
    assert result.state == "blocked" and alerts


def test_spinner_and_historical_refusal_are_never_live_completion_or_refusal():
    spinner = AssignmentWatchdog.classify_screen("Worked for 2m\n\u2713 \u2022 277ms", source="uia")
    history = AssignmentWatchdog.classify_screen("Earlier I cannot continue\nstatus: working", source="ocr")
    assert spinner.state == "submitted" and not spinner.authenticated
    assert history.state == "working" and not history.authenticated


def test_unreadable_screen_is_blind_blocked_evidence(tmp_path):
    _, _, assignment, watch, alerts, _ = _watch(tmp_path, [])
    watch._read_uia = lambda _hwnd: (_ for _ in ()).throw(RuntimeError("uia down"))
    watch._read_ocr = lambda _hwnd: (_ for _ in ()).throw(RuntimeError("ocr down"))
    now = [0.0]
    watch._clock = lambda: now[0]
    result = watch.monitor(
        hwnd=123,
        assignment=assignment,
        payload=PAYLOAD,
        assignment_source="trusted",
        timeout_seconds=1,
        poll_seconds=0.1,
        sleep=lambda _seconds: now.__setitem__(0, 2.0),
    )
    assert result.state == "blocked" and alerts
    assert alerts[0]["screen_evidence"].state == "blocked"
