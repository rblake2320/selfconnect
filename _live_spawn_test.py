"""Live end-to-end proof of sc_spawn.spawn_agent() against a real desktop.

Spawns a real interactive Claude Code window (subscription billing path),
briefs it via doorbell injection, and watches the task go
submitted -> working -> completed on the TaskBoard, then verifies the
hash chain. Run from the selfconnect SDK dir.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

SDK = Path(__file__).resolve().parent
sys.path.insert(0, str(SDK))

import self_connect as sc  # noqa: E402
from sc_spawn import spawn_agent, wait_for_completion  # noqa: E402
from sc_tasks import TaskBoard  # noqa: E402

TASK_ROOT = SDK / ".sc_live_test"
AGENT = "SC-LIVE-1"
PROOF = SDK / "SC_LIVE_PROOF.txt"


def uia_reader(target) -> str:
    """Read visible TUI text via UIA first (per-hwnd, WT-safe), console fallback."""
    try:
        text = sc.get_text_uia(target.hwnd) or ""
        if text.strip():
            return text
    except Exception:
        pass
    try:
        return sc.read_console_fast(target) or ""
    except Exception:
        return ""


def main() -> int:
    print(f"[live] task_root={TASK_ROOT}", flush=True)
    prompt = (
        f'Create a file at "{PROOF}" containing exactly one line: '
        f'"SC-LIVE-TEST ok". Read it back to verify it exists, then follow '
        f"the completion protocol in this briefing (run the sc_done.py "
        f"command). Do nothing else."
    )
    t0 = time.time()
    result = spawn_agent(
        name=AGENT,
        prompt=prompt,
        cwd=SDK,
        task_root=TASK_ROOT,
        launcher="conhost",
        reader=uia_reader,
        window_timeout=45.0,
        ready_timeout=120.0,
        ack_timeout=90.0,
    )
    print(f"[live] spawn_agent returned after {time.time() - t0:.1f}s", flush=True)
    print(json.dumps({
        "ok": result.ok, "task_id": result.task_id, "hwnd": result.hwnd,
        "pid": result.pid, "detail": result.detail, "cwd": result.cwd,
        "briefing": result.briefing_path,
    }, indent=2), flush=True)

    if not result.ok:
        return 1

    print("[live] waiting for completion (sc_done verb)...", flush=True)
    task = wait_for_completion(TASK_ROOT, result.task_id, timeout=600.0)
    if task is None:
        print("[live] TIMEOUT: task never reached a terminal state", flush=True)
        return 2
    print(f"[live] terminal state: {task.state.value}", flush=True)
    print(f"[live] result: {task.result!r}"
          if hasattr(task, "result") else "", flush=True)

    ok, n = TaskBoard(TASK_ROOT).verify_chain()
    print(f"[live] hash chain verified: {ok} over {n} events", flush=True)

    proof_ok = PROOF.exists() and "SC-LIVE-TEST ok" in PROOF.read_text(encoding="utf-8")
    print(f"[live] proof file present+correct: {proof_ok}", flush=True)

    passed = task.state.value == "completed" and ok and proof_ok
    print(f"[live] E2E {'PASS' if passed else 'FAIL'}", flush=True)
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
