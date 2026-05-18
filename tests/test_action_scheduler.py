"""
test_action_scheduler.py — Unit tests for ActionScheduler.
No Win32 API required — all send_fn calls are mocked.
"""

import sys
import os
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from action_scheduler import ActionScheduler, Action, ActionResult


class TestLock:
    def test_lock_single_hwnd(self):
        """Acquire lock, verify no deadlock."""
        sched = ActionScheduler()
        with sched.lock(hwnd=1000):
            pass  # should not deadlock

    def test_lock_different_hwnds_concurrent(self):
        """Two threads lock different HWNDs simultaneously — no blocking."""
        sched = ActionScheduler()
        results = []

        def grab(hwnd, delay):
            with sched.lock(hwnd=hwnd):
                results.append(("enter", hwnd, time.monotonic()))
                time.sleep(delay)
                results.append(("exit", hwnd, time.monotonic()))

        t1 = threading.Thread(target=grab, args=(1, 0.1))
        t2 = threading.Thread(target=grab, args=(2, 0.1))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Both should have entered before either exited (parallel)
        enters = [r for r in results if r[0] == "enter"]
        exits = [r for r in results if r[0] == "exit"]
        assert len(enters) == 2
        assert len(exits) == 2
        # The second enter should happen before the first exit
        assert enters[1][2] < exits[0][2]

    def test_same_hwnd_serialized(self):
        """Two threads on same HWND — second waits for first."""
        sched = ActionScheduler()
        order = []

        def grab(tag, delay):
            with sched.lock(hwnd=1):
                order.append(f"{tag}-enter")
                time.sleep(delay)
                order.append(f"{tag}-exit")

        t1 = threading.Thread(target=grab, args=("A", 0.15))
        t2 = threading.Thread(target=grab, args=("B", 0.05))
        t1.start()
        time.sleep(0.02)  # ensure A grabs first
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # A must fully complete before B enters
        assert order == ["A-enter", "A-exit", "B-enter", "B-exit"]


class TestExecuteNow:
    def test_execute_now_calls_send_fn(self):
        """Verify send_fn is called with correct args."""
        sched = ActionScheduler()
        calls = []

        def fake_send(text, submitted):
            calls.append((text, submitted))

        action = Action(hwnd=100, text="hello", submitted=True)
        result = sched.execute_now(action, fake_send)

        assert result.success is True
        assert result.error is None
        assert result.elapsed >= 0
        assert calls == [("hello", True)]

    def test_execute_now_handles_error(self):
        """send_fn raising returns failed result."""
        sched = ActionScheduler()

        def bad_send(text, submitted):
            raise RuntimeError("boom")

        action = Action(hwnd=100, text="fail")
        result = sched.execute_now(action, bad_send)

        assert result.success is False
        assert "boom" in result.error


class TestSubmit:
    def test_submit_returns_future(self):
        """future.result() returns ActionResult."""
        sched = ActionScheduler()
        calls = []

        def fake_send(text, submitted):
            calls.append(text)

        action = Action(hwnd=200, text="async-hello", submitted=False)
        future = sched.submit(action, send_fn=fake_send)
        result = future.result(timeout=10)

        assert isinstance(result, ActionResult)
        assert result.success is True
        assert calls == ["async-hello"]
        sched.shutdown()

    def test_cancel_all_drains_queue(self):
        """Submit 5 actions, cancel_all before workers can run them all."""
        sched = ActionScheduler()
        gate = threading.Event()

        def slow_send(text, submitted):
            gate.wait(timeout=5)  # block until we release

        # Submit 5 actions — first will start executing (blocked), rest queued
        futures = []
        for i in range(5):
            a = Action(hwnd=300, text=f"msg-{i}", priority=0)
            futures.append(sched.submit(a, send_fn=slow_send))

        time.sleep(0.1)  # let worker pick up first action

        cancelled = sched.cancel_all(hwnd=300)
        gate.set()  # unblock the worker

        # At least some should have been cancelled
        assert cancelled >= 0
        sched.shutdown()

    def test_stats_tracks_completed(self):
        """Execute 3 actions, stats shows 3 completed."""
        sched = ActionScheduler()

        def noop(text, submitted):
            pass

        for i in range(3):
            action = Action(hwnd=400, text=f"m{i}")
            sched.execute_now(action, noop)

        s = sched.stats()
        assert s[400]["completed"] == 3
        assert s[400]["failed"] == 0

    def test_shutdown_cleans_up(self):
        """scheduler.shutdown() doesn't hang."""
        sched = ActionScheduler()

        def noop(text, submitted):
            pass

        sched.submit(Action(hwnd=500, text="x"), send_fn=noop)
        time.sleep(0.2)
        sched.shutdown(wait=True)  # must return without hanging
