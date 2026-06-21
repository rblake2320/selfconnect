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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sc_cli


DEFAULT_RESULTS = ROOT / "experiments" / "fabric_v2" / "results"
TERMINAL_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"
RUN_TITLE_PREFIX = "SC_REAL5_"


@dataclass
class AgentRun:
    provider: str
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
    diagnosis: str = ""


@dataclass(frozen=True)
class ProviderCommand:
    name: str
    display: str
    command: str
    fail_fast_env: dict[str, str] | None = None


PROVIDERS = {
    "codex": ProviderCommand(
        name="codex",
        display="codex exec",
        command=(
            "& codex exec --dangerously-bypass-approvals-and-sandbox "
            "--skip-git-repo-check $prompt"
        ),
    ),
    "claude": ProviderCommand(
        name="claude",
        display="claude -p",
        command="& claude -p --permission-mode bypassPermissions --output-format text $prompt",
    ),
    "gemini": ProviderCommand(
        name="gemini",
        display="gemini -p",
        command="& gemini -p $prompt --approval-mode yolo --skip-trust --output-format text",
        fail_fast_env={"CI": "true", "NO_COLOR": "1"},
    ),
}

PROVIDER_AUTH_ENV = {
    "gemini": (
        "GEMINI_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "CLOUDSDK_CONFIG",
    )
}

