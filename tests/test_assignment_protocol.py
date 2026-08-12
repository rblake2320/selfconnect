from __future__ import annotations

import base64
import copy
import inspect
import json

import pytest
from sc_assignment_protocol import (
    MAX_DETAIL_BYTES,
    MAX_PAYLOAD_BYTES,
    AssignmentReplayError,
    AssignmentStateStore,
    AssignmentVerificationError,
    acknowledge_state_receipt,
    emit_state_receipt,
    issue_assignment,
    poll_state_receipts,
    verify_consume_ack,
    verify_consume_assignment,
    verify_consume_state_receipt,
)
from sc_guarded_submit import TargetIdentity
from sc_identity import AgentIdentity
from sc_seat_identity import create_enrollment
from sc_seat_identity import key_id as seat_key_id
from sc_terminal_tab import RUNTIME_ID_SCOPE, TerminalTabIdentity

PAYLOAD = "ASSIGN-SOL: act only on this coordinator-signed inline payload"
NOW = 2_000_000_000.0
RESULT_HASH = "a" * 64


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _resign(record, identity, **changes):
    body = copy.deepcopy(record)
    body.pop("signature_b64")
    body.update(changes)
    return {
        **body,
        "signature_b64": base64.b64encode(identity.sign(_canonical(body))).decode(),
    }


def _target(*, hwnd=101, pid=202, started=303):
    return TargetIdentity(
        hwnd=hwnd,
        pid=pid,
        exe_name="WindowsTerminal.exe",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        title="Codex seat",
        exe_path=r"C:\Program Files\WindowsApps\WindowsTerminal.exe",
        process_start_time_ns=started,
    )


def _tab(*, hwnd=101, pid=202, started=303, birth="seat-birth-1", runtime=(42, 7)):
    return TerminalTabIdentity(
        window_hwnd=hwnd,
        window_pid=pid,
        window_process_start_time_ns=started,
        tab_runtime_id=runtime,
        term_control_runtime_id=(99, 3),
        peer_birth_id=birth,
        runtime_id_scope=RUNTIME_ID_SCOPE,
    )


def _case(tmp_path, *, consume=True, assignment_ttl=60.0, enrollment_ttl=300.0):
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
        ttl_seconds=enrollment_ttl,
    )
    target = _target()
    tab = _tab()
    channel = {
        "transport": "private_named_pipe_v1",
        "pipe_instance": r"\\.\pipe\selfconnect\assignment\seat-birth-1",
        "server_nonce": "b" * 64,
    }
    coordinator_store = AssignmentStateStore(tmp_path / "coordinator.sqlite3")
    seat_store = AssignmentStateStore(tmp_path / "seat.sqlite3")
    assignment = issue_assignment(
        PAYLOAD,
        coordinator_identity=coordinator,
        coordinator_birth_id="codex-12-4abf6b40",
        coordinator_generation=7,
        receiver_enrollment=enrollment,
        authority_public_key_hex=authority.public_key_hex,
        target_identity=target,
        terminal_tab_identity=tab,
        response_receiver_public_key_hex=response_receiver.public_key_hex,
        response_channel=channel,
        store=coordinator_store,
        now=NOW,
        ttl_seconds=assignment_ttl,
    )
    verification = {
        "pinned_coordinator_public_key_hex": coordinator.public_key_hex,
        "authority_public_key_hex": authority.public_key_hex,
        "expected_coordinator_birth_id": "codex-12-4abf6b40",
        "expected_coordinator_generation": 7,
        "expected_receiver_birth_id": "seat-birth-1",
        "expected_receiver_generation": 3,
        "expected_target_identity": target,
        "expected_terminal_tab_identity": tab,
        "expected_response_receiver_public_key_hex": response_receiver.public_key_hex,
        "expected_response_channel": channel,
        "now": NOW + 1,
    }
    admitted = None
    if consume:
        admitted = verify_consume_assignment(
            assignment, store=seat_store, **verification
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
        "coordinator_store": coordinator_store,
        "seat_store": seat_store,
        "assignment": assignment,
        "admitted": admitted,
        "verification": verification,
    }


