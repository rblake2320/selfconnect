"""Visible local-model repair demo with a safe Codex status packet.

This proves a stronger loop than generation:
- a local Ollama model inspects a failing sandbox task;
- it chooses a constrained JSON tool plan;
- the harness applies only a whitelisted sandbox file edit;
- tests are rerun locally;
- after PASS, a durable outbox record is written and a short visual status
  packet is sent to codex-1 through SelfConnect.

No repo source files are edited by the local model. The repair happens in a
temporary sandbox so the visible proof cannot damage the working tree.
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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _default_outbox_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SelfConnect" / "local_model_outbox.jsonl"


def _find_role(role: str, mesh: str = "default") -> dict[str, Any]:
    registry = sc_mesh_registry.load_registry()
    for agent in registry.get("agents", []):
        if agent.get("mesh") == mesh and agent.get("role") == role:
            return agent
    raise RuntimeError(f"mesh role not found: {role}")


def _write_sandbox(sandbox: Path) -> None:
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "buggy_math.py").write_text(
        "\n".join([
            "def add(a: int, b: int) -> int:",
            "    return a - b",
            "",
        ]),
        encoding="utf-8",
    )
    (sandbox / "test_buggy_math.py").write_text(
        "\n".join([
            "import unittest",
            "",
            "from buggy_math import add",
            "",
            "",
            "class BuggyMathTests(unittest.TestCase):",
            "    def test_adds_two_numbers(self):",
            "        self.assertEqual(add(2, 3), 5)",
            "",
            "",
            "if __name__ == '__main__':",
            "    unittest.main()",
            "",
        ]),
        encoding="utf-8",
    )


def _write_actor_script(
    path: Path,
    *,
    model: str,
    nonce: str,
    sandbox: Path,
    artifact_path: Path,
    outbox_path: Path,
    codex: dict[str, Any],
) -> None:
    path.write_text(
        "\n".join([
            "from __future__ import annotations",
            "import json",
            "import subprocess",
            "import sys",
            "import time",
            "import urllib.error",
            "import urllib.request",
            "from pathlib import Path",
            f"ROOT = Path({str(ROOT)!r})",
            "if str(ROOT) not in sys.path:",
            "    sys.path.insert(0, str(ROOT))",
            "import sc_cli",
            f"MODEL = {model!r}",
            f"NONCE = {nonce!r}",
            f"SANDBOX = Path({str(sandbox)!r})",
            f"ARTIFACT_PATH = Path({str(artifact_path)!r})",
            f"OUTBOX_PATH = Path({str(outbox_path)!r})",
            f"CODEX = json.loads({json.dumps(codex, sort_keys=True)!r})",
            "",
            "def emit(label, value=''):",
            "    if value == '':",
            "        print(label, flush=True)",
            "    else:",
            "        print(f'{label}: {value}', flush=True)",
            "",
            "def ollama(prompt):",
            "    payload = {",
            "        'model': MODEL,",
            "        'prompt': prompt,",
            "        'stream': False,",
            "        'options': {'num_ctx': 2048, 'num_predict': 256, 'temperature': 0.0},",
            "    }",
            "    req = urllib.request.Request(",
            "        'http://127.0.0.1:11434/api/generate',",
            "        data=json.dumps(payload).encode('utf-8'),",
            "        headers={'Content-Type': 'application/json'},",
            "        method='POST',",
            "    )",
            "    with urllib.request.urlopen(req, timeout=90) as response:",
            "        body = json.loads(response.read().decode('utf-8'))",
            "    return str(body.get('response', '')).strip()",
            "",
            "def extract_json(text):",
            "    stripped = text.strip()",
            "    if stripped.startswith('```'):",
            "        stripped = stripped.strip('`').removeprefix('json').strip()",
            "    start = stripped.find('{')",
            "    end = stripped.rfind('}')",
            "    if start < 0 or end < start:",
            "        raise ValueError('model did not return JSON')",
            "    return json.loads(stripped[start:end + 1])",
            "",
            "def run_tests():",
            "    return subprocess.run(",
            "        [sys.executable, '-m', 'unittest', '-q'],",
            "        cwd=SANDBOX,",
            "        capture_output=True,",
            "        text=True,",
            "        timeout=30,",
            "    )",
            "",
            "def validate_plan(plan):",
            "    steps = plan.get('steps')",
            "    if not isinstance(steps, list) or len(steps) != 2:",
            "        raise ValueError('plan must contain exactly two steps')",
            "    repair, notify = steps",
            "    if repair.get('tool') != 'replace_text':",
            "        raise ValueError('first tool must be replace_text')",
            "    args = repair.get('args')",
            "    if not isinstance(args, dict):",
            "        raise ValueError('replace args must be object')",
            "    if args.get('file') != 'buggy_math.py':",
            "        raise ValueError('replace target must be buggy_math.py')",
            "    if args.get('old') != 'return a - b':",
            "        raise ValueError('old text mismatch')",
            "    if args.get('new') != 'return a + b':",
            "        raise ValueError('new text mismatch')",
            "    if notify.get('tool') != 'notify_codex':",
            "        raise ValueError('second tool must be notify_codex')",
            "    n_args = notify.get('args')",
            "    if not isinstance(n_args, dict) or NONCE not in str(n_args.get('message', '')):",
            "        raise ValueError('notify message must include nonce')",
            "    return args, str(n_args.get('message', ''))",
            "",
            "emit('=' * 78)",
            "emit('[LOCAL-OLLAMA-1] VISIBLE REPAIR + CODEX STATUS DEMO')",
            "emit('model', MODEL)",
            "emit('nonce', NONCE)",
            "emit('sandbox', str(SANDBOX))",
            "emit('codex_target', f\"hwnd={CODEX['hwnd']} title={CODEX['title']}\")",
            "emit('=' * 78)",
            "artifact = {'verdict': 'FAIL', 'nonce': NONCE, 'model': MODEL, 'redacted': True}",
            "try:",
            "    code_path = SANDBOX / 'buggy_math.py'",
            "    emit('[1/7] broken code')",
            "    emit(code_path.read_text(encoding='utf-8').strip())",
            "    emit('[2/7] running failing test')",
            "    first = run_tests()",
            "    emit('initial_returncode', str(first.returncode))",
            "    emit('initial_output', (first.stdout + first.stderr).replace('\\n', ' | ')[:700])",
            "    prompt = '\\n'.join([",
            "        'You are LOCAL-OLLAMA-1. Fix a tiny Python sandbox task.',",
            "        'Return only valid compact JSON with exactly this schema:',",
            "        '{\"steps\":[{\"tool\":\"replace_text\",\"args\":{\"file\":\"buggy_math.py\",\"old\":\"return a - b\",\"new\":\"return a + b\"}},{\"tool\":\"notify_codex\",\"args\":{\"message\":\"<one line including NONCE>\"}}]}',",
            "        f'NONCE={NONCE}',",
            "        'Broken file:',",
            "        code_path.read_text(encoding='utf-8'),",
            "        'Failing test output:',",
            "        (first.stdout + first.stderr)[:900],",
            "    ])",
            "    emit('[3/7] asking local model for repair plan')",
            "    raw = ollama(prompt)",
            "    emit('raw_model_output', raw)",
            "    emit('[4/7] validating repair plan')",
            "    plan = extract_json(raw)",
            "    repair_args, notify_message = validate_plan(plan)",
            "    emit('validated_plan', json.dumps(plan, sort_keys=True))",
            "    emit('[5/7] applying sandbox-only replace_text action')",
            "    text = code_path.read_text(encoding='utf-8')",
            "    if text.count(repair_args['old']) != 1:",
            "        raise RuntimeError('old text not found exactly once')",
            "    code_path.write_text(text.replace(repair_args['old'], repair_args['new']), encoding='utf-8')",
            "    emit('fixed_code', code_path.read_text(encoding='utf-8').strip())",
            "    emit('[6/7] rerunning test')",
            "    second = run_tests()",
            "    emit('final_returncode', str(second.returncode))",
            "    emit('final_output', (second.stdout + second.stderr).replace('\\n', ' | ')[:700])",
            "    if second.returncode != 0:",
            "        raise RuntimeError('test still failing')",
            "    emit('[7/7] writing durable local-model outbox and visual Codex status')",
            "    status = f'[LOCAL-OLLAMA-1 -> CODEX-1] sandbox repair PASS nonce={NONCE} message={notify_message}'",
            "    OUTBOX_PATH.parent.mkdir(parents=True, exist_ok=True)",
            "    outbox_record = {",
            "        'from': 'LOCAL-OLLAMA-1',",
            "        'to': 'codex-1',",
            "        'nonce': NONCE,",
            "        'type': 'sandbox_repair_status',",
            "        'message': notify_message,",
            "        'initial_failed': True,",
            "        'final_passed': True,",
            "        'timestamp': time.time(),",
            "    }",
            "    with OUTBOX_PATH.open('a', encoding='utf-8') as fh:",
            "        fh.write(json.dumps(outbox_record, sort_keys=True) + '\\n')",
            "    emit('outbox_record', str(OUTBOX_PATH))",
            "    send_result = sc_cli.send_text_to_window(",
            "        int(CODEX['hwnd']),",
            "        status,",
            "        submit=True,",
            "        allow_input=True,",
            "        expected_pid=int(CODEX['pid']),",
            "        expected_exe=str(CODEX['exe_name']),",
            "        expected_class=str(CODEX['class_name']),",
            "        expected_title=str(CODEX['title']).strip()[:24],",
            "        char_delay=0.005,",
            "    )",
            "    emit('send_to_codex', json.dumps(send_result, sort_keys=True))",
            "    if not send_result.get('ok'):",
            "        raise RuntimeError(f'send to codex failed: {send_result}')",
            "    artifact.update({",
            "        'verdict': 'PASS',",
            "        'initial_failed': first.returncode != 0,",
            "        'final_passed': True,",
            "        'outbox_written': True,",
            "        'outbox_path': str(OUTBOX_PATH),",
            "        'send_to_codex_window_ok': True,",
            "        'codex_status_scope': 'visual delivery to Codex input/queue only; durable handoff is the outbox record',",
            "    })",
            "    emit('PASS', 'local model fixed sandbox bug, test passed, outbox written, visual Codex status sent')",
            "except Exception as exc:",
            "    artifact['failure'] = str(exc)",
            "    emit('FAIL', str(exc))",
            "finally:",
            "    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding='utf-8')",
            "    emit('artifact', str(ARTIFACT_PATH))",
            "    emit('WINDOW STAYS OPEN - inspect local model repair steps')",
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


def _wait_for_actor(title: str, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        matches = sc_cli.list_window_records(query=title, limit=20)
        for item in matches:
            if title in item.get("title", "") and item.get("class_name") in sc_cli.TERMINAL_CLASSES:
                return item
        time.sleep(0.5)
    raise RuntimeError(f"actor window not found: {title}")


def run_demo(
    model: str,
    *,
    codex_role: str = "codex-1",
    close_after: bool = False,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    codex = _find_role(codex_role)
    suffix = uuid.uuid4().hex[:8].upper()
    nonce = f"SC_LOCAL_REPAIR_{suffix}"
    actor_title = f"LOCAL-OLLAMA-1_REPAIR_{suffix}"
    temp_dir = Path(tempfile.gettempdir()) / "selfconnect_visible_local_repair_demo" / suffix
    sandbox = temp_dir / "sandbox"
    _write_sandbox(sandbox)

    out_dir = results_dir or Path("experiments/win32_probe/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = out_dir / f"local_model_visible_repair_{suffix}.json"
    outbox_path = _default_outbox_path()
    actor_script = temp_dir / "actor.py"
    _write_actor_script(
        actor_script,
        model=model,
        nonce=nonce,
        sandbox=sandbox,
        artifact_path=artifact_path,
        outbox_path=outbox_path,
        codex={
            "hwnd": int(codex["hwnd"]),
            "pid": int(codex["pid"]),
            "exe_name": str(codex["exe_name"]),
            "class_name": str(codex["class_name"]),
            "title": str(codex["title"]),
            "birth_id": str(codex.get("birth_id", "")),
        },
    )

    started_at = time.time()
    actor_pid = _spawn_cmd_window(actor_title, f'"{sys.executable}" -u "{actor_script}"')
    actor_window = _wait_for_actor(actor_title)

    verdict = "FAIL"
    failure = ""
    deadline = time.time() + 120.0
    artifact: dict[str, Any] = {}
    while time.time() < deadline:
        if artifact_path.exists():
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            verdict = str(artifact.get("verdict", "FAIL"))
            failure = str(artifact.get("failure", ""))
            if verdict == "PASS" or failure:
                break
        time.sleep(0.5)
    else:
        failure = "visible repair demo timed out"

    if close_after:
        subprocess.run(["taskkill", "/PID", str(actor_pid), "/T", "/F"], check=False, capture_output=True)

    summary = {
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
        "codex_target": {
            "role": codex_role,
            "birth_id": codex.get("birth_id", ""),
            "hwnd": int(codex.get("hwnd", 0) or 0),
            "title_hash": _sha256(str(codex.get("title", ""))),
        },
        "sandbox_hash": _sha256((sandbox / "buggy_math.py").read_text(encoding="utf-8")),
        "outbox_path": str(outbox_path),
        "artifact_path": str(artifact_path),
        "windows_left_open": not close_after,
        "control_path": "visible local model repair plan -> sandbox edit -> local unittest -> durable outbox -> guarded visual SelfConnect status to codex-1",
    }
    if artifact_path.exists():
        artifact_path.write_text(json.dumps({**artifact, **summary}, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run visible local-model repair and Codex status demo")
    parser.add_argument("--model", default=os.environ.get("SC_LOCAL_MODEL", "gemma3:latest"))
    parser.add_argument("--codex-role", default="codex-1")
    parser.add_argument("--close-after", action="store_true")
    args = parser.parse_args(argv)
    artifact = run_demo(args.model, codex_role=args.codex_role, close_after=args.close_after)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
