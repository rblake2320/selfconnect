"""Unit tests for sc_spawn — budget gate, readiness, evented spawn flow.

Win32 is faked by monkeypatching sc_spawn._sc() to return a stub module, so
these run on any platform with no terminals.
"""

import threading
import time
from dataclasses import dataclass

import pytest
import sc_spawn
from sc_spawn import (
    ReadyState,
    check_agent_budget,
    detect_tui_state,
    spawn_agent,
)
from sc_tasks import TaskBoard, TaskState


@dataclass
class FakeWin:
    hwnd: int
    class_name: str = "CASCADIA_HOSTING_WINDOW_CLASS"
    title: str = "SC-Agent"
    pid: int = 4242
    exe_name: str = "cmd.exe"


class FakeSC:
    """Stand-in for the self_connect module."""

    def __init__(self):
        self.windows = [FakeWin(hwnd=1, class_name="Chrome_WidgetWin_1")]
        self.console_text = "❯ ? for shortcuts"  # noqa: RUF001
        self.sent = []
        self.submitted = []
        self._new_added = False

    def list_windows(self):
        return list(self.windows)

    def add_window(self):
        self.windows.append(FakeWin(hwnd=99))
        self._new_added = True

    def read_console_fast(self, target):
        return self.console_text

    def get_window_text(self, hwnd):
        return self.console_text

    def send_string(self, target, text, char_delay=0.05):
        self.sent.append(text)

    def submit_claude_input(self, hwnd):
        self.submitted.append(hwnd)
        return True


# ---------- worktree naming ----------

def test_worktree_names_unique_within_same_second(tmp_path, monkeypatch):
    import sc_spawn as m
    calls = []
    monkeypatch.setattr(m.subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or _ok())
    monkeypatch.setattr(m.time, "time", lambda: 1_000_000.0)  # freeze the clock
    p1 = m.create_worktree(tmp_path / "repo", "B", tmp_path / "wts")
    p2 = m.create_worktree(tmp_path / "repo", "B", tmp_path / "wts")
    assert p1 != p2  # uuid suffix breaks the same-second collision
    branches = [a[a.index("-b") + 1] for a in calls]
    assert branches[0] != branches[1]


class _ok:
    returncode = 0
    stderr = ""


# ---------- pure helpers ----------

def test_detect_tui_state_precedence():
    assert detect_tui_state("Do you want to proceed? 1. Yes") is ReadyState.BLOCKED
    assert detect_tui_state("Cogitating… esc to interrupt") is ReadyState.BUSY
    assert detect_tui_state("❯ ? for shortcuts") is ReadyState.READY  # noqa: RUF001
    assert detect_tui_state("random noise") is ReadyState.UNKNOWN
    # a dialog while busy still reads as blocked (needs a human)
    assert detect_tui_state("esc to interrupt ... Do you want") is ReadyState.BLOCKED


class _Resp:
    def __init__(self, body):
        self._body = body.encode()
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_budget_gate_allows_and_denies(monkeypatch):
    monkeypatch.setattr(sc_spawn.urllib.request, "urlopen",
                        lambda *a, **k: _Resp('{"usd_spent": 3.0, "usd_limit": 10.0}'))
    ok, why = check_agent_budget()
    assert ok and "3.0/10.0" in why

    monkeypatch.setattr(sc_spawn.urllib.request, "urlopen",
                        lambda *a, **k: _Resp('{"usd_spent": 10.0, "usd_limit": 10.0}'))
    ok, _ = check_agent_budget()
    assert not ok

    monkeypatch.setattr(sc_spawn.urllib.request, "urlopen",
                        lambda *a, **k: _Resp('{"blocked": true}'))
    ok, _ = check_agent_budget()
    assert not ok


