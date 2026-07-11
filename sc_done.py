"""sc_done — explicit task-completion verb for spawned agents.

Completion is a signed event, not an inference from screen contents. The
spawn briefing tells the agent to finish its work by running:

    python "<sdk>/sc_done.py" --task-root "<root>" --task-id <id> --result "summary"
    python "<sdk>/sc_done.py" --task-root "<root>" --task-id <id> --fail --error "why"

``--result-file`` reads a longer result from a file (avoids shell quoting).
Exit code 0 on success, 1 on error — the agent can see and report failures.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sc_tasks import TaskBoard, TaskState

__version__ = "0.12.0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--result", default="")
    parser.add_argument("--result-file", default="")
    parser.add_argument("--fail", action="store_true")
    parser.add_argument("--error", default="")
    args = parser.parse_args(argv)

    result = args.result
    if args.result_file:
        try:
            result = Path(args.result_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"sc_done: cannot read result file: {exc}", file=sys.stderr)
            return 1

    board = TaskBoard(args.task_root)
    try:
        task = board.get(args.task_id)
        if task.state is TaskState.SUBMITTED:
            # agent skipped straight to done — pass through working first
            board.transition(args.task_id, TaskState.WORKING, agent=task.agent)
        if args.fail:
            board.transition(args.task_id, TaskState.FAILED, error=args.error or "unspecified")
            print(f"{args.task_id} -> failed")
        else:
            board.transition(args.task_id, TaskState.COMPLETED, result=result)
            print(f"{args.task_id} -> completed")
        return 0
    except Exception as exc:
        print(f"sc_done error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
