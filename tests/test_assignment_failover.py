from __future__ import annotations

import base64
import copy
import json

import pytest
import sc_mesh_registry
from sc_assignment_failover import AssignmentFailoverError, failover_assignment
from sc_assignment_protocol import (
    AssignmentStateStore,
    emit_state_receipt,
    issue_assignment,
    verify_consume_assignment,
    verify_consume_state_receipt,
)
from sc_guarded_submit import TargetIdentity
from sc_identity import AgentIdentity
from sc_seat_identity import create_enrollment
from sc_terminal_tab import RUNTIME_ID_SCOPE, TerminalTabIdentity

NOW = 2_100_000_000.0


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _target(hwnd, pid, started, title):
    return TargetIdentity(
        hwnd=hwnd,
        pid=pid,
        exe_name="WindowsTerminal.exe",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        title=title,
        exe_path=r"C:\Program Files\WindowsApps\WindowsTerminal.exe",
        process_start_time_ns=started,
    )


def _tab(hwnd, pid, started, birth, runtime):
    return TerminalTabIdentity(
        window_hwnd=hwnd,
        window_pid=pid,
        window_process_start_time_ns=started,
        tab_runtime_id=runtime,
        term_control_runtime_id=(91, runtime[-1]),
        peer_birth_id=birth,
        runtime_id_scope=RUNTIME_ID_SCOPE,
    )


def _case(
    tmp_path, *, replacement_generation=4, replacement_seat=None, receipt_state="blocked"
):
    authority = AgentIdentity.generate("authority")
    coordinator = AgentIdentity.generate("coordinator")
    old_seat = AgentIdentity.generate("old-seat")
    new_seat = replacement_seat or AgentIdentity.generate("new-seat")
    old_response = AgentIdentity.generate("old-response")
    new_response = AgentIdentity.generate("new-response")
    old_enrollment = create_enrollment(
        seat_identity=old_seat,
        authority_identity=authority,
        birth_id="seat-old-birth",
        generation=3,
        now=NOW,
    )
    new_enrollment = create_enrollment(
        seat_identity=new_seat,
        authority_identity=authority,
        birth_id="seat-new-birth",
        generation=replacement_generation,
        now=NOW,
    )
    old_target = _target(101, 201, 301, "old seat")
    old_tab = _tab(101, 201, 301, "seat-old-birth", (41, 1))
    new_target = _target(102, 202, 302, "new seat")
    new_tab = _tab(102, 202, 302, "seat-new-birth", (42, 2))
    old_channel = {"transport": "private_named_pipe_v1", "nonce": "a" * 64}
    new_channel = {"transport": "private_named_pipe_v1", "nonce": "b" * 64}
    coordinator_store = AssignmentStateStore(tmp_path / "coordinator.sqlite3")
    seat_store = AssignmentStateStore(tmp_path / "old-seat.sqlite3")
    assignment = issue_assignment(
        "continue the exact signed task on a fresh enrolled seat",
        coordinator_identity=coordinator,
        coordinator_birth_id="coordinator-birth",
        coordinator_generation=7,
        receiver_enrollment=old_enrollment,
        authority_public_key_hex=authority.public_key_hex,
        target_identity=old_target,
        terminal_tab_identity=old_tab,
        response_receiver_public_key_hex=old_response.public_key_hex,
        response_channel=old_channel,
        store=coordinator_store,
        now=NOW,
    )
    old_verification = {
        "pinned_coordinator_public_key_hex": coordinator.public_key_hex,
        "authority_public_key_hex": authority.public_key_hex,
        "expected_coordinator_birth_id": "coordinator-birth",
        "expected_coordinator_generation": 7,
        "expected_receiver_birth_id": "seat-old-birth",
        "expected_receiver_generation": 3,
        "expected_target_identity": old_target,
        "expected_terminal_tab_identity": old_tab,
        "expected_response_receiver_public_key_hex": old_response.public_key_hex,
        "expected_response_channel": old_channel,
    }
    verify_consume_assignment(
        assignment, store=seat_store, now=NOW + 1, **old_verification
    )
    accepted = emit_state_receipt(
        assignment,
        seat_identity=old_seat,
        authority_public_key_hex=authority.public_key_hex,
        current_target_identity=old_target,
        current_terminal_tab_identity=old_tab,
        current_response_receiver_public_key_hex=old_response.public_key_hex,
        current_response_channel=old_channel,
        state="accepted",
        detail={"accepted": True},
        result_sha256=None,
        idempotency_key="accepted-1",
        store=seat_store,
        now=NOW + 2,
    )
    verify_consume_state_receipt(
        accepted,
        assignment,
        store=coordinator_store,
        now=NOW + 2,
        **old_verification,
    )
    blocked = emit_state_receipt(
        assignment,
        seat_identity=old_seat,
        authority_public_key_hex=authority.public_key_hex,
        current_target_identity=old_target,
        current_terminal_tab_identity=old_tab,
        current_response_receiver_public_key_hex=old_response.public_key_hex,
        current_response_channel=old_channel,
        state=receipt_state,
        detail={"reason": f"authenticated worker {receipt_state}"},
        result_sha256=None,
        idempotency_key="blocked-2",
        store=seat_store,
        now=NOW + 3,
    )
    registry_path = tmp_path / "mesh_registry.json"
    registered = sc_mesh_registry.register_virtual_agent(
        "worker",
        mesh="test",
        status="active",
        birth_id="seat-old-birth",
        generation=3,
        registry_path=registry_path,
    )
    assert registered["ok"] is True
    return {
        "authority": authority,
        "coordinator": coordinator,
        "old_seat": old_seat,
        "new_seat": new_seat,
        "new_response": new_response,
        "new_enrollment": new_enrollment,
        "new_target": new_target,
        "new_tab": new_tab,
        "new_channel": new_channel,
        "assignment": assignment,
        "blocked": blocked,
        "store": coordinator_store,
        "registry_path": registry_path,
        "verification": old_verification,
    }


