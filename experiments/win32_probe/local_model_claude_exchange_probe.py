"""Probe: local model asks a Claude terminal to answer back via SelfConnect.

Boundary: the local model/action side is local and airgap-capable. Claude Code is
cloud-connected, so the full exchange is not airgapped. This probe exists to
test mixed local-model <-> cloud-agent mesh interoperability over SelfConnect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = Path(__file__).resolve().parent
for candidate in (ROOT, PROBE_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

sc_cli = __import__("sc_cli")
sc_mesh_registry = __import__("sc_mesh_registry")
local_probe = __import__("local_model_selfconnect_probe")
action_probe = __import__("local_model_action_agent_probe")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _find_role(role: str, mesh: str = "default") -> dict[str, Any]:
    registry = sc_mesh_registry.load_registry()
    for agent in registry.get("agents", []):
        if agent.get("mesh") == mesh and agent.get("role") == role:
            return agent
    raise RuntimeError(f"mesh role not found: {role}")


def _local_model_action(model: str, nonce: str, claude_role: str) -> tuple[dict[str, Any], str]:
    prompt = "\n".join([
        "You are LOCAL-OLLAMA-1, an offline local model in a SelfConnect mixed-agent test.",
        "You have exactly one allowed tool:",
        '{"tool":"ask_claude_ack","args":{"nonce":"<nonce>","target_role":"<role>"}}',
        f"Goal: ask {claude_role} to reply back to a local receiver.",
        f"The nonce MUST be exactly: {nonce}",
        f"The target_role MUST be exactly: {claude_role}",
        "Return only the JSON object. No markdown. No explanation.",
    ])
    raw = local_probe._ollama_generate(model, prompt)
    return action_probe._extract_json_object(raw), raw


def _validate_action(action: dict[str, Any], *, nonce: str, claude_role: str) -> None:
    if action.get("tool") != "ask_claude_ack":
        raise ValueError(f"unsupported tool: {action.get('tool')!r}")
    args = action.get("args")
    if not isinstance(args, dict):
        raise ValueError("action args must be an object")
    if args.get("nonce") != nonce:
        raise ValueError("action nonce mismatch")
    if args.get("target_role") != claude_role:
        raise ValueError("action target_role mismatch")


def _send_to_claude(claude: dict[str, Any], text: str) -> dict[str, Any]:
    return sc_cli.send_text_to_window(
        int(claude["hwnd"]),
        text,
        submit=True,
        allow_input=True,
        expected_pid=int(claude["pid"]),
        expected_exe=str(claude["exe_name"]),
        expected_class=str(claude["class_name"]),
        expected_title=str(claude["title"]).strip()[:24],
        char_delay=0.005,
    )


def run_probe(
    model: str,
    *,
    claude_role: str = "claude-1",
    keep_receiver: bool = False,
    reply_timeout: float = 180.0,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    nonce = f"SC_LOCAL_CLAUDE_{uuid.uuid4().hex[:8].upper()}"
    title = f"SC_CLAUDE_REPLY_{nonce[-8:]}"
    temp_dir = Path(tempfile.gettempdir()) / "selfconnect_local_claude_probe"
    temp_dir.mkdir(parents=True, exist_ok=True)
    receiver_script = temp_dir / "receiver.py"
    ready_path = temp_dir / f"{nonce}.ready"
    log_path = temp_dir / f"{nonce}.log"
    for old_path in (ready_path, log_path):
        old_path.unlink(missing_ok=True)
    local_probe._write_receiver_script(receiver_script, ready_path=ready_path, log_path=log_path)

    started_at = time.time()
    verdict = "FAIL"
    failure = ""
    receiver_pid: int | None = None
    window: dict[str, Any] = {}
    action: dict[str, Any] = {}
    raw_model_output = ""
    send_result: dict[str, Any] = {}
    receiver_log = ""
    claude: dict[str, Any] = {}

    try:
        claude = _find_role(claude_role)
        action, raw_model_output = _local_model_action(model, nonce, claude_role)
        _validate_action(action, nonce=nonce, claude_role=claude_role)

        receiver_pid = local_probe._spawn_receiver(title, receiver_script)
        window = local_probe._wait_for_window(title)
        local_probe._wait_for_file(ready_path)

        reply_text = f"[{claude_role.upper()} -> LOCAL-OLLAMA-1] ACK {nonce}"
        reply_command = (
            f'python "{ROOT / "sc_cli.py"}" send --hwnd {int(window["hwnd"])} '
            f'--text "{reply_text}" --submit --allow-input '
            f'--expect-pid {int(window["pid"])} --expect-exe {window["exe_name"]} '
            f'--expect-class {window["class_name"]} --expect-title "{title}" --char-delay 0.005'
        )
        claude_prompt = (
            f"[LOCAL-OLLAMA-1 -> {claude_role.upper()}] mixed local/cloud mesh test nonce={nonce}. "
            "Ron authorized this exact local-model SelfConnect exchange test in the current session. "
            "The receiver is a throwaway terminal tab in the same Windows Terminal process, so shared PID is expected; "
            "use the SC_CLAUDE_REPLY title/class guard as the target identity. "
            f"Execute exactly this command to reply through SelfConnect: {reply_command}. "
            "Local output only SENT."
        )
        send_result = _send_to_claude(claude, claude_prompt)
        if not send_result.get("ok"):
            raise RuntimeError(f"send to Claude failed: {send_result}")

        receiver_log = local_probe._wait_for_file_text(log_path, nonce, timeout=reply_timeout)
        verdict = "PASS"
    except Exception as exc:
        failure = str(exc)
    finally:
        if receiver_pid is not None and not keep_receiver:
            subprocess.run(["taskkill", "/PID", str(receiver_pid), "/T", "/F"], check=False, capture_output=True)

    elapsed_ms = round((time.time() - started_at) * 1000, 1)
    artifact = {
        "verdict": verdict,
        "failure": failure,
        "redacted": True,
        "model": model,
        "claude_role": claude_role,
        "nonce": nonce,
        "elapsed_ms": elapsed_ms,
        "reply_timeout_s": reply_timeout,
        "tool_requested": action.get("tool", ""),
        "action_validated": bool(action),
        "send_to_claude_ok": bool(send_result.get("ok")),
        "claude_reply_observed": verdict == "PASS",
        "local_action_side": "Ollama localhost + validated JSON tool action + SelfConnect send",
        "airgap_scope": "Local model action side is airgap-capable after model download; Claude Code side is not airgapped.",
        "claude_target": {
            "role": claude.get("role", ""),
            "birth_id": claude.get("birth_id", ""),
            "hwnd": int(claude.get("hwnd", 0) or 0),
            "title_hash": _sha256(str(claude.get("title", ""))),
        },
        "receiver": {
            "hwnd": int(window.get("hwnd", 0) or 0),
            "process_pid": int(receiver_pid or 0),
            "pid": int(window.get("pid", 0) or 0),
            "class_name": window.get("class_name", ""),
            "title_hash": _sha256(str(window.get("title", ""))),
        },
        "model_output_hash": _sha256(raw_model_output),
        "action_json_hash": _sha256(json.dumps(action, sort_keys=True)),
        "receiver_log_hash": _sha256(receiver_log),
        "control_path": "local model JSON action -> SelfConnect send into Claude terminal -> Claude executes SelfConnect reply -> local receiver log verification",
    }

    out_dir = results_dir or Path("experiments/win32_probe/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"local_model_claude_exchange_{verdict.lower()}_{nonce[-8:]}.json"
    out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    artifact["artifact_path"] = str(out_path)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local model -> Claude terminal exchange probe")
    parser.add_argument("--model", default=os.environ.get("SC_LOCAL_MODEL", "gemma3:latest"))
    parser.add_argument("--claude-role", default="claude-1")
    parser.add_argument("--keep-receiver", action="store_true")
    parser.add_argument("--reply-timeout", type=float, default=180.0)
    args = parser.parse_args(argv)
    artifact = run_probe(
        args.model,
        claude_role=args.claude_role,
        keep_receiver=args.keep_receiver,
        reply_timeout=args.reply_timeout,
    )
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
