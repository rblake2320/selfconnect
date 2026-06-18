"""Probe: local Ollama model sends a packet through SelfConnect.

This is intentionally small and conservative:
- uses an already-installed small Ollama model by default;
- spawns a throwaway receiver terminal;
- sends one generated line through the normal SelfConnect Win32 path;
- writes a redacted PASS/FAIL artifact.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sc_cli = __import__("sc_cli")

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _ollama_generate(model: str, prompt: str, *, timeout: float = 60.0) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": 1024,
            "num_predict": 64,
            "temperature": 0.1,
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


def _write_receiver_script(path: Path, *, ready_path: Path, log_path: Path) -> None:
    path.write_text(
        "\n".join([
            "from pathlib import Path",
            "import sys",
            f"ready_path = Path({str(ready_path)!r})",
            f"log_path = Path({str(log_path)!r})",
            "ready_path.write_text('ready', encoding='utf-8')",
            "print('SC_LOCAL_MODEL_RECEIVER_READY', flush=True)",
            "for line in sys.stdin:",
            "    payload = line.rstrip('\\r\\n')",
            "    with log_path.open('a', encoding='utf-8') as fh:",
            "        fh.write(payload + '\\n')",
            "    print('SC_LOCAL_MODEL_RECV:' + payload, flush=True)",
        ]),
        encoding="utf-8",
    )


def _spawn_receiver(title: str, receiver_script: Path) -> int:
    command = f'title {title} && "{sys.executable}" -u "{receiver_script}"'
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
        raise RuntimeError(f"receiver spawn failed: {result.stderr.strip()}")
    return int(result.stdout.strip().splitlines()[-1])


def _wait_for_window(title: str, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        matches = sc_cli.list_window_records(query=title, limit=20)
        for item in matches:
            if title in item.get("title", "") and item.get("class_name") in sc_cli.TERMINAL_CLASSES:
                return item
        time.sleep(0.5)
    raise RuntimeError(f"receiver window not found: {title}")


def _wait_for_file(path: Path, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return
        time.sleep(0.5)
    raise RuntimeError(f"file not observed: {path}")


def _wait_for_file_text(path: Path, needle: str, timeout: float = 45.0) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        if path.exists():
            last = path.read_text(encoding="utf-8", errors="replace")
            if needle in last:
                return last
        time.sleep(0.5)
    raise RuntimeError(f"text not observed in receiver log: {needle}")


def _safe_one_line(text: str, limit: int = 180) -> str:
    line = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return line[:limit].strip() or "LOCAL_MODEL_EMPTY_RESPONSE"


def run_probe(model: str, *, keep_receiver: bool = False, results_dir: Path | None = None) -> dict[str, Any]:
    nonce = f"SC_LOCAL_MODEL_{uuid.uuid4().hex[:8].upper()}"
    prompt = (
        "You are a local model participating in a SelfConnect test. "
        f"Reply in one short line and include this nonce exactly once: {nonce}"
    )

    generated = _safe_one_line(_ollama_generate(model, prompt))
    packet = f"[LOCAL-OLLAMA-1 -> SC-RECEIVER] nonce={nonce} response={generated}"
    title = f"SC_LOCAL_RECEIVER_{nonce[-8:]}"
    temp_dir = Path(tempfile.gettempdir()) / "selfconnect_local_model_probe"
    temp_dir.mkdir(parents=True, exist_ok=True)
    receiver_script = temp_dir / "receiver.py"
    ready_path = temp_dir / f"{nonce}.ready"
    log_path = temp_dir / f"{nonce}.log"
    for old_path in (ready_path, log_path):
        old_path.unlink(missing_ok=True)
    _write_receiver_script(receiver_script, ready_path=ready_path, log_path=log_path)

    receiver_pid: int | None = None
    verdict = "FAIL"
    failure = ""
    window: dict[str, Any] = {}
    readback: dict[str, Any] = {}
    send_result: dict[str, Any] = {}
    receiver_log = ""
    started_at = time.time()
    try:
        receiver_pid = _spawn_receiver(title, receiver_script)
        window = _wait_for_window(title)
        _wait_for_file(ready_path)
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
        receiver_log = _wait_for_file_text(log_path, nonce)
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
        "receiver": {
            "hwnd": int(window.get("hwnd", 0) or 0),
            "process_pid": int(receiver_pid or 0),
            "pid": int(window.get("pid", 0) or 0),
            "class_name": window.get("class_name", ""),
            "title_hash": _sha256(str(window.get("title", ""))),
        },
        "ollama_response_hash": _sha256(generated),
        "packet_hash": _sha256(packet),
        "send_ok": bool(send_result.get("ok")),
        "readback_method": readback.get("method", "") or "receiver_log",
        "observed_nonce": verdict == "PASS",
        "receiver_log_hash": _sha256(receiver_log),
        "control_path": "Ollama local generate -> SelfConnect Win32 send_text_to_window -> throwaway terminal stdin -> receiver log verification",
    }

    out_dir = results_dir or Path("experiments/win32_probe/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"local_model_selfconnect_{verdict.lower()}_{nonce[-8:]}.json"
    out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    artifact["artifact_path"] = str(out_path)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local model -> SelfConnect transport probe")
    parser.add_argument("--model", default=os.environ.get("SC_LOCAL_MODEL", "hermes3:3b"))
    parser.add_argument("--keep-receiver", action="store_true")
    args = parser.parse_args(argv)
    artifact = run_probe(args.model, keep_receiver=args.keep_receiver)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
