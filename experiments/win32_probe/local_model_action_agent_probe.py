"""Probe: local model chooses and executes a SelfConnect action.

This is stricter than a generation-only proof. The local model must return a
structured action:

    {"tool": "selfconnect_send", "args": {"message": "...nonce..."}}

The harness validates the action, executes it through guarded SelfConnect into a
throwaway receiver terminal, and verifies the nonce in the receiver log.
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
local_probe = __import__("local_model_selfconnect_probe")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.removeprefix("json").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model did not return a JSON object")
    data = json.loads(stripped[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("model JSON was not an object")
    return data


def _validate_action(action: dict[str, Any], *, nonce: str) -> str:
    if action.get("tool") != "selfconnect_send":
        raise ValueError(f"unsupported tool: {action.get('tool')!r}")
    args = action.get("args")
    if not isinstance(args, dict):
        raise ValueError("action args must be an object")
    message = args.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("action args.message must be a non-empty string")
    if nonce not in message:
        raise ValueError("action message did not contain nonce")
    one_line = " ".join(message.replace("\r", " ").replace("\n", " ").split())
    if len(one_line) > 220:
        raise ValueError("action message too long")
    return one_line


def _local_model_action(model: str, nonce: str) -> tuple[dict[str, Any], str]:
    prompt = "\n".join([
        "You are LOCAL-OLLAMA-1, an offline local model in a SelfConnect test.",
        "You have exactly one allowed tool:",
        '{"tool":"selfconnect_send","args":{"message":"<one-line message>"}}',
        "Goal: send a one-line status to SC-RECEIVER proving you can choose an action.",
        f"The message MUST include this nonce exactly once: {nonce}",
        "Return only the JSON object. No markdown. No explanation.",
    ])
    raw = local_probe._ollama_generate(model, prompt)
    return _extract_json_object(raw), raw


def run_probe(model: str, *, keep_receiver: bool = False, results_dir: Path | None = None) -> dict[str, Any]:
    nonce = f"SC_LOCAL_ACTION_{uuid.uuid4().hex[:8].upper()}"
    title = f"SC_ACTION_RECEIVER_{nonce[-8:]}"
    temp_dir = Path(tempfile.gettempdir()) / "selfconnect_local_action_probe"
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
    readback: dict[str, Any] = {}
    send_result: dict[str, Any] = {}
    receiver_log = ""
    action: dict[str, Any] = {}
    raw_model_output = ""
    message = ""

    try:
        action, raw_model_output = _local_model_action(model, nonce)
        message = _validate_action(action, nonce=nonce)

        receiver_pid = local_probe._spawn_receiver(title, receiver_script)
        window = local_probe._wait_for_window(title)
        local_probe._wait_for_file(ready_path)

        packet = f"[LOCAL-OLLAMA-1 -> SC-RECEIVER] action=selfconnect_send {message}"
        send_result = sc_cli.send_text_to_window(
            int(window["hwnd"]),
            packet,
            submit=True,
            allow_input=True,
            expected_pid=int(window["pid"]),
            expected_exe=str(window["exe_name"]),
            expected_class=str(window["class_name"]),
            expected_title=title,
            char_delay=0.005,
        )
        if not send_result.get("ok"):
            raise RuntimeError(f"selfconnect send failed: {send_result}")

        receiver_log = local_probe._wait_for_file_text(log_path, nonce)
        try:
            readback = sc_cli.read_window(int(window["hwnd"]))
        except Exception:
            readback = {}
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
        "nonce": nonce,
        "elapsed_ms": elapsed_ms,
        "tool_requested": action.get("tool", ""),
        "action_validated": bool(message),
        "send_ok": bool(send_result.get("ok")),
        "observed_nonce": verdict == "PASS",
        "readback_method": readback.get("method", "") or "receiver_log",
        "receiver": {
            "hwnd": int(window.get("hwnd", 0) or 0),
            "process_pid": int(receiver_pid or 0),
            "pid": int(window.get("pid", 0) or 0),
            "class_name": window.get("class_name", ""),
            "title_hash": _sha256(str(window.get("title", ""))),
        },
        "model_output_hash": _sha256(raw_model_output),
        "action_json_hash": _sha256(json.dumps(action, sort_keys=True)),
        "action_message_hash": _sha256(message),
        "receiver_log_hash": _sha256(receiver_log),
        "control_path": "Ollama local action JSON -> validated tool action -> SelfConnect Win32 send_text_to_window -> throwaway receiver log verification",
        "airgap_scope": "No cloud/API/MCP/CDP/WebDriver/browser extension required for this probe; Ollama endpoint is localhost only.",
    }

    out_dir = results_dir or Path("experiments/win32_probe/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"local_model_action_agent_{verdict.lower()}_{nonce[-8:]}.json"
    out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    artifact["artifact_path"] = str(out_path)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local model action-agent SelfConnect probe")
    parser.add_argument("--model", default=os.environ.get("SC_LOCAL_MODEL", "hermes3:3b"))
    parser.add_argument("--keep-receiver", action="store_true")
    args = parser.parse_args(argv)
    artifact = run_probe(args.model, keep_receiver=args.keep_receiver)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
