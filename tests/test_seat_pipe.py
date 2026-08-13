from __future__ import annotations

import inspect
import os
import threading

import pytest
import sc_seat_identity
from sc_authority_trust import _bootstrap_local_authority_trust_for_test
from sc_identity import AgentIdentity
from sc_seat_identity import (
    _canonical,
    _enroll_local_receiver_key_for_test,
    _sha256,
    create_enrollment,
    create_proof,
    deliver_challenge_postmessage,
    tab_snapshot_digest,
    verify_proof_runtime,
)
from sc_seat_pipe import (
    PipeObservation,
    SeatPipeEndpoint,
    SeatResponseReceiver,
    _open_pipe_observation,
    create_live_challenge,
    load_or_create_receiver_identity,
    send_seat_response,
    windows_process_start_time_100ns,
)
from sc_seat_revocation import (
    apply_revocation_snapshot,
    create_revocation_snapshot,
    sign_revocation_snapshot,
)
from sc_trust_anchor import HighAssuranceUnavailable


def test_production_challenge_api_has_no_caller_sid_or_pipe_instance():
    parameters = inspect.signature(create_live_challenge).parameters
    assert "expected_peer_sid" not in parameters
    assert "expected_pipe_instance" not in parameters


def test_no_public_channel_evidence_signing_constructor_remains():
    assert not hasattr(sc_seat_identity, "secure_channel_evidence")


def test_receiver_identity_cannot_collapse_into_authority(tmp_path):
    authority = AgentIdentity.generate("authority")
    key_path = tmp_path / "receiver.pem"
    key_path.write_bytes(authority.private_pem(allow_private_export=True))
    with pytest.raises(ValueError, match="differ from authority"):
        load_or_create_receiver_identity(
            key_path,
            authority_public_key_hex=authority.public_key_hex,
        )


def test_runtime_verifier_rejects_caller_supplied_evidence_dict():
    with pytest.raises(HighAssuranceUnavailable, match="separately privileged"):
        verify_proof_runtime(
            {"proof": {}, "channel_evidence": {}},
            challenge={},
            delivery={},
            enrollment={},
            authority_trust_store="missing",
            revocation_store="missing",
            receiver_trust_store="missing",
            expected_operation_sha256="11" * 32,
            expected_tab_snapshot_sha256="22" * 32,
            expected_target_hwnd=1,
            replay_store="missing",
            issue_store="missing",
        )


def test_pipe_receipt_has_no_introspectable_issuer_capability():
    import sc_seat_pipe

    assert not hasattr(sc_seat_pipe, "PipeIssuedEvidence")
    kwdefaults = SeatResponseReceiver.serve_once.__kwdefaults__ or {}
    assert "_issue_receipt" not in kwdefaults
    assert all(not callable(value) for value in kwdefaults.values())

    # Exact prior PoC can construct only an untrusted transport value now; no
    # issuer capability exists in Python defaults and runtime authority remains
    # unavailable without the separately privileged anchor boundary.
    forged = PipeObservation(_sha256(_canonical({})), {}, {})
    assert _open_pipe_observation(forged, {}) == ({}, {})
    with pytest.raises(HighAssuranceUnavailable, match="separately privileged"):
        verify_proof_runtime(
            forged,
            challenge={},
            delivery={},
            enrollment={},
            authority_trust_store="attacker-created",
            revocation_store="attacker-created",
            receiver_trust_store="attacker-created",
            expected_operation_sha256="11" * 32,
            expected_tab_snapshot_sha256="22" * 32,
            expected_target_hwnd=1,
            replay_store="attacker-created",
            issue_store="attacker-created",
        )
    with pytest.raises(AttributeError):
        forged.evidence = {"peer_sid": "caller"}


def test_non_windows_pipe_path_fails_closed():
    if os.name == "nt":
        pytest.skip("non-Windows contract")
    with pytest.raises(OSError, match="require Windows"):
        SeatPipeEndpoint.create()


@pytest.mark.skipif(os.name != "nt", reason="real Win32 named-pipe proof")
def test_live_one_shot_pipe_derives_sid_pid_and_process_start(tmp_path):
    authority = AgentIdentity.generate("authority")
    receiver_identity = AgentIdentity.generate("receiver")
    seat = AgentIdentity.generate("seat")
    enrollment = create_enrollment(
        seat_identity=seat,
        authority_identity=authority,
        birth_id="seat-live",
        generation=1,
    )
    endpoint = SeatPipeEndpoint.create()
    challenge = create_live_challenge(
        endpoint,
        enrollment=enrollment,
        operation_sha256="11" * 32,
        tab_snapshot_sha256="22" * 32,
        server_nonce="33" * 32,
        authority_identity=authority,
        expected_peer_pid=os.getpid(),
        expected_peer_process_start_100ns=windows_process_start_time_100ns(os.getpid()),
        issue_store=tmp_path / "issued.sqlite3",
    )
    receiver_store = _enroll_local_receiver_key_for_test(receiver_identity, tmp_path / "receivers.json")
    receiver = SeatResponseReceiver(endpoint, challenge, receiver_identity, receiver_store)
    result = {}
    errors = []

    def run_server():
        try:
            result["receipt"] = receiver.serve_once(timeout=5.0)
        except Exception as exc:  # pragma: no cover - surfaced in assertion
            errors.append(exc)

    thread = threading.Thread(target=run_server)
    thread.start()
    evidence = None
    client_error = None
    try:
        evidence = send_seat_response(endpoint, challenge, {"signature_b64": "placeholder"}, timeout=5.0)
    except Exception as exc:  # pragma: no cover - surfaced after server error
        client_error = exc
    thread.join(6.0)
    assert not errors
    if client_error is not None:
        raise client_error
    assert not thread.is_alive()
    proof, issued_evidence = _open_pipe_observation(result["receipt"], challenge)
    assert proof == {"signature_b64": "placeholder"}
    assert evidence == issued_evidence
    assert evidence["peer_sid"].startswith("S-1-")
    assert evidence["client_pid"] == os.getpid()
    assert evidence["client_process_start_100ns"] == f"{windows_process_start_time_100ns(os.getpid()):016x}"
    assert evidence["pipe_instance"] == endpoint.instance_id
    assert evidence["assurance"] == "same_user_observation"


