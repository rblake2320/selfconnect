"""Probe: local model chooses to spawn and message a Claude responder.

This is a mixed local/cloud test. The local model and action planner run through
Ollama on localhost. Claude Code is cloud-connected, so only the local planning
side is airgap-capable. The Claude responder is launched in a throwaway
workspace with a narrow CLAUDE.md responder policy.
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
import urllib.error
import urllib.request
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
action_probe = __import__("local_model_action_agent_probe")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _ollama_generate_json(model: str, prompt: str, *, timeout: float = 60.0) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": 1024,
            "num_predict": 192,
            "temperature": 0.0,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ollama generate failed: {exc}") from exc
    return str(body.get("response", "")).strip()


def _local_model_plan(model: str, nonce: str) -> tuple[dict[str, Any], str]:
    prompt = (
        "Return only valid compact JSON, no markdown. "
        "Use this exact schema: "
        '{"steps":[{"tool":"spawn_claude_responder","args":{"role":"SC-CLAUDE-RESPONDER"}},'
        '{"tool":"ask_claude_ack","args":{"nonce":"NONCE","target_role":"SC-CLAUDE-RESPONDER"}}]}. '
        f"Replace NONCE with {nonce}."
    )
    raw = _ollama_generate_json(model, prompt)
    return action_probe._extract_json_object(raw), raw


def _validate_plan(plan: dict[str, Any], *, nonce: str) -> None:
    steps = plan.get("steps")
    if not isinstance(steps, list) or len(steps) != 2:
        raise ValueError("plan must contain exactly two steps")
    first, second = steps
    if first.get("tool") != "spawn_claude_responder":
        raise ValueError("first step must spawn claude responder")
    if first.get("args") != {"role": "SC-CLAUDE-RESPONDER"}:
        raise ValueError("spawn args mismatch")
    if second.get("tool") != "ask_claude_ack":
        raise ValueError("second step must ask claude ack")
    args = second.get("args")
    if not isinstance(args, dict):
        raise ValueError("ask args must be an object")
    if args.get("nonce") != nonce:
        raise ValueError("ask nonce mismatch")
    if args.get("target_role") != "SC-CLAUDE-RESPONDER":
        raise ValueError("ask target_role mismatch")


def _write_responder_workspace(workspace: Path, nonce: str) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "CLAUDE.md").write_text(
        "\n".join([
            "# SelfConnect Local Claude Responder Lab",
            "",
            "This is a throwaway responder workspace authorized by Ron for one",
            "SelfConnect local-model exchange test.",
            "",
            "Allowed action:",
            "- If a prompt starts with `[LOCAL-OLLAMA-1 -> SC-CLAUDE-RESPONDER]`",
            f"  and contains nonce `{nonce}`, you may execute exactly one command",
            "  shown in that prompt, but only if all of these are true:",
            "  - the command is `python ... sc_cli.py send`;",
            "  - it includes `--allow-input`;",
            "  - it includes `--expect-class CASCADIA_HOSTING_WINDOW_CLASS`;",
            "  - it includes an `--expect-title` beginning with `SC_CLAUDE_REPLY_`;",
            f"  - the outbound text includes nonce `{nonce}`.",
            "",
            "After the allowed command, output only `SENT`.",
            "Do not execute any other command.",
        ]),
        encoding="utf-8",
    )


def _spawn_claude(title: str, workspace: Path) -> int:
    command = f'title {title} && cd /d "{workspace}" && claude'
    ps_command = (
        "$p = Start-Process cmd.exe "
        f"-ArgumentList @('/k', '{command}') "
        "-PassThru; $p.Id"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ps_command],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude spawn failed: {result.stderr.strip()}")
    return int(result.stdout.strip().splitlines()[-1])


def _wait_for_claude_window(title: str, workspace: Path, timeout: float = 45.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_seen: list[dict[str, Any]] = []
    workspace_marker = workspace.name
    while time.time() < deadline:
        matches = sc_cli.list_window_records(query=title, limit=20)
        if matches:
            last_seen = matches
        for item in matches:
            if item.get("class_name") in sc_cli.TERMINAL_CLASSES:
                return item
        fallback = sc_cli.list_window_records(query="Claude", limit=50)
        if fallback:
            last_seen = fallback
        for item in fallback:
            if item.get("class_name") not in sc_cli.TERMINAL_CLASSES:
                continue
            try:
                readback = sc_cli.read_window(int(item["hwnd"]))
            except Exception:
                continue
            if workspace_marker in str(readback.get("text", "")):
                return item
        time.sleep(0.5)
    raise RuntimeError(f"claude responder window not found: {title}; last={last_seen!r}")


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
    keep_windows: bool = False,
    reply_timeout: float = 240.0,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    nonce = f"SC_LOCAL_CLAUDE_{uuid.uuid4().hex[:8].upper()}"
    suffix = nonce[-8:]
    responder_title = f"SC_CLAUDE_RESPONDER_{suffix}"
    receiver_title = f"SC_CLAUDE_REPLY_{suffix}"
    temp_dir = Path(tempfile.gettempdir()) / "selfconnect_local_spawn_claude_probe"
    workspace = temp_dir / f"workspace_{suffix}"
    receiver_script = temp_dir / "receiver.py"
    ready_path = temp_dir / f"{nonce}.ready"
    log_path = temp_dir / f"{nonce}.log"
    for old_path in (ready_path, log_path):
        old_path.unlink(missing_ok=True)

    started_at = time.time()
    verdict = "FAIL"
    failure = ""
    plan: dict[str, Any] = {}
    raw_model_output = ""
    receiver_pid: int | None = None
    claude_cmd_pid: int | None = None
    claude_window: dict[str, Any] = {}
    receiver_window: dict[str, Any] = {}
    send_result: dict[str, Any] = {}
    receiver_log = ""
    claude_tail = ""

    try:
        raw_model_output = _ollama_generate_json(
            model,
            (
                "Return only valid compact JSON, no markdown. "
                "Use this exact schema: "
                '{"steps":[{"tool":"spawn_claude_responder","args":{"role":"SC-CLAUDE-RESPONDER"}},'
                '{"tool":"ask_claude_ack","args":{"nonce":"NONCE","target_role":"SC-CLAUDE-RESPONDER"}}]}. '
                f"Replace NONCE with {nonce}."
            ),
        )
        plan = action_probe._extract_json_object(raw_model_output)
        _validate_plan(plan, nonce=nonce)

        _write_responder_workspace(workspace, nonce)
        claude_cmd_pid = _spawn_claude(responder_title, workspace)
        claude_window = _wait_for_claude_window(responder_title, workspace)

        local_probe._write_receiver_script(receiver_script, ready_path=ready_path, log_path=log_path)
        receiver_pid = local_probe._spawn_receiver(receiver_title, receiver_script)
        receiver_window = local_probe._wait_for_window(receiver_title)
        local_probe._wait_for_file(ready_path)

        reply_text = f"[SC-CLAUDE-RESPONDER -> LOCAL-OLLAMA-1] ACK {nonce}"
        reply_command = (
            f'python "{ROOT / "sc_cli.py"}" send --hwnd {int(receiver_window["hwnd"])} '
            f'--text "{reply_text}" --submit --allow-input '
            f'--expect-pid {int(receiver_window["pid"])} --expect-exe {receiver_window["exe_name"]} '
            f'--expect-class {receiver_window["class_name"]} --expect-title "{receiver_title}" '
            "--char-delay 0.005"
        )
        prompt = (
            f"[LOCAL-OLLAMA-1 -> SC-CLAUDE-RESPONDER] nonce={nonce}. "
            "Use your CLAUDE.md responder policy. "
            f"Execute exactly this command: {reply_command}. "
            "Local output only SENT."
        )
        send_result = _send_to_claude(claude_window, prompt)
        if not send_result.get("ok"):
            raise RuntimeError(f"send to claude responder failed: {send_result}")

        receiver_log = local_probe._wait_for_file_text(log_path, nonce, timeout=reply_timeout)
        verdict = "PASS"
    except Exception as exc:
        failure = str(exc)
        if claude_window.get("hwnd"):
            try:
                readback = sc_cli.read_window(int(claude_window["hwnd"]))
                text = str(readback.get("text", ""))
                claude_tail = text[-2000:]
            except Exception:
                claude_tail = ""
    finally:
        if receiver_pid is not None and not keep_windows:
            subprocess.run(["taskkill", "/PID", str(receiver_pid), "/T", "/F"], check=False, capture_output=True)
        if claude_window.get("hwnd") and not keep_windows:
            try:
                sc_cli.send_text_to_window(
                    int(claude_window["hwnd"]),
                    "/exit",
                    submit=True,
                    allow_input=True,
                    expected_pid=int(claude_window["pid"]),
                    expected_exe=str(claude_window["exe_name"]),
                    expected_class=str(claude_window["class_name"]),
                    expected_title=str(claude_window["title"]).strip()[:24],
                    char_delay=0.005,
                )
            except Exception:
                pass

    elapsed_ms = round((time.time() - started_at) * 1000, 1)
    artifact = {
        "verdict": verdict,
        "failure": failure,
        "redacted": True,
        "model": model,
        "nonce": nonce,
        "elapsed_ms": elapsed_ms,
        "reply_timeout_s": reply_timeout,
        "plan_validated": bool(plan),
        "steps_requested": [step.get("tool") for step in plan.get("steps", [])] if isinstance(plan.get("steps"), list) else [],
        "send_to_claude_ok": bool(send_result.get("ok")),
        "claude_reply_observed": verdict == "PASS",
        "local_action_side": "Ollama localhost JSON plan -> spawn Claude responder -> SelfConnect send",
        "airgap_scope": "Local model planning/action side is local after model download; Claude Code side is cloud-connected and not airgapped.",
        "claude_responder": {
            "cmd_pid": int(claude_cmd_pid or 0),
            "hwnd": int(claude_window.get("hwnd", 0) or 0),
            "pid": int(claude_window.get("pid", 0) or 0),
            "class_name": claude_window.get("class_name", ""),
            "title_hash": _sha256(str(claude_window.get("title", ""))),
        },
        "receiver": {
            "hwnd": int(receiver_window.get("hwnd", 0) or 0),
            "process_pid": int(receiver_pid or 0),
            "pid": int(receiver_window.get("pid", 0) or 0),
            "class_name": receiver_window.get("class_name", ""),
            "title_hash": _sha256(str(receiver_window.get("title", ""))),
        },
        "model_output_hash": _sha256(raw_model_output),
        "plan_json_hash": _sha256(json.dumps(plan, sort_keys=True)),
        "receiver_log_hash": _sha256(receiver_log),
        "claude_tail_hash": _sha256(claude_tail),
        "control_path": "local model JSON plan -> spawn Claude Code responder workspace -> SelfConnect prompt -> Claude SelfConnect ACK -> receiver log verification",
    }

    out_dir = results_dir or Path("experiments/win32_probe/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"local_model_spawn_claude_{verdict.lower()}_{suffix}.json"
    out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    artifact["artifact_path"] = str(out_path)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local model spawn-Claude SelfConnect probe")
    parser.add_argument("--model", default=os.environ.get("SC_LOCAL_MODEL", "gemma3:latest"))
    parser.add_argument("--reply-timeout", type=float, default=240.0)
    parser.add_argument("--keep-windows", action="store_true")
    args = parser.parse_args(argv)
    artifact = run_probe(args.model, reply_timeout=args.reply_timeout, keep_windows=args.keep_windows)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
