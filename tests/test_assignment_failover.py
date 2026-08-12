from __future__ import annotations

import base64
import copy
import inspect
import json
import shutil
import tomllib
from pathlib import Path

import pytest
import sc_mesh_registry
from sc_assignment_failover import (
    AssignmentFailoverError,
    FailoverConflictError,
    FailoverLaunchContext,
    FailoverSagaStore,
    SeatDeliveryClaimStore,
    _create_launch_context,
    _create_seat_actuation_proof,
    failover_assignment,
    list_pending_failovers,
)
from sc_assignment_protocol import (
    AssignmentStateStore,
    AssignmentVerificationError,
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
ROOT = Path(__file__).resolve().parents[1]


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _actuation_proof(case, claim, *, result_sha256="d" * 64, acted_at=NOW + 3):
    return _create_seat_actuation_proof(
        claim,
        seat_identity=case["new_seat"],
        result_sha256=result_sha256,
        acted_at=acted_at,
    )


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


def _case(tmp_path, *, receipt_state="blocked"):
    authority = AgentIdentity.generate("authority")
    coordinator = AgentIdentity.generate("coordinator")
    old_seat = AgentIdentity.generate("old-seat")
    new_seat = AgentIdentity.generate("new-seat")
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
        generation=4,
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
    verification = {
        "pinned_coordinator_public_key_hex": coordinator.public_key_hex,
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
        assignment,
        store=seat_store,
        authority_public_key_hex=authority.public_key_hex,
        now=NOW + 1,
        **verification,
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
        authority_public_key_hex=authority.public_key_hex,
        now=NOW + 2,
        **verification,
    )
    receipt = emit_state_receipt(
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
        idempotency_key=f"{receipt_state}-2",
        store=seat_store,
        now=NOW + 3,
    )
    registry_path = tmp_path / "mesh_registry.json"
    assert sc_mesh_registry.register_virtual_agent(
        "worker",
        mesh="test",
        status="active",
        birth_id="seat-old-birth",
        generation=3,
        registry_path=registry_path,
    )["ok"]
    saga_path = tmp_path / "failover.sqlite3"
    context = _create_launch_context(
        assignment_store=coordinator_store,
        saga_path=saga_path,
        registry_path=registry_path,
    )
    delivery_store = SeatDeliveryClaimStore(tmp_path / "seat-delivery.sqlite3")
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
        "receipt": receipt,
        "store": coordinator_store,
        "saga_path": saga_path,
        "context": context,
        "delivery_store": delivery_store,
        "registry_path": registry_path,
        "verification": verification,
    }


def _run(case, *, shared=None, receipt=None, **overrides):
    delivery_now = overrides.get("now", NOW + 3)
    shared = shared if shared is not None else {
        "audit": [],
        "guard": [],
        "delivery": [],
        "delivery_receipts": {},
    }

    def audit(event_type, **kwargs):
        shared["audit"].append((event_type, kwargs))
        return {"ok": True, "event": {"event_type": event_type}}

    def guard(**kwargs):
        shared["guard"].append(kwargs)
        return True

    def deliver(**kwargs):
        shared["delivery"].append(kwargs)
        return case["delivery_store"].deliver_exact(
            operation_id=kwargs["operation_id"],
            continuity=kwargs["continuity"],
            replacement_assignment=kwargs["assignment"],
            seat_identity=case["new_seat"],
            actuator=lambda claim: _actuation_proof(
                case,
                claim,
                acted_at=delivery_now,
            ),
            delivered_at=delivery_now,
        )

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
        "context": case["context"],
        "high_assurance_target_resolver": guard,
        "guarded_delivery": deliver,
        "audit_append": audit,
        "now": NOW + 3,
        **case["verification"],
        **overrides,
    }
    return failover_assignment(
        case["assignment"], receipt or case["receipt"], **args
    ), shared


def test_saga_delivers_only_after_cas_final_audit_and_signed_seat_receipt(tmp_path):
    case = _case(tmp_path)
    result, shared = _run(case)

    assert result["ok"] is True
    assert result["stage"] == "delivered"
    assert result["process_action"] == "none"
    assert [item[0] for item in shared["audit"]] == [
        "assignment_failover_intent",
        "assignment_failover_off_rails",
    ]
    assert shared["audit"][0][1]["status"] == "pending"
    assert shared["audit"][1][1]["status"] == "off_rails"
    row = sc_mesh_registry.load_registry_strict(case["registry_path"])["agents"][0]
    assert row["status"] == "off_rails"
    assert result["delivery_receipt"]["receiver_key_id"] == result["replacement_seat"]["seat_key_id"]


