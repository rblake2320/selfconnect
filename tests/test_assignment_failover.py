from __future__ import annotations

import base64
import copy
import inspect
import json
import shutil
import sqlite3
import tomllib
from pathlib import Path

import pytest
import sc_assignment_failover as failover_module
import sc_mesh_registry
from sc_assignment_failover import (
    AssignmentFailoverError,
    FailoverConflictError,
    _create_seat_actuation_proof,
    failover_assignment,
    initialize_failover_runtime,
    list_pending_failovers,
    open_seat_delivery_claim_store,
    provision_failover_trust_root,
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


@pytest.fixture(autouse=True)
def _protected_credential_backend(monkeypatch):
    credentials: dict[str, bytes] = {}

    def read(target):
        value = credentials.get(target)
        return None if value is None else bytes(value)

    def write(target, value):
        credentials[target] = bytes(value)

    monkeypatch.setattr(failover_module, "read_secret", read)
    monkeypatch.setattr(failover_module, "write_secret", write)
    yield credentials


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
    config_path = tmp_path / "failover-root.json"
    claim_store_path = tmp_path / "seat-delivery.sqlite3"
    pinned = provision_failover_trust_root(
        config_path=config_path,
        provisioning_identity=authority,
        assignment_store_path=coordinator_store.path,
        saga_path=saga_path,
        registry_path=registry_path,
        claim_store_path=claim_store_path,
        protected_state_path=tmp_path / "failover-state.json",
    )
    failover_module._LAUNCH_CONFIG_PATH = str(config_path.resolve())
    failover_module._LAUNCH_PUBLIC_KEY_HEX = pinned
    initialize_failover_runtime()
    context = failover_module._require_launch_context()
    delivery_store = open_seat_delivery_claim_store()
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
        "config_path": config_path.resolve(),
        "pinned": pinned,
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
        return sc_mesh_registry.append_event(event_type, **kwargs)

    def guard(**kwargs):
        shared["guard"].append(kwargs)
        return True

    def deliver(**kwargs):
        shared["delivery"].append(kwargs)
        return open_seat_delivery_claim_store().deliver_exact(
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
        "high_assurance_target_resolver": guard,
        "guarded_delivery": deliver,
        "audit_append": audit,
        "now": NOW + 3,
        **case["verification"],
        **overrides,
    }
    failover_module._LAUNCH_CONFIG_PATH = str(case["config_path"])
    failover_module._LAUNCH_PUBLIC_KEY_HEX = case["pinned"]
    return failover_assignment(
        case["assignment"], receipt or case["receipt"], **args
    ), shared


def _pending(case):
    failover_module._LAUNCH_CONFIG_PATH = str(case["config_path"])
    failover_module._LAUNCH_PUBLIC_KEY_HEX = case["pinned"]
    return list_pending_failovers()


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
    assert "sc_windows_credentials.py" in modules
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ruff check\n" in workflow
    assert (
        "python -m py_compile self_connect.py sc_assignment_failover.py "
        "sc_windows_credentials.py"
    ) in workflow


def test_public_action_uses_only_pinned_launch_durability_and_fresh_path_rejected(tmp_path):
    case = _case(tmp_path)
    parameters = inspect.signature(failover_assignment).parameters
    assert "context" not in parameters
    assert "store" not in parameters
    assert "saga_path" not in parameters
    assert "registry_path" not in parameters
    assert not inspect.signature(initialize_failover_runtime).parameters
    assert not inspect.signature(open_seat_delivery_claim_store).parameters
    assert not hasattr(failover_module, "_create_launch_context")
    assert not hasattr(failover_module, "_CONTEXT_SEAL")
    assert not hasattr(failover_module, "SeatDeliveryClaimStore")

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
    assert _pending(case) == []
    assert not hasattr(failover_module, "FailoverLaunchContext")


def test_alternate_signed_root_cannot_be_passed_to_public_action(tmp_path):
    canonical = _case(tmp_path / "canonical")
    alternate_path = tmp_path / "alternate"
    alternate_path.mkdir()
    alternate_store = AssignmentStateStore(alternate_path / "coordinator.sqlite3")
    alternate_registry = alternate_path / "mesh_registry.json"
    assert sc_mesh_registry.register_virtual_agent(
        "worker",
        mesh="test",
        status="active",
        birth_id="seat-old-birth",
        generation=3,
        registry_path=alternate_registry,
    )["ok"]
    attacker = AgentIdentity.generate("attacker-launcher")
    alternate_key = provision_failover_trust_root(
        config_path=alternate_path / "root.json",
        provisioning_identity=attacker,
        assignment_store_path=alternate_store.path,
        saga_path=alternate_path / "saga.sqlite3",
        registry_path=alternate_registry,
        claim_store_path=alternate_path / "claims.sqlite3",
        protected_state_path=alternate_path / "state.json",
    )
    assert alternate_key == attacker.public_key_hex
    assert "context" not in inspect.signature(failover_assignment).parameters
    with pytest.raises(AssignmentFailoverError, match="verification context"):
        _run(canonical, context=object())
    with sqlite3.connect(alternate_path / "saga.sqlite3") as connection:
        assert connection.execute(
            "SELECT count(*) FROM failover_saga_v1"
        ).fetchone()[0] == 0


def test_post_import_environment_rewrite_cannot_select_an_alternate_root(monkeypatch):
    monkeypatch.setattr(failover_module, "_LAUNCH_CONFIG_PATH", "")
    monkeypatch.setattr(failover_module, "_LAUNCH_PUBLIC_KEY_HEX", "")
    monkeypatch.setenv("SELFCONNECT_FAILOVER_ROOT_CONFIG", r"C:\attacker\root.json")
    monkeypatch.setenv("SELFCONNECT_FAILOVER_ROOT_PUBLIC_KEY_HEX", "f" * 64)
    with pytest.raises(AssignmentFailoverError, match="not pinned"):
        initialize_failover_runtime()


def test_signed_launch_config_and_database_ids_are_immutable_at_runtime(tmp_path):
    config_case = _case(tmp_path / "config")
    config = json.loads(config_case["context"]._config_path.read_text(encoding="utf-8"))
    config["saga_store_path"] = str((tmp_path / "attacker.sqlite3").resolve())
    config_case["context"]._config_path.write_text(
        json.dumps(config, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(AssignmentFailoverError, match=r"signature is invalid|config changed"):
        _run(config_case)

    database_case = _case(tmp_path / "database")
    with sqlite3.connect(database_case["saga_path"]) as connection:
        connection.execute(
            "UPDATE failover_saga_store_meta_v1 SET database_id=? WHERE singleton=1",
            ("f" * 64,),
        )
    with pytest.raises(AssignmentFailoverError, match=r"database id conflicts|database identity mismatch"):
        _run(database_case)


def test_provisioning_is_create_once_and_cannot_rebind_existing_state(tmp_path):
    case = _case(tmp_path)
    with pytest.raises(FileExistsError, match="already provisioned"):
        provision_failover_trust_root(
            config_path=case["context"]._config_path,
            provisioning_identity=case["authority"],
            assignment_store_path=case["store"].path,
            saga_path=case["saga_path"],
            registry_path=case["registry_path"],
            claim_store_path=case["delivery_store"].path,
            protected_state_path=case["context"].protected_state_path,
        )


def test_launch_event_head_anchor_rejects_truncated_history(tmp_path):
    case = _case(tmp_path)
    state = case["context"]._read_state()
    assert state["event_anchor"]["head_hash"] != "0" * 64
    case["context"].event_log_path.write_text("", encoding="utf-8")

    with pytest.raises(AssignmentFailoverError, match="rolled back"):
        _run(case)
    with sqlite3.connect(case["saga_path"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM failover_saga_v1"
        ).fetchone()[0] == 0


def test_protected_operation_rejects_canonical_saga_rollback(tmp_path):
    case = _case(tmp_path)
    pristine = tmp_path / "pristine-saga.sqlite3"
    shutil.copy2(case["saga_path"], pristine)
    result, _shared = _run(case)
    assert result["ok"] is True

    shutil.copy2(pristine, case["saga_path"])
    with pytest.raises(AssignmentFailoverError, match="operation is absent"):
        _run(case)


def test_credential_head_rejects_protected_state_file_rollback(tmp_path):
    case = _case(tmp_path)
    pristine = tmp_path / "pristine-protected-state.json"
    shutil.copy2(case["context"].protected_state_path, pristine)
    result, _shared = _run(case)
    assert result["ok"] is True

    shutil.copy2(pristine, case["context"].protected_state_path)
    with pytest.raises(AssignmentFailoverError, match="rolled back or diverged"):
        _run(case)


def test_protected_saga_anchor_rejects_mutable_record_rewrite(tmp_path):
    case = _case(tmp_path)
    result, _shared = _run(case)
    assert result["ok"] is True
    with sqlite3.connect(case["saga_path"]) as connection:
        connection.execute(
            "UPDATE failover_saga_v1 SET receipt_commit_sha256=?",
            ("f" * 64,),
        )
    with pytest.raises(AssignmentFailoverError, match="saga provenance mismatch"):
        _run(case)


def test_protected_audit_event_rejects_valid_rewritten_event_chain(tmp_path):
    case = _case(tmp_path)
    result, _shared = _run(case)
    assert result["ok"] is True
    events = sc_mesh_registry.load_events(
        event_log_path=case["context"].event_log_path,
        limit=100,
    )["events"]
    initial = events[0]
    case["context"].event_log_path.write_text(
        json.dumps(initial, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    assert sc_mesh_registry.verify_events(
        event_log_path=case["context"].event_log_path
    )["ok"] is True
    with pytest.raises(AssignmentFailoverError, match=r"rolled back|anchor is absent"):
        _run(case)


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
    operation = case["context"].saga_store.load(result["operation_id"])
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
        assert _pending(case) == []
    else:
        assert _pending(case)

    result, _shared = _run(case, shared=shared)
    assert result["ok"] is True
    assert result["resumed"] is True
    assert case["context"].saga_store.pending() == []
    if checkpoint == "after_delivery":
        assert len(shared["delivery"]) == 1


def test_prepared_saga_recovers_unconsumed_receipt_after_freshness_window(tmp_path):
    case = _case(tmp_path)

    def crash_after_prepare(name, _operation):
        if name == "after_saga_prepare":
            raise RuntimeError("crash before receipt consumption")

    with pytest.raises(AssignmentFailoverError, match="before receipt consumption"):
        _run(case, stage_hook=crash_after_prepare)
    operation = _pending(case)[0]
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
    assert _pending(case) == []


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
        assert case["context"].saga_store.load(
            _pending(case)[0]["operation_id"]
        )["stage"] == "assignment_issued"

    case = _case(tmp_path / "wrong-signer")
    wrong = AgentIdentity.generate("wrong")

    def wrong_delivery(**kwargs):
        assignment = copy.deepcopy(kwargs["assignment"])
        assignment["receiver_key_id"] = __import__("hashlib").sha256(
            bytes.fromhex(wrong.public_key_hex)
        ).hexdigest()
        return case["delivery_store"].deliver_exact(
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
    assert _pending(case)[0]["stage"] == "assignment_issued"

    result, _shared = _run(case, guarded_delivery=delivery_with_response_loss)
    assert result["ok"] is True
    assert len(actions) == 1
    assert result["delivery_receipt"]["delivery_claim_id"] == actions[0]["claim_id"]


def test_protected_claim_highwater_rejects_claim_database_rollback(tmp_path):
    case = _case(tmp_path)
    pristine = tmp_path / "pristine-claims.sqlite3"
    shutil.copy2(case["delivery_store"].path, pristine)
    actions = []

    def response_lost(**kwargs):
        def actuator(claim):
            actions.append(claim)
            return _actuation_proof(case, claim, result_sha256="e" * 64)

        return case["delivery_store"].deliver_exact(
            operation_id=kwargs["operation_id"],
            continuity=kwargs["continuity"],
            replacement_assignment=kwargs["assignment"],
            seat_identity=case["new_seat"],
            actuator=actuator,
            delivered_at=NOW + 3,
            after_commit=lambda: (_ for _ in ()).throw(OSError("response lost")),
        )

    with pytest.raises(AssignmentFailoverError, match="response lost"):
        _run(case, guarded_delivery=response_lost)
    assert len(actions) == 1
    shutil.copy2(pristine, case["delivery_store"].path)

    with pytest.raises(AssignmentFailoverError, match="claim store rolled back"):
        _run(case, guarded_delivery=response_lost)
    assert len(actions) == 1


def test_crash_after_claim_before_proof_is_ambiguous_and_never_reacts(tmp_path):
    case = _case(tmp_path / "case")
    store = case["delivery_store"]
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
    case = _case(tmp_path / "case")
    store = case["delivery_store"]
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
    assert _pending(case)[0]["stage"] == "intent_audited"

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

    def flaky_audit(event_type, **kwargs):
        if event_type == failed_event and not failed["done"]:
            failed["done"] = True
            raise OSError("audit unavailable")
        return sc_mesh_registry.append_event(event_type, **kwargs)

    with pytest.raises(AssignmentFailoverError, match="audit unavailable"):
        _run(case, audit_append=flaky_audit)
    assert _pending(case)[0]["stage"] == expected_stage
    if failed_event == "assignment_failover_intent":
        assert sc_mesh_registry.load_registry_strict(case["registry_path"])["agents"][0]["status"] == "active"

    result, _shared = _run(case, audit_append=flaky_audit)
    assert result["ok"] is True


def test_wrong_target_channel_guard_and_revocation_fail_closed(tmp_path):
    guard_case = _case(tmp_path / "guard")
    with pytest.raises(AssignmentFailoverError, match="resolver refused"):
        _run(guard_case, high_assurance_target_resolver=lambda **_kwargs: False)
    assert _pending(guard_case)[0]["stage"] == "assignment_issued"

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
    assert _pending(revoked_case) == []


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
        assert not case["saga_path"].exists() or _pending(case) == []


def test_ordinary_mode_labels_same_user_gap_and_high_assurance_refuses_first(tmp_path):
    case = _case(tmp_path / "ordinary")
    callback_calls = {"guard": 0, "delivery": 0, "audit": 0, "registry": 0}

    def called(name):
        def callback(*_args, **_kwargs):
            callback_calls[name] += 1
            return True

        return callback

    with pytest.raises(
        AssignmentFailoverError,
        match="no separately privileged external monotonic authority",
    ):
        _run(
            case,
            high_assurance=True,
            high_assurance_target_resolver=called("guard"),
            guarded_delivery=called("delivery"),
            audit_append=called("audit"),
            registry_transition=called("registry"),
        )
    assert callback_calls == {"guard": 0, "delivery": 0, "audit": 0, "registry": 0}
    with pytest.raises(AssignmentFailoverError, match="exact bool"):
        _run(case, high_assurance=1)

    result, _shared = _run(case)
    expected = {
        "mode": "ordinary",
        "same_user_threat": "excluded",
        "external_monotonic_authority": "absent",
        "monotonicity": "local_best_effort",
        "high_assurance": False,
    }
    assert result["assurance"] == expected
    assert result["continuity"]["assurance"] == expected
    assert result["delivery_receipt"]["assurance"] == expected
    assert "same_user_threat" in result["replacement_assignment"]["payload"]
    assert not hasattr(failover_module, "ORDINARY_ASSURANCE")


def test_rebound_module_assurance_name_cannot_change_signed_or_returned_labels(
    tmp_path,
    monkeypatch,
):
    case = _case(tmp_path)
    attacker_label = {
        "mode": "high_assurance",
        "same_user_threat": "covered",
        "external_monotonic_authority": "present",
        "monotonicity": "rollback_proof",
        "high_assurance": True,
    }
    monkeypatch.setattr(
        failover_module,
        "ORDINARY_ASSURANCE",
        attacker_label,
        raising=False,
    )

    result, _shared = _run(case)
    expected = {
        "mode": "ordinary",
        "same_user_threat": "excluded",
        "external_monotonic_authority": "absent",
        "monotonicity": "local_best_effort",
        "high_assurance": False,
    }
    assert result["assurance"] == expected
    assert result["continuity"]["assurance"] == expected
    assert result["delivery_receipt"]["assurance"] == expected
    assert result["assurance"] != attacker_label

    with pytest.raises(AssignmentFailoverError, match="assurance label is invalid"):
        failover_module._create_delivery_receipt(
            operation_id=result["operation_id"],
            continuity=result["continuity"],
            replacement_assignment=result["replacement_assignment"],
            seat_identity=case["new_seat"],
            delivery_claim_id="a" * 64,
            delivery_idempotency_key=f"failover-delivery:{result['operation_id']}",
            delivery_claim_commit_sha256="b" * 64,
            failover_root_id=result["delivery_receipt"]["failover_root_id"],
            claim_store_database_id=result["delivery_receipt"]["claim_store_database_id"],
            actuation_proof=result["delivery_receipt"]["actuation_proof"],
            result_sha256=result["delivery_receipt"]["result_sha256"],
            assurance=attacker_label,
            delivered_at=NOW + 3,
        )


def test_rebound_assurance_helper_cannot_authorize_false_seat_self_claim(
    tmp_path,
    monkeypatch,
):
    case = _case(tmp_path)
    false_high = {
        "mode": "high_assurance",
        "same_user_threat": "covered",
        "external_monotonic_authority": "present",
        "monotonicity": "rollback_proof",
        "high_assurance": True,
    }
    original = failover_module._require_ordinary_assurance
    monkeypatch.setattr(
        failover_module,
        "_require_ordinary_assurance",
        lambda _value: dict(false_high),
    )

    with pytest.raises(
        AssignmentFailoverError,
        match="assurance self-claim is not canonical ordinary mode",
    ):
        _run(case)
    pending = _pending(case)[0]
    assert pending["stage"] == "assignment_issued"
    assert pending["continuity"]["assurance"]["high_assurance"] is False
    with sqlite3.connect(case["delivery_store"].path) as connection:
        raw = connection.execute(
            "SELECT receipt_json FROM delivery_claim_v1 WHERE state='completed'"
        ).fetchone()[0]
    poisoned_receipt = json.loads(bytes(raw))
    assert poisoned_receipt["assurance"] == false_high

    monkeypatch.setattr(failover_module, "_require_ordinary_assurance", original)
    with pytest.raises(
        AssignmentFailoverError,
        match="assurance self-claim is not canonical ordinary mode",
    ):
        _run(case)


def test_paired_same_user_rollback_can_readmit_only_in_labeled_ordinary_mode(
    tmp_path,
    _protected_credential_backend,
):
    case = _case(tmp_path)
    context = case["context"]
    paths = (
        context.assignment_store_path,
        context.saga_store_path,
        context.registry_path,
        context.event_log_path,
        context.claim_store_path,
        context.protected_state_path,
    )
    before = {path: path.read_bytes() for path in paths}
    credential_before = bytes(_protected_credential_backend[context.credential_target])
    shared = {"audit": [], "guard": [], "delivery": [], "delivery_receipts": {}}

    first, _shared = _run(case, shared=shared)
    assert first["assurance"]["same_user_threat"] == "excluded"
    assert len(shared["delivery"]) == 1

    for path, data in before.items():
        path.write_bytes(data)
    _protected_credential_backend[context.credential_target] = credential_before

    second, _shared = _run(case, shared=shared)
    assert second["assurance"]["external_monotonic_authority"] == "absent"
    assert len(shared["delivery"]) == 2


def test_private_receipt_helpers_without_claim_rows_cannot_complete_failover(tmp_path):
    case = _case(tmp_path)
    assert not hasattr(failover_module, "_SEAT_CLAIM_SEAL")

    def forge_without_claim(**kwargs):
        assignment = kwargs["assignment"]
        continuity = kwargs["continuity"]
        claim_body = {
            "schema": "selfconnect-seat-delivery-claim-v1",
            "claim_id": "a" * 64,
            "idempotency_key": f"failover-delivery:{kwargs['operation_id']}",
            "operation_id": kwargs["operation_id"],
            "replacement_assignment_sha256": failover_module._digest(assignment),
            "continuity_sha256": failover_module._digest(continuity),
            "failover_root_id": case["context"].root_id,
            "claim_store_database_id": case["context"].claim_store_database_id,
        }
        claim = {**claim_body, "claim_commit_sha256": failover_module._digest(claim_body)}
        proof = _create_seat_actuation_proof(
            claim,
            seat_identity=case["new_seat"],
            result_sha256="d" * 64,
            acted_at=NOW + 3,
        )
        return failover_module._create_delivery_receipt(
            operation_id=kwargs["operation_id"],
            continuity=continuity,
            replacement_assignment=assignment,
            seat_identity=case["new_seat"],
            delivery_claim_id=claim["claim_id"],
            delivery_idempotency_key=claim["idempotency_key"],
            delivery_claim_commit_sha256=claim["claim_commit_sha256"],
            failover_root_id=case["context"].root_id,
            claim_store_database_id=case["context"].claim_store_database_id,
            actuation_proof=proof,
            result_sha256="d" * 64,
            assurance={
                "mode": "ordinary",
                "same_user_threat": "excluded",
                "external_monotonic_authority": "absent",
                "monotonicity": "local_best_effort",
                "high_assurance": False,
            },
            delivered_at=NOW + 3,
        )

    with pytest.raises(AssignmentFailoverError, match="no launch-pinned completed seat claim"):
        _run(case, guarded_delivery=forge_without_claim)
    with sqlite3.connect(case["delivery_store"].path) as connection:
        assert connection.execute("SELECT count(*) FROM delivery_claim_v1").fetchone()[0] == 0
    assert case["context"]._read_state()["seat_claims"] == {}
    assert _pending(case)[0]["stage"] == "assignment_issued"


def test_mutated_cached_context_object_cannot_rebind_the_launch_root(tmp_path):
    canonical = _case(tmp_path / "canonical")
    alternate = _case(tmp_path / "alternate")
    stale = canonical["context"]
    object.__setattr__(stale, "assignment_store", alternate["context"].assignment_store)
    object.__setattr__(stale, "assignment_store_path", alternate["context"].assignment_store_path)
    object.__setattr__(stale, "saga_store", alternate["context"].saga_store)
    object.__setattr__(stale, "saga_store_path", alternate["context"].saga_store_path)
    object.__setattr__(stale, "registry_path", alternate["context"].registry_path)
    object.__setattr__(stale, "event_log_path", alternate["context"].event_log_path)
    object.__setattr__(stale, "claim_store_path", alternate["context"].claim_store_path)
    object.__setattr__(stale, "root_id", alternate["context"].root_id)

    result, _shared = _run(canonical)
    launch_root = failover_module._require_launch_context().root_id
    assert result["delivery_receipt"]["failover_root_id"] == launch_root
    assert result["delivery_receipt"]["failover_root_id"] != stale.root_id
    assert alternate["context"].saga_store.pending() == []


def test_registry_only_rollback_cannot_return_delivered_on_resume(tmp_path):
    case = _case(tmp_path)
    active_registry = case["registry_path"].read_bytes()
    shared = {"audit": [], "guard": [], "delivery": [], "delivery_receipts": {}}
    first, _shared = _run(case, shared=shared)
    assert first["ok"] is True
    assert len(shared["delivery"]) == 1

    case["registry_path"].write_bytes(active_registry)
    with pytest.raises(
        AssignmentFailoverError,
        match="ordinary/no_external_anchor registry postcondition mismatch",
    ):
        _run(case, shared=shared)
    row = sc_mesh_registry.load_registry_strict(case["registry_path"])["agents"][0]
    assert row["status"] == "active"
    assert len(shared["delivery"]) == 1
