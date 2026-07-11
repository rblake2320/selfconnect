"""Unit tests for sc_tasks — lifecycle, locked claiming, retry, chain."""

import json
import threading
import time

import pytest
from sc_tasks import (
    GENESIS_HASH,
    LockTimeout,
    TaskBoard,
    TaskState,
    TransitionError,
)


@pytest.fixture
def board(tmp_path):
    return TaskBoard(tmp_path / "mesh")


def test_create_starts_submitted(board):
    task = board.create("do a thing", prompt="details")
    assert task.state is TaskState.SUBMITTED
    on_disk = board.get(task.task_id)
    assert on_disk.title == "do a thing"
    assert on_disk.prompt == "details"
    assert on_disk.attempts == 0


def test_valid_lifecycle_path(board):
    t = board.create("t")
    board.transition(t.task_id, TaskState.WORKING, agent="B")
    board.transition(t.task_id, TaskState.INPUT_REQUIRED)
    board.transition(t.task_id, TaskState.WORKING)
    done = board.transition(t.task_id, TaskState.COMPLETED, result="all good")
    assert done.state is TaskState.COMPLETED
    assert done.result == "all good"
    assert done.is_terminal


def test_illegal_transitions_fail_closed(board):
    t = board.create("t")
    with pytest.raises(TransitionError):
        board.transition(t.task_id, TaskState.COMPLETED)  # submitted -> completed
    board.transition(t.task_id, TaskState.WORKING)
    board.transition(t.task_id, TaskState.COMPLETED)
    with pytest.raises(TransitionError):
        board.transition(t.task_id, TaskState.WORKING)  # terminal is terminal


def test_claim_is_exclusive_across_threads(board):
    for i in range(6):
        board.create(f"task-{i}")
    claimed: list[str] = []
    lock = threading.Lock()

    def worker(agent):
        while True:
            try:
                task = board.claim(agent)
            except LockTimeout:
                continue  # another thread holds the lock; retry
            if task is None:
                return
            with lock:
                claimed.append(task.task_id)

    threads = [threading.Thread(target=worker, args=(f"agent-{i}",)) for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(claimed) == 6
    assert len(set(claimed)) == 6  # no double-claims


def test_dependencies_gate_claiming(board):
    a = board.create("a")
    b = board.create("b", depends_on=[a.task_id])
    assert [t.task_id for t in board.claimable()] == [a.task_id]
    assert board.claim("agent", task_id=b.task_id) is None  # dep not done
    board.claim("agent", task_id=a.task_id)
    board.transition(a.task_id, TaskState.COMPLETED)
    unblocked = board.claim("agent")
    assert unblocked is not None and unblocked.task_id == b.task_id


def test_retry_then_dead_letter(board, tmp_path):
    t = board.create("flaky", max_attempts=2)
    board.claim("B", task_id=t.task_id)
    board.transition(t.task_id, TaskState.FAILED, error="boom 1")
    retried = board.retry(t.task_id, reason="boom 1")
    assert retried is not None and retried.state is TaskState.SUBMITTED

    board.claim("B", task_id=t.task_id)
    board.transition(t.task_id, TaskState.FAILED, error="boom 2")
    assert board.retry(t.task_id, reason="boom 2") is None  # attempts exhausted

    inbox = tmp_path / "inbox"
    dl_path = board.dead_letter(t.task_id, "gave up", inbox)
    assert dl_path.exists()
    body = dl_path.read_text(encoding="utf-8")
    assert t.task_id in body and "gave up" in body
    final = board.get(t.task_id)
    assert final.state is TaskState.FAILED
    assert final.meta["dead_letter"]["reason"] == "gave up"


def test_event_chain_verifies_and_detects_tamper(board):
    t = board.create("t")
    board.transition(t.task_id, TaskState.WORKING)
    board.transition(t.task_id, TaskState.COMPLETED)
    ok, idx = board.verify_chain()
    assert ok and idx == -1

    lines = board.events_path.read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(lines[1])
    entry["event_type"] = "task.forged"
    lines[1] = json.dumps(entry, sort_keys=True)
    board.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, idx = board.verify_chain()
    assert not ok and idx == 1


def test_chain_starts_at_genesis(board):
    board.create("t")
    first = json.loads(board.events_path.read_text(encoding="utf-8").splitlines()[0])
    assert first["prior_hash"] == GENESIS_HASH


def test_wait_for_state_returns_on_transition(board):
    t = board.create("t")

    def later():
        time.sleep(0.2)
        board.transition(t.task_id, TaskState.WORKING)

    threading.Thread(target=later).start()
    got = board.wait_for_state(t.task_id, {TaskState.WORKING}, timeout=5.0, poll=0.05)
    assert got is not None and got.state is TaskState.WORKING


def test_wait_for_state_times_out(board):
    t = board.create("t")
    assert board.wait_for_state(t.task_id, {TaskState.COMPLETED},
                                timeout=0.3, poll=0.05) is None
