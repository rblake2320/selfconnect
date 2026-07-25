"""Durable task state with evidence-gated completion and checkpoint chaining."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sc_tasks import FileLock

TERMINAL = {"completed", "failed", "blocked", "cancelled"}
TRANSITIONS = {
    "pending": {"running", "blocked", "cancelled"},
    "running": {"completed", "failed", "blocked"},
    "blocked": {"pending", "cancelled"},
    "failed": {"pending", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class CompletionPredicate:
    """Runtime-owned proof requirements; a model completion claim is irrelevant."""

    kind: str = "capability_verified"
    require_output_ok: bool = True
    require_verification_ok: bool = True
    require_completed_evidence: bool = True

    def __post_init__(self) -> None:
        if self.kind != "capability_verified":
            raise ValueError(f"unsupported completion predicate: {self.kind}")

    def evaluate(
        self,
        *,
        capability: str,
        execution_id: str,
        evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if evidence is None:
            reasons.append("completion_evidence_missing")
            return {"ok": False, "reasons": reasons}
        details = evidence.get("details", {})
        if self.require_completed_evidence and evidence.get("event") != "capability_completed":
            reasons.append("wrong_evidence_event")
        if details.get("capability") != capability:
            reasons.append("capability_mismatch")
        if details.get("execution_id") != execution_id:
            reasons.append("execution_id_mismatch")
        if self.require_output_ok and not bool(details.get("output", {}).get("ok")):
            reasons.append("output_not_ok")
        if self.require_verification_ok and not bool(details.get("verification", {}).get("ok")):
            reasons.append("verification_not_ok")
        if not bool(details.get("ok")):
            reasons.append("broker_result_not_ok")
        return {
            "ok": not reasons,
            "reasons": reasons,
            "evidence_id": evidence.get("event_id", ""),
        }


@dataclass
class TaskStep:
    step_id: str
    capability: str
    arguments: dict[str, Any]
    depends_on: tuple[str, ...] = ()
    completion: CompletionPredicate = field(default_factory=CompletionPredicate)
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    execution_id: str = ""
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        capability: str,
        arguments: dict[str, Any],
        *,
        depends_on: tuple[str, ...] = (),
        step_id: str = "",
        completion: CompletionPredicate | None = None,
    ) -> TaskStep:
        return cls(
            step_id or uuid.uuid4().hex,
            capability,
            arguments,
            depends_on,
            completion or CompletionPredicate(),
        )


class TaskGraph:
    def __init__(self, path: Path, *, task_id: str = "", goal: str = ""):
        self.path = path
        self.task_id = task_id or uuid.uuid4().hex
        self.goal = goal
        self.created_at = time.time()
        self.steps: dict[str, TaskStep] = {}
        self.checkpoint_sequence = 0
        self.checkpoint_hash = ""

    @property
    def journal_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".checkpoints.jsonl")

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

    def start(self, step_id: str, execution_id: str) -> None:
        if not execution_id:
            raise ValueError("execution_id is required")
        step = self.steps[step_id]
        step.execution_id = execution_id
        self.transition(step_id, "running")

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
            "checkpoint_sequence": self.checkpoint_sequence,
            "checkpoint_hash": self.checkpoint_hash,
            "complete": bool(self.steps) and all(step.status == "completed" for step in self.steps.values()),
        }

    def _snapshot(self, sequence: int, previous_hash: str) -> dict[str, Any]:
        return {
            "version": 2,
            "task_id": self.task_id,
            "goal": self.goal,
            "created_at": self.created_at,
            "checkpoint_sequence": sequence,
            "previous_checkpoint_hash": previous_hash,
            "steps": [asdict(step) for step in self.steps.values()],
        }

    @staticmethod
    def _snapshot_hash(snapshot: dict[str, Any]) -> str:
        value = dict(snapshot)
        value.pop("checkpoint_hash", None)
        return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()

    @classmethod
    def _verify_snapshot(cls, snapshot: dict[str, Any]) -> str:
        expected = cls._snapshot_hash(snapshot)
        if snapshot.get("checkpoint_hash") != expected:
            raise ValueError("task checkpoint hash mismatch")
        return expected

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.journal_path.with_suffix(self.journal_path.suffix + ".lock")
        with FileLock(lock):
            journal = self._read_journal()
            journal_head = str(journal[-1].get("checkpoint_hash", "")) if journal else ""
            if journal_head != self.checkpoint_hash:
                raise ValueError("task checkpoint fork or rollback detected")
            sequence = self.checkpoint_sequence + 1
            snapshot = self._snapshot(sequence, self.checkpoint_hash)
            checkpoint_hash = self._snapshot_hash(snapshot)
            snapshot["checkpoint_hash"] = checkpoint_hash
            with self.journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(snapshot, sort_keys=True, ensure_ascii=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
            temp.write_text(json.dumps(snapshot, indent=2, ensure_ascii=True), encoding="utf-8")
            os.replace(temp, self.path)
            self.checkpoint_sequence = sequence
            self.checkpoint_hash = checkpoint_hash

    def _read_journal(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        rows = [
            json.loads(line)
            for line in self.journal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        previous = ""
        for index, row in enumerate(rows, 1):
            self._verify_snapshot(row)
            if row.get("checkpoint_sequence") != index:
                raise ValueError("task checkpoint sequence mismatch")
            if row.get("previous_checkpoint_hash") != previous:
                raise ValueError("task checkpoint chain mismatch")
            previous = str(row["checkpoint_hash"])
        return rows

    @classmethod
    def load(cls, path: Path) -> TaskGraph:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("version") == 2:
            cls._verify_snapshot(value)
        graph = cls(path, task_id=value["task_id"], goal=value.get("goal", ""))
        graph.created_at = float(value.get("created_at", time.time()))
        graph.checkpoint_sequence = int(value.get("checkpoint_sequence", 0))
        graph.checkpoint_hash = str(value.get("checkpoint_hash", ""))
        for item in value.get("steps", []):
            item["depends_on"] = tuple(item.get("depends_on", ()))
            item["completion"] = CompletionPredicate(**item.get("completion", {}))
            step = TaskStep(**item)
            graph.steps[step.step_id] = step
        if value.get("version") == 2:
            journal = graph._read_journal()
            if not journal or journal[-1]["checkpoint_hash"] != graph.checkpoint_hash:
                raise ValueError("task snapshot is not the latest witnessed checkpoint")
        return graph

    @classmethod
    def recover(cls, path: Path) -> TaskGraph:
        probe = cls(path)
        journal = probe._read_journal()
        if not journal:
            raise ValueError("no task checkpoint journal is available")
        snapshot = journal[-1]
        temp = path.with_suffix(path.suffix + f".{os.getpid()}.recovery.tmp")
        temp.write_text(json.dumps(snapshot, indent=2, ensure_ascii=True), encoding="utf-8")
        os.replace(temp, path)
        return cls.load(path)
