import sys
import tempfile
import uuid
from pathlib import Path

import pytest
import sc_fabric_host as host
import sc_fabric_v2 as fabric


@pytest.mark.skipif(sys.platform != "win32", reason="Fabric host IOCP proof requires Windows")
def test_fabric_host_roundtrip_uses_iocp_dispatch():
    session = fabric.FabricSession.ephemeral(session_id="host-test")
    service = host.FabricHostService(
        session=session,
        address=f"SelfConnectFabricHostTest_{uuid.uuid4().hex}",
        mailbox_depth=5,
    )

    try:
        service.start()
        result = host.host_roundtrip(
            session=session,
            address=service.address,
            sender="a",
            receiver="b",
            payload="hello",
        )
        stats = service.stats()
    finally:
        service.stop()

    assert result["ok"] is True
    assert result["ack_payload"] == "ACK:a:1"
    assert stats["completion_count"] == 1
    assert stats["mailboxes"]["b"]["depth"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Fabric host IOCP proof requires Windows")
def test_fabric_host_rejects_replayed_frame():
    session = fabric.FabricSession.ephemeral(session_id="host-replay")
    service = host.FabricHostService(
        session=session,
        address=f"SelfConnectFabricHostReplay_{uuid.uuid4().hex}",
        mailbox_depth=5,
    )
    replay = session.seal(sender="a", receiver="b", payload="hello", sequence=10)

    try:
        service.start()
        import multiprocessing.connection

        conn = multiprocessing.connection.Client(service.address, family="AF_PIPE")
        try:
            conn.send_bytes(replay)
            first = conn.recv_bytes()
        finally:
            conn.close()

        conn = multiprocessing.connection.Client(service.address, family="AF_PIPE")
        try:
            conn.send_bytes(replay)
            second = conn.recv_bytes()
        finally:
            conn.close()
        stats = service.stats()
    finally:
        service.stop()

    assert session.open(first, expected_receiver="a").payload == b"ACK:a:10"
    assert b"ReplayRejectedError" in second
    assert stats["completion_count"] == 1
    assert stats["rejected_count"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Fabric host IOCP proof requires Windows")
def test_fabric_host_selftest_writes_redacted_artifact():
    temp_dir = tempfile.TemporaryDirectory()

    try:
        artifact = host.selftest(output_dir=temp_dir.name)
        assert artifact["ok"] is True
        assert artifact["completion_dispatch"] == "win32_iocp_post_get"
        assert artifact["overlapped_pipe_io"] is False
        assert artifact["raw_text_included"] is False
        assert Path(artifact["artifact_path"]).exists()
    finally:
        temp_dir.cleanup()
