"""Tests for sc_fabric_service — FabricService long-lived wrapper."""

from __future__ import annotations

import json
import sys
import tempfile
import uuid

import pytest
import sc_fabric_service as svc_mod
from sc_fabric_service import FabricService, FabricServiceConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_config(tmp_dir: str) -> FabricServiceConfig:
    unique = uuid.uuid4().hex[:10]
    return FabricServiceConfig(
        state_dir=tmp_dir,
        pipe_name=f"SelfConnectFabricSvcUT_{unique}",
        session_secret=f"ut-secret-{unique}",
        session_id=f"sfv2-svctest-{unique}",
        mailbox_depth=10,
        request_timeout_s=5.0,
        watchdog_timeout_s=60.0,
        save_interval_s=300.0,  # don't auto-save during tests
    )


# ---------------------------------------------------------------------------
# 1. selftest
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Fabric service selftest requires Windows")
def test_service_selftest_on_windows():
    with tempfile.TemporaryDirectory() as tmp:
        artifact = svc_mod.selftest(output_dir=tmp)
    assert artifact["ok"] is True
    assert artifact["roundtrip_ok"] is True
    assert artifact["watchdog_ok"] is True
    assert artifact["restart_ok"] is True
    assert artifact["raw_text_included"] is False


# ---------------------------------------------------------------------------
# 2. start/stop roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Fabric service requires Windows")
def test_service_start_stop_roundtrip():
    from sc_fabric_host import host_roundtrip

    with tempfile.TemporaryDirectory() as tmp:
        config = _unique_config(tmp)
        service = FabricService(config)
        service.start()

        try:
            assert service._host is not None
            assert service._session is not None
            assert service._router is not None

            rt = host_roundtrip(
                session=service._session,
                address=service._host.address,
                sender="ut-client",
                receiver="ut-server",
                payload="SC_FABRIC_SVC_UT_ROUNDTRIP",
            )
        finally:
            service.stop()

    assert rt["ok"] is True
    assert rt["ack_payload"] == "ACK:ut-client:1"


# ---------------------------------------------------------------------------
# 3. state survives restart
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Fabric service requires Windows")
def test_service_state_survives_restart():
    with tempfile.TemporaryDirectory() as tmp:
        config = _unique_config(tmp)

        # First run: start, register agent, save state, stop
        service = FabricService(config)
        service.start()
        try:
            service.register_agent(role="ut-role", birth_id="ut-agent-1")
            saved = service.save_state()
        finally:
            service.stop()

        assert saved.exists(), "State file must exist after save_state()"

        # Verify JSON directly
        state = json.loads(saved.read_text(encoding="utf-8"))
        birth_ids = [a["birth_id"] for a in state.get("agents", [])]
        assert "ut-agent-1" in birth_ids, f"Expected 'ut-agent-1' in state agents, got: {birth_ids}"

        # Second run: reload state and confirm router has the agent
        config2 = FabricServiceConfig(
            state_dir=config.state_dir,
            pipe_name=f"SelfConnectFabricSvcUT2_{uuid.uuid4().hex[:10]}",
            session_secret=config.session_secret,
            session_id=config.session_id,
            mailbox_depth=config.mailbox_depth,
            request_timeout_s=config.request_timeout_s,
            watchdog_timeout_s=config.watchdog_timeout_s,
            save_interval_s=config.save_interval_s,
        )
        service2 = FabricService(config2)
        service2.start()
        try:
            assert service2._router is not None
            restored_agents = list(service2._router._agents.keys())
        finally:
            service2.stop()

        assert "ut-agent-1" in restored_agents, (
            f"Expected 'ut-agent-1' in restored router agents, got: {restored_agents}"
        )
