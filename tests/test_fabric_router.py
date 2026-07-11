import tempfile
from pathlib import Path

import pytest
import sc_fabric_router as router_mod
import sc_fabric_v2 as fabric


def _router(session_id: str = "router-test") -> tuple[fabric.FabricSession, router_mod.FabricSessionRouter]:
    session = fabric.FabricSession.from_secret("router-secret", session_id=session_id)
    router = router_mod.FabricSessionRouter(session=session, mailbox_depth=3)
    router.register_agent(role="a", birth_id="a")
    router.register_agent(role="b", birth_id="b")
    router.register_agent(role="c", birth_id="c")
    return session, router


def test_router_routes_frame_to_registered_mailbox():
    session, router = _router()
    frame = session.seal(sender="a", receiver="b", payload="hello")

    ack = session.open(router.route_frame(frame), expected_receiver="a")
    state = router.snapshot_state()

    assert ack.payload == b"ACK:a:1"
    assert state["mailboxes"]["b"]["depth"] == 1
    assert state["accepted_sequences"][0]["sequence"] == 1
    assert state["raw_text_included"] is False


def test_router_rejects_unknown_receiver():
    session, router = _router()
    frame = session.seal(sender="a", receiver="missing", payload="hello")

    with pytest.raises(router_mod.UnknownRouteError):
        router.route_frame(frame)


def test_router_restart_preserves_replay_rejection():
    session, router = _router("router-restart")
    frame = session.seal(sender="a", receiver="b", payload="hello")

    session.open(router.route_frame(frame), expected_receiver="a")
    state = router.snapshot_state()
    restored_session = fabric.FabricSession.from_secret("router-secret", session_id="router-restart")
    restored = router_mod.FabricSessionRouter.from_state(state, session=restored_session)

    with pytest.raises(fabric.ReplayRejectedError):
        restored.route_frame(frame)

    next_frame = restored_session.seal(sender="a", receiver="c", payload="after", sequence=2)
    ack = restored_session.open(restored.route_frame(next_frame), expected_receiver="a")
    assert ack.payload == b"ACK:a:2"


def test_router_selftest_writes_redacted_artifact():
    temp_dir = tempfile.TemporaryDirectory()

    try:
        artifact = router_mod.selftest(output_dir=temp_dir.name)
        assert artifact["ok"] is True
        assert artifact["replay_rejected_after_restart"] is True
        assert artifact["recovered_replay_state"] is True
        assert artifact["mailbox_payload_recovery"] is True
        assert artifact["accepted_sequence_count"] >= 2
        assert artifact["raw_text_included"] is False
        assert Path(artifact["artifact_path"]).exists()
        assert Path(artifact["state_path"]).exists()
    finally:
        temp_dir.cleanup()


def test_router_mailbox_payload_survives_restart():
    session = fabric.FabricSession.from_secret("payload-secret", session_id="payload-restart")
    router = router_mod.FabricSessionRouter(session=session, mailbox_depth=10)
    router.register_agent(role="a", birth_id="a")
    router.register_agent(role="b", birth_id="b")

    frame1 = session.seal(sender="a", receiver="b", payload="msg1", sequence=1)
    frame2 = session.seal(sender="a", receiver="b", payload="msg2", sequence=2)
    router.route_frame(frame1)
    router.route_frame(frame2)

    state = router.snapshot_state()
    assert "mailbox_payloads" in state
    assert len(state["mailbox_payloads"]["b"]) == 2

    restored_session = fabric.FabricSession.from_secret("payload-secret", session_id="payload-restart")
    restored = router_mod.FabricSessionRouter.from_state(state, session=restored_session)
    assert restored._mailboxes["b"].depth() == 2


def test_router_watchdog_detects_stale_agent():
    session = fabric.FabricSession.from_secret("watchdog-secret", session_id="watchdog-test")
    router = router_mod.FabricSessionRouter(session=session, mailbox_depth=5)
    router.register_agent(role="w1", birth_id="w1")

    # Agent with no heartbeat (heartbeat_at_ns == 0) is never stale
    assert router.check_watchdog(now_ns=1_000_000_000, timeout_ns=30_000_000_000) == []

    # Record a heartbeat at t=1000
    router.record_heartbeat("w1", now_ns=1000)

    # Past timeout threshold: stale
    stale = router.check_watchdog(now_ns=1000 + 30_000_000_001, timeout_ns=30_000_000_000)
    assert stale == ["w1"]

    # Within timeout: not stale
    not_stale = router.check_watchdog(now_ns=1000 + 1_000_000, timeout_ns=30_000_000_000)
    assert not_stale == []

    # No-op for unregistered birth_id
    router.record_heartbeat("nonexistent", now_ns=9999)  # should not raise
