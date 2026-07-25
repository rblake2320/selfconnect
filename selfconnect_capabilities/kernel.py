"""Feature-flagged Capability Kernel facade."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .broker import CapabilityBroker
from .builtin import BUILTIN_SKILLS
from .evidence import EvidenceStore
from .permissions import Authority
from .registry import SkillRegistry
from .task_graph import CompletionPredicate, TaskGraph, TaskStep
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
    def __init__(
        self,
        config: KernelConfig,
        authority: Authority,
        *,
        task_owner: str = "",
    ):
        self.config = config
        self.authority = authority
        self.task_owner = task_owner or authority.principal
        self.registry = SkillRegistry()
        for manifest in BUILTIN_SKILLS:
            self.registry.register(manifest)
        if config.enabled:
            for path in config.skill_paths:
                self.registry.load_directory(path, require_digest=True)
        self.evidence = EvidenceStore(config.state_dir / "evidence.jsonl")
        self.world = WorldStateStore(config.state_dir)
        self._state_refresher: Callable[[str], dict[str, Any]] | None = None
        self.broker = CapabilityBroker(self.registry, self.evidence)
        self.broker.register_verifier(
            "output-ok",
            lambda arguments, output: {"ok": bool(output.get("ok"))},
        )
        self.broker.register_adapter("world-state-query", self.query_world_state)

    def bind_adapter(self, adapter: str, callback: Callable[..., dict[str, Any]]) -> None:
        self.broker.register_adapter(adapter, callback)

    def register_state_refresher(self, callback: Callable[[str], dict[str, Any]]) -> None:
        self._state_refresher = callback

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

    def authorize(self, capability: str) -> dict[str, Any]:
        """Trusted-host preflight that always emits the broker policy decision."""
        self._require_enabled()
        return self.broker.authorize(capability, self.authority)

    def execute(
        self,
        capability: str,
        arguments: dict[str, Any],
        expected_manifest_digest: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        return self.broker.execute(
            capability,
            arguments,
            self.authority,
            expected_manifest_digest=expected_manifest_digest,
        ).as_dict()

    def query_world_state(
        self,
        prefix: str = "",
        include_stale: bool = False,
        limit: int = 100,
        refresh_if_stale: bool = True,
    ) -> dict[str, Any]:
        before = self.world.snapshot(prefix=prefix, include_stale=True, limit=limit)
        refreshed = False
        refresh_result: dict[str, Any] = {}
        if refresh_if_stale and before["fresh"] == 0 and self._state_refresher is not None:
            record = self.evidence.append("world_refresh_requested", prefix=prefix)
            refresh_result = self._state_refresher(prefix)
            refreshed = bool(refresh_result.get("ok"))
            self.evidence.append(
                "world_refresh_completed",
                prefix=prefix,
                request_evidence_id=record["event_id"],
                ok=refreshed,
                result=refresh_result,
            )
        result = self.world.snapshot(
            prefix=prefix,
            include_stale=include_stale,
            limit=limit,
        )
        result["refreshed"] = refreshed
        if refresh_result and not refreshed:
            result["refresh_error"] = refresh_result.get("error", "refresh failed")
        return result

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

    def new_task(
        self,
        goal: str,
        task_id: str = "",
        *,
        deadline_at: float = 0.0,
        max_total_attempts: int = 20,
        max_attempts_per_step: int = 2,
    ) -> TaskGraph:
        self._require_enabled()
        if not self.config.task_graphs:
            raise RuntimeError("durable task graphs are disabled")
        graph = TaskGraph(
            self.config.state_dir / "tasks" / f"{task_id or 'new'}.json",
            task_id=task_id,
            goal=goal,
            owner=self.task_owner,
            deadline_at=deadline_at,
            max_total_attempts=max_total_attempts,
            max_attempts_per_step=max_attempts_per_step,
        )
        if not task_id:
            graph.path = graph.path.with_name(f"{graph.task_id}.json")
        graph.save()
        return graph

    def load_task(self, task_id: str) -> TaskGraph:
        self._require_enabled()
        if not self.config.task_graphs:
            raise RuntimeError("durable task graphs are disabled")
        graph = TaskGraph.load(self.config.state_dir / "tasks" / f"{task_id}.json")
        self._require_task_owner(graph)
        self._reconcile_running(graph)
        return graph

    def add_task_step(
        self,
        graph: TaskGraph,
        capability: str,
        arguments: dict[str, Any],
        *,
        depends_on: tuple[str, ...] = (),
        step_id: str = "",
        completion: CompletionPredicate | None = None,
    ) -> TaskStep:
        self._require_enabled()
        self._require_task_owner(graph)
        manifest = self.registry.get(capability)
        step = TaskStep.create(
            capability,
            arguments,
            depends_on=depends_on,
            step_id=step_id,
            completion=completion,
            manifest_digest=manifest.manifest_digest or manifest.digest(),
        )
        graph.add(step)
        return step

    def recover_task(self, task_id: str) -> TaskGraph:
        self._require_enabled()
        if not self.config.task_graphs:
            raise RuntimeError("durable task graphs are disabled")
        graph = TaskGraph.recover(self.config.state_dir / "tasks" / f"{task_id}.json")
        self._require_task_owner(graph)
        self._reconcile_running(graph)
        return graph

    def run_ready(self, graph: TaskGraph, *, max_steps: int = 1) -> dict[str, Any]:
        self._require_enabled()
        if not self.config.task_graphs:
            raise RuntimeError("durable task graphs are disabled")
        self._require_task_owner(graph)
        admission = graph.admission()
        if not admission["ok"]:
            blocked = graph.block_unstarted(str(admission["reason"]))
            self.evidence.append(
                "task_admission_denied",
                task_id=graph.task_id,
                principal=self.authority.principal,
                reason=admission["reason"],
                attempts=admission["attempts"],
                blocked_steps=blocked,
            )
            return {"ok": False, "task": graph.summary(), "executed": [], "reason": admission["reason"]}
        executed = []
        for step in graph.ready()[:max(1, min(max_steps, 20))]:
            execution_id = uuid.uuid4().hex
            graph.start(step.step_id, execution_id)
            result = self.broker.execute(
                step.capability,
                step.arguments,
                self.authority,
                expected_manifest_digest=step.manifest_digest,
                evidence_context={
                    "task_id": graph.task_id,
                    "step_id": step.step_id,
                    "execution_id": execution_id,
                },
            ).as_dict()
            if result["ok"]:
                evidence = self.evidence.get(result.get("evidence_id", ""))
                completion = step.completion.evaluate(
                    capability=step.capability,
                    execution_id=execution_id,
                    evidence=evidence,
                )
                if completion["ok"]:
                    status = "completed"
                else:
                    status = "failed"
                    result["completion"] = completion
                    self.evidence.append(
                        "task_completion_rejected",
                        task_id=graph.task_id,
                        step_id=step.step_id,
                        execution_id=execution_id,
                        reasons=completion["reasons"],
                    )
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

    def retry_task_step(self, graph: TaskGraph, step_id: str, *, reason: str) -> dict[str, Any]:
        self._require_enabled()
        self._require_task_owner(graph)
        graph.retry(step_id, requester=self.task_owner, reason=reason)
        record = self.evidence.append(
            "task_step_retry_authorized",
            task_id=graph.task_id,
            step_id=step_id,
            principal=self.authority.principal,
            task_owner=self.task_owner,
            reason=reason,
            attempt=graph.steps[step_id].attempts,
        )
        return {"ok": True, "task": graph.summary(), "evidence_id": record["event_id"]}

    def cancel_task(self, graph: TaskGraph) -> dict[str, Any]:
        self._require_enabled()
        self._require_task_owner(graph)
        graph.cancel(requester=self.task_owner)
        record = self.evidence.append(
            "task_cancelled",
            task_id=graph.task_id,
            principal=self.authority.principal,
            task_owner=self.task_owner,
        )
        return {"ok": True, "task": graph.summary(), "evidence_id": record["event_id"]}

    def _reconcile_running(self, graph: TaskGraph) -> None:
        for step in graph.steps.values():
            if step.status != "running":
                continue
            evidence = self.evidence.find(
                "capability_completed",
                task_id=graph.task_id,
                step_id=step.step_id,
                execution_id=step.execution_id,
            )
            completion = step.completion.evaluate(
                capability=step.capability,
                execution_id=step.execution_id,
                evidence=evidence,
            )
            if completion["ok"]:
                graph.transition(
                    step.step_id,
                    "completed",
                    {
                        "ok": True,
                        "recovered": True,
                        "evidence_id": evidence["event_id"],
                        "completion": completion,
                    },
                )
                self.evidence.append(
                    "task_step_reconciled",
                    task_id=graph.task_id,
                    step_id=step.step_id,
                    execution_id=step.execution_id,
                    evidence_id=evidence["event_id"],
                )
            else:
                graph.transition(
                    step.step_id,
                    "blocked",
                    {
                        "ok": False,
                        "error": "ambiguous interrupted execution",
                        "completion": completion,
                    },
                )
                self.evidence.append(
                    "task_step_recovery_blocked",
                    task_id=graph.task_id,
                    step_id=step.step_id,
                    execution_id=step.execution_id,
                    reasons=completion["reasons"],
                )

    def _require_task_owner(self, graph: TaskGraph) -> None:
        if graph.owner != self.task_owner:
            raise PermissionError("task continuity owner mismatch")

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise RuntimeError("SelfConnect Capability Kernel is disabled")