def _emit(case, state, sequence, *, detail=None, result=None, idem=None, now=None):
    return emit_state_receipt(
        case["assignment"],
        seat_identity=case["seat"],
        authority_public_key_hex=case["authority"].public_key_hex,
        current_target_identity=case["target"],
        current_terminal_tab_identity=case["tab"],
        current_response_receiver_public_key_hex=case["response_receiver"].public_key_hex,
        current_response_channel=case["channel"],
        state=state,
        detail={} if detail is None else detail,
        result_sha256=result,
        idempotency_key=idem or f"receipt-{sequence}",
        store=case["seat_store"],
        now=NOW + sequence if now is None else now,
    )


def _verify(case, receipt, *, now=None, **overrides):
    verification = {**case["verification"], **overrides}
    verification["now"] = NOW + receipt["sequence"] if now is None else now
    return verify_consume_state_receipt(
        receipt,
        case["assignment"],
        store=case["coordinator_store"],
        **verification,
    )


def _ack_verification(case):
    return {
        "pinned_coordinator_public_key_hex": case["coordinator"].public_key_hex,
        "authority_public_key_hex": case["authority"].public_key_hex,
        "expected_target_identity": case["target"],
        "expected_terminal_tab_identity": case["tab"],
        "expected_response_receiver_public_key_hex": case["response_receiver"].public_key_hex,
        "expected_response_channel": case["channel"],
    }


def test_end_to_end_inline_assignment_receipt_chain_and_post_commit_ack(tmp_path):
    case = _case(tmp_path)
    assert case["admitted"]["payload"] == PAYLOAD
    assert case["assignment"]["payload"] == PAYLOAD
    prior = None
    receipts = []
    for sequence, state in enumerate(("accepted", "working", "blocked", "working"), 1):
        receipt = _emit(case, state, sequence, detail={"status": state})
        verified = _verify(case, receipt)
        assert verified["sequence"] == sequence
        assert verified["newly_committed"] is True
        expected_prior = (
            "0" * 64
            if prior is None
            else __import__("hashlib").sha256(_canonical(prior)).hexdigest()
        )
        assert receipt["prior_receipt_sha256"] == expected_prior
        prior = receipt
        receipts.append(receipt)
    completed = _emit(
        case,
        "completed",
        5,
        detail={"artifact_count": 2, "tests": ["pytest", "ruff"]},
        result=RESULT_HASH,
    )
    committed = _verify(case, completed)
    assert committed["commit_sha256"] != "0" * 64
    ack = acknowledge_state_receipt(
        completed,
        coordinator_identity=case["coordinator"],
        store=case["coordinator_store"],
        now=NOW + 5.5,
    )
    verified_ack = verify_consume_ack(
        ack,
        completed,
        case["assignment"],
        store=case["seat_store"],
        now=NOW + 6,
        **_ack_verification(case),
    )
    assert verified_ack["receipt_commit_sha256"] == committed["commit_sha256"]


