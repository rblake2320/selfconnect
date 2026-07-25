"""Trusted MCP-to-Capability bridge with schema quarantine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

from sc_tasks import FileLock

from .broker import CapabilityBroker
from .evidence import EvidenceStore
from .integrity import IntegrityKey
from .models import SkillManifest
from .registry import SkillRegistry

SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
MAX_MCP_RESULT_CHARS = 12_000
MAX_MCP_TOOLS = 100
MAX_SCHEMA_DEPTH = 8
MAX_SCHEMA_PROPERTIES = 100


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_component(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not result or not result[0].isalpha():
        result = f"tool-{result}"
    return result


def _validate_schema(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise ValueError("MCP input schema exceeds maximum depth")
    if not isinstance(value, dict):
        raise ValueError("MCP input schema nodes must be objects")
    forbidden = {"$ref", "$dynamicRef", "allOf", "anyOf", "oneOf", "not"}
    present = forbidden.intersection(value)
    if present:
        raise ValueError(f"MCP input schema uses unsupported keywords: {sorted(present)}")
    schema_type = value.get("type", "object" if depth == 0 else None)
    if schema_type not in {None, "string", "integer", "number", "boolean", "array", "object"}:
        raise ValueError(f"unsupported MCP schema type: {schema_type!r}")
    properties = value.get("properties", {})
    if properties:
        if not isinstance(properties, dict) or len(properties) > MAX_SCHEMA_PROPERTIES:
            raise ValueError("MCP schema has invalid or excessive properties")
        for name, child in properties.items():
            if not isinstance(name, str) or not name:
                raise ValueError("MCP schema property names must be non-empty strings")
            _validate_schema(child, depth=depth + 1)
    items = value.get("items")
    if items is not None:
        _validate_schema(items, depth=depth + 1)
    required = value.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise ValueError("MCP schema required must be a string array")
    if set(required) - set(properties):
        raise ValueError("MCP schema requires undeclared properties")


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str
    command: str
    args: tuple[str, ...] = ()
    cwd: str = ""
    env_names: tuple[str, ...] = ()
    timeout_seconds: float = 30.0
    tool_permissions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    config_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        if not SAFE_NAME.fullmatch(self.name):
            raise ValueError(f"invalid MCP server name: {self.name!r}")
        if self.transport != "stdio":
            raise ValueError("M4 supports only stdio MCP servers")
        if not self.command:
            raise ValueError("MCP command is required")
        if not 0.1 <= self.timeout_seconds <= 300:
            raise ValueError("MCP timeout must be between 0.1 and 300 seconds")
        if any("=" in name or not name for name in self.env_names):
            raise ValueError("MCP env_names must contain names, not assignments")
        if self.config_digest and self.config_digest != self.digest():
            raise ValueError(f"MCP config digest mismatch for {self.name}")

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "cwd": self.cwd,
            "env_names": list(self.env_names),
            "timeout_seconds": self.timeout_seconds,
            "tool_permissions": {
                name: list(permissions)
                for name, permissions in sorted(self.tool_permissions.items())
            },
        }

    def digest(self) -> str:
        return _digest(self.unsigned_dict())

    def public_dict(self) -> dict[str, Any]:
        value = self.unsigned_dict()
        value["config_digest"] = self.config_digest or self.digest()
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, require_digest: bool = True) -> MCPServerConfig:
        allowed = {
            "name", "transport", "command", "args", "cwd", "env_names",
            "timeout_seconds", "tool_permissions", "config_digest",
        }
        unexpected = set(value) - allowed
        if unexpected:
            raise ValueError(f"unexpected MCP config fields: {sorted(unexpected)}")
        digest = str(value.get("config_digest", ""))
        if require_digest and not digest:
            raise ValueError("MCP server config is not digest-pinned")
        return cls(
            name=str(value.get("name", "")),
            transport=str(value.get("transport", "")),
            command=str(value.get("command", "")),
            args=tuple(map(str, value.get("args", []))),
            cwd=str(value.get("cwd", "")),
            env_names=tuple(map(str, value.get("env_names", []))),
            timeout_seconds=float(value.get("timeout_seconds", 30)),
            tool_permissions={
                str(name): tuple(map(str, permissions))
                for name, permissions in dict(value.get("tool_permissions", {})).items()
            },
            config_digest=digest,
        )


@dataclass(frozen=True)
class MCPToolDescriptor:
    name: str
    description: str
    input_schema: dict[str, Any]

    def __post_init__(self) -> None:
        if not SAFE_NAME.fullmatch(self.name):
            raise ValueError(f"invalid MCP tool name: {self.name!r}")
        if not self.description.strip() or len(self.description) > 1_000:
            raise ValueError("MCP tool description must contain 1-1000 characters")
        _validate_schema(self.input_schema)

    def fingerprint(self) -> str:
        return _digest({
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        })


class MCPClient(Protocol):
    def list_tools(self) -> list[MCPToolDescriptor]: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class MCPSchemaTrustStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.integrity = IntegrityKey(path.parent)

    def status(
        self,
        server: str,
        tool: MCPToolDescriptor,
        config_digest: str,
    ) -> dict[str, Any]:
        values = self._read()
        key = f"{server}:{tool.name}"
        row = values.get(key)
        fingerprint = tool.fingerprint()
        if not row:
            return {"status": "quarantined", "reason": "first_seen", "fingerprint": fingerprint}
        signature = str(row.pop("approval_hmac", ""))
        if signature != self.integrity.digest(row):
            return {
                "status": "quarantined",
                "reason": "approval_authentication_failed",
                "fingerprint": fingerprint,
            }
        if row.get("config_digest") != config_digest:
            return {
                "status": "quarantined",
                "reason": "server_config_changed",
                "fingerprint": fingerprint,
            }
        if row.get("fingerprint") != fingerprint:
            return {
                "status": "quarantined",
                "reason": "schema_changed",
                "fingerprint": fingerprint,
                "approved_fingerprint": row.get("fingerprint", ""),
            }
        return {"status": "approved", "reason": "exact_match", "fingerprint": fingerprint}

    def approve(
        self,
        server: str,
        tool: str,
        fingerprint: str,
        config_digest: str,
    ) -> None:
        if not fingerprint or len(fingerprint) != 64:
            raise ValueError("approval requires a SHA-256 fingerprint")
        lock = self.path.with_suffix(".lock")
        with FileLock(lock):
            values = self._read_unlocked()
            row = {
                "server": server,
                "tool": tool,
                "fingerprint": fingerprint,
                "config_digest": config_digest,
            }
            row["approval_hmac"] = self.integrity.digest(row)
            values[f"{server}:{tool}"] = row
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(values, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temp, self.path)

    def _read(self) -> dict[str, Any]:
        lock = self.path.with_suffix(".lock")
        with FileLock(lock):
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}


class MCPBridge:
    def __init__(
        self,
        *,
        config: MCPServerConfig,
        client: MCPClient,
        registry: SkillRegistry,
        broker: CapabilityBroker,
        evidence: EvidenceStore,
        trust: MCPSchemaTrustStore,
    ):
        self.config = config
        self.client = client
        self.registry = registry
        self.broker = broker
        self.evidence = evidence
        self.trust = trust

    def ingest(self) -> dict[str, Any]:
        tools = self._with_timeout(self.client.list_tools)
        if len(tools) > MAX_MCP_TOOLS:
            raise ValueError(f"MCP server exposes too many tools: {len(tools)}")
        approved = []
        quarantined = []
        for tool in tools:
            config_digest = self.config.config_digest or self.config.digest()
            state = self.trust.status(self.config.name, tool, config_digest)
            record = {
                "server": self.config.name,
                "tool": tool.name,
                **state,
            }
            if state["status"] != "approved":
                quarantined.append(record)
                self.evidence.append("mcp_tool_quarantined", **record)
                continue
            if tool.name not in self.config.tool_permissions:
                record["status"] = "quarantined"
                record["reason"] = "permission_mapping_missing"
                quarantined.append(record)
                self.evidence.append("mcp_tool_quarantined", **record)
                continue
            try:
                manifest = self._manifest(tool)
            except ValueError as exc:
                record["status"] = "quarantined"
                record["reason"] = "manifest_rejected"
                record["error"] = str(exc)
                quarantined.append(record)
                self.evidence.append("mcp_tool_quarantined", **record)
                continue
            self.registry.register(manifest, replace=True)
            self._bind(tool, manifest)
            approved.append({
                "capability": manifest.name,
                "tool": tool.name,
                "fingerprint": state["fingerprint"],
            })
            self.evidence.append(
                "mcp_tool_approved",
                server=self.config.name,
                tool=tool.name,
                capability=manifest.name,
                fingerprint=state["fingerprint"],
                config_digest=config_digest,
            )
        return {
            "ok": True,
            "server": self.config.name,
            "approved": approved,
            "quarantined": quarantined,
        }

    def _manifest(self, tool: MCPToolDescriptor) -> SkillManifest:
        server = _safe_component(self.config.name)
        name = _safe_component(tool.name)
        schema = dict(tool.input_schema)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        schema.setdefault("required", [])
        schema["additionalProperties"] = False
        return SkillManifest(
            name=f"mcp.{server}.{name}",
            version=f"schema-{tool.fingerprint()[:12]}",
            description=tool.description or f"MCP tool {tool.name} from {self.config.name}.",
            adapter=f"mcp-{server}-{name}",
            permissions=self.config.tool_permissions[tool.name],
            input_schema=schema,
            verification=("output-ok",),
            tags=("mcp", self.config.name, tool.name),
            provenance=f"mcp:{self.config.name}:{self.config.digest()}",
        )

    def _bind(self, tool: MCPToolDescriptor, manifest: SkillManifest) -> None:
        def invoke(**arguments: Any) -> dict[str, Any]:
            return self._with_timeout(lambda: self.client.call_tool(tool.name, arguments))

        try:
            self.broker.register_adapter(manifest.adapter, invoke)
        except ValueError:
            # A same-schema re-ingest keeps the existing trusted adapter.
            pass

    def _with_timeout(self, callback: Callable[[], Any]) -> Any:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"mcp-{self.config.name}")
        future = executor.submit(callback)
        try:
            return future.result(timeout=self.config.timeout_seconds)
        except FutureTimeout as exc:
            future.cancel()
            raise TimeoutError(f"MCP server {self.config.name} timed out") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


class StdioMCPClient:
    """Short-lived stdio MCP client; credentials stay in the child environment."""

    SAFE_ENV = (
        "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP",
        "LOCALAPPDATA", "APPDATA", "USERPROFILE",
    )

    def __init__(self, config: MCPServerConfig):
        self.config = config

    def list_tools(self) -> list[MCPToolDescriptor]:
        result = asyncio.run(self._list_tools())
        return [
            MCPToolDescriptor(
                name=str(item.get("name", "")),
                description=str(item.get("description", "")),
                input_schema=dict(item.get("inputSchema", {})),
            )
            for item in result
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(self._call_tool(name, arguments))

    def _environment(self) -> dict[str, str]:
        names = set(self.SAFE_ENV) | set(self.config.env_names)
        return {name: os.environ[name] for name in names if name in os.environ}

    async def _list_tools(self) -> list[dict[str, Any]]:
        async with self._session_context() as session:
            result = await session.list_tools()
            return [tool.model_dump(by_alias=True) for tool in result.tools]

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._session_context() as session:
            result = await session.call_tool(name, arguments)
            value = result.model_dump(by_alias=True)
            encoded = _canonical(value)
            if len(encoded) > MAX_MCP_RESULT_CHARS:
                value = {
                    "isError": bool(value.get("isError")),
                    "content": [{"type": "text", "text": encoded[:MAX_MCP_RESULT_CHARS] + "\n...[truncated]"}],
                    "truncated": True,
                }
            return {
                "ok": not bool(value.get("isError")),
                "server": self.config.name,
                "tool": name,
                "result": value,
                "untrusted_data": True,
            }

    def _session_context(self):
        from contextlib import asynccontextmanager

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        parameters = StdioServerParameters(
            command=self.config.command,
            args=list(self.config.args),
            env=self._environment(),
            cwd=self.config.cwd or None,
        )

        @asynccontextmanager
        async def session_context():
            async with stdio_client(parameters) as (read_stream, write_stream), ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=self.config.timeout_seconds),
            ) as session:
                await session.initialize()
                yield session

        return session_context()
