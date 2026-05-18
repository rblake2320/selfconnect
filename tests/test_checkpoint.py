"""
test_checkpoint.py — Unit tests for CheckpointManager.
All tests use tmp_path for isolation.
"""

import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkpoint import CheckpointManager, StepRecord


class TestCheckpointBasic:
    def test_new_task_no_steps_done(self, tmp_path):
        """Fresh CheckpointManager, is_done returns False."""
        mgr = CheckpointManager("task-1", checkpoint_dir=tmp_path)
        assert mgr.is_done("step-a") is False

    def test_mark_done_persists(self, tmp_path):
        """mark_done, create new manager from same file, is_done returns True."""
        mgr1 = CheckpointManager("task-2", checkpoint_dir=tmp_path)
        mgr1.mark_done("step-a", result={"count": 42})

        mgr2 = CheckpointManager("task-2", checkpoint_dir=tmp_path)
        assert mgr2.is_done("step-a") is True
        assert mgr2.steps()["step-a"].result == {"count": 42}

    def test_mark_failed_records_error(self, tmp_path):
        """mark_failed, steps() shows error field."""
        mgr = CheckpointManager("task-3", checkpoint_dir=tmp_path)
        mgr.mark_failed("step-b", error="connection timeout")

        rec = mgr.steps()["step-b"]
        assert rec.status == "failed"
        assert rec.error == "connection timeout"

    def test_reset_step(self, tmp_path):
        """mark_done, reset_step, is_done returns False."""
        mgr = CheckpointManager("task-4", checkpoint_dir=tmp_path)
        mgr.mark_done("step-c")
        assert mgr.is_done("step-c") is True

        mgr.reset_step("step-c")
        assert mgr.is_done("step-c") is False

    def test_reset_all_deletes_file(self, tmp_path):
        """mark_done 3 steps, reset_all, checkpoint file gone."""
        mgr = CheckpointManager("task-5", checkpoint_dir=tmp_path)
        for s in ["a", "b", "c"]:
            mgr.mark_done(s)

        cp_path = os.path.join(str(tmp_path), "task-5.json")
        assert os.path.exists(cp_path)

        mgr.reset_all()
        assert not os.path.exists(cp_path)


class TestSummary:
    def test_summary_correct(self, tmp_path):
        """2 done, 1 failed, 1 running — summary counts correct."""
        mgr = CheckpointManager("task-6", checkpoint_dir=tmp_path)
        mgr.mark_done("s1")
        mgr.mark_done("s2")
        mgr.mark_failed("s3", error="oops")
        mgr.mark_running("s4")

        s = mgr.summary()
        assert sorted(s["done"]) == ["s1", "s2"]
        assert s["failed"] == ["s3"]
        assert s["pending"] == ["s4"]  # running is not done/failed
        assert s["total"] == 4


class TestAtomicSave:
    def test_atomic_save(self, tmp_path):
        """Concurrent saves don't corrupt the file."""
        mgr = CheckpointManager("task-7", checkpoint_dir=tmp_path)
        errors = []

        def save_step(name):
            try:
                mgr.mark_done(name, result=f"result-{name}")
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=save_step, args=(f"step-{i}",))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors

        # Verify file is valid JSON and has all steps
        cp_path = os.path.join(str(tmp_path), "task-7.json")
        with open(cp_path, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["steps"]) == 10

    def test_context_manager_saves_on_exit(self, tmp_path):
        """Verify file written after 'with' block."""
        cp_path = os.path.join(str(tmp_path), "task-8.json")

        with CheckpointManager("task-8", checkpoint_dir=tmp_path) as mgr:
            mgr.mark_done("final-step")

        assert os.path.exists(cp_path)
        with open(cp_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "final-step" in data["steps"]
