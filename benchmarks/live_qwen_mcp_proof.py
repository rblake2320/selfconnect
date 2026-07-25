"""Live Qwen proof for the read-only, schema-approved MCP bridge."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tomllib
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sc_local_agent_runtime import ActivityLedger, LocalAgentRuntime, RuntimeConfig  # noqa: E402
from selfconnect_capabilities.mcp_bridge import (  # noqa: E402
    MCPServerConfig,
    MCPSchemaTrustStore,
    StdioMCPClient,
)

SERVER = REPO_ROOT / "tests" / "fixtures" / "read_only_mcp_server.py"


@contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _stop_model(model: str) -> None:
    subprocess.run(
        ["ollama", "stop", model],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=30,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3.6:27b")
    parser.add_argument("--output", required=True)
    parser.add_argument("--state-dir", default="")
    args = parser.parse_args()

    output = Path(args.output).resolve()
    expected_version = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    state_dir = (
        Path(args.state_dir).resolve()
        if args.state_dir
        else output.parent / f"{output.stem}-state"
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    config = MCPServerConfig(
        name="selfconnect-read-only",
        transport="stdio",
        command=sys.executable,
        args=(str(SERVER),),
        cwd=str(REPO_ROOT),
        env_names=("SC_MCP_READ_ROOT",),
        timeout_seconds=15,
        tool_permissions={
            "repository_identity": ("mcp.selfconnect.read",),
        },
    )
    config_path = state_dir / "server.json"
    config_path.write_text(
        json.dumps(config.public_dict(), indent=2),
        encoding="utf-8",
    )
    controller_env = {"SC_MCP_READ_ROOT": str(REPO_ROOT)}
    with _environment(controller_env):
        descriptors = StdioMCPClient(config).list_tools()
    descriptor = next(
        item for item in descriptors
        if item.name == "repository_identity"
    )
    trust = MCPSchemaTrustStore(state_dir / "mcp_schema_trust.json")
    trust.approve(
        config.name,
        descriptor.name,
        descriptor.fingerprint(),
        config.digest(),
    )

    role = f"qwen-mcp-proof-{uuid.uuid4().hex[:8]}"
    runtime_config = RuntimeConfig(
        role=role,
        model=args.model,
        context_window=32_768,
        max_output_tokens=512,
        request_timeout_seconds=120,
        harness_profile="auto",
        temperature=0.0,
        seed=42,
        repo_root=REPO_ROOT,
    )
    env = {
        "SC_MCP_READ_ROOT": str(REPO_ROOT),
        "SC_CAPABILITY_KERNEL": "1",
        "SC_DYNAMIC_SKILLS": "1",
        "SC_CAPABILITY_STATE_DIR": str(state_dir),
        "SC_LOCAL_AGENT_STATE_DIR": str(state_dir / "activity"),
        "SC_MCP_SERVER_CONFIGS": str(config_path),
        "SC_CAPABILITY_EXTRA_PERMISSIONS": "mcp.selfconnect.read",
    }
    _stop_model(args.model)
    started = time.perf_counter()
    try:
        with _environment(env):
            runtime = LocalAgentRuntime(runtime_config)
            answer = runtime.respond(
                "Use the capability meta-tools to discover the approved read-only MCP "
                "repository identity skill. Inspect that exact skill, then execute it "
                "using its inspected manifest digest and empty arguments. Return "
                "MCP-QWEN-READ-OK followed by the real repository version and Git head. "
                "Do not call native file, command, window, or mesh tools."
            )
            ledger = ActivityLedger(runtime_config)
            events = ledger.history(
                limit=200,
                instance_id=runtime_config.instance_id,
            )["events"]
            called = [
                event["details"]["tool"]
                for event in events
                if event["event"] == "tool_called"
            ]
            evidence = runtime.kernel.evidence.verify()
            completed = runtime.kernel.evidence.find(
                "capability_completed",
                capability="mcp.selfconnect-read-only.repository-identity",
            )
            approved = runtime.kernel.evidence.find(
                "mcp_tool_approved",
                tool="repository_identity",
            )
    finally:
        _stop_model(args.model)

    expected_tools = [
        "capability_discover",
        "capability_inspect",
        "capability_execute",
    ]
    ok = (
        called == expected_tools
        and "MCP-QWEN-READ-OK" in answer
        and f'"version": "{expected_version}"' in json.dumps(completed or {})
        and evidence.get("ok") is True
        and completed is not None
        and approved is not None
    )
    report = {
        "schema": "selfconnect.live-qwen-mcp-proof.v1",
        "ok": ok,
        "model": args.model,
        "role": role,
        "instance_id": runtime_config.instance_id,
        "seconds": round(time.perf_counter() - started, 3),
        "server_config_digest": config.digest(),
        "tool_fingerprint": descriptor.fingerprint(),
        "called_tools": called,
        "answer": answer,
        "evidence_verification": evidence,
        "approved_evidence_id": (approved or {}).get("event_id", ""),
        "completed_evidence_id": (completed or {}).get("event_id", ""),
        "expected_repository_version": expected_version,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