def test_real_strict_audit_finalizes_off_rails_only_after_registry_cas(tmp_path):
    case = _case(tmp_path, receipt_state="rejected")

    def audit(event_type, **kwargs):
        row = sc_mesh_registry.load_registry_strict(case["registry_path"])["agents"][0]
        if event_type == "assignment_failover_intent":
            assert row["status"] == "active"
        else:
            assert event_type == "assignment_failover_off_rails"
            assert row["status"] == "off_rails"
        return sc_mesh_registry.append_event(event_type, **kwargs)

    result, _shared = _run(case, audit_append=audit)
    assert result["ok"] is True
    verified = sc_mesh_registry.verify_events(registry_path=case["registry_path"])
    assert verified["ok"] is True
    events = sc_mesh_registry.load_events(registry_path=case["registry_path"], limit=10)["events"]
    assert [item["event_type"] for item in events] == [
        "virtual_role_registered",
        "assignment_failover_intent",
        "assignment_failover_off_rails",
    ]


def test_package_manifest_and_ci_include_failover_module():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    modules = package["tool"]["hatch"]["build"]["targets"]["wheel"]["include"]
    assert "sc_assignment_failover.py" in modules
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ruff check\n" in workflow
    assert "python -m py_compile self_connect.py sc_assignment_failover.py" in workflow


def test_public_action_uses_only_pinned_launch_durability_and_fresh_path_rejected(tmp_path):
    case = _case(tmp_path)
    parameters = inspect.signature(failover_assignment).parameters
    assert "context" in parameters
    assert "store" not in parameters
    assert "saga_path" not in parameters
    assert "registry_path" not in parameters

    fresh_store_path = tmp_path / "fresh-coordinator.sqlite3"
    shutil.copy2(case["store"].path, fresh_store_path)
    fresh_store = AssignmentStateStore(fresh_store_path)
    fresh_saga = tmp_path / "fresh-saga.sqlite3"
    _run(case)
    with pytest.raises(AssignmentVerificationError, match="not durably consumed"):
        fresh_store.require_receipt("consumed", case["receipt"])
    with pytest.raises(AssignmentFailoverError, match="verification context"):
        _run(case, store=fresh_store, saga_path=fresh_saga)
    assert not fresh_saga.exists()
    assert list_pending_failovers(case["saga_path"]) == []
    with pytest.raises(TypeError, match="runtime-owned"):
        FailoverLaunchContext(
            assignment_store=fresh_store,
            saga_path=fresh_saga,
            registry_path=case["registry_path"],
            seal=object(),
        )


def test_launch_event_head_anchor_rejects_truncated_history(tmp_path):
    case = _case(tmp_path)
    assert case["context"].event_head_anchor != "0" * 64
    case["context"].event_log_path.write_text("", encoding="utf-8")

    with pytest.raises(AssignmentFailoverError, match="anchor is absent"):
        _run(case)
    assert list_pending_failovers(case["saga_path"]) == []


def test_continuity_artifact_binds_predecessor_and_authorizing_receipt(tmp_path):
    case = _case(tmp_path)
    result, _shared = _run(case)
    continuity = result["continuity"]
    payload = json.loads(result["replacement_assignment"]["payload"])

    assert continuity["predecessor_assignment_sha256"] == __import__("hashlib").sha256(
        _canonical(case["assignment"])
    ).hexdigest()
    assert continuity["authorizing_receipt_sha256"] == __import__("hashlib").sha256(
        _canonical(case["receipt"])
    ).hexdigest()
    assert payload["continuity"] == continuity
    assert payload["predecessor_payload"] == case["assignment"]["payload"]
    operation = FailoverSagaStore(case["saga_path"]).load(result["operation_id"])
    assert operation["spec"]["predecessor_assignment"] == case["assignment"]
    assert operation["spec"]["authorizing_receipt"] == case["receipt"]
    assert operation["spec"]["replacement_enrollment"] == case["new_enrollment"]


