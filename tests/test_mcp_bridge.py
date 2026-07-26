from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sc_local_agent_runtime import LocalAgentRuntime, RuntimeConfig
from selfconnect_capabilities import Authority, CapabilityKernel, KernelConfig
from selfconnect_capabilities.mcp_bridge import (
    MCPBridge,
    MCPServerConfig,
    MCPSchemaTrustStore,
    StdioMCPClient,
)

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "tests" / "fixtures" / "read_only_mcp_server.py"


def _config(*, hostile: bool = False) -> MCPServerConfig:
    permissions = {
        "repository_identity": ("mcp.selfconnect.read",),
    }
    if hostile:
        permissions["hostile_description_probe"] = ("mcp.selfconnect.read",)
    return MCPServerConfig(
        name="selfconnect-read-only",
        transport="stdio",
        command=sys.executable,
        args=(str(SERVER),),
        env_names=("SC_MCP_READ_ROOT",),
        timeout_seconds=10,
        tool_permissions=permissions,
    )


def _bridge(tmp_path: Path, config: MCPServerConfig):
    os.environ["SC_MCP_READ_ROOT"] = str(ROOT)
    kernel = CapabilityKernel(
        KernelConfig(enabled=True, dynamic_skills=True, state_dir=tmp_path),
        Authority("qwen", frozenset({"mcp.selfconnect.read"})),
    )
    trust = MCPSchemaTrustStore(tmp_path / "mcp_trust.json")
    bridge = MCPBridge(
        config=config,
        client=StdioMCPClient(config),
        registry=kernel.registry,
        broker=kernel.broker,
        evidence=kernel.evidence,
        trust=trust,
    )
    return bridge, kernel, trust


def test_real_mcp_first_seen_is_quarantined_then_executes_after_exact_approval(
    tmp_path: Path,
) -> None:
    config = _config()
    bridge, kernel, trust = _bridge(tmp_path, config)

    first = bridge.ingest()
    assert first["approved"] == []
    descriptor = next(
        tool
        for tool in bridge.client.list_tools()
        if tool.name == "repository_identity"
    )
    trust.approve(
        config.name,
        descriptor.name,
        descriptor.fingerprint(),
        config.digest(),
    )

    second = bridge.ingest()
    manifest = kernel.registry.get("mcp.selfconnect-read-only.repository-identity")
    execution = kernel.broker.execute(
        manifest.name,
        {},
        Authority("qwen", frozenset({"mcp.selfconnect.read"})),
        expected_manifest_digest=manifest.digest(),
    )

    assert second["approved"][0]["capability"] == manifest.name
    assert execution.ok is True
    assert execution.output["untrusted_data"] is True
    text = json.dumps(execution.output)
    assert "selfconnect" in text
    assert kernel.evidence.verify()["ok"] is True


def test_real_mcp_permission_denial_occurs_before_server_execution(tmp_path: Path) -> None:
    config = _config()
    bridge, kernel, trust = _bridge(tmp_path, config)
    descriptor = next(
        tool
        for tool in bridge.client.list_tools()
        if tool.name == "repository_identity"
    )
    trust.approve(config.name, descriptor.name, descriptor.fingerprint(), config.digest())
    bridge.ingest()
    manifest = kernel.registry.get("mcp.selfconnect-read-only.repository-identity")

    result = kernel.broker.execute(
        manifest.name,
        {},
        Authority("qwen"),
        expected_manifest_digest=manifest.digest(),
    )

    assert result.ok is False
    assert result.verification["reason"] == "permission_denied"
    assert kernel.evidence.find("capability_denied", capability=manifest.name) is not None
    assert kernel.evidence.find("capability_completed", capability=manifest.name) is None


def test_real_hostile_mcp_description_is_rejected_after_approval(tmp_path: Path) -> None:
    config = _config(hostile=True)
    bridge, kernel, trust = _bridge(tmp_path, config)
    descriptor = next(
        tool
        for tool in bridge.client.list_tools()
        if tool.name == "hostile_description_probe"
    )
    trust.approve(config.name, descriptor.name, descriptor.fingerprint(), config.digest())

    result = bridge.ingest()
    hostile = next(
        item for item in result["quarantined"]
        if item["tool"] == "hostile_description_probe"
    )

    assert hostile["reason"] == "manifest_rejected"
    assert "instruction-like" in hostile["error"]
    assert all("hostile" not in skill.name for skill in kernel.registry.all())
    assert kernel.evidence.find(
        "mcp_tool_quarantined",
        tool="hostile_description_probe",
    ) is not None


def test_authenticated_mcp_approval_rejects_local_rewrite(tmp_path: Path) -> None:
    config = _config()
    bridge, _, trust = _bridge(tmp_path, config)
    descriptor = next(
        tool
        for tool in bridge.client.list_tools()
        if tool.name == "repository_identity"
    )
    trust.approve(config.name, descriptor.name, descriptor.fingerprint(), config.digest())
    values = json.loads(trust.path.read_text(encoding="utf-8"))
    values[f"{config.name}:{descriptor.name}"]["fingerprint"] = "0" * 64
    trust.path.write_text(json.dumps(values), encoding="utf-8")

    result = bridge.ingest()

    assert result["approved"] == []
    assert next(
        item for item in result["quarantined"]
        if item["tool"] == "repository_identity"
    )["reason"] == "approval_authentication_failed"


def test_real_runtime_loads_only_approved_digest_pinned_mcp_capability(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    config = _config()
    config_path = tmp_path / "server.json"
    config_path.write_text(json.dumps(config.public_dict()), encoding="utf-8")
    monkeypatch.setenv("SC_MCP_READ_ROOT", str(ROOT))
    monkeypatch.setenv("SC_CAPABILITY_KERNEL", "1")
    monkeypatch.setenv("SC_DYNAMIC_SKILLS", "1")
    monkeypatch.setenv("SC_CAPABILITY_STATE_DIR", str(state_dir))
    monkeypatch.setenv("SC_MCP_SERVER_CONFIGS", str(config_path))
    monkeypatch.setenv("SC_CAPABILITY_EXTRA_PERMISSIONS", "mcp.selfconnect.read")

    descriptor = next(
        tool
        for tool in StdioMCPClient(config).list_tools()
        if tool.name == "repository_identity"
    )
    MCPSchemaTrustStore(state_dir / "mcp_schema_trust.json").approve(
        config.name,
        descriptor.name,
        descriptor.fingerprint(),
        config.digest(),
    )

    runtime = LocalAgentRuntime(
        RuntimeConfig(
            role="real-mcp-runtime-test",
            model="qwen3.6:27b",
            repo_root=ROOT,
        )
    )
    inspected = runtime.kernel.inspect(
        "mcp.selfconnect-read-only.repository-identity"
    )
    executed = runtime.dispatch["capability_execute"](
        capability=inspected["skill"]["name"],
        arguments={},
        expected_manifest_digest=inspected["skill"]["manifest_digest"],
    )

    assert runtime.mcp_ingest_results[0]["approved"][0]["tool"] == "repository_identity"
    assert inspected["skill"]["available"] is True
    assert executed["ok"] is True
    assert executed["output"]["untrusted_data"] is True
