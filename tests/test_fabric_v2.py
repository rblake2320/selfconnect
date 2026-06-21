import json
import sys
import tempfile
import time
from pathlib import Path

import pytest
import sc_fabric_benchmark as bench
import sc_fabric_v2 as fabric

_RESOURCES = {
    "ram_free_mb": 100_000,
    "gpu": {"vram_free_mb": 24_000},
}


def test_fabric_session_seals_and_opens_frame():
    session = fabric.FabricSession.from_secret("secret", session_id="test-session")

    encoded = session.seal(sender="a", receiver="b", payload="hello")
    verified = session.open(encoded, expected_receiver="b")

    assert verified.sender == "a"
    assert verified.receiver == "b"
    assert verified.payload == b"hello"
    assert len(verified.payload_hash) == 64


def test_fabric_session_rejects_tampered_payload():
    session = fabric.FabricSession.from_secret("secret", session_id="test-session")
    encoded = session.seal(sender="a", receiver="b", payload="hello")
    record = json.loads(encoded.decode("utf-8"))
    record["payload_b64"] = "dGFtcGVyZWQ="
    tampered = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")

    with pytest.raises(fabric.FrameVerificationError, match="MAC mismatch"):
        session.open(tampered, expected_receiver="b")


def test_fabric_session_rejects_replay():
    session = fabric.FabricSession.from_secret("secret", session_id="test-session")
    encoded = session.seal(sender="a", receiver="b", payload="hello")

    session.open(encoded, expected_receiver="b")
    with pytest.raises(fabric.ReplayRejectedError):
        session.open(encoded, expected_receiver="b")


def test_fabric_session_rejects_expired_deadline():
    session = fabric.FabricSession.from_secret("secret", session_id="test-session")
    now = time.time_ns()
    encoded = session.seal(
        sender="a",
        receiver="b",
        payload="hello",
        deadline_ms=1,
        created_at_ns=now,
    )

    with pytest.raises(fabric.DeadlineExpiredError):
        session.open(encoded, expected_receiver="b", now_ns=now + 2_000_000)


def test_bounded_mailbox_applies_backpressure():
    mailbox = fabric.BoundedMailbox("b", max_depth=1)

    mailbox.put(b"one")
    with pytest.raises(fabric.MailboxFullError):
        mailbox.put(b"two", timeout_ms=1)
    assert mailbox.get() == b"one"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named-pipe proof requires Win32")
def test_named_pipe_roundtrip_uses_real_windows_pipe():
    session = fabric.FabricSession.ephemeral(session_id="pipe-test")

    result = fabric.named_pipe_roundtrip(
        session=session,
        sender="a",
        receiver="b",
        payload="hello",
        timeout_s=5,
    )

    assert result["ok"] is True
    assert result["transport"] == "windows_named_pipe"
    assert result["ack_payload"] == "ACK:a:1"
    assert result["raw_text_included"] is False


def test_fabric_v2_benchmark_uses_frame_mailbox_transport():
    temp_dir = tempfile.TemporaryDirectory()

    try:
        artifact = bench.run_benchmark(
            agent_count=3,
            messages_per_agent=2,
            stage="dry-run",
            profiles="normal",
            transport=bench.TRANSPORT_FABRIC_V2,
            output_dir=temp_dir.name,
            run_id="fabric_v2_test",
            resources=_RESOURCES,
        )

        assert artifact["ok"] is True
        assert artifact["transport"] == bench.TRANSPORT_FABRIC_V2
        assert artifact["logical_message_count"] == 6
        profile = artifact["profiles"][0]
        assert profile["fabric_v2"]["enabled"] is True
        assert profile["fabric_v2"]["mac_failures"] == 0
        assert profile["replay_attempts"]["accepted"] == 0
        assert profile["replay_attempts"]["rejected"] == 1
        assert Path(artifact["artifact_path"]).exists()
    finally:
        temp_dir.cleanup()


def test_fabric_v2_five_agent_baseline_uses_transport_specific_name():
    temp_dir = tempfile.TemporaryDirectory()

    try:
        artifact = bench.run_benchmark(
            agent_count=5,
            messages_per_agent=1,
            stage="production",
            profiles="normal",
            transport=bench.TRANSPORT_FABRIC_V2,
            output_dir=temp_dir.name,
            run_id="fabric_v2_baseline_name",
            resources=_RESOURCES,
        )

        baseline_path = Path(artifact["baseline"]["written_path"])
        assert baseline_path.name == "baseline_5agent_fabric_v2_frame_mailbox.json"
        loaded = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert loaded["transport"] == bench.TRANSPORT_FABRIC_V2
    finally:
        temp_dir.cleanup()
