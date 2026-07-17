"""Produce restricted real-agent scale evidence for the ecosystem v2 gate.

This runner is intentionally separate from ``real_agent_baseline.py``.  It is
only for a protected, disposable Windows runner and never enables unattended
write access or bypasses a provider's trust controls.
"""

from __future__ import annotations

# ruff: noqa: E402,I001,S603

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from collections.abc import Callable, Mapping, Sequence

import psutil

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sc_cli


SCHEMA = "selfconnect.scale_readiness_evidence.v2"
RUNG_SCHEMA = "selfconnect.restricted_scale_result.v2"
CORE_REMOTE = "https://github.com/rblake2320/selfconnect"
CORE_BRANCH = "master"
ECOSYSTEM_CONTRACT_SHA = "93610d14414cd4e273a4192bfbfafd65a0bad67a"
RUN_TITLE_PREFIX = "SC_SCALE"
TERMINAL_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"
TERMINAL_EXE = "WindowsTerminal.exe"
MAX_FUTURE_SKEW = timedelta(minutes=5)
MAX_CHECKOUT_AGE = timedelta(minutes=30)
MAX_RUNG_DURATION = timedelta(minutes=30)
MAX_RUNG_AGE = timedelta(minutes=30)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN_TOKENS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-skip-permissions",
    "bypassPermissions",
    "--yolo",
    "yolo",
    "--skip-trust",
}
GEMINI_DENY_ALL_POLICY = (
    b'[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\n'
    b'denyMessage = "Restricted scale producer forbids all tool calls."\n'
)
GEMINI_DENY_ALL_POLICY_SHA256 = "ff0198012262e2a478eb9d26ebe175924079cc7d16379ef809d18c012649029a"
COMMON_ENV = {
    "ALLUSERSPROFILE",
    "APPDATA",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
}


class ProducerError(RuntimeError):
    """A bounded, non-secret producer failure."""


@dataclass(frozen=True)
class ProviderPolicy:
    credential: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class ProviderRuntime:
    command_prefix: tuple[str, ...]
    entrypoint: Path


