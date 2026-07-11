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


def test_export_replay_state_empty_on_fresh_session():
    session = fabric.FabricSession.from_secret("secret", session_id="test-session")
    assert session.export_replay_state() == []


def test_export_replay_state_captures_accepted_tuples():
    session = fabric.FabricSession.from_secret("secret", session_id="test-session")
    session.seal(sender="a", receiver="b", payload="msg1")
    encoded2 = session.seal(sender="c", receiver="d", payload="msg2")

    # Open one frame so it lands in accepted_sequences
    encoded1 = session.seal(sender="a", receiver="b", payload="hello")
    session.open(encoded1, expected_receiver="b")
    session.open(encoded2, expected_receiver="d")

    entries = session.export_replay_state()
    assert len(entries) == 2
    senders = {e["sender"] for e in entries}
    assert senders == {"a", "c"}
    for e in entries:
        assert "tuple_hash" in e
        assert len(e["tuple_hash"]) == 64


def test_import_replay_state_causes_replay_rejection():
    # Original session seals and opens a frame
    original = fabric.FabricSession.from_secret("secret", session_id="recover-test")
    encoded = original.seal(sender="a", receiver="b", payload="important")
    original.open(encoded, expected_receiver="b")

    # Export and re-import into a fresh session (simulating router restart)
    state = original.export_replay_state()
    recovered = fabric.FabricSession.from_secret("secret", session_id="recover-test")
    recovered.import_replay_state(state)

    # The recovered session must reject the already-accepted frame
    with pytest.raises(fabric.ReplayRejectedError):
        recovered.open(encoded, expected_receiver="b")


def test_import_replay_state_tuple_hash_matches():
    import hashlib

    session = fabric.FabricSession.from_secret("secret", session_id="hash-test")
    encoded = session.seal(sender="x", receiver="y", payload="data")
    session.open(encoded, expected_receiver="y")

    entries = session.export_replay_state()
    assert len(entries) == 1
    e = entries[0]
    expected = hashlib.sha256(f"{e['sender']}|{e['receiver']}|{e['sequence']}".encode()).hexdigest()
    assert e["tuple_hash"] == expected


def test_pipe_security_summary_returns_dict():
    from sc_fabric_v2 import pipe_security_summary

    result = pipe_security_summary()
    assert isinstance(result, dict)
    assert "dacl_hardened" in result
    assert "win32_security_available" in result
    assert isinstance(result["dacl_hardened"], bool)


def test_create_pipe_security_attributes_returns_none_or_object():
    from sc_fabric_v2 import create_pipe_security_attributes

    sa = create_pipe_security_attributes()
    if sys.platform == "win32":
        # May be None if win32security not installed, or a real SA object
        assert sa is None or hasattr(sa, "SECURITY_DESCRIPTOR")
    else:
        assert sa is None


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