def _delivery_receipt(assignment):
    return {
        "ok": True,
        "state": "delivered",
        **{
            field: assignment[field]
            for field in (
                "assignment_id",
                "receiver_birth_id",
                "receiver_generation",
                "receiver_key_id",
                "receiver_seat_epoch",
                "target_identity_sha256",
                "terminal_tab_identity_sha256",
                "response_channel_sha256",
            )
        },
    }


def _run(case, **overrides):
    calls = overrides.pop("calls", {"audit": [], "guard": [], "delivery": []})
    receipt = overrides.pop("receipt", case["blocked"])

    def audit(event_type, **kwargs):
        calls["audit"].append((event_type, kwargs))
        return {"ok": True, "event": {"event_type": event_type}}

    def guard(**kwargs):
        calls["guard"].append(kwargs)
        return True

    def deliver(**kwargs):
        calls["delivery"].append(kwargs)
        return _delivery_receipt(kwargs["assignment"])

    args = {
        "role": "worker",
        "mesh": "test",
        "coordinator_identity": case["coordinator"],
        "authority_public_key_hex": case["authority"].public_key_hex,
        "replacement_enrollment": case["new_enrollment"],
        "replacement_target_identity": case["new_target"],
        "replacement_terminal_tab_identity": case["new_tab"],
        "replacement_response_receiver_public_key_hex": case["new_response"].public_key_hex,
        "replacement_response_channel": case["new_channel"],
        "store": case["store"],
        "registry_path": case["registry_path"],
        "exact_target_guard": guard,
        "guarded_delivery": deliver,
        "audit_append": audit,
        "now": NOW + 3,
        **case["verification"],
        **overrides,
    }
    return failover_assignment(case["assignment"], receipt, **args), calls


def test_authenticated_blocked_receipt_transitions_exact_old_seat_and_delivers_fresh_assignment(tmp_path):
    case = _case(tmp_path)
    result, calls = _run(case)

    assert result["ok"] is True
    assert result["process_action"] == "none"
    assert result["old_seat"]["birth_id"] == "seat-old-birth"
    assert result["replacement_seat"]["birth_id"] == "seat-new-birth"
    assert result["replacement_assignment"]["receiver_generation"] == 4
    assert [item["stage"] for item in calls["guard"]] == [
        "before_authorization",
        "before_delivery",
    ]
    assert len(calls["delivery"]) == 1
    event_type, audit = calls["audit"][0]
    assert event_type == "blocked"
    assert audit["strict"] is True
    assert audit["birth_id"] == "seat-old-birth"
    assert audit["generation"] == 3
    assert audit["data"]["old_seat_key_id"] == case["blocked"]["receiver_key_id"]
    assert audit["data"]["process_action"] == "none"
    registry = sc_mesh_registry.load_registry_strict(case["registry_path"])
    row = registry["agents"][0]
    assert row["status"] == "off_rails"
    assert row["off_rails_identity"] == result["old_seat"]


def test_authenticated_rejected_receipt_uses_real_strict_audit_log(tmp_path):
    case = _case(tmp_path, receipt_state="rejected")
    result, _calls = _run(case, audit_append=sc_mesh_registry.append_event)

    assert result["ok"] is True
    verification = sc_mesh_registry.verify_events(registry_path=case["registry_path"])
    assert verification["ok"] is True
    events = sc_mesh_registry.load_events(
        event_type="blocked", registry_path=case["registry_path"]
    )["events"]
    assert len(events) == 1
    assert events[0]["data"]["receipt_state"] == "rejected"
    assert events[0]["data"]["process_action"] == "none"