@pytest.mark.parametrize(
    "checkpoint",
        [
            "after_saga_prepare",
            "after_intent_audit",
        "after_registry_cas",
        "after_off_rails_audit",
        "after_assignment_build",
        "after_assignment_issue",
        "after_delivery",
    ],
)
def test_every_crash_boundary_resumes_exact_outbox(checkpoint, tmp_path):
    case = _case(tmp_path)
    shared = {"audit": [], "guard": [], "delivery": [], "delivery_receipts": {}}
    crashed = {"done": False}

    def crash_once(name, _operation):
        if name == checkpoint and not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("simulated crash")

    with pytest.raises(AssignmentFailoverError, match="simulated crash"):
        _run(case, shared=shared, stage_hook=crash_once)
    if checkpoint == "after_delivery":
        assert list_pending_failovers(case["saga_path"]) == []
    else:
        assert list_pending_failovers(case["saga_path"])

    result, _shared = _run(case, shared=shared)
    assert result["ok"] is True
    assert result["resumed"] is True
    assert FailoverSagaStore(case["saga_path"]).pending() == []
    if checkpoint == "after_delivery":
        assert len(shared["delivery"]) == 1


def test_prepared_saga_recovers_unconsumed_receipt_after_freshness_window(tmp_path):
    case = _case(tmp_path)

    def crash_after_prepare(name, _operation):
        if name == "after_saga_prepare":
            raise RuntimeError("crash before receipt consumption")

    with pytest.raises(AssignmentFailoverError, match="before receipt consumption"):
        _run(case, stage_hook=crash_after_prepare)
    operation = list_pending_failovers(case["saga_path"])[0]
    assert operation["stage"] == "prepared"
    with pytest.raises(AssignmentVerificationError, match="durably consumed"):
        case["store"].require_receipt("consumed", case["receipt"])

    result, _shared = _run(case, now=NOW + 100)
    assert result["ok"] is True
    assert result["resumed"] is True


def test_exact_reentry_is_idempotent_but_conflicting_receipt_reuse_rejects(tmp_path):
    case = _case(tmp_path)
    first, shared = _run(case)
    second, _shared = _run(case, shared=shared)
    assert second["operation_id"] == first["operation_id"]
    assert second["resumed"] is True
    assert len(shared["delivery"]) == 1

    wrong_target = _target(999, 202, 302, "conflicting target")
    with pytest.raises(FailoverConflictError):
        _run(case, shared=shared, replacement_target_identity=wrong_target)


def test_receipt_consumed_outside_saga_cannot_authorize_new_failover(tmp_path):
    case = _case(tmp_path)
    verify_consume_state_receipt(
        case["receipt"],
        case["assignment"],
        store=case["store"],
        authority_public_key_hex=case["authority"].public_key_hex,
        now=NOW + 3,
        **case["verification"],
    )
    with pytest.raises(FailoverConflictError, match="outside a durable failover saga"):
        _run(case)
    assert list_pending_failovers(case["saga_path"]) == []


def test_unsigned_echo_zero_and_wrong_signed_delivery_never_go(tmp_path):
    variants = (
        lambda **_kwargs: None,
        lambda **_kwargs: {},
        lambda **kwargs: {
            "ok": True,
            "assignment_id": kwargs["assignment"]["assignment_id"],
        },
    )
    for index, boundary in enumerate(variants):
        case = _case(tmp_path / f"unsigned-{index}")
        with pytest.raises(AssignmentFailoverError, match="delivery receipt"):
            _run(case, guarded_delivery=boundary)
        assert FailoverSagaStore(case["saga_path"]).load(
            list_pending_failovers(case["saga_path"])[0]["operation_id"]
        )["stage"] == "assignment_issued"

    case = _case(tmp_path / "wrong-signer")
    wrong = AgentIdentity.generate("wrong")

    def wrong_delivery(**kwargs):
        assignment = copy.deepcopy(kwargs["assignment"])
        assignment["receiver_key_id"] = __import__("hashlib").sha256(
            bytes.fromhex(wrong.public_key_hex)
        ).hexdigest()
        return SeatDeliveryClaimStore(tmp_path / "wrong-signer.sqlite3").deliver_exact(
            operation_id=kwargs["operation_id"],
            continuity=kwargs["continuity"],
            replacement_assignment=assignment,
            seat_identity=wrong,
            actuator=lambda claim: _create_seat_actuation_proof(
                claim,
                seat_identity=wrong,
                result_sha256="d" * 64,
                acted_at=NOW + 3,
            ),
            delivered_at=NOW + 3,
        )

    with pytest.raises(AssignmentFailoverError, match="signature"):
        _run(case, guarded_delivery=wrong_delivery)


