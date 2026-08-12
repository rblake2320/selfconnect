from __future__ import annotations

import inspect
import os
import threading

import pytest
from sc_identity import AgentIdentity
from sc_seat_identity import create_enrollment, enroll_receiver_key
from sc_seat_pipe import (
    SeatPipeEndpoint,
    SeatResponseReceiver,
    create_live_challenge,
    load_or_create_receiver_identity,
    send_seat_response,
    windows_process_start_time_100ns,
)


def test_production_challenge_api_has_no_caller_sid_or_pipe_instance():
    parameters = inspect.signature(create_live_challenge).parameters
    assert "expected_peer_sid" not in parameters
    assert "expected_pipe_instance" not in parameters


def test_receiver_identity_cannot_collapse_into_authority(tmp_path):
    authority = AgentIdentity.generate("authority")
    key_path = tmp_path / "receiver.pem"
    key_path.write_bytes(authority.private_pem(allow_private_export=True))
    with pytest.raises(ValueError, match="differ from authority"):
        load_or_create_receiver_identity(
            key_path,
            authority_public_key_hex=authority.public_key_hex,
        )


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
    receiver_store = enroll_receiver_key(receiver_identity, tmp_path / "receivers.json")
    receiver = SeatResponseReceiver(endpoint, challenge, receiver_identity, receiver_store)
    result = {}
    errors = []

    def run_server():
        try:
            result.update(receiver.serve_once(timeout=5.0))
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
    assert evidence == result["channel_evidence"]
    assert evidence["peer_sid"].startswith("S-1-")
    assert evidence["client_pid"] == os.getpid()
    assert evidence["client_process_start_100ns"] == windows_process_start_time_100ns(os.getpid())
    assert evidence["pipe_instance"] == endpoint.instance_id
