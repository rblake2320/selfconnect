from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# ruff: noqa: E402,I001

ROOT = Path(__file__).resolve().parents[1]
PROBE_DIR = ROOT / "experiments" / "win32_probe"
for candidate in (ROOT, PROBE_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import local_model_action_agent_probe as probe


NONCE = "SC_LOCAL_ACTION_TEST1234"
TMP_ROOT = ROOT / "tests" / "_tmp" / "local_model_action_agent"


def _case_dir(name: str) -> Path:
    path = TMP_ROOT / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_extract_json_object_accepts_plain_json() -> None:
    action = probe._extract_json_object(
        '{"tool":"selfconnect_send","args":{"message":"hello SC_LOCAL_ACTION_TEST1234"}}'
    )

    assert action["tool"] == "selfconnect_send"
    assert action["args"]["message"].endswith("SC_LOCAL_ACTION_TEST1234")


def test_extract_json_object_accepts_markdown_fence() -> None:
    action = probe._extract_json_object(
        '```json\n{"tool":"selfconnect_send","args":{"message":"ok SC_LOCAL_ACTION_TEST1234"}}\n```'
    )

    assert action["tool"] == "selfconnect_send"


def test_extract_json_object_rejects_missing_object() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        probe._extract_json_object("not json")


def test_validate_action_returns_compact_one_line() -> None:
    message = probe._validate_action(
        {
            "tool": "selfconnect_send",
            "args": {"message": f"hello\r\n  from local model   {NONCE}"},
        },
        nonce=NONCE,
    )

    assert message == f"hello from local model {NONCE}"


@pytest.mark.parametrize(
    ("action", "error"),
    [
        ({"tool": "shell", "args": {"message": NONCE}}, "unsupported tool"),
        ({"tool": "selfconnect_send", "args": []}, "args must be an object"),
        ({"tool": "selfconnect_send", "args": {"message": ""}}, "non-empty string"),
        ({"tool": "selfconnect_send", "args": {"message": "missing nonce"}}, "did not contain nonce"),
        (
            {"tool": "selfconnect_send", "args": {"message": f"{NONCE} " + ("x" * 230)}},
            "too long",
        ),
    ],
)
def test_validate_action_rejects_invalid_actions(action: dict[str, object], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        probe._validate_action(action, nonce=NONCE)


def test_run_probe_success_writes_redacted_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    results_dir = _case_dir("success")
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        probe,
        "_local_model_action",
        lambda model, nonce: (
            {"tool": "selfconnect_send", "args": {"message": f"ready {nonce}"}},
            '{"tool":"selfconnect_send"}',
        ),
    )
    monkeypatch.setattr(probe.local_probe, "_write_receiver_script", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe.local_probe, "_spawn_receiver", lambda title, script: 1234)
    monkeypatch.setattr(
        probe.local_probe,
        "_wait_for_window",
        lambda title: {
            "hwnd": 987654,
            "pid": 4321,
            "exe_name": "WindowsTerminal.exe",
            "class_name": "CASCADIA_HOSTING_WINDOW_CLASS",
            "title": title,
        },
    )
    monkeypatch.setattr(probe.local_probe, "_wait_for_file", lambda path: None)
    monkeypatch.setattr(probe.local_probe, "_wait_for_file_text", lambda path, nonce: f"observed {nonce}")
    monkeypatch.setattr(probe.sc_cli, "read_window", lambda hwnd: {"method": "TextPattern_poll"})

    def fake_send(hwnd: int, packet: str, **kwargs: object) -> dict[str, object]:
        calls["hwnd"] = hwnd
        calls["packet"] = packet
        calls["kwargs"] = kwargs
        return {"ok": True, "guard": {"ok": True}}

    monkeypatch.setattr(probe.sc_cli, "send_text_to_window", fake_send)
    monkeypatch.setattr(probe.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    artifact = probe.run_probe("fake-model", results_dir=results_dir)

    assert artifact["verdict"] == "PASS"
    assert artifact["redacted"] is True
    assert artifact["action_validated"] is True
    assert artifact["send_ok"] is True
    assert artifact["observed_nonce"] is True
    assert artifact["readback_method"] == "TextPattern_poll"
    assert artifact["tool_requested"] == "selfconnect_send"
    assert artifact["receiver"]["hwnd"] == 987654
    assert "LOCAL-OLLAMA-1 -> SC-RECEIVER" in str(calls["packet"])
    assert calls["kwargs"] == {
        "submit": True,
        "allow_input": True,
        "expected_pid": 4321,
        "expected_exe": "WindowsTerminal.exe",
        "expected_class": "CASCADIA_HOSTING_WINDOW_CLASS",
        "expected_title": artifact["nonce"].replace("SC_LOCAL_ACTION_", "SC_ACTION_RECEIVER_"),
        "char_delay": 0.005,
    }
    saved = Path(str(artifact["artifact_path"]))
    assert saved.exists()
    assert json.loads(saved.read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_run_probe_fails_closed_when_guarded_send_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results_dir = _case_dir("guard_reject")
    monkeypatch.setattr(
        probe,
        "_local_model_action",
        lambda model, nonce: (
            {"tool": "selfconnect_send", "args": {"message": f"ready {nonce}"}},
            '{"tool":"selfconnect_send"}',
        ),
    )
    monkeypatch.setattr(probe.local_probe, "_write_receiver_script", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe.local_probe, "_spawn_receiver", lambda title, script: 2222)
    monkeypatch.setattr(
        probe.local_probe,
        "_wait_for_window",
        lambda title: {
            "hwnd": 123,
            "pid": 456,
            "exe_name": "notepad.exe",
            "class_name": "Notepad",
            "title": "wrong target",
        },
    )
    monkeypatch.setattr(probe.local_probe, "_wait_for_file", lambda path: None)
    monkeypatch.setattr(probe.sc_cli, "send_text_to_window", lambda *args, **kwargs: {"ok": False})
    monkeypatch.setattr(probe.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    artifact = probe.run_probe("fake-model", results_dir=results_dir)

    assert artifact["verdict"] == "FAIL"
    assert "selfconnect send failed" in artifact["failure"]
    assert artifact["send_ok"] is False
    assert artifact["observed_nonce"] is False


def test_run_probe_falls_back_to_receiver_log_when_readback_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results_dir = _case_dir("readback_error")
    monkeypatch.setattr(
        probe,
        "_local_model_action",
        lambda model, nonce: (
            {"tool": "selfconnect_send", "args": {"message": f"ready {nonce}"}},
            '{"tool":"selfconnect_send"}',
        ),
    )
    monkeypatch.setattr(probe.local_probe, "_write_receiver_script", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe.local_probe, "_spawn_receiver", lambda title, script: 3333)
    monkeypatch.setattr(
        probe.local_probe,
        "_wait_for_window",
        lambda title: {
            "hwnd": 777,
            "pid": 888,
            "exe_name": "WindowsTerminal.exe",
            "class_name": "CASCADIA_HOSTING_WINDOW_CLASS",
            "title": title,
        },
    )
    monkeypatch.setattr(probe.local_probe, "_wait_for_file", lambda path: None)
    monkeypatch.setattr(probe.local_probe, "_wait_for_file_text", lambda path, nonce: f"observed {nonce}")
    monkeypatch.setattr(probe.sc_cli, "send_text_to_window", lambda *args, **kwargs: {"ok": True})

    def fail_read(hwnd: int) -> dict[str, object]:
        raise RuntimeError("uia unavailable")

    monkeypatch.setattr(probe.sc_cli, "read_window", fail_read)
    monkeypatch.setattr(probe.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    artifact = probe.run_probe("fake-model", results_dir=results_dir)

    assert artifact["verdict"] == "PASS"
    assert artifact["readback_method"] == "receiver_log"