@pytest.mark.skipif(os.name != "nt", reason="real Win32 named-pipe proof")
def test_live_pipe_rejects_client_outside_challenged_process_binding(tmp_path):
    authority = AgentIdentity.generate("authority")
    receiver_identity = AgentIdentity.generate("receiver")
    seat = AgentIdentity.generate("seat")
    enrollment = create_enrollment(
        seat_identity=seat,
        authority_identity=authority,
        birth_id="seat-live-wrong-pid",
        generation=1,
    )
    endpoint = SeatPipeEndpoint.create()
    challenge = create_live_challenge(
        endpoint,
        enrollment=enrollment,
        operation_sha256="11" * 32,
        tab_snapshot_sha256="22" * 32,
        server_nonce="33" * 32,
        authority_identity=authority,
        expected_peer_pid=os.getpid() + 100_000,
        expected_peer_process_start_100ns=windows_process_start_time_100ns(os.getpid()),
        issue_store=tmp_path / "issued.sqlite3",
    )
    receiver_store = _enroll_local_receiver_key_for_test(
        receiver_identity, tmp_path / "receivers.json"
    )
    receiver = SeatResponseReceiver(endpoint, challenge, receiver_identity, receiver_store)
    errors = []

    def run_server():
        try:
            receiver.serve_once(timeout=5.0)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=run_server)
    thread.start()
    with pytest.raises(OSError):
        send_seat_response(endpoint, challenge, {"signature_b64": "placeholder"}, timeout=5.0)
    thread.join(6.0)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], PermissionError)
    assert "PID" in str(errors[0])


@pytest.mark.skipif(os.name != "nt", reason="real Win32 named-pipe proof")
def test_live_pipe_receipt_does_not_claim_high_assurance_without_privileged_anchor(tmp_path):
    authority = AgentIdentity.generate("authority")
    recovery = AgentIdentity.generate("recovery")
    receiver_identity = AgentIdentity.generate("receiver")
    seat = AgentIdentity.generate("seat")
    trust = _bootstrap_local_authority_trust_for_test(
        tmp_path / "authority.json",
        root_public_keys=[authority.public_key_hex],
        quorum=1,
        recovery_public_keys=[recovery.public_key_hex],
        recovery_quorum=1,
    )
    revocations = tmp_path / "revocations.json"
    snapshot = create_revocation_snapshot(revocations, trust, [])
    apply_revocation_snapshot(
        revocations,
        trust,
        snapshot,
        [sign_revocation_snapshot(snapshot, authority)],
    )
    receiver_store = _enroll_local_receiver_key_for_test(receiver_identity, tmp_path / "receivers.json")
    enrollment = create_enrollment(
        seat_identity=seat,
        authority_identity=authority,
        birth_id="seat-live-runtime",
        generation=1,
    )
    live_snapshot = {
        "ok": True,
        "tab_runtime_id": [7, 11],
        "term_control_runtime_id": [7, 19],
        "peer_birth_id": "seat-live-runtime",
    }
    endpoint = SeatPipeEndpoint.create()
    issue_store = tmp_path / "issued.sqlite3"
    challenge = create_live_challenge(
        endpoint,
        enrollment=enrollment,
        operation_sha256="11" * 32,
        tab_snapshot_sha256=tab_snapshot_digest(live_snapshot),
        server_nonce="33" * 32,
        authority_identity=authority,
        expected_peer_pid=os.getpid(),
        expected_peer_process_start_100ns=windows_process_start_time_100ns(os.getpid()),
        issue_store=issue_store,
    )
    delivery = deliver_challenge_postmessage(
        challenge=challenge,
        target_hwnd=123,
        authority_identity=authority,
        sender=lambda hwnd, text: {
            "ok": True,
            "transport": "postmessage_wm_char",
            "chars_accepted": len(text),
            "target_hwnd": hwnd,
        },
        tab_checkpoint=lambda stage: {**live_snapshot, "stage": stage},
    )
    proof = create_proof(
        seat_identity=seat,
        challenge=challenge,
        delivery=delivery,
        authority_public_key_hex=authority.public_key_hex,
        issue_store=issue_store,
    )
    receiver = SeatResponseReceiver(endpoint, challenge, receiver_identity, receiver_store)
    result = {}

    def run_server():
        result["receipt"] = receiver.serve_once(timeout=5.0)

    thread = threading.Thread(target=run_server)
    thread.start()
    send_seat_response(endpoint, challenge, proof, timeout=5.0)
    thread.join(6.0)
    assert not thread.is_alive()
    with pytest.raises(HighAssuranceUnavailable, match="separately privileged"):
        verify_proof_runtime(
            result["receipt"],
            challenge=challenge,
            delivery=delivery,
            enrollment=enrollment,
            authority_trust_store=trust,
            revocation_store=revocations,
            receiver_trust_store=receiver_store,
            expected_operation_sha256="11" * 32,
            expected_tab_snapshot_sha256=tab_snapshot_digest(live_snapshot),
            expected_target_hwnd=123,
            replay_store=tmp_path / "replay.sqlite3",
            issue_store=issue_store,
        )
