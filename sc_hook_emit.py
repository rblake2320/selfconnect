"""sc_hook_emit — CLI invoked by Claude Code hooks inside spawned agents.

Reads the hook payload from stdin (session_id, transcript_path, cwd, ...),
appends a chained event to the task board, and advances the agent's active
task through its lifecycle:

    ack           submitted       -> working         (delivery receipt)
    notification  working         -> input-required  (stuck on permission)
    ack           input-required  -> working         (unstuck)
    stop          (no transition)  records turn_ended + transcript_path

``stop`` deliberately does NOT complete the task — completion is an explicit
verb the agent runs (``sc_done.py``), Gas Town style, so "finished a turn"
and "finished the work" stay distinct.

Always exits 0: a broken emitter must never block the agent's own turn.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sc_tasks import TaskBoard, TaskState

__version__ = "0.12.0"


def _active_task(board: TaskBoard, agent: str):
    """The agent's most recent non-terminal task."""
    mine = [t for t in board.all_tasks() if t.agent == agent and not t.is_terminal]
    return sorted(mine, key=lambda t: t.created_at)[-1] if mine else None


def handle_event(board: TaskBoard, agent: str, event: str, payload: dict) -> str:
    """Apply one hook event. Returns a short description of what happened."""
    task = _active_task(board, agent)
    detail = {
        "session_id": payload.get("session_id", ""),
        "transcript_path": payload.get("transcript_path", ""),
        "hook_event": payload.get("hook_event_name", ""),
    }
    board.record_event(f"hook.{event}", task.task_id if task else "", agent, detail)
    if task is None:
        return "no active task"

    meta = {}
    if detail["transcript_path"]:
        meta["transcript_path"] = detail["transcript_path"]
    if detail["session_id"]:
        meta["session_id"] = detail["session_id"]

    if event == "ack":
        meta["last_ack_ts"] = time.time()
        if task.state in (TaskState.SUBMITTED, TaskState.INPUT_REQUIRED):
            board.transition(task.task_id, TaskState.WORKING, agent=agent, meta_update=meta)
            return f"{task.task_id} -> working"
    elif event == "notification":
        meta["last_notification"] = payload.get("message", "")
        if task.state is TaskState.WORKING:
            board.transition(task.task_id, TaskState.INPUT_REQUIRED, agent=agent,
                             meta_update=meta)
            return f"{task.task_id} -> input-required"
    elif event == "stop":
        meta["turn_ended_ts"] = time.time()

    if meta:  # record metadata even when no transition fired
        with board._board_lock():
            fresh = board.get(task.task_id)
            fresh.meta.update(meta)
            board._write_task(fresh)
    return f"{task.task_id} noted {event}"


def main(argv: list[str] | None = None, stdin_text: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--event", required=True,
                        choices=["ack", "notification", "stop"])
    args = parser.parse_args(argv)

    try:
        raw = stdin_text if stdin_text is not None else sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        payload = {}

    try:
        board = TaskBoard(args.task_root)
        msg = handle_event(board, args.agent, args.event, payload)
        print(msg)
    except Exception as exc:  # never block the agent's turn
        print(f"sc_hook_emit error: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
