"""Unit tests for sc_transcript — project-dir encoding, tailing, replies."""

import json
import threading
import time

import pytest
import sc_transcript
from sc_transcript import (
    assistant_messages,
    encode_project_dir,
    find_session_files,
    last_assistant_message,
    newest_session,
    read_entries,
    wait_for_assistant_reply,
    wait_for_session,
)


def _entry(role: str, text: str) -> str:
    if role == "assistant":
        return json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]},
        })
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    return tmp_path


def test_encode_project_dir_matches_claude_convention():
    assert encode_project_dir(r"C:\Users\techai") == "C--Users-techai"
    assert encode_project_dir(r"C:\Users\techai\PKA testing\selfconnect") == \
        "C--Users-techai-PKA-testing-selfconnect"


def test_find_and_newest_session(profile):
    cwd = r"C:\work\proj"
    proj = sc_transcript.project_dir_for(cwd)
    proj.mkdir(parents=True)
    old = proj / "old.jsonl"
    old.write_text(_entry("user", "hi") + "\n", encoding="utf-8")
    time.sleep(0.05)
    new = proj / "new.jsonl"
    new.write_text(_entry("user", "hi again") + "\n", encoding="utf-8")

    files = find_session_files(cwd)
    assert files[0] == new
    assert newest_session(cwd) == new
    # since_ts filters out sessions older than the spawn
    assert find_session_files(cwd, since_ts=new.stat().st_mtime) == [new]


def test_wait_for_session_sees_late_file(profile):
    cwd = r"C:\work\late"
    proj = sc_transcript.project_dir_for(cwd)

    def create_later():
        time.sleep(0.2)
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(_entry("user", "x") + "\n", encoding="utf-8")

    threading.Thread(target=create_later).start()
    found = wait_for_session(cwd, since_ts=0.0, timeout=5.0, poll=0.05)
    assert found is not None and found.name == "s1.jsonl"


def test_assistant_messages_and_last(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text(
        _entry("user", "q1") + "\n" + _entry("assistant", "a1") + "\n"
        + _entry("user", "q2") + "\n" + _entry("assistant", "a2") + "\n",
        encoding="utf-8",
    )
    assert assistant_messages(t) == ["a1", "a2"]
    assert last_assistant_message(t) == "a2"


def test_string_content_and_garbage_lines_tolerated(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text(
        json.dumps({"type": "assistant", "message": {"content": "plain string"}})
        + "\nnot json at all\n",
        encoding="utf-8",
    )
    assert assistant_messages(t) == ["plain string"]


def test_read_entries_incremental_offsets_and_partial_line(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text(_entry("user", "one") + "\n", encoding="utf-8")
    entries, off = read_entries(t)
    assert len(entries) == 1

    with t.open("a", encoding="utf-8") as f:
        f.write(_entry("assistant", "two") + "\n")
        f.write('{"type": "assis')  # partial write in flight
    entries2, off2 = read_entries(t, offset=off)
    assert len(entries2) == 1
    assert entries2[0]["type"] == "assistant"

    with t.open("a", encoding="utf-8") as f:
        f.write('tant", "message": {"content": "three"}}\n')
    entries3, _ = read_entries(t, offset=off2)
    assert len(entries3) == 1  # completed partial line now parses


def test_wait_for_assistant_reply(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text(_entry("assistant", "old") + "\n", encoding="utf-8")
    baseline = len(assistant_messages(t))

    def reply_later():
        time.sleep(0.2)
        with t.open("a", encoding="utf-8") as f:
            f.write(_entry("assistant", "fresh reply") + "\n")

    threading.Thread(target=reply_later).start()
    got = wait_for_assistant_reply(t, after_count=baseline, timeout=5.0, poll=0.05)
    assert got == "fresh reply"


def test_wait_for_assistant_reply_timeout(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text(_entry("assistant", "only") + "\n", encoding="utf-8")
    assert wait_for_assistant_reply(t, after_count=1, timeout=0.3, poll=0.05) == ""
