"""
checkpoint.py — Lightweight checkpoint/resume for multi-step agent tasks.

Persists task state to disk as JSON after each step. On restart, the agent
can resume from the last successful checkpoint rather than starting over.

Usage:
    from checkpoint import CheckpointManager, StepRecord

    with CheckpointManager("my-task-id", checkpoint_dir="checkpoints/") as mgr:
        for step in steps:
            if mgr.is_done(step.name):
                continue  # already completed in a previous run
            result = execute(step)
            mgr.mark_done(step.name, result=result)
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class StepRecord:
    """Record of a single step in a checkpointed task."""

    name: str
    status: str = "pending"  # pending / running / done / failed
    result: Any = None
    error: str | None = None
    started_at: float | None = None
    completed_at: float | None = None


class CheckpointManager:
    """Persist task state to disk as JSON after each step change."""

    def __init__(
        self,
        task_id: str,
        *,
        checkpoint_dir: str | os.PathLike = "checkpoints",
    ) -> None:
        self._task_id = task_id
        self._dir = os.path.abspath(str(checkpoint_dir))
        os.makedirs(self._dir, exist_ok=True)
        self._path = os.path.join(self._dir, f"{task_id}.json")
        self._steps: dict[str, StepRecord] = {}
        self._created_at: float = time.time()
        self._lock = threading.Lock()
        self._load()

    # -- public API --

    def is_done(self, step_name: str) -> bool:
        """True if step has status 'done' in persisted state."""
        rec = self._steps.get(step_name)
        return rec is not None and rec.status == "done"

    def mark_running(self, step_name: str) -> None:
        """Set step to running and persist."""
        with self._lock:
            rec = self._steps.get(step_name)
            if rec is None:
                rec = StepRecord(name=step_name)
                self._steps[step_name] = rec
            rec.status = "running"
            rec.started_at = time.time()
            self._save()

    def mark_done(self, step_name: str, *, result: Any = None) -> None:
        """Set step to done and persist."""
        with self._lock:
            rec = self._steps.get(step_name)
            if rec is None:
                rec = StepRecord(name=step_name)
                self._steps[step_name] = rec
            rec.status = "done"
            rec.result = result
            rec.completed_at = time.time()
            self._save()

    def mark_failed(self, step_name: str, *, error: str) -> None:
        """Set step to failed and persist."""
        with self._lock:
            rec = self._steps.get(step_name)
            if rec is None:
                rec = StepRecord(name=step_name)
                self._steps[step_name] = rec
            rec.status = "failed"
            rec.error = error
            rec.completed_at = time.time()
            self._save()

    def reset_step(self, step_name: str) -> None:
        """Remove a step from the checkpoint to force re-run."""
        with self._lock:
            self._steps.pop(step_name, None)
            self._save()

    def reset_all(self) -> None:
        """Delete the checkpoint file entirely."""
        with self._lock:
            self._steps.clear()
            try:
                os.remove(self._path)
            except FileNotFoundError:
                pass

    def summary(self) -> dict[str, Any]:
        """Return counts by status."""
        done = [n for n, r in self._steps.items() if r.status == "done"]
        failed = [n for n, r in self._steps.items() if r.status == "failed"]
        pending = [
            n for n, r in self._steps.items()
            if r.status not in ("done", "failed")
        ]
        return {
            "done": done,
            "failed": failed,
            "pending": pending,
            "total": len(self._steps),
        }

    def steps(self) -> dict[str, StepRecord]:
        """Return all recorded steps."""
        return dict(self._steps)

    # -- context manager --

    def __enter__(self) -> CheckpointManager:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        with self._lock:
            self._save()

    # -- internal --

    def _load(self) -> None:
        """Read JSON from disk; silent if file missing."""
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        self._created_at = data.get("created_at", self._created_at)
        for name, fields in data.get("steps", {}).items():
            self._steps[name] = StepRecord(**fields)

    def _save(self) -> None:
        """Atomically write JSON (write to temp, rename)."""
        data = {
            "task_id": self._task_id,
            "created_at": self._created_at,
            "steps": {name: asdict(rec) for name, rec in self._steps.items()},
        }
        fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            # On Windows, os.rename fails if target exists — remove first
            try:
                os.remove(self._path)
            except FileNotFoundError:
                pass
            os.rename(tmp, self._path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