def test_assignment_payload_is_inline_bounded_and_external_substitution_is_impossible(tmp_path):
    case = _case(tmp_path)
    assert case["assignment"]["payload_sha256"] == __import__("hashlib").sha256(
        PAYLOAD.encode()
    ).hexdigest()
    assert "payload" not in inspect.signature(verify_consume_assignment).parameters
    forbidden_payloads = (
        "",
        "x" * (MAX_PAYLOAD_BYTES + 1),
        "bad\x00payload",
        "line one\rline two",
        "line one\nline two",
        "text \x1b[2J\x1b[H wiped",
        "\x04\x05\x0e\x14",
        "direction\u202eoverride",
        "line\u2028separator",
        "Cafe\u0301",
    )
    for index, payload in enumerate(forbidden_payloads):
        with pytest.raises(AssignmentVerificationError):
            issue_assignment(
                payload,
                coordinator_identity=case["coordinator"],
                coordinator_birth_id="codex-12-4abf6b40",
                coordinator_generation=7,
                receiver_enrollment=case["enrollment"],
                authority_public_key_hex=case["authority"].public_key_hex,
                target_identity=case["target"],
                terminal_tab_identity=case["tab"],
                response_receiver_public_key_hex=case["response_receiver"].public_key_hex,
                response_channel=case["channel"],
                store=AssignmentStateStore(tmp_path / f"bad-{index}.sqlite3"),
                now=NOW,
            )

    allowed = issue_assignment(
        "Café assignment for seat 🛡️",
        coordinator_identity=case["coordinator"],
        coordinator_birth_id="codex-12-4abf6b40",
        coordinator_generation=7,
        receiver_enrollment=case["enrollment"],
        authority_public_key_hex=case["authority"].public_key_hex,
        target_identity=case["target"],
        terminal_tab_identity=case["tab"],
        response_receiver_public_key_hex=case["response_receiver"].public_key_hex,
        response_channel=case["channel"],
        store=AssignmentStateStore(tmp_path / "unicode-ok.sqlite3"),
        now=NOW,
    )
    assert allowed["payload"] == "Café assignment for seat 🛡️"


def test_exact_target_terminal_tab_and_response_receiver_bindings_reject(tmp_path):
    case = _case(tmp_path, consume=False)
    wrong_values = (
        {"expected_target_identity": _target(hwnd=102)},
        {"expected_terminal_tab_identity": _tab(runtime=(42, 8))},
        {"expected_response_receiver_public_key_hex": AgentIdentity.generate("wrong").public_key_hex},
        {"expected_response_channel": {**case["channel"], "server_nonce": "c" * 64}},
        {"expected_receiver_birth_id": "another-seat"},
        {"expected_receiver_generation": 4},
        {"expected_coordinator_birth_id": "another-coordinator"},
        {"expected_coordinator_generation": 8},
    )
    for index, override in enumerate(wrong_values):
        with pytest.raises(AssignmentVerificationError):
            verify_consume_assignment(
                case["assignment"],
                store=AssignmentStateStore(tmp_path / f"wrong-{index}.sqlite3"),
                **{**case["verification"], **override},
            )
    with pytest.raises(AssignmentVerificationError, match="different terminal"):
        issue_assignment(
            PAYLOAD,
            coordinator_identity=case["coordinator"],
            coordinator_birth_id="codex-12-4abf6b40",
            coordinator_generation=7,
            receiver_enrollment=case["enrollment"],
            authority_public_key_hex=case["authority"].public_key_hex,
            target_identity=case["target"],
            terminal_tab_identity=_tab(hwnd=999),
            response_receiver_public_key_hex=case["response_receiver"].public_key_hex,
            response_channel=case["channel"],
            store=AssignmentStateStore(tmp_path / "mismatch.sqlite3"),
            now=NOW,
        )


def test_enrollment_hash_seat_epoch_and_all_receipt_bindings_are_exact(tmp_path):
    case = _case(tmp_path)
    receipt = _emit(case, "accepted", 1)
    for field, value in (
        ("receiver_seat_epoch", "f" * 64),
        ("receiver_enrollment_sha256", "e" * 64),
        ("target_identity_sha256", "d" * 64),
        ("terminal_tab_identity_sha256", "c" * 64),
        ("response_channel_sha256", "b" * 64),
        ("assignment_sha256", "a" * 64),
    ):
        forged = _resign(receipt, case["seat"], **{field: value})
        with pytest.raises(AssignmentVerificationError, match="binding"):
            _verify(case, forged)