def test_seat_claim_persists_before_actuation_and_response_loss_never_reacts(tmp_path):
    case = _case(tmp_path)
    actions = []
    lose_once = {"value": True}

    def delivery_with_response_loss(**kwargs):
        def actuator(claim):
            actions.append(claim)
            return _actuation_proof(
                case,
                claim,
                result_sha256="e" * 64,
            )

        def after_commit():
            if lose_once["value"]:
                lose_once["value"] = False
                raise OSError("response lost after durable seat receipt")

        return case["delivery_store"].deliver_exact(
            operation_id=kwargs["operation_id"],
            continuity=kwargs["continuity"],
            replacement_assignment=kwargs["assignment"],
            seat_identity=case["new_seat"],
            actuator=actuator,
            delivered_at=NOW + 3,
            after_commit=after_commit,
        )

    with pytest.raises(AssignmentFailoverError, match="response lost"):
        _run(case, guarded_delivery=delivery_with_response_loss)
    assert len(actions) == 1
    assert list_pending_failovers(case["saga_path"])[0]["stage"] == "assignment_issued"

    result, _shared = _run(case, guarded_delivery=delivery_with_response_loss)
    assert result["ok"] is True
    assert len(actions) == 1
    assert result["delivery_receipt"]["delivery_claim_id"] == actions[0]["claim_id"]


def test_crash_after_claim_before_proof_is_ambiguous_and_never_reacts(tmp_path):
    store = SeatDeliveryClaimStore(tmp_path / "seat-claim.sqlite3")
    case = _case(tmp_path / "case")
    actions = []

    def broken_actuator(claim):
        actions.append(claim)
        raise RuntimeError("remote result lost")

    kwargs = {
        "operation_id": "a" * 64,
        "continuity": {"continuity": "proof"},
        "replacement_assignment": case["assignment"],
        "seat_identity": case["old_seat"],
        "actuator": broken_actuator,
        "delivered_at": NOW + 3,
    }
    with pytest.raises(AssignmentFailoverError, match="after durable claim"):
        store.deliver_exact(**kwargs)
    with pytest.raises(AssignmentFailoverError, match="ambiguous"):
        store.deliver_exact(**kwargs)
    assert len(actions) == 1


def test_unsigned_echo_callback_cannot_complete_claim_or_go(tmp_path):
    store = SeatDeliveryClaimStore(tmp_path / "seat-claim.sqlite3")
    case = _case(tmp_path / "case")
    calls = []

    def unsigned_echo(claim):
        calls.append(claim)
        return {**claim, "result_sha256": "f" * 64}

    kwargs = {
        "operation_id": "b" * 64,
        "continuity": {"continuity": "proof"},
        "replacement_assignment": case["assignment"],
        "seat_identity": case["old_seat"],
        "actuator": unsigned_echo,
        "delivered_at": NOW + 3,
    }
    with pytest.raises(AssignmentFailoverError, match="actuation proof is unsigned"):
        store.deliver_exact(**kwargs)
    with pytest.raises(AssignmentFailoverError, match="ambiguous"):
        store.deliver_exact(**kwargs)
    assert len(calls) == 1


def test_cas_drift_has_intent_only_and_restart_reconciles_after_restore(tmp_path):
    case = _case(tmp_path)
    registry = json.loads(case["registry_path"].read_text(encoding="utf-8"))
    registry["agents"][0]["birth_id"] = "drifted-birth"
    case["registry_path"].write_text(json.dumps(registry), encoding="utf-8")
    shared = {"audit": [], "guard": [], "delivery": [], "delivery_receipts": {}}

    with pytest.raises(AssignmentFailoverError, match="registry birth drift"):
        _run(case, shared=shared)
    assert [item[0] for item in shared["audit"]] == ["assignment_failover_intent"]
    assert list_pending_failovers(case["saga_path"])[0]["stage"] == "intent_audited"

    registry["agents"][0]["birth_id"] = "seat-old-birth"
    case["registry_path"].write_text(json.dumps(registry), encoding="utf-8")
    result, _shared = _run(case, shared=shared)
    assert result["ok"] is True
    assert [item[0] for item in shared["audit"]] == [
        "assignment_failover_intent",
        "assignment_failover_off_rails",
    ]


