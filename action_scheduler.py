"""
action_scheduler.py — Per-HWND mutex and ordered action queue for SelfConnect mesh.

Prevents multiple agents from injecting into the same terminal simultaneously.
Each HWND gets its own lock. Actions are queued and executed in order.

Usage:
    from action_scheduler import ActionScheduler, Action

    scheduler = ActionScheduler()

    # Synchronous: acquire lock, inject, release
    with scheduler.lock(hwnd=9307910):
        send_string(win, message)

    # Async queue: submit action, get future result
    future = scheduler.submit(Action(hwnd=9307910, text="hello", submitted=True))
    result = future.result(timeout=10)
"""

from __future__ import annotations

import concurrent.futures
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable


@dataclass(order=True)
class Action:
    """A single injection action targeting a specific HWND."""

    priority: int = field(default=0, compare=True)
    created_at: float = field(default_factory=time.time, compare=True)
    hwnd: int = field(default=0, compare=False)
    text: str = field(default="", compare=False)
    submitted: bool = field(default=False, compare=False)
    timeout: float = field(default=30.0, compare=False)


@dataclass
class ActionResult:
    """Result of executing an action."""

    action: Action
    success: bool
    elapsed: float
    error: str | None = None


class ActionScheduler:
    """Per-HWND mutex and priority queue for serialised terminal injection."""

    def __init__(self, *, max_queue_per_hwnd: int = 100) -> None:
        self._max_queue = max_queue_per_hwnd
        self._locks: dict[int, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._queues: dict[int, queue.PriorityQueue[Action]] = {}
        self._workers: dict[int, threading.Thread] = {}
        self._send_fns: dict[int, Callable[..., None]] = {}
        self._stats: dict[int, dict[str, int]] = {}
        self._shutdown_event = threading.Event()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=32, thread_name_prefix="sc-sched"
        )
        self._futures: dict[int, list[tuple[Action, concurrent.futures.Future[ActionResult]]]] = {}
        self._futures_lock = threading.Lock()

    def _get_lock(self, hwnd: int) -> threading.RLock:
        with self._locks_guard:
            if hwnd not in self._locks:
                self._locks[hwnd] = threading.RLock()
            return self._locks[hwnd]

    @contextmanager
    def lock(self, hwnd: int):
        """Acquire per-HWND lock as a context manager."""
        rlock = self._get_lock(hwnd)
        rlock.acquire()
        try:
            yield
        finally:
            rlock.release()

    def execute_now(
        self,
        action: Action,
        send_fn: Callable[[str, bool], None],
    ) -> ActionResult:
        """Synchronous execution: acquire lock, call send_fn, return result."""
        start = time.monotonic()
        with self.lock(action.hwnd):
            try:
                send_fn(action.text, action.submitted)
                elapsed = time.monotonic() - start
                self._bump_stat(action.hwnd, "completed")
                return ActionResult(action=action, success=True, elapsed=elapsed)
            except Exception as exc:
                elapsed = time.monotonic() - start
                self._bump_stat(action.hwnd, "failed")
                return ActionResult(
                    action=action, success=False, elapsed=elapsed, error=str(exc)
                )

    def submit(
        self,
        action: Action,
        send_fn: Callable[[str, bool], None] | None = None,
    ) -> concurrent.futures.Future[ActionResult]:
        """Enqueue an action for background execution. Returns a Future."""
        hwnd = action.hwnd
        if send_fn is not None:
            self._send_fns[hwnd] = send_fn

        future: concurrent.futures.Future[ActionResult] = concurrent.futures.Future()

        # Ensure queue exists
        if hwnd not in self._queues:
            self._queues[hwnd] = queue.PriorityQueue(maxsize=self._max_queue)

        self._queues[hwnd].put(action)
        self._bump_stat(hwnd, "queued")

        with self._futures_lock:
            self._futures.setdefault(hwnd, []).append((action, future))

        # Ensure worker thread exists
        if hwnd not in self._workers or not self._workers[hwnd].is_alive():
            t = threading.Thread(
                target=self._worker_loop, args=(hwnd,), daemon=True,
                name=f"sc-worker-{hwnd}",
            )
            self._workers[hwnd] = t
            t.start()

        return future

    def _worker_loop(self, hwnd: int) -> None:
        q = self._queues[hwnd]
        while not self._shutdown_event.is_set():
            try:
                action = q.get(timeout=0.5)
            except queue.Empty:
                # If queue is empty and no pending futures, exit thread
                with self._futures_lock:
                    if not self._futures.get(hwnd):
                        break
                continue

            # Find the matching future
            future: concurrent.futures.Future[ActionResult] | None = None
            with self._futures_lock:
                pending = self._futures.get(hwnd, [])
                for i, (a, f) in enumerate(pending):
                    if a is action:
                        future = f
                        pending.pop(i)
                        break

            send_fn = self._send_fns.get(hwnd)
            if send_fn is None:
                # No send function registered — fail the action
                result = ActionResult(
                    action=action, success=False, elapsed=0.0,
                    error="no send_fn registered for hwnd",
                )
                self._bump_stat(hwnd, "failed")
                self._decr_stat(hwnd, "queued")
            else:
                result = self.execute_now(action, send_fn)
                self._decr_stat(hwnd, "queued")

            if future is not None:
                future.set_result(result)

    def pending(self, hwnd: int) -> int:
        """Number of queued actions for an HWND."""
        q = self._queues.get(hwnd)
        return q.qsize() if q else 0

    def cancel_all(self, hwnd: int) -> int:
        """Drain the queue for an HWND, return count cancelled."""
        q = self._queues.get(hwnd)
        if q is None:
            return 0
        count = 0
        while True:
            try:
                action = q.get_nowait()
                count += 1
                # Cancel matching future
                with self._futures_lock:
                    pending = self._futures.get(hwnd, [])
                    for i, (a, f) in enumerate(pending):
                        if a is action:
                            f.cancel()
                            pending.pop(i)
                            break
            except queue.Empty:
                break
        # Reset queued stat
        if hwnd in self._stats:
            self._stats[hwnd]["queued"] = 0
        return count

    def shutdown(self, wait: bool = True) -> None:
        """Stop all worker threads."""
        self._shutdown_event.set()
        if wait:
            for t in self._workers.values():
                t.join(timeout=5.0)
        self._executor.shutdown(wait=wait)

    def stats(self) -> dict[int, dict[str, int]]:
        """Per-HWND stats: queued, completed, failed."""
        result: dict[int, dict[str, int]] = {}
        all_hwnds = set(self._stats.keys()) | set(self._queues.keys())
        for hwnd in all_hwnds:
            s = self._stats.get(hwnd, {})
            result[hwnd] = {
                "queued": max(0, s.get("queued", 0)),
                "completed": s.get("completed", 0),
                "failed": s.get("failed", 0),
            }
        return result

    def _bump_stat(self, hwnd: int, key: str) -> None:
        if hwnd not in self._stats:
            self._stats[hwnd] = {"queued": 0, "completed": 0, "failed": 0}
        self._stats[hwnd][key] = self._stats[hwnd].get(key, 0) + 1

    def _decr_stat(self, hwnd: int, key: str) -> None:
        if hwnd in self._stats:
            self._stats[hwnd][key] = max(0, self._stats[hwnd].get(key, 0) - 1)
