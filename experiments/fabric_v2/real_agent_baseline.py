"""Run the 5-real-agent SelfConnect baseline using visible CLI agent windows.

This is intentionally not the logical harness. It launches real visible
PowerShell/Windows Terminal windows and runs real agent CLI commands inside
them. A run passes only when UIA readback from each visible window contains the
expected ACK line.
"""

from __future__ import annotations

# ruff: noqa: E402,I001

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sc_cli


DEFAULT_RESULTS = ROOT / "experiments" / "fabric_v2" / "results"
TERMINAL_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"


@dataclass
class AgentRun:
    role: str
    nonce: str
    expected: str
    script: Path
    log: Path
    hwnd: int | None = None
    pid: int | None = None
    title: str = ""
    launch_ms: float | None = None
    ack_ms: float | None = None
    status: str = "pending"
    error: str = ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _window_rows() -> list[dict[str, Any]]:
    return [
        row
        for row in sc_cli.list_window_records(query="", limit=300)
        if row.get("class_name") == TERMINAL_CLASS
    ]


def _write_agent_script(
    *,
    workdir: Path,
    run_id: str,
    role: str,
    nonce: str,
    expected: str,
    log: Path,
    keep_open: bool,
) -> Path:
    prompt = f"Reply with exactly one line and no markdown: {expected}"
    script = workdir / f"{role}.ps1"
    stay_open = (
        "Write-Host 'WINDOW_STAYS_OPEN_FOR_UIA_INSPECTION';"
        " while ($true) { Start-Sleep -Seconds 3600 }"
        if keep_open
        else ""
    )
    text = f"""
$ErrorActionPreference = 'Continue'
$Host.UI.RawUI.WindowTitle = { _ps_quote(run_id + ' ' + role) }
Set-Location -LiteralPath { _ps_quote(str(ROOT)) }
Write-Host 'SELFCONNECT_REAL_AGENT_START role={role} nonce={nonce}'
$prompt = { _ps_quote(prompt) }
& codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check $prompt 2>&1 |
    Tee-Object -FilePath { _ps_quote(str(log)) }
Write-Host 'SELFCONNECT_REAL_AGENT_DONE role={role}'
$Host.UI.RawUI.WindowTitle = { _ps_quote(run_id + ' ' + role + ' DONE') }
{stay_open}
"""
    script.write_text(text.strip() + "\n", encoding="utf-8")
    return script


def _spawn_visible(script: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            "powershell.exe",
            "-NoExit",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def _find_window(run_id: str, role: str) -> dict[str, Any] | None:
    title_fragment = f"{run_id} {role}"
    for row in _window_rows():
        if title_fragment in str(row.get("title", "")):
            return row
    # Some TUIs rewrite the title. Fall back to UIA content.
    for row in _window_rows():
        try:
            text = str(sc_cli.read_window(int(row["hwnd"])).get("text", ""))
        except Exception:
            continue
        if f"SELFCONNECT_REAL_AGENT_START role={role}" in text:
            return row
    return None


def run_baseline(
    *,
    count: int,
    timeout_s: float,
    results_dir: Path,
    keep_open: bool,
) -> dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("SC_REAL5_%Y%m%d_%H%M%S")
    workdir = Path(tempfile.gettempdir()) / f"selfconnect_real_agent_baseline_{run_id}"
    workdir.mkdir(parents=True, exist_ok=True)

    agents: list[AgentRun] = []
    for index in range(1, count + 1):
        role = f"realcodex-{index}"
        nonce = f"{run_id}_{index:02d}"
        expected = f"ACK_REAL5 role={role} nonce={nonce}"
        log = workdir / f"{role}.log"
        script = _write_agent_script(
            workdir=workdir,
            run_id=run_id,
            role=role,
            nonce=nonce,
            expected=expected,
            log=log,
            keep_open=keep_open,
        )
        agents.append(AgentRun(role=role, nonce=nonce, expected=expected, script=script, log=log))

    started = time.perf_counter()
    processes = [_spawn_visible(agent.script) for agent in agents]

    try:
        deadline = time.perf_counter() + timeout_s
        pending = {agent.role for agent in agents}
        while pending and time.perf_counter() < deadline:
            for agent in agents:
                if agent.role not in pending:
                    continue
                row = _find_window(run_id, agent.role)
                if row and agent.hwnd is None:
                    agent.hwnd = int(row["hwnd"])
                    agent.pid = int(row["pid"])
                    agent.title = str(row.get("title", ""))
                    agent.launch_ms = (time.perf_counter() - started) * 1000.0
                if agent.hwnd is None:
                    continue
                try:
                    readback = str(sc_cli.read_window(agent.hwnd).get("text", ""))
                except Exception as exc:
                    agent.error = f"readback failed: {exc}"
                    continue
                if agent.expected in readback:
                    agent.ack_ms = (time.perf_counter() - started) * 1000.0
                    agent.status = "pass"
                    pending.remove(agent.role)
            time.sleep(1.0)

        for agent in agents:
            if agent.status != "pass":
                agent.status = "fail"
                if not agent.error:
                    if agent.hwnd is None:
                        agent.error = "visible terminal window not found"
                    else:
                        agent.error = "expected ACK not observed via UIA before timeout"

        passed = all(agent.status == "pass" for agent in agents)
        ack_times = [agent.ack_ms for agent in agents if agent.ack_ms is not None]
        result = {
            "schema": "selfconnect.real_agent_baseline.v1",
            "run_id": run_id,
            "verdict": "PASS" if passed else "FAIL",
            "agent_count": count,
            "real_agent_cli": "codex exec",
            "visible_windows": True,
            "uia_readback_required": True,
            "logical_simulation": False,
            "baseline_file": "baseline_5agent_real.json" if passed and count == 5 else None,
            "started_at": started,
            "duration_ms": (time.perf_counter() - started) * 1000.0,
            "ack_latency_ms": {
                "max": max(ack_times) if ack_times else None,
                "min": min(ack_times) if ack_times else None,
            },
            "agents": [
                {
                    "role": agent.role,
                    "nonce_hash": _sha256(agent.nonce),
                    "expected_hash": _sha256(agent.expected),
                    "hwnd": agent.hwnd,
                    "pid": agent.pid,
                    "title": agent.title,
                    "launch_ms": agent.launch_ms,
                    "ack_ms": agent.ack_ms,
                    "status": agent.status,
                    "error": agent.error,
                    "log": str(agent.log),
                }
                for agent in agents
            ],
        }

        out_path = results_dir / f"real_agent_baseline_{run_id}.json"
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        if passed and count == 5:
            baseline = results_dir / "baseline_5agent_real.json"
            baseline.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["baseline_path"] = str(baseline)
        result["result_path"] = str(out_path)
        return result
    finally:
        # Keep visible windows open by default for inspection. If --close-windows
        # was requested, the child shells exit naturally when their script ends.
        if not keep_open:
            for proc in processes:
                if proc.poll() is None:
                    proc.terminate()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--close-windows", action="store_true")
    args = parser.parse_args(argv)

    result = run_baseline(
        count=args.agents,
        timeout_s=args.timeout,
        results_dir=args.results_dir,
        keep_open=not args.close_windows,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