@pytest.mark.parametrize(
    ("failed_event", "expected_stage"),
    [
        ("assignment_failover_intent", "authorized"),
        ("assignment_failover_off_rails", "off_rails"),
    ],
)
def test_audit_failure_is_durable_and_exact_retry_resumes(
    failed_event, expected_stage, tmp_path
):
    case = _case(tmp_path)
    failed = {"done": False}

    def flaky_audit(event_type, **_kwargs):
        if event_type == failed_event and not failed["done"]:
            failed["done"] = True
            raise OSError("audit unavailable")
        return {"ok": True}

    with pytest.raises(AssignmentFailoverError, match="audit unavailable"):
        _run(case, audit_append=flaky_audit)
    assert list_pending_failovers(case["saga_path"])[0]["stage"] == expected_stage
    if failed_event == "assignment_failover_intent":
        assert sc_mesh_registry.load_registry_strict(case["registry_path"])["agents"][0]["status"] == "active"

    result, _shared = _run(case, audit_append=flaky_audit)
    assert result["ok"] is True


def test_wrong_target_channel_guard_and_revocation_fail_closed(tmp_path):
    guard_case = _case(tmp_path / "guard")
    with pytest.raises(AssignmentFailoverError, match="resolver refused"):
        _run(guard_case, high_assurance_target_resolver=lambda **_kwargs: False)
    assert list_pending_failovers(guard_case["saga_path"])[0]["stage"] == "assignment_issued"

    channel_case = _case(tmp_path / "channel")

    def wrong_channel_delivery(**kwargs):
        receipt = channel_case["delivery_store"].deliver_exact(
            operation_id=kwargs["operation_id"],
            continuity=kwargs["continuity"],
            replacement_assignment=kwargs["assignment"],
            seat_identity=channel_case["new_seat"],
            actuator=lambda claim: _actuation_proof(channel_case, claim),
            delivered_at=NOW + 3,
        )
        body = dict(receipt)
        body.pop("signature_b64")
        body["response_channel_sha256"] = "f" * 64
        return {
            **body,
            "signature_b64": base64.b64encode(
                channel_case["new_seat"].sign(_canonical(body))
            ).decode(),
        }

    with pytest.raises(AssignmentFailoverError, match="exact binding"):
        _run(channel_case, guarded_delivery=wrong_channel_delivery)

    revoked_case = _case(tmp_path / "revoked")
    with pytest.raises(AssignmentFailoverError, match="revoked"):
        _run(
            revoked_case,
            revoked_seat_key_ids=frozenset(
                {revoked_case["new_enrollment"]["seat_key_id"]}
            ),
        )
    assert list_pending_failovers(revoked_case["saga_path"]) == []


def test_forged_stale_wrong_seat_and_noncanonical_receipt_never_create_saga(tmp_path):
    variants = []
    forged_case = _case(tmp_path / "forged")
    forged = copy.deepcopy(forged_case["receipt"])
    forged["state"] = "rejected"
    variants.append((forged_case, forged, NOW + 3))

    stale_case = _case(tmp_path / "stale")
    variants.append((stale_case, stale_case["receipt"], NOW + 100))

    wrong_case = _case(tmp_path / "wrong")
    wrong = copy.deepcopy(wrong_case["receipt"])
    wrong["receiver_key_id"] = "f" * 64
    variants.append((wrong_case, wrong, NOW + 3))

    canonical_case = _case(tmp_path / "canonical")
    variants.append((canonical_case, json.dumps(canonical_case["receipt"], indent=2), NOW + 3))

    for case, receipt, verification_time in variants:
        with pytest.raises(AssignmentFailoverError):
            _run(case, receipt=receipt, now=verification_time)
        assert not case["saga_path"].exists() or list_pending_failovers(case["saga_path"]) == []