def test_revoked_wrong_and_stale_coordinator_or_seat_reject(tmp_path):
    case = _case(tmp_path, consume=False)
    coordinator_id = seat_key_id(case["coordinator"].public_key_hex)
    seat_id = seat_key_id(case["seat"].public_key_hex)
    with pytest.raises(AssignmentVerificationError, match="coordinator key is revoked"):
        verify_consume_assignment(
            case["assignment"],
            store=AssignmentStateStore(tmp_path / "revoked-coordinator.sqlite3"),
            revoked_coordinator_key_ids=frozenset({coordinator_id}),
            **case["verification"],
        )
    with pytest.raises(AssignmentVerificationError, match="revoked"):
        verify_consume_assignment(
            case["assignment"],
            store=AssignmentStateStore(tmp_path / "revoked-seat.sqlite3"),
            revoked_seat_key_ids=frozenset({seat_id}),
            **case["verification"],
        )
    wrong = AgentIdentity.generate("wrong-coordinator")
    with pytest.raises(AssignmentVerificationError, match="signature"):
        verify_consume_assignment(
            case["assignment"],
            store=AssignmentStateStore(tmp_path / "wrong-coordinator.sqlite3"),
            **{**case["verification"], "pinned_coordinator_public_key_hex": wrong.public_key_hex},
        )
    with pytest.raises(AssignmentVerificationError, match="freshness"):
        verify_consume_assignment(
            case["assignment"],
            store=AssignmentStateStore(tmp_path / "stale-assignment.sqlite3"),
            **{**case["verification"], "now": NOW + 61},
        )


def test_assignment_consume_is_durable_and_rejects_replay_after_restart(tmp_path):
    case = _case(tmp_path)
    restarted = AssignmentStateStore(case["seat_store"].path)
    with pytest.raises(AssignmentReplayError, match="replay"):
        verify_consume_assignment(case["assignment"], store=restarted, **case["verification"])


def test_idempotent_receipt_retry_returns_exact_record_and_rejects_key_fork(tmp_path):
    case = _case(tmp_path)
    first = _emit(case, "accepted", 1, detail={"accepted": True}, idem="retry-1")
    retry = _emit(
        case,
        "accepted",
        99,
        detail={"accepted": True},
        idem="retry-1",
        now=NOW + 20,
    )
    assert retry == first
    restarted = AssignmentStateStore(case["seat_store"].path)
    case["seat_store"] = restarted
    assert _emit(case, "accepted", 100, detail={"accepted": True}, idem="retry-1") == first
    with pytest.raises(AssignmentReplayError, match="different content"):
        _emit(case, "working", 2, detail={"accepted": True}, idem="retry-1")


def test_consumer_accepts_exact_receipt_retry_but_rejects_fork_gap_and_backward(tmp_path):
    case = _case(tmp_path)
    accepted = _emit(case, "accepted", 1)
    first = _verify(case, accepted)
    case["coordinator_store"] = AssignmentStateStore(case["coordinator_store"].path)
    second = _verify(case, accepted)
    assert first["newly_committed"] is True
    assert second["newly_committed"] is False
    assert second["commit_sha256"] == first["commit_sha256"]
    fork = _resign(accepted, case["seat"], nonce="f" * 64)
    with pytest.raises(AssignmentReplayError, match="fork"):
        _verify(case, fork)
    working = _emit(case, "working", 2)
    gap = _resign(working, case["seat"], sequence=3)
    with pytest.raises(AssignmentReplayError, match="gap"):
        _verify(case, gap)
    _verify(case, working)
    backward = _resign(working, case["seat"], sequence=1)
    with pytest.raises(AssignmentReplayError, match=r"fork|backward"):
        _verify(case, backward)


