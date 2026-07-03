"""sc_tasks — file-backed task board with A2A-style lifecycle states.

Fixes SelfConnect's fire-and-forget dispatch: every unit of work is a Task
with a durable ID and a lifecycle any party can query by reading a file.
Claiming is file-locked (Agent Teams pattern), dependencies auto-unblock,
failed tasks retry then dead-letter to an escalation inbox, and every
transition is appended to a hash-chained JSONL event log (ProvenanceLedger
pattern from sc_identity).

Layout under a task root directory:

    <root>/tasks/<task_id>.json     one file per task (atomic replace)
    <root>/locks/<name>.lock        cross-process locks (O_EXCL + stale detect)
    <root>/events.jsonl             hash-chained event log

No Win32, no network — safe to import anywhere (hooks, remote nodes, tests).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

__version__ = "0.12.0"

GENESIS_HASH = "0" * 64
LOCK_STALE_SECONDS = 300.0
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05


class TaskState(str, Enum):
    """A2A task lifecycle states (a2a-protocol.org v1.0 names, lowercased)."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"


TERMINAL_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED}
)

# state -> allowed next states (fail closed on anything else)
_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.SUBMITTED: frozenset(
        {TaskState.WORKING, TaskState.CANCELED, TaskState.REJECTED, TaskState.FAILED}
    ),
    TaskState.WORKING: frozenset(
        {TaskState.INPUT_REQUIRED, TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}
    ),
    TaskState.INPUT_REQUIRED: frozenset(
        {TaskState.WORKING, TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset({TaskState.SUBMITTED}),  # retry re-submits
    TaskState.CANCELED: frozenset(),
    TaskState.REJECTED: frozenset(),
}


class TransitionError(RuntimeError):
    """Raised on an illegal lifecycle transition."""


class LockTimeout(RuntimeError):
    """Raised when a file lock cannot be acquired in time."""


@dataclass
class Task:
    task_id: str
    title: str
    prompt: str = ""
    agent: str = ""  # assigned/claiming agent name ("" = unassigned)
    state: TaskState = TaskState.SUBMITTED
    created_at: float = 0.0
    updated_at: float = 0.0
    attempts: int = 0
    max_attempts: int = 2
    depends_on: list[str] = field(default_factory=list)
    result: str = ""
    error: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Task:
        d = dict(d)
        d["state"] = TaskState(d["state"])
        return cls(**d)


class FileLock:
    """Cross-process lock via O_CREAT|O_EXCL with stale-lock breaking.

    Lock file holds "<pid>:<unix_ts>". A lock older than ``stale_after``
    seconds, or whose owning pid is dead, is broken. Works on Windows and
    POSIX; no dependencies beyond stdlib (+psutil if available for pid check).
    """

    def __init__(self, path: Path, timeout: float = LOCK_TIMEOUT_SECONDS,
                 stale_after: float = LOCK_STALE_SECONDS):
        self.path = Path(path)
        self.timeout = timeout
        self.stale_after = stale_after
        self._fd: Optional[int] = None

    def _pid_alive(self, pid: int) -> bool:
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            return True  # assume alive; stale_after still applies

    def _try_break_stale(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            pid_s, ts_s = raw.split(":", 1)
            pid, ts = int(pid_s), float(ts_s)
        except (OSError, ValueError):
            return  # unreadable or vanished — let acquire retry
        expired = (time.time() - ts) > self.stale_after
        if expired or not self._pid_alive(pid):
            try:
                self.path.unlink()
            except OSError:
                pass

    def acquire(self) -> FileLock:
        deadline = time.time() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, f"{os.getpid()}:{time.time()}".encode())
                return self
            except FileExistsError:
                self._try_break_stale()
                if time.time() >= deadline:
                    raise LockTimeout(f"could not acquire {self.path}") from None
                time.sleep(LOCK_POLL_SECONDS)

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> FileLock:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


class TaskBoard:
    """File-backed task board. Every mutation is locked, evented, chained."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.tasks_dir = self.root / "tasks"
        self.locks_dir = self.root / "locks"
        self.events_path = self.root / "events.jsonl"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    # ---------- locking ----------

    def _lock(self, name: str) -> FileLock:
        return FileLock(self.locks_dir / f"{name}.lock")

    def _board_lock(self) -> FileLock:
        return self._lock("board")

    # ---------- event chain ----------

    def _last_event_hash(self) -> str:
        if not self.events_path.exists():
            return GENESIS_HASH
        prior = GENESIS_HASH
        with self.events_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    prior = json.loads(line).get("entry_hash", prior)
        return prior

    def record_event(self, event_type: str, task_id: str = "", agent: str = "",
                     detail: Optional[dict] = None) -> dict:
        """Append a hash-chained event. Caller should hold the board lock
        when the event accompanies a task mutation (all internal callers do)."""
        entry = {
            "event_id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "event_type": event_type,
            "task_id": task_id,
            "agent": agent,
            "detail": detail or {},
            "prior_hash": self._last_event_hash(),
        }
        canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        entry["entry_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def verify_chain(self) -> tuple[bool, int]:
        """Replay the event chain. Returns (True, -1) or (False, bad_index)."""
        if not self.events_path.exists():
            return True, -1
        prior = GENESIS_HASH
        with self.events_path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                claimed = entry.pop("entry_hash", "")
                if entry.get("prior_hash") != prior:
                    return False, i
                canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
                if hashlib.sha256(canonical.encode()).hexdigest() != claimed:
                    return False, i
                prior = claimed
        return True, -1

    # ---------- task persistence ----------

    def _task_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.json"

    def _write_task(self, task: Task) -> None:
        task.updated_at = time.time()
        tmp = self._task_path(task.task_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(task.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, self._task_path(task.task_id))

    def get(self, task_id: str) -> Task:
        raw = self._task_path(task_id).read_text(encoding="utf-8")
        return Task.from_dict(json.loads(raw))

    def all_tasks(self) -> list[Task]:
        out = []
        for p in sorted(self.tasks_dir.glob("*.json")):
            try:
                out.append(Task.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue  # partial write in flight or foreign file
        return out

    # ---------- lifecycle ----------

    def create(self, title: str, prompt: str = "", agent: str = "",
               depends_on: Optional[list[str]] = None, max_attempts: int = 2,
               meta: Optional[dict] = None) -> Task:
        task = Task(
            task_id=uuid.uuid4().hex[:12],
            title=title,
            prompt=prompt,
            agent=agent,
            created_at=time.time(),
            depends_on=list(depends_on or []),
            max_attempts=max_attempts,
            meta=dict(meta or {}),
        )
        with self._board_lock():
            self._write_task(task)
            self.record_event("task.created", task.task_id, agent, {"title": title})
        return task

    def transition(self, task_id: str, new_state: TaskState, agent: str = "",
                   result: str = "", error: str = "",
                   meta_update: Optional[dict] = None) -> Task:
        """Locked read-modify-write with lifecycle validation."""
        new_state = TaskState(new_state)
        with self._board_lock():
            task = self.get(task_id)
            if new_state not in _TRANSITIONS[task.state]:
                raise TransitionError(
                    f"{task_id}: {task.state.value} -> {new_state.value} not allowed"
                )
            task.state = new_state
            if agent:
                task.agent = agent
            if result:
                task.result = result
            if error:
                task.error = error
            if meta_update:
                task.meta.update(meta_update)
            self._write_task(task)
            self.record_event(f"task.{new_state.value}", task_id, agent or task.agent,
                              {"error": error} if error else None)
        return task

    def _deps_satisfied(self, task: Task, by_id: dict[str, Task]) -> bool:
        for dep in task.depends_on:
            dep_task = by_id.get(dep)
            if dep_task is None or dep_task.state is not TaskState.COMPLETED:
                return False
        return True

    def claimable(self) -> list[Task]:
        """SUBMITTED tasks whose dependencies are all COMPLETED."""
        tasks = self.all_tasks()
        by_id = {t.task_id: t for t in tasks}
        return [
            t for t in tasks
            if t.state is TaskState.SUBMITTED and self._deps_satisfied(t, by_id)
        ]

    def claim(self, agent: str, task_id: str = "") -> Optional[Task]:
        """Atomically claim a task (specific id, or oldest claimable).

        File-locked so two agents can never claim the same task — the
        Agent Teams claiming pattern.
        """
        with self._board_lock():
            candidates = self.claimable()
            if task_id:
                candidates = [t for t in candidates if t.task_id == task_id]
            if not candidates:
                return None
            task = sorted(candidates, key=lambda t: t.created_at)[0]
            task.state = TaskState.WORKING
            task.agent = agent
            task.attempts += 1
            self._write_task(task)
            self.record_event("task.claimed", task.task_id, agent,
                              {"attempt": task.attempts})
            return task

    # ---------- retry / dead-letter ----------

    def retry(self, task_id: str, reason: str = "") -> Optional[Task]:
        """Re-submit a FAILED task if attempts remain; else None (dead-letter it)."""
        with self._board_lock():
            task = self.get(task_id)
            if task.state is not TaskState.FAILED:
                raise TransitionError(f"{task_id}: retry requires FAILED, got {task.state.value}")
            if task.attempts >= task.max_attempts:
                return None
            task.state = TaskState.SUBMITTED
            task.meta.setdefault("retries", []).append(
                {"ts": time.time(), "reason": reason}
            )
            self._write_task(task)
            self.record_event("task.retried", task_id, task.agent, {"reason": reason})
            return task

    def dead_letter(self, task_id: str, reason: str, inbox_dir: Path | str) -> Path:
        """Escalate an exhausted task to a human inbox and mark it failed."""
        inbox = Path(inbox_dir)
        inbox.mkdir(parents=True, exist_ok=True)
        with self._board_lock():
            task = self.get(task_id)
            if task.state is not TaskState.FAILED:
                task.state = TaskState.FAILED
                task.error = task.error or reason
            task.meta["dead_letter"] = {"ts": time.time(), "reason": reason}
            self._write_task(task)
            self.record_event("task.dead_letter", task_id, task.agent, {"reason": reason})
        path = inbox / f"dead-letter-{task.task_id}.md"
        path.write_text(
            f"# Dead letter: {task.title}\n\n"
            f"- task_id: `{task.task_id}`\n"
            f"- agent: {task.agent or '(unassigned)'}\n"
            f"- attempts: {task.attempts}/{task.max_attempts}\n"
            f"- reason: {reason}\n"
            f"- error: {task.error}\n\n"
            f"## Prompt\n\n{task.prompt}\n",
            encoding="utf-8",
        )
        return path

    # ---------- waiting ----------

    def wait_for_state(self, task_id: str, states: set[TaskState] | frozenset[TaskState],
                       timeout: float = 300.0, poll: float = 0.5) -> Optional[Task]:
        """Poll a task file until it reaches one of ``states``. State waits,
        not timing waits — replaces the blind sleep-and-check protocol."""
        states = {TaskState(s) for s in states}
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.get(task_id)
            if task.state in states:
                return task
            time.sleep(poll)
        return None


__all__ = [
    "GENESIS_HASH",
    "TERMINAL_STATES",
    "FileLock",
    "LockTimeout",
    "Task",
    "TaskBoard",
    "TaskState",
    "TransitionError",
]