def test_forged_noncanonical_stale_replayed_and_wrong_seat_receipts_fail_before_audit(tmp_path):
    variants = []

    forged_case = _case(tmp_path / "forged")
    forged = copy.deepcopy(forged_case["blocked"])
    forged["state"] = "rejected"
    variants.append((forged_case, forged, NOW + 3))

    noncanonical_case = _case(tmp_path / "noncanonical")
    noncanonical = json.dumps(noncanonical_case["blocked"], indent=2)
    variants.append((noncanonical_case, noncanonical, NOW + 3))

    stale_case = _case(tmp_path / "stale")
    variants.append((stale_case, stale_case["blocked"], NOW + 100))

    wrong_case = _case(tmp_path / "wrong")
    wrong_identity = AgentIdentity.generate("wrong-seat")
    wrong = copy.deepcopy(wrong_case["blocked"])
    body = dict(wrong)
    body.pop("signature_b64")
    body["receiver_key_id"] = "f" * 64
    wrong = {
        **body,
        "signature_b64": base64.b64encode(wrong_identity.sign(_canonical(body))).decode(),
    }
    variants.append((wrong_case, wrong, NOW + 3))

    for case, receipt, verification_time in variants:
        audits = []
        with pytest.raises(AssignmentFailoverError):
            _run(
                case,
                receipt=receipt,
                audit_append=lambda *args, sink=audits, **kwargs: sink.append((args, kwargs)),
                now=verification_time,
            )
        assert audits == []
        assert sc_mesh_registry.load_registry_strict(case["registry_path"])["agents"][0]["status"] == "active"

    replay_case = _case(tmp_path / "replay")
    _run(replay_case)
    with pytest.raises(AssignmentFailoverError, match="replay"):
        _run(replay_case)


@pytest.mark.parametrize("failure", ["birth", "generation", "key", "epoch"])
def test_replacement_must_have_fresh_birth_higher_generation_and_distinct_key(tmp_path, failure):
    case = _case(tmp_path)
    enrollment = copy.deepcopy(case["new_enrollment"])
    body = dict(enrollment)
    body.pop("authority_signature_b64")
    if failure == "birth":
        body["birth_id"] = "seat-old-birth"
    elif failure == "generation":
        body["generation"] = 3
    elif failure == "key":
        body["seat_public_key_hex"] = case["old_seat"].public_key_hex
        body["seat_key_id"] = case["blocked"]["receiver_key_id"]
    else:
        body["seat_epoch"] = case["blocked"]["receiver_seat_epoch"]
    enrollment = {
        **body,
        "authority_signature_b64": base64.b64encode(
            case["authority"].sign(_canonical(body))
        ).decode(),
    }
    audits = []
    with pytest.raises(AssignmentFailoverError):
        _run(case, replacement_enrollment=enrollment, audit_append=lambda *args, **kwargs: audits.append((args, kwargs)))
    assert audits == []


def test_registry_birth_drift_audit_failure_and_target_guard_failure_fail_closed(tmp_path):
    guard_case = _case(tmp_path / "guard")
    audits = []
    with pytest.raises(AssignmentFailoverError, match="guard refused"):
        _run(
            guard_case,
            exact_target_guard=lambda **kwargs: False,
            audit_append=lambda *args, **kwargs: audits.append((args, kwargs)),
        )
    assert audits == []

    audit_case = _case(tmp_path / "audit")
    transitions = []
    with pytest.raises(AssignmentFailoverError, match="audit append failed"):
        _run(
            audit_case,
            audit_append=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")),
            registry_transition=lambda *args, **kwargs: transitions.append((args, kwargs)),
        )
    assert transitions == []
    assert sc_mesh_registry.load_registry_strict(audit_case["registry_path"])["agents"][0]["status"] == "active"

    drift_case = _case(tmp_path / "drift")
    registry = json.loads(drift_case["registry_path"].read_text(encoding="utf-8"))
    registry["agents"][0]["birth_id"] = "seat-drifted-birth"
    drift_case["registry_path"].write_text(json.dumps(registry), encoding="utf-8")
    delivered = []
    with pytest.raises(AssignmentFailoverError, match="registry birth drift"):
        _run(
            drift_case,
            guarded_delivery=lambda **kwargs: delivered.append(kwargs),
        )
    assert delivered == []
    assert sc_mesh_registry.load_registry_strict(drift_case["registry_path"])["agents"][0]["status"] == "active"


def test_second_guard_or_delivery_binding_failure_never_targets_a_process(tmp_path):
    case = _case(tmp_path / "second-guard")
    stages = []

    def guard(**kwargs):
        stages.append(kwargs["stage"])
        return kwargs["stage"] != "before_delivery"

    deliveries = []
    with pytest.raises(AssignmentFailoverError, match="before_delivery"):
        _run(case, exact_target_guard=guard, guarded_delivery=lambda **kwargs: deliveries.append(kwargs))
    assert stages == ["before_authorization", "before_delivery"]
    assert deliveries == []
    assert sc_mesh_registry.load_registry_strict(case["registry_path"])["agents"][0]["status"] == "off_rails"

    delivery_case = _case(tmp_path / "delivery")
    with pytest.raises(AssignmentFailoverError, match="exact binding"):
        _run(
            delivery_case,
            guarded_delivery=lambda **kwargs: {
                **_delivery_receipt(kwargs["assignment"]),
                "receiver_birth_id": "wrong-birth",
            },
        )
    assert sc_mesh_registry.load_registry_strict(delivery_case["registry_path"])["agents"][0]["status"] == "off_rails"