def test_illegal_direct_terminal_and_post_terminal_transitions_reject(tmp_path):
    case = _case(tmp_path)
    for state, result in (("completed", RESULT_HASH), ("rejected", None)):
        with pytest.raises(AssignmentReplayError, match="transition"):
            _emit(case, state, 1, result=result, idem=f"direct-{state}")
    accepted = _emit(case, "accepted", 1)
    working = _emit(case, "working", 2)
    completed = _emit(case, "completed", 3, result=RESULT_HASH)
    assert [accepted["sequence"], working["sequence"], completed["sequence"]] == [1, 2, 3]
    with pytest.raises(AssignmentReplayError, match="transition"):
        _emit(case, "working", 4)
    restarted = AssignmentStateStore(case["seat_store"].path)
    case["seat_store"] = restarted
    with pytest.raises(AssignmentReplayError, match="transition"):
        _emit(case, "blocked", 5)


def test_receipt_structured_detail_and_result_hash_are_bounded_and_state_typed(tmp_path):
    case = _case(tmp_path)
    with pytest.raises(AssignmentVerificationError, match="bounded size"):
        _emit(case, "accepted", 1, detail={"text": "x" * (MAX_DETAIL_BYTES + 1)})

    for detail in (
        {"note": "done \x1b[2J\x1b[H cleared"},
        {"bidi": "safe\u202egnp.exe"},
        {"nested": ["line one\rline two"]},
        {"bad\x04key": "value"},
        {"not_nfc": "Cafe\u0301"},
    ):
        with pytest.raises(AssignmentVerificationError):
            _emit(case, "accepted", 1, detail=detail)
    with pytest.raises(AssignmentVerificationError, match="requires result"):
        _emit(case, "completed", 1)
    with pytest.raises(AssignmentVerificationError, match="only completed"):
        _emit(case, "accepted", 1, result=RESULT_HASH)
    with pytest.raises(AssignmentVerificationError):
        _emit(case, "accepted", 1, detail=float("nan"))


def test_emit_and_ack_recheck_live_target_channel_enrollment_and_revocation(tmp_path):
    case = _case(tmp_path)
    with pytest.raises(AssignmentVerificationError, match="live seat, target"):
        emit_state_receipt(
            case["assignment"],
            seat_identity=case["seat"],
            authority_public_key_hex=case["authority"].public_key_hex,
            current_target_identity=_target(hwnd=909),
            current_terminal_tab_identity=_tab(hwnd=909),
            current_response_receiver_public_key_hex=case["response_receiver"].public_key_hex,
            current_response_channel=case["channel"],
            state="accepted",
            detail={},
            result_sha256=None,
            idempotency_key="wrong-live-target",
            store=case["seat_store"],
            now=NOW + 1,
        )
    with pytest.raises(AssignmentVerificationError, match="coordinator key is revoked"):
        emit_state_receipt(
            case["assignment"],
            seat_identity=case["seat"],
            authority_public_key_hex=case["authority"].public_key_hex,
            current_target_identity=case["target"],
            current_terminal_tab_identity=case["tab"],
            current_response_receiver_public_key_hex=case["response_receiver"].public_key_hex,
            current_response_channel=case["channel"],
            state="accepted",
            detail={},
            result_sha256=None,
            idempotency_key="revoked-coordinator",
            store=case["seat_store"],
            revoked_coordinator_key_ids=frozenset(
                {seat_key_id(case["coordinator"].public_key_hex)}
            ),
            now=NOW + 1,
        )
    accepted = _emit(case, "accepted", 1)
    _verify(case, accepted)
    ack = acknowledge_state_receipt(
        accepted,
        coordinator_identity=case["coordinator"],
        store=case["coordinator_store"],
        now=NOW + 1,
    )
    with pytest.raises(AssignmentVerificationError, match="binding"):
        verify_consume_ack(
            ack,
            accepted,
            case["assignment"],
            store=case["seat_store"],
            now=NOW + 2,
            **{
                **_ack_verification(case),
                "expected_response_channel": {
                    **case["channel"],
                    "server_nonce": "d" * 64,
                },
            },
        )


