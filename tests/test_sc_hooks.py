"""Unit tests for sc_hooks (install/uninstall) and the hook emitter / done verb."""

import json

import pytest
from sc_done import main as done_main
from sc_hook_emit import handle_event
from sc_hook_emit import main as emit_main
from sc_hooks import HOOK_EVENTS, build_hook_command, install_hooks, uninstall_hooks
from sc_tasks import TaskBoard, TaskState

# ---------- sc_hooks ----------

def _read_settings(project):
    return json.loads((project / ".claude" / "settings.local.json").read_text("utf-8"))


def test_install_writes_all_three_hooks(tmp_path):
    project, root = tmp_path / "proj", tmp_path / "mesh"
    path = install_hooks(project, root, agent="B")
    assert path.exists()
    settings = _read_settings(project)
    assert set(settings["hooks"].keys()) == set(HOOK_EVENTS.keys())
    stop_cmd = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "sc_hook_emit.py" in stop_cmd
    assert "--event stop" in stop_cmd
    assert str(root) in stop_cmd


def test_install_is_idempotent(tmp_path):
    project, root = tmp_path / "proj", tmp_path / "mesh"
    install_hooks(project, root, agent="B")
    install_hooks(project, root, agent="B")
    settings = _read_settings(project)
    assert len(settings["hooks"]["Stop"]) == 1


def test_install_preserves_existing_settings_and_hooks(tmp_path):
    project, root = tmp_path / "proj", tmp_path / "mesh"
    claude_dir = project / ".claude"
    claude_dir.mkdir(parents=True)
    existing = {
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]},
    }
    (claude_dir / "settings.local.json").write_text(json.dumps(existing), "utf-8")

    install_hooks(project, root, agent="B")
    settings = _read_settings(project)
    assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}
    commands = [h["command"] for m in settings["hooks"]["Stop"] for h in m["hooks"]]
    assert "echo mine" in commands
    assert any("sc_hook_emit.py" in c for c in commands)


def test_uninstall_removes_only_ours(tmp_path):
    project, root = tmp_path / "proj", tmp_path / "mesh"
    claude_dir = project / ".claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / "settings.local.json").write_text(json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    ), "utf-8")
    install_hooks(project, root, agent="B")

    assert uninstall_hooks(project) is True
    settings = _read_settings(project)
    commands = [h["command"] for m in settings["hooks"].get("Stop", []) for h in m["hooks"]]
    assert commands == ["echo mine"]
    assert "UserPromptSubmit" not in settings["hooks"]
    assert uninstall_hooks(project) is False  # second pass: nothing to do


def test_build_hook_command_quotes_paths():
    cmd = build_hook_command(r"C:\has space\mesh", "B", "ack", python_exe=r"C:\py\python.exe")
    assert '"C:\\has space\\mesh"' in cmd
    assert cmd.startswith('"C:\\py\\python.exe"')


# ---------- sc_hook_emit ----------

@pytest.fixture
def board(tmp_path):
    return TaskBoard(tmp_path / "mesh")


def test_ack_advances_submitted_to_working(board):
    t = board.create("t", agent="B")
    msg = handle_event(board, "B", "ack",
                       {"session_id": "s1", "transcript_path": "x.jsonl"})
    assert "working" in msg
    fresh = board.get(t.task_id)
    assert fresh.state is TaskState.WORKING
    assert fresh.meta["session_id"] == "s1"
    assert fresh.meta["transcript_path"] == "x.jsonl"


def test_notification_marks_input_required_and_ack_unsticks(board):
    t = board.create("t", agent="B")
    handle_event(board, "B", "ack", {})
    handle_event(board, "B", "notification", {"message": "needs permission"})
    assert board.get(t.task_id).state is TaskState.INPUT_REQUIRED
    handle_event(board, "B", "ack", {})
    assert board.get(t.task_id).state is TaskState.WORKING


def test_stop_records_turn_end_without_completing(board):
    t = board.create("t", agent="B")
    handle_event(board, "B", "ack", {})
    handle_event(board, "B", "stop", {"transcript_path": "t.jsonl"})
    fresh = board.get(t.task_id)
    assert fresh.state is TaskState.WORKING  # stop != done
    assert "turn_ended_ts" in fresh.meta


def test_emit_main_never_fails(tmp_path, capsys):
    root = tmp_path / "mesh"
    TaskBoard(root)  # init dirs
    rc = emit_main(["--task-root", str(root), "--agent", "ghost", "--event", "ack"],
                   stdin_text="not json{{{")
    assert rc == 0
    assert "no active task" in capsys.readouterr().out


# ---------- sc_done ----------

def test_done_completes_task(board, capsys):
    t = board.create("t", agent="B")
    board.claim("B", task_id=t.task_id)
    rc = done_main(["--task-root", str(board.root), "--task-id", t.task_id,
                    "--result", "shipped"])
    assert rc == 0
    fresh = board.get(t.task_id)
    assert fresh.state is TaskState.COMPLETED
    assert fresh.result == "shipped"


def test_done_from_submitted_passes_through_working(board):
    t = board.create("t", agent="B")
    rc = done_main(["--task-root", str(board.root), "--task-id", t.task_id,
                    "--result", "quick"])
    assert rc == 0
    assert board.get(t.task_id).state is TaskState.COMPLETED


def test_done_fail_flag_and_result_file(board, tmp_path):
    t = board.create("t", agent="B")
    board.claim("B", task_id=t.task_id)
    rc = done_main(["--task-root", str(board.root), "--task-id", t.task_id,
                    "--fail", "--error", "blocked on creds"])
    assert rc == 0
    assert board.get(t.task_id).state is TaskState.FAILED

    t2 = board.create("t2", agent="B")
    board.claim("B", task_id=t2.task_id)
    rf = tmp_path / "result.md"
    rf.write_text("long result body", encoding="utf-8")
    rc = done_main(["--task-root", str(board.root), "--task-id", t2.task_id,
                    "--result-file", str(rf)])
    assert rc == 0
    assert board.get(t2.task_id).result == "long result body"


def test_done_unknown_task_returns_1(board):
    rc = done_main(["--task-root", str(board.root), "--task-id", "nope"])
    assert rc == 1
