"""Feature-flagged Capability Kernel facade."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .broker import CapabilityBroker
from .builtin import BUILTIN_SKILLS
from .evidence import EvidenceStore
from .permissions import Authority
from .registry import SkillRegistry
from .task_graph import TaskGraph
from .world_state import WorldStateStore


def _enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class KernelConfig:
    enabled: bool = False
    dynamic_skills: bool = False
    task_graphs: bool = False
    skill_learning: str = "off"
    state_dir: Path = Path.cwd() / ".selfconnect-capabilities"
    skill_paths: tuple[Path, ...] = ()

    @classmethod
    def from_env(cls, state_dir: Path | None = None) -> KernelConfig:
        learning = os.environ.get("SC_SKILL_LEARNING", "off").strip().casefold()
        if learning not in {"off", "shadow"}:
            raise ValueError("SC_SKILL_LEARNING supports only off or shadow in v1")
        root = state_dir or Path(os.environ.get(
            "SC_CAPABILITY_STATE_DIR",
            Path(os.environ.get("LOCALAPPDATA", Path.cwd())) / "SelfConnect" / "capabilities",
        ))
        skill_paths = tuple(
            Path(item).resolve()
            for item in os.environ.get("SC_CAPABILITY_SKILL_PATHS", "").split(os.pathsep)
            if item.strip()
        )
        return cls(
            enabled=_enabled("SC_CAPABILITY_KERNEL"),
            dynamic_skills=_enabled("SC_DYNAMIC_SKILLS"),
            task_graphs=_enabled("SC_TASK_GRAPH"),
            skill_learning=learning,
            state_dir=root.resolve(),
            skill_paths=skill_paths,
        )


class CapabilityKernel:
    def __init__(self, config: KernelConfig, authority: Authority):
        self.config = config
        self.authority = authority
        self.registry = SkillRegistry()
        for manifest in BUILTIN_SKILLS:
            self.registry.register(manifest)
        if config.enabled:
            for path in config.skill_paths:
                self.registry.load_directory(path, require_digest=True)
        self.evidence = EvidenceStore(config.state_dir / "evidence.jsonl")
        self.world = WorldStateStore(config.state_dir)
        self.broker = CapabilityBroker(self.registry, self.evidence)
        self.broker.register_verifier(
            "output-ok",
            lambda arguments, output: {"ok": bool(output.get("ok"))},
        )
        self.broker.register_adapter("world-state-query", self.world.snapshot)

    def bind_adapter(self, adapter: str, callback: Callable[..., dict[str, Any]]) -> None:
        self.broker.register_adapter(adapter, callback)

    def discover(self, query: str, limit: int = 5) -> dict[str, Any]:
        self._require_enabled()
        return {
            "ok": True,
            "query": query,
            "skills": self.registry.discover(query, self.authority, limit=limit),
        }

    def inspect(self, capability: str) -> dict[str, Any]:
        self._require_enabled()
        manifest = self.registry.get(capability)
        result = manifest.public_dict()
        result["available"] = self.authority.can(manifest.permissions)
        result["missing_permissions"] = self.authority.missing(manifest.permissions)
        return {"ok": True, "skill": result}

    def execute(self, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        return self.broker.execute(capability, arguments, self.authority).as_dict()

    def observe(
        self,
        key: str,
        value: Any,
        *,
        source: str,
        confidence: float = 1.0,
        ttl_seconds: float = 60.0,
        sensitive: bool = False,
    ) -> dict[str, Any]:
        """Trusted-host observation path; intentionally not exposed as a model tool."""
        self._require_enabled()
        observation = self.world.observe(
            key,
            value,
            source=source,
            confidence=confidence,
            ttl_seconds=ttl_seconds,
            sensitive=sensitive,
        )
        record = self.evidence.append(
            "world_observation",
            key=key,
            source=source,
            confidence=confidence,
            expires_at=observation.expires_at,
            value_digest=observation.value_digest,
            sensitive=sensitive,
        )
        result = observation.public_dict()
        result["evidence_id"] = record["event_id"]
        return {"ok": True, "observation": result}

    def new_task(self, goal: str, task_id: str = "") -> TaskGraph:
        self._require_enabled()
        if not self.config.task_graphs:
            raise RuntimeError("durable task graphs are disabled")
        graph = TaskGraph(
            self.config.state_dir / "tasks" / f"{task_id or 'new'}.json",
            task_id=task_id,
            goal=goal,
        )
        if not task_id:
            graph.path = graph.path.with_name(f"{graph.task_id}.json")
        graph.save()
        return graph

    def load_task(self, task_id: str) -> TaskGraph:
        self._require_enabled()
        if not self.config.task_graphs:
            raise RuntimeError("durable task graphs are disabled")
        return TaskGraph.load(self.config.state_dir / "tasks" / f"{task_id}.json")

    def run_ready(self, graph: TaskGraph, *, max_steps: int = 1) -> dict[str, Any]:
        self._require_enabled()
        if not self.config.task_graphs:
            raise RuntimeError("durable task graphs are disabled")
        executed = []
        for step in graph.ready()[:max(1, min(max_steps, 20))]:
            graph.transition(step.step_id, "running")
            result = self.execute(step.capability, step.arguments)
            if result["ok"]:
                status = "completed"
            elif result.get("verification", {}).get("reason") == "permission_denied":
                status = "blocked"
            else:
                status = "failed"
            graph.transition(step.step_id, status, result)
            executed.append({"step_id": step.step_id, "status": status, "result": result})
            self.evidence.append(
                "task_step_transition",
                task_id=graph.task_id,
                step_id=step.step_id,
                status=status,
                capability=step.capability,
                evidence_id=result.get("evidence_id", ""),
            )
        return {"ok": True, "task": graph.summary(), "executed": executed}

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise RuntimeError("SelfConnect Capability Kernel is disabled")