def test_receipt_and_enrollment_freshness_bool_and_nonfinite_times_reject(tmp_path):
    case = _case(tmp_path)
    for bad_time in (True, float("nan"), float("inf")):
        with pytest.raises(AssignmentVerificationError):
            emit_state_receipt(
                case["assignment"],
                seat_identity=case["seat"],
                authority_public_key_hex=case["authority"].public_key_hex,
                current_target_identity=case["target"],
                current_terminal_tab_identity=case["tab"],
                current_response_receiver_public_key_hex=case["response_receiver"].public_key_hex,
                current_response_channel=case["channel"],
                state="accepted",
                detail={},
                result_sha256=None,
                idempotency_key=f"bad-time-{bad_time!s}",
                store=case["seat_store"],
                now=bad_time,
            )
    accepted = _emit(case, "accepted", 1, now=NOW + 10)
    with pytest.raises(AssignmentVerificationError, match="freshness"):
        _verify(case, accepted, now=NOW + 41)
    short = _case(tmp_path / "short", enrollment_ttl=20, assignment_ttl=10)
    receipt = _emit(short, "accepted", 1, now=NOW + 19)
    with pytest.raises(AssignmentVerificationError, match="enrollment"):
        _verify(short, receipt, now=NOW + 21)


def test_ack_requires_commit_is_idempotent_and_wrong_or_replayed_ack_rejects(tmp_path):
    case = _case(tmp_path)
    accepted = _emit(case, "accepted", 1)
    with pytest.raises(AssignmentVerificationError, match="not committed"):
        acknowledge_state_receipt(
            accepted,
            coordinator_identity=case["coordinator"],
            store=case["coordinator_store"],
            now=NOW + 1,
        )
    _verify(case, accepted)
    wrong = AgentIdentity.generate("wrong")
    with pytest.raises(AssignmentVerificationError, match="not the assignment coordinator"):
        acknowledge_state_receipt(
            accepted,
            coordinator_identity=wrong,
            store=case["coordinator_store"],
            now=NOW + 1,
        )
    ack = acknowledge_state_receipt(
        accepted,
        coordinator_identity=case["coordinator"],
        store=case["coordinator_store"],
        now=NOW + 1,
    )
    retry = acknowledge_state_receipt(
        accepted,
        coordinator_identity=case["coordinator"],
        store=AssignmentStateStore(case["coordinator_store"].path),
        now=NOW + 20,
    )
    assert retry == ack
    verify_consume_ack(
        ack,
        accepted,
        case["assignment"],
        store=case["seat_store"],
        now=NOW + 2,
        **_ack_verification(case),
    )
    with pytest.raises(AssignmentReplayError, match="replay"):
        verify_consume_ack(
            ack,
            accepted,
            case["assignment"],
            store=AssignmentStateStore(case["seat_store"].path),
            now=NOW + 2,
            **_ack_verification(case),
        )


def test_ack_tamper_expiry_and_receipt_binding_reject(tmp_path):
    case = _case(tmp_path)
    accepted = _emit(case, "accepted", 1)
    _verify(case, accepted)
    ack = acknowledge_state_receipt(
        accepted,
        coordinator_identity=case["coordinator"],
        store=case["coordinator_store"],
        now=NOW + 1,
        ttl_seconds=5,
    )
    forged = _resign(ack, case["coordinator"], target_identity_sha256="f" * 64)
    with pytest.raises(AssignmentVerificationError, match="binding"):
        verify_consume_ack(
            forged,
            accepted,
            case["assignment"],
            store=case["seat_store"],
            now=NOW + 2,
            **_ack_verification(case),
        )
    with pytest.raises(AssignmentVerificationError, match="freshness"):
        verify_consume_ack(
            ack,
            accepted,
            case["assignment"],
            store=case["seat_store"],
            now=NOW + 7,
            **_ack_verification(case),
        )


