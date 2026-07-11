from __future__ import annotations

import json
import shutil
from pathlib import Path

import sc_local_model_role as rolemod
import sc_mesh_registry

TMP_ROOT = Path(__file__).resolve().parent / "_tmp" / "local_model_role"


def _case_dir(name: str) -> Path:
    path = TMP_ROOT / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_ensure_role_creates_mailboxes_and_virtual_registry_row() -> None:
    root = _case_dir("ensure")
    registry_path = root / "mesh_registry.json"

    result = rolemod.ensure_role(
        "local-ollama-1",
        model="gemma3:latest",
        task="durable test",
        root=root / "roles",
        registry_path=registry_path,
    )

    assert result["ok"] is True
    state = result["state"]
    assert state["role"] == "local-ollama-1"
    assert state["model"] == "gemma3:latest"
    assert state["transport"] == "mailbox"
    assert Path(state["inbox"]).exists()
    assert Path(state["outbox"]).exists()

    registry = sc_mesh_registry.load_registry(registry_path)
    [agent] = registry["agents"]
    assert agent["role"] == "local-ollama-1"
    assert agent["agent"] == "local_model"
    assert agent["transport"] == "mailbox"
    assert agent["hwnd"] == 0
    assert agent["class_name"] == "virtual"
    assert agent["model"] == "gemma3:latest"


def test_ensure_role_is_stable_for_same_virtual_role() -> None:
    root = _case_dir("stable")
    registry_path = root / "mesh_registry.json"

    first = rolemod.ensure_role("local-ollama-1", root=root / "roles", registry_path=registry_path)
    second = rolemod.ensure_role("local-ollama-1", root=root / "roles", registry_path=registry_path)

    assert first["state"]["birth_id"] == second["state"]["birth_id"]
    assert first["state"]["generation"] == second["state"]["generation"]


def test_ensure_role_restores_identity_when_registry_is_lost() -> None:
    root = _case_dir("registry_lost")
    registry_path = root / "mesh_registry.json"

    first = rolemod.ensure_role("local-ollama-1", root=root / "roles", registry_path=registry_path)
    registry_path.unlink()
    restored = rolemod.ensure_role("local-ollama-1", root=root / "roles", registry_path=registry_path)

    assert restored["state"]["birth_id"] == first["state"]["birth_id"]
    assert restored["state"]["generation"] == first["state"]["generation"]
    registry = sc_mesh_registry.load_registry(registry_path)
    [agent] = registry["agents"]
    assert agent["birth_id"] == first["state"]["birth_id"]


def test_write_inbox_and_outbox_are_durable_jsonl() -> None:
    root = _case_dir("mailbox")
    rolemod.ensure_role("local-ollama-1", root=root / "roles", registry_path=root / "mesh.json")

    inbound = rolemod.write_inbox(
        "local-ollama-1",
        from_role="codex-1",
        text="hello\r\nlocal model",
        nonce="SC_TEST",
        root=root / "roles",
    )
    outbound = rolemod.write_outbox(
        "local-ollama-1",
        to_role="codex-1",
        text="ACK SC_TEST",
        nonce="SC_TEST",
        root=root / "roles",
    )

    assert inbound["ok"] is True
    assert outbound["ok"] is True
    assert inbound["message"]["text"] == "hello local model"
    assert inbound["message"]["birth_id"]
    assert inbound["message"]["generation"] == 1
    assert outbound["message"]["from"] == "local-ollama-1"

    inbox = rolemod.read_box("local-ollama-1", box="inbox", root=root / "roles")
    outbox = rolemod.read_box("local-ollama-1", box="outbox", root=root / "roles")
    assert inbox["messages"][0]["nonce"] == "SC_TEST"
    assert outbox["messages"][0]["text"] == "ACK SC_TEST"

    raw_line = Path(inbound["path"]).read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(raw_line)["id"] == inbound["message"]["id"]


def test_write_before_init_fails_closed() -> None:
    root = _case_dir("write_before_init")

    result = rolemod.write_inbox(
        "local-ollama-1",
        from_role="codex-1",
        text="hello",
        root=root / "roles",
    )

    assert result["ok"] is False
    assert result["error"] == "role not initialized"
    assert "init" in result["hint"]


def test_message_size_limit_rejects_unbounded_payload() -> None:
    root = _case_dir("message_size")
    rolemod.ensure_role("local-ollama-1", root=root / "roles", registry_path=root / "mesh.json")

    result = rolemod.write_inbox(
        "local-ollama-1",
        from_role="codex-1",
        text="x" * (rolemod.MAX_MESSAGE_CHARS + 1),
        root=root / "roles",
    )

    assert result["ok"] is False
    assert result["error"] == "message too long"
    assert result["limit"] == rolemod.MAX_MESSAGE_CHARS


def test_corrupted_jsonl_is_reported_without_losing_valid_messages() -> None:
    root = _case_dir("corrupt_jsonl")
    rolemod.ensure_role("local-ollama-1", root=root / "roles", registry_path=root / "mesh.json")
    rolemod.write_inbox(
        "local-ollama-1",
        from_role="codex-1",
        text="valid one",
        root=root / "roles",
    )
    inbox_path = rolemod.paths_for("local-ollama-1", root=root / "roles")["inbox"]
    with inbox_path.open("a", encoding="utf-8") as fh:
        fh.write("{not-json}\n")
        fh.write("[]\n")
    rolemod.write_inbox(
        "local-ollama-1",
        from_role="codex-1",
        text="valid two",
        root=root / "roles",
    )

    inbox = rolemod.read_box("local-ollama-1", box="inbox", root=root / "roles")
    status = rolemod.status("local-ollama-1", root=root / "roles")

    assert inbox["parse_error_count"] == 2
    assert [item["text"] for item in inbox["messages"]] == ["valid one", "valid two"]
    assert status["parse_error_count"] == 2


def test_virtual_heartbeat_does_not_require_hwnd() -> None:
    root = _case_dir("heartbeat")
    registry_path = root / "mesh_registry.json"
    rolemod.ensure_role("local-ollama-1", root=root / "roles", registry_path=registry_path)

    result = sc_mesh_registry.heartbeat("local-ollama-1", registry_path=registry_path)

    assert result["ok"] is True
    assert result["guard"] is None
    assert result["agent"]["guard_ok"] is None


def test_invalid_mailbox_write_rejects_bad_inputs() -> None:
    root = _case_dir("invalid")

    bad_box = rolemod.write_message(
        "local-ollama-1",
        box="wrong",
        from_role="codex-1",
        to_role="local-ollama-1",
        text="hello",
        root=root,
    )
    empty = rolemod.write_inbox(
        "local-ollama-1",
        from_role="codex-1",
        text="",
        root=root,
    )

    assert bad_box["ok"] is False
    assert empty["ok"] is False