GEMINI_AUTH_CHOICES = ("current", "gemini-api-key", "oauth-personal", "vertex-ai")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _latency_stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "p50": None,
            "p95": None,
            "p99": None,
        }
    ordered = sorted(values)

    def percentile(pct: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * pct
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return {
        "count": len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
    }


def _provider_counts(provider_plan: list[str]) -> dict[str, int]:
    return {provider: provider_plan.count(provider) for provider in sorted(set(provider_plan))}


def _provider_key(provider_plan: list[str]) -> str:
    return "_".join(
        f"{provider}{count}" for provider, count in _provider_counts(provider_plan).items()
    )


def _gemini_settings_path() -> Path:
    return Path.home() / ".gemini" / "settings.json"


def _set_gemini_auth_type(auth_type: str) -> None:
    if auth_type not in GEMINI_AUTH_CHOICES or auth_type == "current":
        raise ValueError(f"cannot set Gemini auth type: {auth_type}")

    path = _gemini_settings_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(parsed, dict):
                data = parsed
        except json.JSONDecodeError:
            data = {}

    security = data.get("security")
    if not isinstance(security, dict):
        security = {}
    auth = security.get("auth")
    if not isinstance(auth, dict):
        auth = {}
    auth["selectedType"] = auth_type
    security["auth"] = auth
    data["security"] = security

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@contextmanager
def _temporary_gemini_auth_type(auth_type: str) -> Iterator[None]:
    """Temporarily select a Gemini CLI auth mode and restore the file exactly.

    The Gemini CLI reads its auth mode from ~/.gemini/settings.json. API-key
    tests can provide GEMINI_API_KEY through the process environment, but if the
    CLI is still configured for oauth-personal it asks for interactive auth and
    never reaches the key path. This helper changes only the auth-mode selector,
    never writes a key, and restores the original bytes in a finally block.
    """

    if auth_type == "current":
        yield
        return
    if auth_type not in GEMINI_AUTH_CHOICES:
        raise ValueError(f"unknown Gemini auth type: {auth_type}")

    path = _gemini_settings_path()
    original = path.read_bytes() if path.exists() else None
    try:
        _set_gemini_auth_type(auth_type)
        yield
    finally:
        if original is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            path.write_bytes(original)


def _baseline_file_name(provider_plan: list[str], count: int) -> str | None:
    if count != 5:
        return None
    if set(provider_plan) == {"codex"}:
        return "baseline_5agent_real.json"
    return f"baseline_5agent_real_{_provider_key(provider_plan)}.json"


def _has_exact_line(text: str, expected: str) -> bool:
    return any(line.strip() == expected for line in text.splitlines())


def _state_path(results_dir: Path, run_id: str) -> Path:
    return results_dir / f"real_agent_state_{run_id}.json"


def _stale_run_windows() -> list[dict[str, Any]]:
    return [
        row
        for row in sc_cli.list_window_records(query=RUN_TITLE_PREFIX, limit=300)
        if RUN_TITLE_PREFIX in str(row.get("title", ""))
    ]


def _cleanup_run_command(run_id: str) -> str:
    escaped = run_id.replace("'", "''")
    return (
        "$run = '" + escaped + "'\n"
        "$pattern = \"selfconnect_real_agent_baseline_$run\"\n"
        "$procs = @(Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -eq 'powershell.exe' -and $_.CommandLine -like \"*$pattern*\" })\n"
        "$ids = @($procs | ForEach-Object { $_.ProcessId })\n"
        "$ids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }\n"
        "Start-Sleep -Seconds 2\n"
        "$remaining = @(Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -eq 'powershell.exe' -and $_.CommandLine -like \"*$pattern*\" })\n"
        "[pscustomobject]@{ stopped = $ids.Count; remaining = $remaining.Count } | ConvertTo-Json -Compress\n"
    )


def cleanup_run(run_id: str, *, restore_gemini_auth: str = "current") -> dict[str, Any]:
    if not run_id.startswith(RUN_TITLE_PREFIX):
        raise ValueError(f"cleanup run id must start with {RUN_TITLE_PREFIX}")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            _cleanup_run_command(run_id),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    payload: dict[str, Any] = {
        "run_id": run_id,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
    try:
        parsed = json.loads(completed.stdout)
        if isinstance(parsed, dict):
            payload.update(parsed)
    except json.JSONDecodeError:
        pass
    if restore_gemini_auth != "current":
        _set_gemini_auth_type(restore_gemini_auth)
        payload["gemini_auth_restored_to"] = restore_gemini_auth
    return payload


def _write_run_state(
    *,
    results_dir: Path,
    run_id: str,
    phase: str,
    started: float,
    agents: list[AgentRun],
    processes: list[subprocess.Popen[bytes]],
    pending: set[str],
    provider_plan: list[str],
    gemini_auth_type: str,
) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema": "selfconnect.real_agent_state.v1",
        "run_id": run_id,
        "phase": phase,
        "agent_count": len(agents),
        "provider_counts": _provider_counts(provider_plan),
        "gemini_auth_type": gemini_auth_type,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "process_pids": [proc.pid for proc in processes],
        "pending_roles": sorted(pending),
        "pass_count": sum(1 for agent in agents if agent.status == "pass"),
        "fail_count": sum(1 for agent in agents if agent.status == "fail"),
        "agents": [
            {
                "provider": agent.provider,
                "role": agent.role,
                "expected_hash": _sha256(agent.expected),
                "hwnd": agent.hwnd,
                "pid": agent.pid,
                "title": agent.title,
                "launch_ms": agent.launch_ms,
                "ack_ms": agent.ack_ms,
                "status": agent.status,
                "error": agent.error,
                "diagnosis": agent.diagnosis,
            }
            for agent in agents
        ],
    }
    path = _state_path(results_dir, run_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _provider_env_lines(provider: str) -> str:
    lines: list[str] = []
    provider_command = PROVIDERS[provider]
    if provider_command.fail_fast_env:
        lines.extend(
            f"$env:{key} = { _ps_quote(value) }"
            for key, value in provider_command.fail_fast_env.items()
        )
    for key in PROVIDER_AUTH_ENV.get(provider, ()):
        quoted = _ps_quote(key)
        lines.extend(
            [
                f"if (-not [Environment]::GetEnvironmentVariable({quoted}, 'Process')) {{",
                f"    $value = [Environment]::GetEnvironmentVariable({quoted}, 'User')",
                "    if (-not $value) {",
                f"        $value = [Environment]::GetEnvironmentVariable({quoted}, 'Machine')",
                "    }",
                "    if ($value) {",
                f"        [Environment]::SetEnvironmentVariable({quoted}, $value, 'Process')",
                "    }",
                "}",
            ]
        )
    return "\n".join(lines)


def _provider_plan(spec: str | None, count: int) -> list[str]:
    if count < 1:
        raise ValueError("--agents must be at least 1")
    if not spec:
        return ["codex"] * count

    parts = [part.strip().lower() for part in spec.split(",") if part.strip()]
    if not parts:
        raise ValueError("--providers cannot be empty")

    expanded: list[str] = []
    round_robin: list[str] = []
    explicit_counts = False
    for part in parts:
        if ":" in part:
            explicit_counts = True
            name, raw_count = part.split(":", 1)
            name = name.strip().lower()
            if name not in PROVIDERS:
                raise ValueError(f"unknown provider: {name}")
            try:
                provider_count = int(raw_count)
            except ValueError as exc:
                raise ValueError(f"invalid provider count for {name}: {raw_count}") from exc
            if provider_count < 0:
                raise ValueError(f"provider count must be non-negative for {name}")
            expanded.extend([name] * provider_count)
        else:
            if part not in PROVIDERS:
                raise ValueError(f"unknown provider: {part}")
            round_robin.append(part)

    if explicit_counts and round_robin:
        raise ValueError("do not mix counted and round-robin provider specs")
    if explicit_counts:
        if len(expanded) != count:
            raise ValueError(
                f"provider counts sum to {len(expanded)} but --agents is {count}"
            )
        return expanded

    return [round_robin[index % len(round_robin)] for index in range(count)]


def _window_rows() -> list[dict[str, Any]]:
    return [
        row
        for row in sc_cli.list_window_records(query="", limit=300)
        if row.get("class_name") == TERMINAL_CLASS
    ]


def _title_matches(title: str, run_id: str, role: str) -> bool:
    target = f"{run_id} {role}"
    return title == target or title.startswith(target + " ")


def _write_agent_script(
    *,
    workdir: Path,
    run_id: str,
    provider: str,
    role: str,
    nonce: str,
    expected: str,
    log: Path,
    keep_open: bool,
) -> Path:
    provider_command = PROVIDERS[provider]
    prompt = (
        "Reply with exactly this one line and nothing else. "
        "Do not change the provider, role, nonce, spacing, or field names. "
        f"Line: {expected}"
    )
    script = workdir / f"{role}.ps1"
    env_lines = _provider_env_lines(provider)
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
{env_lines}
Write-Host 'SELFCONNECT_REAL_AGENT_START provider={provider} role={role} nonce={nonce}'
$prompt = { _ps_quote(prompt) }
{provider_command.command} 2>&1 |
    Tee-Object -FilePath { _ps_quote(str(log)) }
Write-Host 'SELFCONNECT_REAL_AGENT_DONE provider={provider} role={role}'
$Host.UI.RawUI.WindowTitle = { _ps_quote(run_id + ' ' + role + ' DONE') }
{stay_open}
"""
    script.write_text(text.strip() + "\n", encoding="utf-8")
    return script


def _write_provider_preflight_script(
    *,
    workdir: Path,
    provider: str,
    expected: str,
    log: Path,
) -> Path:
    provider_command = PROVIDERS[provider]
    prompt = (
        "Reply with exactly this one line and nothing else. "
        "Do not change the provider, nonce, spacing, or field names. "
        f"Line: {expected}"
    )
    env_lines = _provider_env_lines(provider)
    script = workdir / f"preflight_{provider}.ps1"
    text = f"""
$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath { _ps_quote(str(ROOT)) }
{env_lines}
$prompt = { _ps_quote(prompt) }
{provider_command.command} 2>&1 |
    Tee-Object -FilePath { _ps_quote(str(log)) }
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
    for row in _window_rows():
        if _title_matches(str(row.get("title", "")), run_id, role):
            return row
    # Some TUIs rewrite the title. Fall back to UIA content.
    for row in _window_rows():
        try:
            text = str(sc_cli.read_window(int(row["hwnd"])).get("text", ""))
        except Exception:
            continue
        if "SELFCONNECT_REAL_AGENT_START" in text and f"role={role}" in text:
            return row
    return None


def _diagnose_failed_agent(agent: AgentRun) -> None:
    try:
        log_text = agent.log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if "Manual authorization is required" in log_text or "FatalAuthenticationError" in log_text:
        agent.diagnosis = "provider_auth_required"
        agent.error = "provider authentication required; exact ACK could not be produced"
        return
    if agent.nonce in log_text and not _has_exact_line(log_text, agent.expected):
        agent.diagnosis = "wrong_ack_format"
        agent.error = "nonce observed in provider output, but exact expected ACK was not produced"
        return
    if agent.nonce not in log_text:
        agent.diagnosis = "no_nonce_observed"


def _classify_provider_output(
    *,
    output: str,
    expected: str,
    nonce: str,
    returncode: int | None,
    timed_out: bool,
) -> tuple[str, str]:
    if timed_out:
        return "timeout", "provider command timed out before exact ACK"
    if _has_exact_line(output, expected):
        return "ready", ""
    if "Manual authorization is required" in output or "FatalAuthenticationError" in output:
        return "provider_auth_required", "provider authentication required before non-interactive ACK"
    if nonce in output:
        return "wrong_ack_format", "nonce observed, but exact expected ACK was not produced"
    if returncode not in (0, None):
        return "provider_error", f"provider command exited {returncode} without exact ACK"
    return "no_nonce_observed", "provider output did not include expected nonce"


def run_provider_preflight(
    *,
    providers: str | None,
    count: int,
    timeout_s: float,
    results_dir: Path,
    gemini_auth_type: str = "current",
) -> dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    provider_plan = _provider_plan(providers, count)
    provider_counts = _provider_counts(provider_plan)
    unique_providers = sorted(provider_counts)
    active_gemini_auth_type = gemini_auth_type if "gemini" in unique_providers else "current"
    run_id = time.strftime("SC_PROVIDER_PREFLIGHT_%Y%m%d_%H%M%S")
    workdir = Path(tempfile.gettempdir()) / f"selfconnect_provider_preflight_{run_id}"
    workdir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    checks: list[dict[str, Any]] = []
    with _temporary_gemini_auth_type(active_gemini_auth_type):
        for provider in unique_providers:
            nonce = f"{run_id}_{provider}"
            expected = f"ACK_PREFLIGHT provider={provider} nonce={nonce}"
            log = workdir / f"{provider}.log"
            script = _write_provider_preflight_script(
                workdir=workdir,
                provider=provider,
                expected=expected,
                log=log,
            )
            check_started = time.perf_counter()
            timed_out = False
            output = ""
            returncode: int | None = None
            try:
                completed = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                    ],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_s,
                    check=False,
                )
                returncode = completed.returncode
                output = (completed.stdout or "") + (completed.stderr or "")
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + (
                    (exc.stderr or "") if isinstance(exc.stderr, str) else ""
                )
            try:
                output += "\n" + log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
            status, error = _classify_provider_output(
                output=output,
                expected=expected,
                nonce=nonce,
                returncode=returncode,
                timed_out=timed_out,
            )
            checks.append(
                {
                    "provider": provider,
                    "status": status,
                    "ready": status == "ready",
                    "returncode": returncode,
                    "timed_out": timed_out,
                    "duration_ms": (time.perf_counter() - check_started) * 1000.0,
                    "expected_hash": _sha256(expected),
                    "nonce_hash": _sha256(nonce),
                    "error": error,
                    "log": str(log),
                }
            )

    result = {
        "schema": "selfconnect.provider_preflight.v1",
        "run_id": run_id,
        "verdict": "PASS" if all(check["ready"] for check in checks) else "FAIL",
        "provider_counts": provider_counts,
        "unique_providers": unique_providers,
        "gemini_auth_type": active_gemini_auth_type,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "checks": checks,
    }
    out_path = results_dir / f"provider_preflight_{run_id}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["result_path"] = str(out_path)
    return result