def test_duplicate_json_and_mutable_mapping_snapshot_semantics_reject_or_freeze(tmp_path):
    case = _case(tmp_path, consume=False)
    raw = json.dumps(case["assignment"], separators=(",", ":"))
    duplicate = raw[:-1] + ',"assignment_id":"duplicate"}'
    with pytest.raises(AssignmentVerificationError, match="duplicate JSON member"):
        verify_consume_assignment(
            duplicate,
            store=AssignmentStateStore(tmp_path / "duplicate.sqlite3"),
            **case["verification"],
        )
    mutable = copy.deepcopy(case["assignment"])
    admitted = verify_consume_assignment(
        mutable,
        store=AssignmentStateStore(tmp_path / "snapshot.sqlite3"),
        **case["verification"],
    )
    mutable["payload"] = "mutated after consume"
    assert admitted["payload"] == PAYLOAD


def test_signature_shape_bool_timestamp_and_unknown_fields_never_authenticate(tmp_path):
    case = _case(tmp_path, consume=False)
    with pytest.raises(AssignmentVerificationError):
        verify_consume_assignment(
            {"state": "working", "signed": True, "signature": "plain English"},
            store=AssignmentStateStore(tmp_path / "shape.sqlite3"),
            **case["verification"],
        )
    forged = _resign(case["assignment"], case["coordinator"], issued_at=True)
    with pytest.raises(AssignmentVerificationError):
        verify_consume_assignment(
            forged,
            store=AssignmentStateStore(tmp_path / "bad-issued-at.sqlite3"),
            **case["verification"],
        )
    nonfinite = json.dumps(case["assignment"]).replace(
        f'"expires_at": {case["assignment"]["expires_at"]}',
        '"expires_at": Infinity',
    )
    with pytest.raises(AssignmentVerificationError, match="non-finite"):
        verify_consume_assignment(
            nonfinite,
            store=AssignmentStateStore(tmp_path / "bad-expires-at.sqlite3"),
            **case["verification"],
        )
    extra = _resign(case["assignment"], case["coordinator"], extra=True)
    with pytest.raises(AssignmentVerificationError, match="fields"):
        verify_consume_assignment(
            extra,
            store=AssignmentStateStore(tmp_path / "extra.sqlite3"),
            **case["verification"],
        )


def test_polling_requires_guards_and_consumes_only_signed_receipts(tmp_path):
    case = _case(tmp_path)
    queue = [_emit(case, "accepted", 1), _emit(case, "working", 2)]
    ticks = [0.0]
    checks = []

    def guard():
        checks.append(True)
        return True

    result = poll_state_receipts(
        case["assignment"],
        receipt_source=lambda: queue.pop(0) if queue else None,
        source_guard=guard,
        target_guard=guard,
        until_states={"working"},
        timeout_seconds=2,
        poll_seconds=0.1,
        sleep=lambda seconds: ticks.__setitem__(0, ticks[0] + seconds),
        clock=lambda: ticks[0],
        store=case["coordinator_store"],
        **case["verification"],
    )
    assert result["state"] == "working" and len(checks) == 4
    with pytest.raises(AssignmentVerificationError, match="guard failed closed"):
        poll_state_receipts(
            case["assignment"],
            receipt_source=lambda: {"signed": True},
            source_guard=lambda: True,
            target_guard=lambda: False,
            until_states={"working"},
            timeout_seconds=1,
            poll_seconds=0.1,
            store=case["coordinator_store"],
            **case["verification"],
        )


def test_coordinator_and_seat_private_keys_never_share_a_signing_api():
    coordinator_parameters = set(inspect.signature(issue_assignment).parameters)
    ack_parameters = set(inspect.signature(acknowledge_state_receipt).parameters)
    seat_parameters = set(inspect.signature(emit_state_receipt).parameters)
    assert "coordinator_identity" in coordinator_parameters
    assert "coordinator_identity" in ack_parameters
    assert "seat_identity" not in coordinator_parameters | ack_parameters
    assert "seat_identity" in seat_parameters
    assert "coordinator_identity" not in seat_parameters
