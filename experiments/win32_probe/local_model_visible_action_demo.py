"""Visible local-model SelfConnect action demo.

This opens two throwaway terminal tabs:
- LOCAL-OLLAMA-1 visible actor: calls the local Ollama model and prints each step.
- SC_VISIBLE_RECEIVER: receives the SelfConnect message and prints it.

The windows are intentionally left open by default so a human can inspect the
local model output and the receiver readback directly.
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


def _write_actor_script(
    path: Path,
    *,
    model: str,
    nonce: str,
    receiver_title: str,
    ready_path: Path,
    artifact_path: Path,
) -> None:
    path.write_text(
        "\n".join([
            "from __future__ import annotations",
            "import json",
            "import sys",
            "import time",
            "from pathlib import Path",
            f"ROOT = Path({str(ROOT)!r})",
            f"PROBE_DIR = Path({str(PROBE_DIR)!r})",
            "for candidate in (ROOT, PROBE_DIR):",
            "    if str(candidate) not in sys.path:",
            "        sys.path.insert(0, str(candidate))",
            "import sc_cli",
            "import local_model_action_agent_probe as action_probe",
            "import local_model_selfconnect_probe as local_probe",
            f"MODEL = {model!r}",
            f"NONCE = {nonce!r}",
            f"RECEIVER_TITLE = {receiver_title!r}",
            f"READY_PATH = Path({str(ready_path)!r})",
            f"ARTIFACT_PATH = Path({str(artifact_path)!r})",
            "",
            "def emit(label, value=''):",
            "    if value == '':",
            "        print(label, flush=True)",
            "    else:",
            "        print(f'{label}: {value}', flush=True)",
            "",
            "emit('=' * 78)",
            "emit('[LOCAL-OLLAMA-1] VISIBLE LOCAL MODEL ACTION DEMO')",
            "emit('model', MODEL)",
            "emit('nonce', NONCE)",
            "emit('receiver_title', RECEIVER_TITLE)",
            "emit('this side uses Ollama localhost plus SelfConnect Win32 send')",
            "emit('=' * 78)",
            "artifact = {'verdict': 'FAIL', 'nonce': NONCE, 'model': MODEL, 'redacted': True}",
            "try:",
            "    emit('[1/5] waiting for receiver ready file')",
            "    local_probe._wait_for_file(READY_PATH, timeout=45.0)",
            "    emit('[2/5] asking local Ollama model for JSON action')",
            "    action, raw = action_probe._local_model_action(MODEL, NONCE)",
            "    emit('raw_model_output', raw)",
            "    emit('[3/5] validating model-selected action')",
            "    message = action_probe._validate_action(action, nonce=NONCE)",
            "    emit('validated_tool', action.get('tool', ''))",
            "    emit('validated_message', message)",
            "    emit('[4/5] locating receiver window')",
            "    window = local_probe._wait_for_window(RECEIVER_TITLE, timeout=45.0)",
            "    emit('receiver_hwnd', window['hwnd'])",
            "    emit('receiver_pid', window['pid'])",
            "    emit('receiver_class', window['class_name'])",
            "    packet = f'[LOCAL-OLLAMA-1 -> SC-VISIBLE-RECEIVER] action=selfconnect_send {message}'",
            "    emit('[5/5] sending through guarded SelfConnect Win32 transport')",
            "    result = sc_cli.send_text_to_window(",
            "        int(window['hwnd']),",
            "        packet,",
            "        submit=True,",
            "        allow_input=True,",
            "        expected_pid=int(window['pid']),",
            "        expected_exe=str(window['exe_name']),",
            "        expected_class=str(window['class_name']),",
            "        expected_title=RECEIVER_TITLE,",
            "        char_delay=0.005,",
            "    )",
            "    emit('send_result', json.dumps(result, sort_keys=True))",
            "    if not result.get('ok'):",
            "        raise RuntimeError(f'send failed: {result}')",
            "    artifact.update({",
            "        'verdict': 'PASS',",
            "        'tool_requested': action.get('tool', ''),",
            "        'send_ok': True,",
            "        'receiver_hwnd': int(window['hwnd']),",
            "    })",
            "    emit('PASS', 'local model chose action and SelfConnect delivered it')",
            "except Exception as exc:",
            "    artifact['failure'] = str(exc)",
            "    emit('FAIL', str(exc))",
            "finally:",
            "    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding='utf-8')",
            "    emit('artifact', str(ARTIFACT_PATH))",
            "    emit('WINDOW STAYS OPEN - inspect this output and the receiver tab')",
        ]),
        encoding="utf-8",
    )


def _spawn_cmd_window(title: str, command: str) -> int:
    ps_command = (
        "$p = Start-Process cmd.exe "
        f"-ArgumentList @('/k', 'title {title} && {command}') "
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
        raise RuntimeError(f"spawn failed: {result.stderr.strip()}")
    return int(result.stdout.strip().splitlines()[-1])


def run_demo(model: str, *, close_after: bool = False, results_dir: Path | None = None) -> dict[str, Any]:
    nonce = f"SC_VISIBLE_LOCAL_{uuid.uuid4().hex[:8].upper()}"
    suffix = nonce[-8:]
    actor_title = f"LOCAL-OLLAMA-1_VISIBLE_{suffix}"
    receiver_title = f"SC_VISIBLE_RECEIVER_{suffix}"
    temp_dir = Path(tempfile.gettempdir()) / "selfconnect_visible_local_model_demo"
    temp_dir.mkdir(parents=True, exist_ok=True)
    receiver_script = temp_dir / f"receiver_{suffix}.py"
    actor_script = temp_dir / f"actor_{suffix}.py"
    ready_path = temp_dir / f"{nonce}.ready"
    log_path = temp_dir / f"{nonce}.log"
    for old_path in (ready_path, log_path):
        old_path.unlink(missing_ok=True)

    out_dir = results_dir or Path("experiments/win32_probe/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = out_dir / f"local_model_visible_action_{suffix}.json"

    local_probe._write_receiver_script(receiver_script, ready_path=ready_path, log_path=log_path)
    _write_actor_script(
        actor_script,
        model=model,
        nonce=nonce,
        receiver_title=receiver_title,
        ready_path=ready_path,
        artifact_path=artifact_path,
    )

    started_at = time.time()
    receiver_pid = _spawn_cmd_window(receiver_title, f'"{sys.executable}" -u "{receiver_script}"')
    actor_pid = _spawn_cmd_window(actor_title, f'"{sys.executable}" -u "{actor_script}"')
    receiver_window = local_probe._wait_for_window(receiver_title)
    actor_window = local_probe._wait_for_window(actor_title)

    verdict = "FAIL"
    failure = ""
    receiver_log = ""
    try:
        receiver_log = local_probe._wait_for_file_text(log_path, nonce, timeout=90.0)
        verdict = "PASS"
    except Exception as exc:
        failure = str(exc)
    finally:
        if close_after:
            for pid in (actor_pid, receiver_pid):
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)

    artifact = {
        "verdict": verdict,
        "failure": failure,
        "redacted": True,
        "model": model,
        "nonce": nonce,
        "elapsed_ms": round((time.time() - started_at) * 1000, 1),
        "actor": {
            "title": actor_title,
            "cmd_pid": actor_pid,
            "hwnd": int(actor_window.get("hwnd", 0) or 0),
        },
        "receiver": {
            "title": receiver_title,
            "cmd_pid": receiver_pid,
            "hwnd": int(receiver_window.get("hwnd", 0) or 0),
        },
        "receiver_log_hash": _sha256(receiver_log),
        "windows_left_open": not close_after,
        "control_path": "visible actor terminal -> Ollama localhost JSON action -> guarded SelfConnect send -> visible receiver terminal",
    }
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    artifact["artifact_path"] = str(artifact_path)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run visible local-model SelfConnect action demo")
    parser.add_argument("--model", default=os.environ.get("SC_LOCAL_MODEL", "gemma3:latest"))
    parser.add_argument("--close-after", action="store_true", help="close throwaway windows after the run")
    args = parser.parse_args(argv)
    artifact = run_demo(args.model, close_after=args.close_after)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