def run_baseline(
    *,
    count: int,
    providers: str | None,
    timeout_s: float,
    results_dir: Path,
    keep_open: bool,
    gemini_auth_type: str = "current",
    allow_stale_windows: bool = False,
) -> dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    stale_windows = _stale_run_windows()
    if stale_windows and not allow_stale_windows:
        return {
            "schema": "selfconnect.real_agent_baseline.v3",
            "run_id": "",
            "verdict": "FAIL",
            "error": "stale SC_REAL5_ windows exist; clean them before starting a new run",
            "stale_window_count": len(stale_windows),
            "stale_windows": [
                {
                    "hwnd": int(row.get("hwnd", 0)),
                    "pid": int(row.get("pid", 0)),
                    "title": str(row.get("title", "")),
                }
                for row in stale_windows
            ],
        }
    run_id = time.strftime("SC_REAL5_%Y%m%d_%H%M%S")
    workdir = Path(tempfile.gettempdir()) / f"selfconnect_real_agent_baseline_{run_id}"
    workdir.mkdir(parents=True, exist_ok=True)

    agents: list[AgentRun] = []
    provider_plan = _provider_plan(providers, count)
    provider_ordinals: dict[str, int] = {}
    for index, provider in enumerate(provider_plan, start=1):
        provider_ordinals[provider] = provider_ordinals.get(provider, 0) + 1
        role = f"real{provider}-{provider_ordinals[provider]}"
        nonce = f"{run_id}_{index:02d}"
        expected = f"ACK_REAL_VENDOR provider={provider} role={role} nonce={nonce}"
        log = workdir / f"{role}.log"
        script = _write_agent_script(
            workdir=workdir,
            run_id=run_id,
            provider=provider,
            role=role,
            nonce=nonce,
            expected=expected,
            log=log,
            keep_open=keep_open,
        )
        agents.append(
            AgentRun(
                provider=provider,
                role=role,
                nonce=nonce,
                expected=expected,
                script=script,
                log=log,
            )
    )

    started = time.perf_counter()
    active_gemini_auth_type = gemini_auth_type if "gemini" in provider_plan else "current"
    with _temporary_gemini_auth_type(active_gemini_auth_type):
        processes = [_spawn_visible(agent.script) for agent in agents]

        try:
            for agent in agents:
                agent.status = "pending"

            deadline = time.perf_counter() + timeout_s
            pending = {agent.role for agent in agents}
            last_progress = 0.0
            _write_run_state(
                results_dir=results_dir,
                run_id=run_id,
                phase="spawned",
                started=started,
                agents=agents,
                processes=processes,
                pending=pending,
                provider_plan=provider_plan,
                gemini_auth_type=active_gemini_auth_type,
            )
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
                    if _has_exact_line(readback, agent.expected):
                        agent.ack_ms = (time.perf_counter() - started) * 1000.0
                        agent.status = "pass"
                        pending.remove(agent.role)
                now = time.perf_counter()
                if now - last_progress >= 10.0 or not pending:
                    last_progress = now
                    print(
                        json.dumps(
                            {
                                "run_id": run_id,
                                "phase": "polling",
                                "pass_count": sum(
                                    1 for agent in agents if agent.status == "pass"
                                ),
                                "pending_count": len(pending),
                                "duration_ms": (now - started) * 1000.0,
                                "state_path": str(_state_path(results_dir, run_id)),
                            }
                        ),
                        flush=True,
                    )
                    _write_run_state(
                        results_dir=results_dir,
                        run_id=run_id,
                        phase="polling" if pending else "acks-complete",
                        started=started,
                        agents=agents,
                        processes=processes,
                        pending=pending,
                        provider_plan=provider_plan,
                        gemini_auth_type=active_gemini_auth_type,
                    )
                time.sleep(1.0)

            for agent in agents:
                if agent.status != "pass":
                    agent.status = "fail"
                    if not agent.error:
                        if agent.hwnd is None:
                            agent.error = "visible terminal window not found"
                        else:
                            agent.error = "expected ACK not observed via UIA before timeout"
                    _diagnose_failed_agent(agent)
            _write_run_state(
                results_dir=results_dir,
                run_id=run_id,
                phase="classifying",
                started=started,
                agents=agents,
                processes=processes,
                pending=pending,
                provider_plan=provider_plan,
                gemini_auth_type=active_gemini_auth_type,
            )

            passed = all(agent.status == "pass" for agent in agents)
            ack_times = [agent.ack_ms for agent in agents if agent.ack_ms is not None]
            launch_times = [agent.launch_ms for agent in agents if agent.launch_ms is not None]
            failed = [agent for agent in agents if agent.status != "pass"]
            baseline_file = _baseline_file_name(provider_plan, count) if passed else None
            result = {
                "schema": "selfconnect.real_agent_baseline.v3",
                "run_id": run_id,
                "verdict": "PASS" if passed else "FAIL",
                "agent_count": count,
                "provider_counts": _provider_counts(provider_plan),
                "gemini_auth_type": active_gemini_auth_type,
                "real_agent_cli": (
                    "mixed"
                    if len(set(provider_plan)) > 1
                    else PROVIDERS[provider_plan[0]].display
                ),
                "visible_windows": True,
                "uia_readback_required": True,
                "logical_simulation": False,
                "baseline_file": baseline_file,
                "started_at": started,
                "duration_ms": (time.perf_counter() - started) * 1000.0,
                "ack_latency_ms": _latency_stats(ack_times),
                "launch_latency_ms": _latency_stats(launch_times),
                "governance_transport_latency_ms": {
                    "measured": False,
                    "reason": (
                        "real CLI baseline measures real agent launch/ACK/readback; "
                        "transport/governance p50/p95/p99 are measured by the logical "
                        "Fabric V0 harness"
                    ),
                },
                "model_call_accounting": {
                    "real_model_calls_total": count,
                    "real_model_calls_per_ack_task": 1.0,
                    "known_deterministic_task": False,
                    "model_calls_per_known_task": None,
                    "note": (
                        "This real-agent ACK baseline intentionally invokes one real "
                        "provider model turn per visible agent. It does not claim the zero-model-"
                        "call deterministic replay property."
                    ),
                },
                "failure_counters": {
                    "missed_acks": len(failed),
                    "visible_window_missing": sum(1 for agent in failed if agent.hwnd is None),
                    "uia_readback_failures": sum(
                        1 for agent in failed if agent.error.startswith("readback failed:")
                    ),
                    "wrong_window_guard_failures": 0,
                    "drift_or_narration_events": 0,
                    "approval_stalls": 0,
                    "wrong_ack_format": sum(
                        1 for agent in failed if agent.diagnosis == "wrong_ack_format"
                    ),
                    "provider_auth_required": sum(
                        1 for agent in failed if agent.diagnosis == "provider_auth_required"
                    ),
                    "provider_failures": {
                        provider: sum(
                            1
                            for agent in failed
                            if agent.provider == provider
                        )
                        for provider in sorted(set(provider_plan))
                    },
                },
                "agents": [
                    {
                        "provider": agent.provider,
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
                        "diagnosis": agent.diagnosis,
                        "log": str(agent.log),
                    }
                    for agent in agents
                ],
            }

            out_path = results_dir / f"real_agent_baseline_{run_id}.json"
            out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            if baseline_file:
                baseline = results_dir / baseline_file
                baseline.write_text(json.dumps(result, indent=2), encoding="utf-8")
                result["baseline_path"] = str(baseline)
            result["result_path"] = str(out_path)
            _write_run_state(
                results_dir=results_dir,
                run_id=run_id,
                phase="complete",
                started=started,
                agents=agents,
                processes=processes,
                pending=set(),
                provider_plan=provider_plan,
                gemini_auth_type=active_gemini_auth_type,
            )
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
    parser.add_argument(
        "--providers",
        default=None,
        help=(
            "Provider plan. Use counted form such as codex:3,claude:2 or "
            "round-robin form such as codex,claude. Default: codex only."
        ),
    )
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="run real provider one-shot ACK readiness checks without visible windows",
    )
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--close-windows", action="store_true")
    parser.add_argument(
        "--allow-stale-windows",
        action="store_true",
        help="allow starting a run even if old SC_REAL5_ windows are visible",
    )
    parser.add_argument(
        "--cleanup-run",
        default=None,
        help="terminate visible child shells for a specific SC_REAL5_ run id and exit",
    )
    parser.add_argument(
        "--cleanup-restore-gemini-auth",
        choices=GEMINI_AUTH_CHOICES,
        default="oauth-personal",
        help="Gemini auth selector to set after --cleanup-run; use current to leave unchanged",
    )
    parser.add_argument(
        "--gemini-auth-type",
        choices=GEMINI_AUTH_CHOICES,
        default="current",
        help=(
            "Temporarily select a Gemini CLI auth mode for this run, restoring "
            "~/.gemini/settings.json afterward. Use gemini-api-key when "
            "GEMINI_API_KEY is supplied in the environment."
        ),
    )
    args = parser.parse_args(argv)

    if args.cleanup_run:
        result = cleanup_run(
            args.cleanup_run,
            restore_gemini_auth=args.cleanup_restore_gemini_auth,
        )
        print(json.dumps(result, indent=2))
        return 0 if int(result.get("remaining", 0)) == 0 else 1

    if args.preflight_only:
        result = run_provider_preflight(
            providers=args.providers,
            count=args.agents,
            timeout_s=args.timeout,
            results_dir=args.results_dir,
            gemini_auth_type=args.gemini_auth_type,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["verdict"] == "PASS" else 1

    result = run_baseline(
        count=args.agents,
        providers=args.providers,
        timeout_s=args.timeout,
        results_dir=args.results_dir,
        keep_open=not args.close_windows,
        gemini_auth_type=args.gemini_auth_type,
        allow_stale_windows=args.allow_stale_windows,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
