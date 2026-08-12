from __future__ import annotations

import pytest
import self_connect
from sc_identity import AgentIdentity
from sc_seat_identity import (
    CHANNEL_SCHEMA,
    _signed,
    _time_ms,
    create_challenge,
    create_enrollment,
    create_proof,
    deliver_challenge_postmessage,
    key_id,
    tab_snapshot_digest,
)


def _test_channel(challenge, receiver, *, peer_sid, pipe_instance):
    body = {
        "schema": CHANNEL_SCHEMA,
        "transport": "private_named_pipe_v1",
        "response_address_sha256": challenge["response_address_sha256"],
        "server_nonce": challenge["server_nonce"],
        "peer_sid": peer_sid,
        "pipe_instance": pipe_instance,
        "observed_at": _time_ms(),
    }
    return {
        **_signed(body, receiver, "receiver_signature_b64"),
        "receiver_key_id": key_id(receiver.public_key_hex),
    }


def _enrolled_registry() -> tuple[self_connect.AgentRegistry, self_connect.PeerRecord]:
    registry = self_connect.AgentRegistry()
    record = registry.register(
        200,
        "peer-b",
        pid=77,
        birth_id="peer-b-birth",
        generation=2,
        seat_key_id="a" * 64,
    )
    return registry, record


def test_forged_visible_frame_is_advisory_and_cannot_transition_ready():
    registry, record = _enrolled_registry()
    watchdog = self_connect.WatchdogLoop(registry)
    events: list[dict] = []
    watchdog.on(events.append)

    forged = self_connect.parse_frame(self_connect.build_frame(200, 999, "PEER_READY", topic="legacy"))
    assert forged is not None
    watchdog._on_inbound_frame(forged)

    assert record.state is self_connect.PeerState.UNKNOWN
    assert record.observation_state is self_connect.PeerState.READY
    assert record.observation_source == "visible_frame"
    assert events[-1]["event"] == "PEER_FRAME_OBSERVED"
    assert events[-1]["authoritative"] is False


def test_clear_composer_screen_is_advisory_not_ready(monkeypatch):
    registry, record = _enrolled_registry()
    watchdog = self_connect.WatchdogLoop(registry)
    events: list[dict] = []
    watchdog.on(events.append)
    monkeypatch.setattr(self_connect, "_get_window_title", lambda _hwnd: "peer-b")
    monkeypatch.setattr(self_connect, "get_text_uia", lambda _hwnd: "Work complete\n")

    watchdog._poll_peer(record)

    assert record.state is self_connect.PeerState.UNKNOWN
    assert record.observation_state is self_connect.PeerState.READY
    assert record.observation_source == "uia_screen"
    assert events[-1]["event"] == "PEER_SCREEN_OBSERVED"
    assert events[-1]["authoritative"] is False


def test_unreadable_screen_fails_unknown_not_ready():
    registry, record = _enrolled_registry()
    watchdog = self_connect.WatchdogLoop(registry)

    assert watchdog._classify("", record) is self_connect.PeerState.UNKNOWN
    assert watchdog._classify("   \r\n", record) is self_connect.PeerState.UNKNOWN


def test_same_pid_windows_are_distinct_and_ready_requires_crypto_seat_proof(monkeypatch):
    own = self_connect.WindowTarget(
        hwnd=100,
        title="Owner",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        pid=77,
        exe_name="WindowsTerminal.exe",
    )
    peer = self_connect.WindowTarget(
        hwnd=200,
        title="Peer B",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        pid=77,
        exe_name="WindowsTerminal.exe",
    )
    monkeypatch.setattr(self_connect, "list_windows", lambda: [own, peer])

    selected = self_connect.find_target("Peer B", own_pid=77, own_hwnd=100)
    assert selected is peer

    registry, record = _enrolled_registry()
    with pytest.raises(ValueError, match="cryptographic seat proof"):
        registry.update_state(200, self_connect.PeerState.READY)
    assert record.state is self_connect.PeerState.UNKNOWN


def test_crypto_seat_proof_is_bound_to_exact_hwnd_even_when_pid_matches(tmp_path):
    authority = AgentIdentity.generate(label="authority")
    seat = AgentIdentity.generate(label="seat")
    receiver = AgentIdentity.generate(label="receiver")
    enrollment = create_enrollment(
        seat_identity=seat,
        authority_identity=authority,
        birth_id="peer-b-birth",
        generation=2,
    )
    operation_sha256 = "1" * 64
    tab_snapshot = {
        "ok": True,
        "tab_runtime_id": [1, 2],
        "term_control_runtime_id": [1, 3],
        "peer_birth_id": "peer-b-birth",
    }
    tab_snapshot_sha256 = tab_snapshot_digest(tab_snapshot)
    issue_store = tmp_path / "seat-issues.sqlite3"
    replay_store = tmp_path / "seat-replay.sqlite3"
    challenge = create_challenge(
        enrollment=enrollment,
        operation_sha256=operation_sha256,
        tab_snapshot_sha256=tab_snapshot_sha256,
        response_address_sha256="3" * 64,
        server_nonce="4" * 64,
        authority_identity=authority,
        issue_store=issue_store,
        expected_peer_sid="S-1-5-21-test",
        expected_pipe_instance="pipe-test",
    )
    delivery = deliver_challenge_postmessage(
        challenge=challenge,
        target_hwnd=200,
        authority_identity=authority,
        sender=lambda target, text: {
            "ok": True,
            "transport": "postmessage_wm_char",
            "chars_accepted": len(text),
            "target_hwnd": target,
        },
        tab_checkpoint=lambda _stage: dict(tab_snapshot),
    )
    proof = create_proof(
        seat_identity=seat,
        challenge=challenge,
        delivery=delivery,
        authority_public_key_hex=authority.public_key_hex,
        issue_store=issue_store,
    )
    channel = _test_channel(
        challenge,
        receiver,
        peer_sid="S-1-5-21-test",
        pipe_instance="pipe-test",
    )
    registry = self_connect.AgentRegistry()
    exact = registry.register(
        200,
        "peer-b",
        pid=77,
        birth_id=enrollment["birth_id"],
        generation=enrollment["generation"],
        seat_key_id=enrollment["seat_key_id"],
    )
    other = registry.register(
        201,
        "peer-c",
        pid=77,
        birth_id=enrollment["birth_id"],
        generation=enrollment["generation"],
        seat_key_id=enrollment["seat_key_id"],
    )
    verify_args = {
        "proof": proof,
        "challenge": challenge,
        "delivery": delivery,
        "enrollment": enrollment,
        "channel_evidence": channel,
        "authority_public_key_hex": authority.public_key_hex,
        "receiver_public_key_hex": receiver.public_key_hex,
        "expected_operation_sha256": operation_sha256,
        "expected_tab_snapshot_sha256": tab_snapshot_sha256,
        "replay_store": str(replay_store),
        "issue_store": str(issue_store),
    }

    with pytest.raises(ValueError, match="different HWND"):
        registry.confirm_ready(201, **verify_args)
    assert other.state is self_connect.PeerState.UNKNOWN

    registry.confirm_ready(200, **verify_args)
    assert exact.state is self_connect.PeerState.READY