def test_budget_gate_honors_daemon_verdict(monkeypatch):
    # live /agent-status shape: verdict wins even when usd is under the limit
    monkeypatch.setattr(sc_spawn.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(
                            '{"usd_spent": 3.0, "usd_limit": 10.0, "status": "pause",'
                            ' "recommended_action": "pause all agents"}'))
    ok, why = check_agent_budget()
    assert not ok and "pause" in why

    monkeypatch.setattr(sc_spawn.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(
                            '{"usd_spent": 3.0, "usd_limit": 10.0, "status": "warning"}'))
    ok, _ = check_agent_budget()
    assert ok

    # observed live 2026-07-02: spent > limit but daemon verdict is "warning"
    # (not pause) — the verdict must win over the raw usd comparison
    monkeypatch.setattr(sc_spawn.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(
                            '{"usd_spent": 839.28, "usd_limit": 600.0, "status": "warning"}'))
    ok, why = check_agent_budget()
    assert ok and "warning" in why


def test_budget_gate_fail_open_vs_strict(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(sc_spawn.urllib.request, "urlopen", boom)
    ok, why = check_agent_budget(strict=False)
    assert ok and "allowing" in why
    ok, why = check_agent_budget(strict=True)
    assert not ok and "strict" in why


# ---------- spawn flow ----------

@pytest.fixture
def fake_sc(monkeypatch):
    fake = FakeSC()
    monkeypatch.setattr(sc_spawn, "_sc", lambda: fake)
    # bypass network budget check by default
    monkeypatch.setattr(sc_spawn, "check_agent_budget", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(sc_spawn, "budget_snapshot",
                        lambda *a, **k: {"reachable": True, "status": "test"})
    # don't actually launch a process
    monkeypatch.setattr(sc_spawn, "_launch", lambda cwd, cmd, launcher: fake.add_window())
    return fake


def test_spawn_happy_path_acks(tmp_path, fake_sc, monkeypatch):
    root = tmp_path / "mesh"
    project = tmp_path / "proj"

    # simulate the receiver's UserPromptSubmit hook firing shortly after doorbell
    board = TaskBoard(root)

    def ack_soon(target, briefing, task):
        fake_sc.sent.append("doorbell")

        def flip():
            time.sleep(0.1)
            board.transition(task.task_id, TaskState.WORKING, agent="B")
        threading.Thread(target=flip).start()

    monkeypatch.setattr(sc_spawn, "_ring_doorbell", ack_soon)

    result = spawn_agent("B", "do the thing", cwd=project, task_root=root,
                         window_timeout=5, ready_timeout=3, ack_timeout=3)
    assert result.ok
    assert result.task_id
    assert board.get(result.task_id).state is TaskState.WORKING
    assert (project / ".claude" / "settings.local.json").exists()
    # briefing file written with the completion command
    assert "sc_done.py" in (tmp_path / "mesh" / "briefings" /
                            f"B-{result.task_id}.md").read_text("utf-8")


def test_spawn_no_window_dead_letters(tmp_path, monkeypatch):
    root = tmp_path / "mesh"
    fake = FakeSC()
    monkeypatch.setattr(sc_spawn, "_sc", lambda: fake)
    monkeypatch.setattr(sc_spawn, "check_agent_budget", lambda *a, **k: (True, "t"))
    monkeypatch.setattr(sc_spawn, "budget_snapshot", lambda *a, **k: {"reachable": True})
    monkeypatch.setattr(sc_spawn, "_launch", lambda *a: None)  # no new window ever

    inbox = tmp_path / "inbox"
    result = spawn_agent("B", "task", cwd=tmp_path / "proj", task_root=root,
                         window_timeout=0.5, inbox_dir=inbox)
    assert not result.ok
    assert "no window" in result.detail
    assert list(inbox.glob("dead-letter-*.md"))
    assert TaskBoard(root).get(result.task_id).state is TaskState.FAILED


def test_spawn_no_ack_retries_then_dead_letters(tmp_path, fake_sc, monkeypatch):
    root = tmp_path / "mesh"
    inbox = tmp_path / "inbox"
    rings = []
    monkeypatch.setattr(sc_spawn, "_ring_doorbell",
                        lambda t, b, task: rings.append(task.task_id))

    result = spawn_agent("B", "task", cwd=tmp_path / "proj", task_root=root,
                         window_timeout=5, ready_timeout=0.5, ack_timeout=0.4,
                         inbox_dir=inbox)
    assert not result.ok
    assert len(rings) == 2  # doorbell rung twice before escalation
    assert "escalated" in result.detail
    assert list(inbox.glob("dead-letter-*.md"))
    assert TaskBoard(root).get(result.task_id).state is TaskState.FAILED


def test_budget_denial_blocks_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(sc_spawn, "check_agent_budget",
                        lambda *a, **k: (False, "budget exhausted"))
    result = spawn_agent("B", "task", cwd=tmp_path / "proj", task_root=tmp_path / "mesh")
    assert not result.ok
    assert "exhausted" in result.detail
    assert result.task_id == ""  # never created a task


# ---------- budget override (test-mode) ----------

def test_budget_override_proceeds_despite_pause(tmp_path, fake_sc, monkeypatch):
    """Gate says DENY (pause) but override lets the spawn proceed, and the
    verdict is recorded on the task board so it's still accounted for."""
    root = tmp_path / "mesh"
    board = TaskBoard(root)
    monkeypatch.setattr(sc_spawn, "check_agent_budget",
                        lambda *a, **k: (False, "budget daemon verdict: pause"))
    monkeypatch.setattr(sc_spawn, "budget_snapshot",
                        lambda *a, **k: {"reachable": True, "usd_spent": 1321.0,
                                         "usd_limit": 600.0, "status": "pause"})
    monkeypatch.setattr(sc_spawn, "_ring_doorbell",
                        lambda t, b, task: board.transition(task.task_id,
                                                            TaskState.WORKING, agent="B"))

    result = spawn_agent("B", "task", cwd=tmp_path / "proj", task_root=root,
                         budget_override=True, window_timeout=5,
                         ready_timeout=3, ack_timeout=3)
    assert result.ok
    assert "OVERRIDDEN" in result.detail
    # the budget verdict is durably recorded on the hash-chained event log
    events = board.read_events(event_type="spawn.budget")
    assert events, "budget verdict must be recorded even when overridden"
    detail = events[-1]["detail"]
    assert detail["overridden"] is True
    assert detail["snapshot"]["usd_spent"] == 1321.0
    ok, bad = board.verify_chain()
    assert ok and bad == -1


def test_budget_override_via_env(tmp_path, fake_sc, monkeypatch):
    root = tmp_path / "mesh"
    board = TaskBoard(root)
    monkeypatch.setattr(sc_spawn, "check_agent_budget", lambda *a, **k: (False, "pause"))
    monkeypatch.setattr(sc_spawn, "budget_snapshot", lambda *a, **k: {"reachable": True})
    monkeypatch.setenv(sc_spawn.BUDGET_OVERRIDE_ENV, "1")
    monkeypatch.setattr(sc_spawn, "_ring_doorbell",
                        lambda t, b, task: board.transition(task.task_id,
                                                            TaskState.WORKING, agent="B"))
    result = spawn_agent("B", "task", cwd=tmp_path / "proj", task_root=root,
                         window_timeout=5, ready_timeout=3, ack_timeout=3)
    assert result.ok and "OVERRIDDEN" in result.detail


def test_budget_allowed_records_clean_verdict(tmp_path, fake_sc, monkeypatch):
    """When allowed, the same accounting event fires with overridden=False."""
    root = tmp_path / "mesh"
    board = TaskBoard(root)
    monkeypatch.setattr(sc_spawn, "_ring_doorbell",
                        lambda t, b, task: board.transition(task.task_id,
                                                            TaskState.WORKING, agent="B"))
    result = spawn_agent("B", "task", cwd=tmp_path / "proj", task_root=root,
                         window_timeout=5, ready_timeout=3, ack_timeout=3)
    assert result.ok
    events = board.read_events(event_type="spawn.budget")
    assert events and events[-1]["detail"]["overridden"] is False


def test_budget_snapshot_shape(monkeypatch):
    monkeypatch.setattr(sc_spawn.urllib.request, "urlopen",
                        lambda *a, **k: _Resp('{"usd_spent": 1321.9, "usd_limit": 600.0,'
                                              ' "status": "pause", "reasons": ["over"]}'))
    snap = sc_spawn.budget_snapshot()
    assert snap["reachable"] is True
    assert snap["usd_spent"] == 1321.9
    assert snap["status"] == "pause"

    def boom(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr(sc_spawn.urllib.request, "urlopen", boom)
    snap = sc_spawn.budget_snapshot()
    assert snap["reachable"] is False and "refused" in snap["error"]