PROVIDERS = {
    "codex": ProviderPolicy(
        "OPENAI_API_KEY",
        ("codex", "exec", "--sandbox", "read-only", "--ephemeral", "--ignore-user-config"),
    ),
    "claude": ProviderPolicy(
        "ANTHROPIC_API_KEY",
        ("claude", "--print", "--bare", "--safe-mode", "--permission-mode", "plan", "--tools", ""),
    ),
    "gemini": ProviderPolicy(
        "GEMINI_API_KEY",
        (
            "gemini",
            "--prompt",
            "{prompt}",
            "--approval-mode",
            "plan",
            "--sandbox",
            "--admin-policy",
            "{policy}",
        ),
    ),
}
PROVIDER_PROJECTIONS = {
    "codex": ["exec", "--sandbox", "read-only", "--ephemeral", "--ignore-user-config"],
    "claude": ["--print", "--bare", "--safe-mode", "--permission-mode", "plan", "--tools", ""],
    "gemini": ["--prompt", "--approval-mode", "plan", "--sandbox", "--admin-policy"],
}
PROVIDER_VERSIONS = {
    "codex": "codex-cli 0.144.4",
    "claude": "2.1.183 (Claude Code)",
    "gemini": "0.46.0",
}
PROVIDER_HELP_COMMANDS = {
    "codex": ["codex", "exec", "--help"],
    "claude": ["claude", "--help"],
    "gemini": ["gemini", "--help"],
}
PROVIDER_ENTRYPOINT_SHA256 = {
    "codex": "51398051c2332b6afe08dc3b9dbb4056085c197f35ca57a307ee303d450cada5",
    "claude": "ba6e71d0e39b33c42a519bd10fc6d79b04d62cedcc918b3991ff863462261eb0",
    "gemini": "6970329338ab5726d015b4ed847b1d2fd960244baefc86cbeacd3786b677dddc",
}
PROVIDER_EXE_NAMES = {
    "codex": "codex.exe",
    "claude": "claude.exe",
    "gemini": "node.exe",
}
PROVIDER_HELP_SHA256 = {
    "codex": "9f86f0115238ddde2514587e5f95b0ab0aa6b89495e5912878d49ad26038aa19",
    "claude": "6c5e44dd5a1c5b04f7deb4d734ac6d2585561509c6a2d5deaed6914665e03b29",
    "gemini": "b5c6e1af180f48adb3700982e7b06e905f29d2965f047eaeebd0b5c4f676b632",
}
RUNGS = {
    10: {"gemini": 10},
    15: {"claude": 5, "codex": 5, "gemini": 5},
    20: {"claude": 7, "codex": 7, "gemini": 6},
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def crypto_nonce() -> str:
    raw = secrets.token_bytes(32)
    if type(raw) is not bytes or len(raw) != 32:
        raise ProducerError("nonce_source_invalid")
    return raw.hex()


def parse_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ProducerError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProducerError("timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise ProducerError("timestamp_invalid")
    return parsed.astimezone(UTC)


def validate_interval(started: datetime, completed: datetime, *, now: datetime) -> None:
    if (
        started.tzinfo is None
        or completed.tzinfo is None
        or completed <= started
        or completed - started > MAX_RUNG_DURATION
        or now - completed > MAX_RUNG_AGE
        or started > now + MAX_FUTURE_SKEW
        or completed > now + MAX_FUTURE_SKEW
    ):
        raise ProducerError("rung_time_invalid")


def provider_env(provider: str, source: Mapping[str, str]) -> dict[str, str]:
    policy = PROVIDERS[provider]
    env = {key: value for key, value in source.items() if key.upper() in COMMON_ENV}
    credential = source.get(policy.credential)
    if not credential:
        raise ProducerError(f"{provider}_credential_missing")
    env[policy.credential] = credential
    env["NO_COLOR"] = "1"
    env["CI"] = "true"
    if provider == "gemini":
        env["GEMINI_CLI_NO_RELAUNCH"] = "true"
    return env


def validate_restricted_argv(provider: str, argv: Sequence[str]) -> None:
    if not argv or argv[0].lower().removesuffix(".exe") != provider:
        raise ProducerError("provider_argv_invalid")
    lowered = {token.lower() for token in argv}
    if lowered & {token.lower() for token in FORBIDDEN_TOKENS}:
        raise ProducerError("unsafe_provider_flag")
    required = [
        item
        for item in PROVIDERS[provider].argv
        if item not in {"{prompt}", "{policy}"}  # noqa: S105
    ]
    cursor = 0
    for token in argv:
        if cursor < len(required) and token == required[cursor]:
            cursor += 1
    if cursor != len(required):
        raise ProducerError("provider_mode_not_restricted")


def provider_argv(provider: str, prompt: str, *, admin_policy: Path | None = None) -> list[str]:
    provider_policy = PROVIDERS[provider]
    argv = [
        prompt
        if item == "{prompt}"
        else str(admin_policy)
        if item == "{policy}" and admin_policy is not None
        else item
        for item in provider_policy.argv
    ]  # noqa: S105
    if "{policy}" in argv:
        raise ProducerError("gemini_admin_policy_missing")
    if provider in {"codex", "claude"}:
        argv.append(prompt)
    validate_restricted_argv(provider, argv)
    return argv


def provider_policy_projection(provider: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "cli_version": PROVIDER_VERSIONS[provider],
        "help_command": PROVIDER_HELP_COMMANDS[provider],
        "required_runtime_policy": PROVIDER_PROJECTIONS[provider],
        "forbidden_runtime_tokens": sorted(FORBIDDEN_TOKENS),
    }


def provider_pins() -> dict[str, dict[str, str | None]]:
    return {
        provider: {
            "required_policy_projection_sha256": sha256_bytes(
                canonical_json(provider_policy_projection(provider))
            ),
            "required_tool_policy_sha256": (
                GEMINI_DENY_ALL_POLICY_SHA256 if provider == "gemini" else None
            ),
            "expected_cli_version": PROVIDER_VERSIONS[provider],
            "expected_help_sha256": PROVIDER_HELP_SHA256[provider],
            "expected_entrypoint_sha256": PROVIDER_ENTRYPOINT_SHA256[provider],
            "expected_provider_exe_name": PROVIDER_EXE_NAMES[provider],
        }
        for provider in ("codex", "claude", "gemini")
    }


def resolve_provider_runtime(provider: str, env: Mapping[str, str]) -> ProviderRuntime:
    shim = shutil.which(provider, path=env.get("PATH"))
    if not shim:
        raise ProducerError("provider_executable_missing")
    base = Path(shim).parent
    if provider == "codex":
        candidates = list(
            (
                base
                / "node_modules"
                / "@openai"
                / "codex"
                / "node_modules"
                / "@openai"
                / "codex-win32-x64"
                / "vendor"
            ).glob("*/bin/codex.exe")
        )
        if len(candidates) != 1:
            raise ProducerError("provider_entrypoint_ambiguous")
        entrypoint = candidates[0]
        prefix = (str(entrypoint),)
    elif provider == "claude":
        entrypoint = base / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        prefix = (str(entrypoint),)
    else:
        entrypoint = base / "node_modules" / "@google" / "gemini-cli" / "bundle" / "gemini.js"
        node = shutil.which("node.exe", path=env.get("PATH"))
        if not node:
            raise ProducerError("provider_runtime_missing")
        prefix = (node, str(entrypoint))
    if not entrypoint.is_file():
        raise ProducerError("provider_entrypoint_missing")
    if sha256_file(entrypoint) != PROVIDER_ENTRYPOINT_SHA256[provider]:
        raise ProducerError("provider_entrypoint_digest_mismatch")
    return ProviderRuntime(prefix, entrypoint)


def verify_provider_pins(
    source_env: Mapping[str, str],
) -> dict[str, dict[str, str | None]]:
    pins = provider_pins()
    for provider in PROVIDERS:
        env = provider_env(provider, source_env)
        runtime = resolve_provider_runtime(provider, env)
        commands = (
            [*runtime.command_prefix, "--version"],
            [*runtime.command_prefix, *PROVIDER_HELP_COMMANDS[provider][1:]],
        )
        outputs: list[str] = []
        for command in commands:
            completed = subprocess.run(
                command,
                env=env,
                cwd=ROOT,
                capture_output=True,
                check=False,
                timeout=30,
            )
            if completed.returncode:
                raise ProducerError("provider_pin_query_failed")
            outputs.append(completed.stdout.decode("utf-8", errors="strict"))
        version, help_text = outputs
        if version.strip() != PROVIDER_VERSIONS[provider]:
            raise ProducerError("provider_version_mismatch")
        normalized_help = help_text.replace("\r\n", "\n").replace("\r", "\n")
        if sha256_bytes(normalized_help.encode()) != PROVIDER_HELP_SHA256[provider]:
            raise ProducerError("provider_help_digest_mismatch")
        required_flags = {item for item in PROVIDER_PROJECTIONS[provider] if item.startswith("--")}
        if any(flag not in help_text for flag in required_flags):
            raise ProducerError("provider_help_policy_mismatch")
    return pins


def verify_provider_home_isolation(source_env: Mapping[str, str]) -> None:
    user_profile = source_env.get("USERPROFILE")
    program_data = source_env.get("PROGRAMDATA", r"C:\ProgramData")
    if not user_profile:
        raise ProducerError("provider_home_unavailable")
    forbidden = [
        Path(user_profile) / ".codex",
        Path(user_profile) / ".claude",
        Path(user_profile) / ".gemini",
    ]
    if any(path.exists() for path in forbidden):
        raise ProducerError("provider_home_not_disposable")
    standard_admin = Path(program_data) / "gemini-cli" / "policies"
    if standard_admin.exists() and any(standard_admin.glob("*.toml")):
        # Gemini ignores supplemental --admin-policy when this directory is populated.
        raise ProducerError("gemini_supplemental_policy_shadowed")


def _run_git(args: list[str], *, cwd: Path) -> str:
    git = shutil.which("git")
    if not git:
        raise ProducerError("git_missing")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "NUL" if sys.platform == "win32" else "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    completed = subprocess.run(
        [git, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        timeout=30,
    )
    if completed.returncode:
        raise ProducerError("checkout_query_failed")
    return completed.stdout.strip()


def verify_checkout(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or utc_now()).astimezone(UTC)
    git_dir = root / ".git"
    if not git_dir.is_dir() or (git_dir / "commondir").exists():
        raise ProducerError("fresh_detached_checkout_required")
    if _run_git(["status", "--porcelain=v1", "--untracked-files=normal"], cwd=root):
        raise ProducerError("checkout_dirty")
    if _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root) != "HEAD":
        raise ProducerError("checkout_not_detached")
    head = _run_git(["rev-parse", "HEAD"], cwd=root).lower()
    remote = _run_git(["remote", "get-url", "origin"], cwd=root).removesuffix(".git")
    remote_head = (
        _run_git(["ls-remote", "--exit-code", CORE_REMOTE, f"refs/heads/{CORE_BRANCH}"], cwd=root)
        .split()[0]
        .lower()
    )
    if remote.lower() != CORE_REMOTE.lower() or not SHA1_RE.fullmatch(head) or head != remote_head:
        raise ProducerError("checkout_identity_invalid")
    head_log = git_dir / "logs" / "HEAD"
    created = datetime.fromtimestamp(head_log.stat().st_mtime, UTC)
    if created > now + MAX_FUTURE_SKEW or now - created > MAX_CHECKOUT_AGE:
        raise ProducerError("checkout_not_fresh")
    tree = _run_git(["ls-tree", "-r", "--full-tree", "HEAD"], cwd=root)
    return {
        "core_remote": CORE_REMOTE,
        "core_branch": CORE_BRANCH,
        "core_head_sha": head,
        "fresh_detached_checkout": True,
        "git_config_cleared": os.environ.get("GIT_CONFIG_NOSYSTEM") == "1"
        and os.environ.get("GIT_CONFIG_GLOBAL", "").upper() == "NUL",
        "python_env_cleared": not any(
            os.environ.get(name) for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV")
        ),
        "core_tree_sha256": sha256_bytes(tree.encode()),
        "producer_sha256": sha256_file(Path(__file__)),
        "guard_module_sha256": sha256_file(ROOT / "sc_cli.py"),
    }


def _process_rows(root_pid: int) -> list[dict[str, int | str | None]]:
    try:
        root = psutil.Process(root_pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil.Error, OSError) as exc:
        raise ProducerError("process_tree_unavailable") from exc
    rows: list[dict[str, int | str | None]] = []
    for process in processes:
        try:
            rows.append(
                {
                    "pid": process.pid,
                    "parent_pid": None if process.pid == root_pid else process.ppid(),
                    "exe_name": process.name(),
                }
            )
        except psutil.Error:
            continue
    rows.sort(key=lambda row: int(row["pid"]))
    if not (3 <= len(rows) <= 64):
        raise ProducerError("process_tree_projection_invalid")
    pids = {int(row["pid"]) for row in rows}
    if any(
        row["pid"] != root_pid and row["parent_pid"] not in pids
        for row in rows
    ):
        raise ProducerError("process_tree_projection_invalid")
    return rows


def _projected_pids(rows: Sequence[Mapping[str, int | str | None]]) -> set[int]:
    try:
        return {int(row["pid"]) for row in rows}
    except (KeyError, TypeError, ValueError) as exc:
        raise ProducerError("process_tree_projection_invalid") from exc


def _process_session_id(pid: int) -> int:
    import ctypes

    session_id = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
        raise ProducerError("process_session_unavailable")
    return int(session_id.value)


def _verify_provider_process(
    pid: int,
    runtime: ProviderRuntime,
    *,
    provider: str | None = None,
    expected_argv: Sequence[str] | None = None,
    expected_nonce: str | None = None,
) -> list[str]:
    try:
        process = psutil.Process(pid)
        actual_exe = Path(process.exe()).resolve()
        expected_exe = Path(runtime.command_prefix[0]).resolve()
        raw_command_line = process.cmdline()
        command_line = [item.lower() for item in raw_command_line]
    except (psutil.Error, OSError) as exc:
        raise ProducerError("provider_process_unavailable") from exc
    if actual_exe != expected_exe:
        raise ProducerError("provider_process_executable_mismatch")
    if len(runtime.command_prefix) > 1 and not all(
        item.lower() in command_line for item in runtime.command_prefix[1:]
    ):
        raise ProducerError("provider_process_entrypoint_mismatch")
    if expected_nonce is not None and not any(expected_nonce in item for item in command_line):
        raise ProducerError("provider_process_role_mismatch")
    if provider is None or expected_argv is None:
        return []
    expected_command = [*runtime.command_prefix, *expected_argv[1:]]
    if len(raw_command_line) != len(expected_command):
        raise ProducerError("provider_process_argv_mismatch")
    for actual, expected in zip(raw_command_line, expected_command, strict=True):
        if actual == expected:
            continue
        try:
            if Path(actual).resolve() == Path(expected).resolve():
                continue
        except OSError:
            pass
        raise ProducerError("provider_process_argv_mismatch")
    # The full native command line is checked above.  Only the non-secret,
    # stable policy projection is emitted; prompts and temporary paths are not.
    return list(PROVIDER_PROJECTIONS[provider])


def _assert_concurrent_provider_barrier(states: Sequence[Mapping[str, Any]]) -> None:
    for state in states:
        pid = int(state["provider_pid"])
        runtime = state["runtime"]
        try:
            if not psutil.Process(pid).is_running():
                raise ProducerError("provider_concurrency_barrier_failed")
        except psutil.Error as exc:
            raise ProducerError("provider_concurrency_barrier_failed") from exc
        _verify_provider_process(pid, runtime, expected_nonce=str(state["nonce"]))


def build_guard_receipt(
    *,
    provider: str,
    role: str,
    nonce: str,
    pre: Mapping[str, Any],
    post: Mapping[str, Any],
    provider_pid: int,
    process_rows: Sequence[Mapping[str, int | str | None]],
    session_id: int,
    shell_session_id: int,
    provider_session_id: int,
    provider_entrypoint_sha256: str,
) -> dict[str, Any]:
    title = f"SC_SCALE {provider} {role} {nonce}"
    required = {
        "hwnd": int(pre.get("hwnd", 0)) > 0 and pre.get("hwnd") == post.get("hwnd"),
        "pid": int(pre.get("pid", 0)) > 0 and pre.get("pid") == post.get("pid"),
        "exe": str(pre.get("exe_name", "")).lower() == TERMINAL_EXE.lower()
        and pre.get("exe_name") == post.get("exe_name"),
        "class": pre.get("class_name") == TERMINAL_CLASS
        and pre.get("class_name") == post.get("class_name"),
        "title": pre.get("title") == title and post.get("title") == title,
    }
    pids = {int(row["pid"]) for row in process_rows}
    parent_by_pid = {int(row["pid"]): row["parent_pid"] for row in process_rows}
    exe_by_pid = {int(row["pid"]): str(row["exe_name"]) for row in process_rows}
    same_session = session_id == shell_session_id == provider_session_id and session_id > 0
    if (
        not all(required.values())
        or provider_pid not in pids
        or int(pre["pid"]) not in pids
        or parent_by_pid[int(pre["pid"])] is not None
        or exe_by_pid[provider_pid].lower() != PROVIDER_EXE_NAMES[provider].lower()
        or not same_session
    ):
        raise ProducerError("window_process_guard_failed")
    cursor = provider_pid
    visited: set[int] = set()
    while cursor != int(pre["pid"]):
        if cursor in visited or parent_by_pid.get(cursor) not in pids:
            raise ProducerError("window_process_guard_failed")
        visited.add(cursor)
        cursor = int(parent_by_pid[cursor])
    claim = {
        "pre_guard_ok": True,
        "post_guard_ok": True,
        "spawn_alive_during_guard": True,
        "provider_in_spawn_tree": True,
        "same_session": True,
        "tree_root_pid": int(pre["pid"]),
        "provider_pid": provider_pid,
        "window_pid": int(pre["pid"]),
        "session_id": session_id,
        "exe_name": str(pre["exe_name"]),
        "class_name": str(pre["class_name"]),
        "title_sha256": sha256_bytes(title.encode()),
        "process_tree_projection": list(process_rows),
        "process_tree_sha256": sha256_bytes(canonical_json(list(process_rows))),
        "provider_entrypoint_sha256": provider_entrypoint_sha256,
    }
    return {"claim": claim, "digest": sha256_bytes(canonical_json(claim))}


def _find_exact_window(title: str) -> dict[str, Any] | None:
    matches = [
        row
        for row in sc_cli.list_window_records(query=title, limit=300)
        if row.get("title") == title
    ]
    return matches[0] if len(matches) == 1 else None


def _has_exact_line(text: str, expected: str) -> bool:
    return any(line.strip() == expected for line in text.splitlines())


def _observed_exact_line(text: str, expected: str) -> str | None:
    return next((line.strip() for line in text.splitlines() if line.strip() == expected), None)


def _provider_failure_observed(text: str) -> bool:
    markers = (
        "RESOURCE_EXHAUSTED",
        "Quota exceeded",
        "FatalAuthenticationError",
        "Manual authorization is required",
        "authentication failed",
    )
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def cleanup_process_tree(root_pids: Sequence[int], *, wait_s: float = 10.0) -> dict[str, Any]:
    targets: dict[int, psutil.Process] = {}
    for pid in root_pids:
        try:
            root = psutil.Process(pid)
            for process in [*root.children(recursive=True), root]:
                targets[process.pid] = process
        except psutil.Error:
            continue
    for process in targets.values():
        try:
            process.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(list(targets.values()), timeout=wait_s)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(alive, timeout=wait_s)
    receipt = {
        "requested": True,
        "completed": not alive,
        "target_count": len(targets),
        "remaining_count": len(alive),
        "completed_at_utc": utc_text(utc_now()),
    }
    if alive:
        raise ProducerError("cleanup_incomplete")
    return receipt


def assert_disposable_terminal_host(
    *,
    preexisting_terminal_pids: set[int],
    launched_terminal_pids: set[int],
    launched_titles: set[str],
    observed_terminal_windows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Prove that cleanup cannot terminate a pre-existing shared terminal host."""
    if preexisting_terminal_pids or not launched_terminal_pids:
        raise ProducerError("disposable_terminal_host_not_established")
    if preexisting_terminal_pids & launched_terminal_pids:
        raise ProducerError("disposable_terminal_host_not_established")
    observed = {
        (int(row.get("pid", 0)), str(row.get("title", "")))
        for row in observed_terminal_windows
        if str(row.get("exe_name", "")).lower() == TERMINAL_EXE.lower()
    }
    expected = {
        (int(row.get("pid", 0)), str(row.get("title", "")))
        for row in observed_terminal_windows
        if int(row.get("pid", 0)) in launched_terminal_pids
        and str(row.get("title", "")) in launched_titles
    }
    if not observed or observed != expected:
        raise ProducerError("disposable_terminal_host_not_established")
    return {
        "preexisting_terminal_process_count": 0,
        "launched_terminal_process_count": len(launched_terminal_pids),
        "safe_to_terminate_launched_hosts": True,
    }


def build_ack_observations(
    *,
    stdout_line: str,
    stdout_captured_at: datetime,
    rendered_line: str,
    rendered_captured_at: datetime,
) -> dict[str, Any]:
    """Describe stdout and its later terminal rendering without independence claims."""
    stdout_event_id = secrets.token_hex(32)
    return {
        "process_stdout": {
            "event_id": stdout_event_id,
            "source": "process_stdout",
            "provenance": "provider_stdout_pipe",
            "sha256": sha256_bytes(stdout_line.encode()),
            "captured_at_utc": utc_text(stdout_captured_at),
        },
        "rendered_terminal_copy": {
            "event_id": secrets.token_hex(32),
            "source": "rendered_terminal_copy",
            "provenance": "uia_copy_of_provider_stdout",
            "derivative_of_event_id": stdout_event_id,
            "sha256": sha256_bytes(rendered_line.encode()),
            "captured_at_utc": utc_text(rendered_captured_at),
        },
    }


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _write_agent_script(
    workdir: Path,
    provider: str,
    role: str,
    nonce: str,
    prompt: str,
    log: Path,
    *,
    admin_policy: Path,
    runtime: ProviderRuntime,
) -> tuple[Path, Path, Path, Path, Path, list[str]]:
    title = f"SC_SCALE {provider} {role} {nonce}"
    ready, running, go, done = (
        workdir / f"{role}.{suffix}.json" for suffix in ("ready", "running", "go", "done")
    )
    argv = provider_argv(provider, prompt, admin_policy=admin_policy)
    executable, *runtime_arguments = runtime.command_prefix
    arguments = [*runtime_arguments, *argv[1:]]
    argument_text = _ps_quote(subprocess.list2cmdline(arguments))
    script = workdir / f"{role}.ps1"
    error_log = workdir / f"{role}.err.log"
    environment_names = sorted(provider_env(provider, os.environ))
    ps_environment_names = "@(" + ",".join(_ps_quote(name) for name in environment_names) + ")"
    text = f"""
$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = {_ps_quote(title)}
@{{ pid = $PID }} | ConvertTo-Json -Compress | Set-Content -LiteralPath {_ps_quote(str(ready))}
while (-not (Test-Path -LiteralPath {_ps_quote(str(go))})) {{ Start-Sleep -Milliseconds 100 }}
$allowedNames = {ps_environment_names}
$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = {_ps_quote(executable)}
$psi.Arguments = {argument_text}
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.EnvironmentVariables.Clear()
foreach ($name in $allowedNames) {{
    $value = [Environment]::GetEnvironmentVariable($name, 'Process')
    if ($null -eq $value) {{ throw "required provider environment missing: $name" }}
    $psi.EnvironmentVariables[$name] = $value
}}
$proc = [System.Diagnostics.Process]::new()
$proc.StartInfo = $psi
if (-not $proc.Start()) {{ throw 'provider process did not start' }}
$stdoutTask = $proc.StandardOutput.ReadToEndAsync()
$stderrTask = $proc.StandardError.ReadToEndAsync()
@{{ provider_pid = $proc.Id; actual_environment_names = @($allowedNames | Sort-Object) }} | ConvertTo-Json -Compress | `
    Set-Content -LiteralPath {_ps_quote(str(running))}
$proc.WaitForExit()
$utf8 = [System.Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText({_ps_quote(str(log))}, $stdoutTask.GetAwaiter().GetResult(), $utf8)
[IO.File]::WriteAllText({_ps_quote(str(error_log))}, $stderrTask.GetAwaiter().GetResult(), $utf8)
$Host.UI.RawUI.WindowTitle = {_ps_quote(title)}
Get-Content -LiteralPath {_ps_quote(str(log))} | Write-Host
@{{ provider_pid = $proc.Id; exit_code = $proc.ExitCode }} | ConvertTo-Json -Compress | `
    Set-Content -LiteralPath {_ps_quote(str(done))}
while ($true) {{ Start-Sleep -Seconds 3600 }}
"""
    script.write_text(text.strip() + "\n", encoding="utf-8")
    return script, ready, running, done, error_log, argv


def _wait_for(predicate: Callable[[], Any], timeout_s: float) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise ProducerError("operation_timed_out")


def _readback_exact_ack(hwnd: int, expected: str) -> str | None:
    try:
        text = str(sc_cli.read_window(hwnd).get("text", ""))
    except Exception:
        return None
    return _observed_exact_line(text, expected)


def _requested_runner_config() -> dict[str, Any]:
    required = {
        "repository": os.environ.get("GITHUB_REPOSITORY"),
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
        "environment": os.environ.get("SCALE_PRODUCER_ENVIRONMENT"),
        "runner_group": os.environ.get("SCALE_RUNNER_GROUP"),
        "job": os.environ.get("SCALE_PRODUCER_JOB"),
        "ref": os.environ.get("GITHUB_REF"),
    }
    expected = {
        "repository": "rblake2320/selfconnect",
        "workflow": "Restricted Real-Agent Scale Producer",
        "environment": "scale-readiness-producer",
        "runner_group": "selfconnect-scale-ephemeral",
        "job": "restricted-scale-producer",
        "ref": "refs/heads/master",
    }
    if required != expected:
        raise ProducerError("producer_context_invalid")
    image = os.environ.get("SCALE_RUNNER_IMAGE_SHA256", "").lower()
    ecosystem_sha = os.environ.get("ECOSYSTEM_CONTRACT_SHA", "").lower()
    core_sha = os.environ.get("GITHUB_SHA", "").lower()
    if (
        not SHA256_RE.fullmatch(image)
        or ecosystem_sha != ECOSYSTEM_CONTRACT_SHA
        or not SHA1_RE.fullmatch(core_sha)
    ):
        raise ProducerError("producer_context_invalid")
    return {
        **required,
        "producer_run_id": int(os.environ["GITHUB_RUN_ID"]),
        "producer_run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "actor": os.environ["GITHUB_ACTOR"],
        "requested_runner_image_sha256": image,
        "ecosystem_contract_sha": ecosystem_sha,
        "core_head_sha": core_sha,
    }


def _roles(counts: Mapping[str, int]) -> list[tuple[str, str]]:
    return [
        (provider, f"real{provider}-{ordinal}")
        for provider, count in counts.items()
        for ordinal in range(1, count + 1)
    ]


def run_rung(agent_count: int, output: Path, *, timeout_s: float) -> dict[str, Any]:
    if agent_count not in RUNGS:
        raise ProducerError("unsupported_rung")
    started = utc_now()
    run_id = "SC_SCALE_" + secrets.token_hex(16)
    processes: list[subprocess.Popen[bytes]] = []
    spawn_roots: list[int] = []
    launched_titles: list[str] = []
    agents: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    nonces: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="selfconnect-restricted-scale-") as temp_name:
        workdir = Path(temp_name)
        admin_policy = workdir / "gemini-deny-all.toml"
        admin_policy.write_bytes(GEMINI_DENY_ALL_POLICY)
        if sha256_file(admin_policy) != GEMINI_DENY_ALL_POLICY_SHA256:
            raise ProducerError("gemini_admin_policy_digest_mismatch")
        try:
            preexisting_terminal_pids = {
                process.pid
                for process in psutil.process_iter(["name"])
                if str(process.info.get("name", "")).lower() == TERMINAL_EXE.lower()
            }
            if preexisting_terminal_pids:
                raise ProducerError("disposable_terminal_host_not_established")
            original_hwnds = {
                row["hwnd"] for row in sc_cli.list_window_records(query="", limit=300)
            }
            for provider, role in _roles(RUNGS[agent_count]):
                nonce = crypto_nonce()
                if nonce in nonces:
                    raise ProducerError("nonce_reused")
                nonces.add(nonce)
                expected = f"ACK_REAL_VENDOR provider={provider} role={role} nonce={nonce}"
                prompt = f"Reply with exactly this one line and nothing else: {expected}"
                log = workdir / f"{role}.log"
                env = provider_env(provider, os.environ)
                runtime = resolve_provider_runtime(provider, env)
                script, ready, running, done, error_log, expected_argv = _write_agent_script(
                    workdir,
                    provider,
                    role,
                    nonce,
                    prompt,
                    log,
                    admin_policy=admin_policy,
                    runtime=runtime,
                )
                title = f"SC_SCALE {provider} {role} {nonce}"
                wt = shutil.which("wt.exe", path=env.get("PATH"))
                powershell = shutil.which("powershell.exe", path=env.get("PATH"))
                if not wt or not powershell:
                    raise ProducerError("terminal_executable_missing")
                proc = subprocess.Popen(
                    [
                        wt,
                        "-w",
                        "new",
                        "new-tab",
                        "--title",
                        title,
                        powershell,
                        "-NoLogo",
                        "-NoProfile",
                        "-File",
                        str(script),
                    ],
                    cwd=ROOT,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                processes.append(proc)
                launched_titles.append(title)
                ready_data = _wait_for(
                    lambda ready=ready: (
                        json.loads(ready.read_text(encoding="utf-8")) if ready.exists() else None
                    ),
                    30,
                )
                pre = _wait_for(lambda title=title: _find_exact_window(title), 30)
                if pre["hwnd"] in original_hwnds:
                    raise ProducerError("stale_window_reused")
                spawn_roots.append(int(ready_data["pid"]))
                states.append(
                    {
                        "provider": provider,
                        "role": role,
                        "nonce": nonce,
                        "expected": expected,
                        "prompt": prompt,
                        "log": log,
                        "error_log": error_log,
                        "running": running,
                        "done": done,
                        "go": workdir / f"{role}.go.json",
                        "env": env,
                        "runtime": runtime,
                        "expected_argv": expected_argv,
                        "title": title,
                        "ready_data": ready_data,
                        "pre": pre,
                    }
                )

            # All visible guarded windows exist before any provider is invoked.
            for state in states:
                state["go"].write_text("{}\n", encoding="utf-8")

            # Capture each provider while it is alive in the guarded window tree.
            for state in states:
                running = state["running"]
                running_data = _wait_for(
                    lambda running=running: (
                        json.loads(running.read_text(encoding="utf-8"))
                        if running.exists()
                        else None
                    ),
                    30,
                )
                provider_pid = int(running_data["provider_pid"])
                spawn_roots.append(provider_pid)
                pre = state["pre"]
                runtime = state["runtime"]
                actual_argv_projection = _verify_provider_process(
                    provider_pid,
                    runtime,
                    provider=str(state["provider"]),
                    expected_argv=state["expected_argv"],
                    expected_nonce=str(state["nonce"]),
                )
                actual_environment_names = running_data.get("actual_environment_names")
                expected_environment_names = sorted(state["env"])
                if actual_environment_names != expected_environment_names:
                    raise ProducerError("provider_process_environment_mismatch")
                state["started_at"] = utc_now()
                rows = _process_rows(int(pre["pid"]))
                if provider_pid not in _projected_pids(rows):
                    raise ProducerError("provider_outside_spawn_tree")
                provider_session_id = _process_session_id(provider_pid)
                state.update(
                    provider_pid=provider_pid,
                    process_rows=rows,
                    provider_session_id=provider_session_id,
                    actual_argv_projection=actual_argv_projection,
                    actual_environment_names=actual_environment_names,
                )

            # This is the scale barrier: every role process is alive together.
            _assert_concurrent_provider_barrier(states)

            for state in states:
                done = state["done"]
                done_data = _wait_for(
                    lambda done=done: (
                        json.loads(done.read_text(encoding="utf-8")) if done.exists() else None
                    ),
                    timeout_s,
                )
                state["done_data"] = done_data

            for state in states:
                provider = state["provider"]
                role = state["role"]
                nonce = state["nonce"]
                expected = state["expected"]
                prompt = state["prompt"]
                log = state["log"]
                error_log = state["error_log"]
                env = state["env"]
                runtime = state["runtime"]
                title = state["title"]
                pre = state["pre"]
                ready_data = state["ready_data"]
                provider_pid = state["provider_pid"]
                done_data = state["done_data"]
                post = _find_exact_window(title)
                if post is None:
                    raise ProducerError("post_window_missing")
                guard = build_guard_receipt(
                    provider=provider,
                    role=role,
                    nonce=nonce,
                    pre=pre,
                    post=post,
                    provider_pid=provider_pid,
                    process_rows=state["process_rows"],
                    session_id=_process_session_id(int(pre["pid"])),
                    shell_session_id=_process_session_id(int(ready_data["pid"])),
                    provider_session_id=state["provider_session_id"],
                    provider_entrypoint_sha256=sha256_file(runtime.entrypoint),
                )
                text = log.read_text(encoding="utf-8", errors="replace")
                diagnostic_text = text + error_log.read_text(encoding="utf-8", errors="replace")
                stdout_line = _observed_exact_line(text, expected)
                stdout_captured_at = utc_now()
                rendered_line = _wait_for(
                    lambda hwnd=int(post["hwnd"]), expected=expected: (
                        _readback_exact_ack(hwnd, expected)
                    ),
                    10,
                )
                rendered_captured_at = utc_now()
                while rendered_captured_at <= stdout_captured_at:
                    time.sleep(0.001)
                    rendered_captured_at = utc_now()
                agent_completed_at = utc_now()
                if (
                    done_data.get("exit_code") != 0
                    or stdout_line is None
                    or rendered_line is None
                    or _provider_failure_observed(diagnostic_text)
                ):
                    raise ProducerError("provider_ack_failed")
                pin = provider_pins()[provider]
                agents.append(
                    {
                        "provider": provider,
                        "role": role,
                        "nonce": nonce,
                        "nonce_sha256": sha256_bytes(nonce.encode()),
                        "expected_sha256": sha256_bytes(expected.encode()),
                        "started_at_utc": utc_text(state["started_at"]),
                        "completed_at_utc": utc_text(agent_completed_at),
                        "observed_acks": build_ack_observations(
                            stdout_line=stdout_line,
                            stdout_captured_at=stdout_captured_at,
                            rendered_line=rendered_line,
                            rendered_captured_at=rendered_captured_at,
                        ),
                        "status": "pass",
                        "provider_outcome": {"auth_failed": False, "quota_exceeded": False},
                        "invocation": {
                            "provider": provider,
                            "exit_code": 0,
                            "requested_auth_mode": "api-key",
                            "credential_env_allowlist": [PROVIDERS[provider].credential],
                            "argv_policy": PROVIDER_PROJECTIONS[provider],
                            "actual_argv_projection": state["actual_argv_projection"],
                            "actual_environment_names": state["actual_environment_names"],
                            "observed_cli_version": pin["expected_cli_version"],
                            "observed_help_sha256": pin["expected_help_sha256"],
                            "observed_entrypoint_sha256": sha256_file(runtime.entrypoint),
                            "observed_provider_exe_name": Path(
                                runtime.command_prefix[0]
                            ).name,
                        },
                        "producer_guard_assertion": guard,
                    }
                )
            completed = utc_now()
            launched_terminal_pids = {int(state["pre"]["pid"]) for state in states}
            disposable_host_proof = assert_disposable_terminal_host(
                preexisting_terminal_pids=preexisting_terminal_pids,
                launched_terminal_pids=launched_terminal_pids,
                launched_titles=set(launched_titles),
                observed_terminal_windows=sc_cli.list_window_records(query="", limit=300),
            )
            # Terminal roots are only eligible for termination after proving
            # that no pre-existing or concurrently opened terminal shares them.
            spawn_roots.extend(launched_terminal_pids)
            validate_interval(started, completed, now=completed)
            result = {
                "schema": RUNG_SCHEMA,
                "run_id": run_id,
                "verdict": "PASS",
                "agent_count": agent_count,
                "provider_counts": RUNGS[agent_count],
                "logical_simulation": False,
                "visible_windows": True,
                "disposable_terminal_host_proof": disposable_host_proof,
                "started_at_utc": utc_text(started),
                "completed_at_utc": utc_text(completed),
                "cli_invocation_accounting": {"cli_invocations_total": len(agents)},
                "agents": agents,
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return result
        finally:
            try:
                cleanup_process_tree(sorted(set(spawn_roots)))
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()


def produce_bundle(output_dir: Path, *, timeout_s: float) -> None:
    requested_runner_config = _requested_runner_config()
    identity = verify_checkout(ROOT)
    verify_provider_home_isolation(os.environ)
    pins = verify_provider_pins(os.environ)
    if not all(identity[name] for name in ("git_config_cleared", "python_env_cleared")):
        raise ProducerError("producer_environment_not_cleared")
    if output_dir.exists():
        raise ProducerError("output_already_exists")
    output_dir.mkdir(parents=True)
    rows = []
    for count in RUNGS:
        path = output_dir / f"rung-{count}.json"
        run_rung(count, path, timeout_s=timeout_s)
        rows.append(
            {
                "agent_count": count,
                "file": path.name,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    manifest = {
        "schema": SCHEMA,
        "generated_at": utc_text(utc_now()),
        "requested_runner_config": requested_runner_config,
        "code_identity": identity,
        "provider_pins": pins,
        "rungs": rows,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=1200.0)
    args = parser.parse_args(argv)
    try:
        produce_bundle(args.output_dir.resolve(), timeout_s=args.timeout)
    except (ProducerError, OSError, ValueError, psutil.Error) as exc:
        print(json.dumps({"schema": SCHEMA, "ok": False, "status": str(exc)}))
        return 2
    print(json.dumps({"schema": SCHEMA, "ok": True, "status": "produced"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
