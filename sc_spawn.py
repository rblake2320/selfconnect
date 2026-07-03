"""sc_spawn — evented spawn wrapper: doorbell in, hooks + task board back.

Replaces "inject and hope" with a full request/response lifecycle:

    1. budget gate      agent-status daemon (:8089) approves the spawn
    2. worktree         optional git worktree so agents never trample a repo
    3. task created     durable unit of work on the TaskBoard (submitted)
    4. spawn            ``cmd.exe /k claude`` in its OWN conhost
                        (interactive TUI -> subscription billing; unique PID
                        -> console fast path + readable state)
    5. hooks installed  ack / notification / stop emitters in the agent cwd
    6. readiness wait   inject only when the TUI shows an input prompt
    7. doorbell         one-line injection: "Read <briefing> ..." — the
                        payload lives in a file, not in keystrokes
    8. ack wait         UserPromptSubmit hook flips the task to working;
                        no ack -> one re-ring -> dead-letter escalation

Win32 calls go through ``_sc()`` so tests can swap in a fake module.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from sc_hooks import install_hooks
from sc_tasks import TERMINAL_STATES, Task, TaskBoard, TaskState

__version__ = "0.12.0"

DEFAULT_BUDGET_URL = "http://localhost:8089"
DEFAULT_INBOX = Path(r"C:\Users\techai\PKA testing\Owner's Inbox")
SDK_DIR = Path(__file__).resolve().parent

READY_PATTERNS = ("? for shortcuts", "❯", "shift+tab to cycle")  # noqa: RUF001
BUSY_PATTERNS = ("esc to interrupt", "Cogitating", "✳")
BLOCKED_PATTERNS = ("Do you want", "1. Yes", "y/n", "Allow this")


class SpawnError(RuntimeError):
    pass


class ReadyState(str, Enum):
    READY = "ready"
    BUSY = "busy"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


def _sc():
    """Lazy import of the Win32 SDK — monkeypatch point for tests."""
    import self_connect
    return self_connect


# ---------- budget gate ----------

def check_agent_budget(url: str = DEFAULT_BUDGET_URL, strict: bool = False,
                       timeout: float = 2.0) -> tuple[bool, str]:
    """Ask the agent-status daemon whether another spawn fits the budget.

    Understands ``allowed``/``blocked`` booleans or ``usd_spent``/``usd_limit``
    pairs. Daemon unreachable -> allow (fail open) unless ``strict``.
    """
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/status", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        if strict:
            return False, f"budget daemon unreachable ({exc}) and strict mode on"
        return True, f"budget daemon unreachable ({exc}) — allowing (non-strict)"
    if data.get("blocked") is True or data.get("allowed") is False:
        return False, f"budget daemon denied spawn: {data}"
    spent, limit = data.get("usd_spent"), data.get("usd_limit")
    if isinstance(spent, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        if spent >= limit:
            return False, f"budget exhausted: {spent} >= {limit} USD"
        return True, f"budget ok: {spent}/{limit} USD"
    return True, "budget daemon reachable, no limit fields — allowing"


# ---------- readiness ----------

def detect_tui_state(text: str) -> ReadyState:
    """Classify Claude Code TUI state from visible text. Order matters:
    a permission dialog outranks a spinner outranks a prompt."""
    if not text:
        return ReadyState.UNKNOWN
    if any(p in text for p in BLOCKED_PATTERNS):
        return ReadyState.BLOCKED
    if any(p in text for p in BUSY_PATTERNS):
        return ReadyState.BUSY
    if any(p in text for p in READY_PATTERNS):
        return ReadyState.READY
    return ReadyState.UNKNOWN


def _default_reader(target) -> str:
    sc = _sc()
    try:
        text = sc.read_console_fast(target)
        if text:
            return text
    except Exception:
        pass
    try:
        return sc.get_window_text(target.hwnd) or ""
    except Exception:
        return ""


def wait_until_ready(target, timeout: float = 60.0, poll: float = 1.0,
                     reader: Optional[Callable] = None) -> ReadyState:
    """Poll the TUI until it shows an input prompt. Returns the final state
    (READY, or whatever it was stuck in at timeout)."""
    reader = reader or _default_reader
    state = ReadyState.UNKNOWN
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = detect_tui_state(reader(target))
        if state is ReadyState.READY:
            return state
        time.sleep(poll)
    return state


# ---------- worktree ----------

def create_worktree(repo: Path | str, agent: str, base_dir: Path | str) -> Path:
    """Give the agent an isolated ``git worktree`` so parallel agents never
    trample one checkout."""
    repo, base_dir = Path(repo), Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    # uuid4 suffix: same agent spawning twice within one second must not collide
    slug = f"{agent}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    wt_path = base_dir / f"wt-{slug}"
    branch = f"sc/{slug}"
    proc = subprocess.run(
        ["git", "worktree", "add", str(wt_path), "-b", branch],
        cwd=str(repo), capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise SpawnError(f"git worktree add failed: {proc.stderr.strip()}")
    return wt_path


# ---------- briefing ----------

def write_briefing(task_root: Path | str, agent: str, task: Task, cwd: Path | str,
                   extra: str = "") -> Path:
    """The payload lives here — injection only carries the doorbell."""
    task_root = Path(task_root)
    briefings = task_root / "briefings"
    briefings.mkdir(parents=True, exist_ok=True)
    done = SDK_DIR / "sc_done.py"
    path = briefings / f"{agent}-{task.task_id}.md"
    path.write_text(
        f"# Briefing for {agent} — task {task.task_id}\n\n"
        f"You are **{agent}**, a spawned SelfConnect mesh agent.\n"
        f"Working directory: `{cwd}`\n\n"
        f"## Task\n\n{task.prompt}\n\n"
        f"## Protocol (mandatory)\n\n"
        f"1. Work only inside your working directory (it may be a dedicated git worktree).\n"
        f"2. When the work is DONE and verified, run exactly:\n\n"
        f'   python "{done}" --task-root "{task_root}" --task-id {task.task_id} '
        f'--result "one-paragraph summary"\n\n'
        f"   (use --result-file for long results; use --fail --error \"why\" if blocked)\n"
        f"3. Do NOT claim completion without running that command — it is the only\n"
        f"   completion signal the orchestrator accepts.\n"
        f"4. Larger artifacts go to `{task_root / 'results' / task.task_id}`.\n"
        + (f"\n## Additional context\n\n{extra}\n" if extra else ""),
        encoding="utf-8",
    )
    return path


# ---------- spawn ----------

@dataclass
class SpawnResult:
    agent: str
    task_id: str
    ok: bool
    hwnd: int = 0
    pid: int = 0
    briefing_path: str = ""
    cwd: str = ""
    detail: str = ""
    meta: dict = field(default_factory=dict)


def _launch(cwd: Path, claude_cmd: str, launcher: str) -> None:
    if launcher == "wt":
        subprocess.Popen(
            ["wt.exe", "-w", "new", "--title", "SC-Agent", "-d", str(cwd),
             "cmd", "/k", claude_cmd],
        )
    else:  # dedicated conhost: unique PID, readable console, proven billing path
        # cwd= instead of `cd /d "..."`: list2cmdline backslash-escapes the
        # inner quotes, which cmd.exe rejects on paths with spaces (live-caught).
        subprocess.Popen(
            ["cmd.exe", "/k", claude_cmd],
            cwd=str(cwd),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )


def _wait_new_window(before_hwnds: set[int], timeout: float, poll: float = 1.0):
    sc = _sc()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for w in sc.list_windows():
            if w.hwnd not in before_hwnds and w.class_name in (
                "CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass",
            ):
                return w
        time.sleep(poll)
    return None


def _ring_doorbell(target, briefing: Path, task: Task) -> None:
    sc = _sc()
    line = f'Read "{briefing}" and follow it exactly. Your task id is {task.task_id}.'
    sc.send_string(target, line, char_delay=0.02)
    time.sleep(1.0)
    sc.submit_claude_input(target.hwnd)


def spawn_agent(
    name: str,
    prompt: str,
    cwd: Path | str,
    task_root: Path | str,
    claude_cmd: str = "claude",
    launcher: str = "conhost",
    worktree_from: Optional[Path | str] = None,
    briefing_extra: str = "",
    budget_url: str = DEFAULT_BUDGET_URL,
    budget_strict: bool = False,
    inbox_dir: Path | str = DEFAULT_INBOX,
    window_timeout: float = 45.0,
    ready_timeout: float = 90.0,
    ack_timeout: float = 60.0,
    reader: Optional[Callable] = None,
) -> SpawnResult:
    """Spawn a peer Claude, brief it, and confirm delivery. Never hangs
    silently: no ack -> one retry -> dead-letter in the Owner's Inbox."""
    task_root = Path(task_root)
    board = TaskBoard(task_root)

    allowed, why = check_agent_budget(budget_url, strict=budget_strict)
    if not allowed:
        return SpawnResult(agent=name, task_id="", ok=False, detail=why)

    if worktree_from is not None:
        cwd = create_worktree(worktree_from, name, task_root / "worktrees")
    cwd = Path(cwd)

    task = board.create(title=f"{name}: {prompt[:60]}", prompt=prompt, agent=name,
                        meta={"cwd": str(cwd)})
    briefing = write_briefing(task_root, name, task, cwd, extra=briefing_extra)
    install_hooks(cwd, task_root, name)

    sc = _sc()
    before = {w.hwnd for w in sc.list_windows()}
    _launch(cwd, claude_cmd, launcher)

    target = _wait_new_window(before, window_timeout)
    if target is None:
        board.transition(task.task_id, TaskState.FAILED, error="no window appeared")
        board.dead_letter(task.task_id, "spawn produced no terminal window", inbox_dir)
        return SpawnResult(agent=name, task_id=task.task_id, ok=False,
                           briefing_path=str(briefing), cwd=str(cwd),
                           detail="no window appeared")

    state = wait_until_ready(target, timeout=ready_timeout, reader=reader)
    board.record_event("spawn.ready_state", task.task_id, name, {"state": state.value})

    ack_states = (TERMINAL_STATES | {TaskState.WORKING, TaskState.INPUT_REQUIRED})
    for attempt in (1, 2):  # ring, and re-ring once before escalating
        _ring_doorbell(target, briefing, task)
        acked = board.wait_for_state(task.task_id, ack_states, timeout=ack_timeout)
        if acked is not None:
            return SpawnResult(
                agent=name, task_id=task.task_id, ok=True, hwnd=target.hwnd,
                pid=target.pid, briefing_path=str(briefing), cwd=str(cwd),
                detail=f"acked on attempt {attempt} (state={acked.state.value}); {why}",
            )
    board.transition(task.task_id, TaskState.FAILED, error="no ack after 2 doorbells")
    dl = board.dead_letter(task.task_id, "agent never acknowledged briefing", inbox_dir)
    return SpawnResult(agent=name, task_id=task.task_id, ok=False, hwnd=target.hwnd,
                       pid=target.pid, briefing_path=str(briefing), cwd=str(cwd),
                       detail=f"no ack; escalated to {dl}")


def wait_for_completion(task_root: Path | str, task_id: str,
                        timeout: float = 3600.0, poll: float = 2.0) -> Optional[Task]:
    """Block until the agent runs its sc_done verb (or fails/cancels)."""
    return TaskBoard(task_root).wait_for_state(task_id, TERMINAL_STATES, timeout, poll)


__all__ = [
    "ReadyState",
    "SpawnError",
    "SpawnResult",
    "check_agent_budget",
    "create_worktree",
    "detect_tui_state",
    "spawn_agent",
    "wait_for_completion",
    "wait_until_ready",
    "write_briefing",
]
