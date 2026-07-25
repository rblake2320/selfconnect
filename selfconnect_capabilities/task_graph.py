"""Durable dependency-aware task state with fail-closed transitions."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TERMINAL = {"completed", "failed", "blocked", "cancelled"}
TRANSITIONS = {
    "pending": {"running", "blocked", "cancelled"},
    "running": {"completed", "failed", "blocked"},
    "blocked": {"pending", "cancelled"},
    "failed": {"pending", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


@dataclass
class TaskStep:
    step_id: str
    capability: str
    arguments: dict[str, Any]
    depends_on: tuple[str, ...] = ()
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        capability: str,
        arguments: dict[str, Any],
        *,
        depends_on: tuple[str, ...] = (),
        step_id: str = "",
    ) -> TaskStep:
        return cls(step_id or uuid.uuid4().hex, capability, arguments, depends_on)


class TaskGraph:
    def __init__(self, path: Path, *, task_id: str = "", goal: str = ""):
        self.path = path
        self.task_id = task_id or uuid.uuid4().hex
        self.goal = goal
        self.created_at = time.time()
        self.steps: dict[str, TaskStep] = {}

    def add(self, step: TaskStep) -> None:
        if step.step_id in self.steps:
            raise ValueError(f"duplicate step id: {step.step_id}")
        unknown = set(step.depends_on) - set(self.steps)
        if unknown:
            raise ValueError(f"step dependencies must already exist: {sorted(unknown)}")
        self.steps[step.step_id] = step
        self.save()

    def ready(self) -> list[TaskStep]:
        return [
            step for step in self.steps.values()
            if step.status == "pending"
            and all(self.steps[parent].status == "completed" for parent in step.depends_on)
        ]

    def transition(self, step_id: str, status: str, result: dict[str, Any] | None = None) -> None:
        step = self.steps[step_id]
        if status not in TRANSITIONS.get(step.status, set()):
            raise ValueError(f"invalid task transition: {step.status} -> {status}")
        if status == "running":
            step.attempts += 1
        step.status = status
        step.updated_at = time.time()
        if result is not None:
            step.result = result
        self.save()

    def summary(self) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        for step in self.steps.values():
            statuses[step.status] = statuses.get(step.status, 0) + 1
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "steps": len(self.steps),
            "statuses": statuses,
            "complete": bool(self.steps) and all(step.status == "completed" for step in self.steps.values()),
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "version": 1,
            "task_id": self.task_id,
            "goal": self.goal,
            "created_at": self.created_at,
            "steps": [asdict(step) for step in self.steps.values()],
        }
        temp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding="utf-8")
        os.replace(temp, self.path)

    @classmethod
    def load(cls, path: Path) -> TaskGraph:
        value = json.loads(path.read_text(encoding="utf-8"))
        graph = cls(path, task_id=value["task_id"], goal=value.get("goal", ""))
        graph.created_at = float(value.get("created_at", time.time()))
        for item in value.get("steps", []):
            item["depends_on"] = tuple(item.get("depends_on", ()))
            step = TaskStep(**item)
            graph.steps[step.step_id] = step
        return graph
