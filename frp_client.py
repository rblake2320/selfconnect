"""
FRP Client SDK — Failure Remediation Protocol client for AI agents.

Provides lookup and contribution of verified fix paths from the aihangout.ai
pathbook registry. Any agent can query known-fix paths before burning tokens
guessing at recovery from known failures.

Public API:
    lookup_frp(error_text, env_class) -> dict | None
    contribute_frp(title, error_text, env_class, ...) -> dict
    compute_fingerprint(error_text, env_class) -> str
    detect_env_class() -> str
    FRPSession — context manager that auto-looks-up fixes on exception
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import ssl
import subprocess
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any, TypedDict

__all__ = [  # noqa: RUF022
    "lookup_frp",
    "contribute_frp",
    "compute_fingerprint",
    "detect_env_class",
    "FRPSession",
    "FRPEntry",
]

# ── Configuration ────────────────────────────────────────────────────────────

API_BASE = "https://aihangout.ai/api/pathbooks"
LOOKUP_TIMEOUT = 5
CONTRIBUTE_TIMEOUT = 10
_HEADERS_BASE = {
    "Content-Type": "application/json",
    "User-Agent": "FRP-Client/1.0 (SelfConnect SDK)",
    "Origin": "https://aihangout.ai",
}


# ── Types ────────────────────────────────────────────────────────────────────


class FRPEntry(TypedDict, total=False):
    """Schema for an FRP pathbook entry returned from the API."""

    id: int
    pathbook_id: str
    protocol_version: str
    title: str
    summary: str
    status: str
    trust_tier: str
    ecosystem: str
    runtime: str
    package_name: str
    error_fingerprint: str
    error_signature: str
    trigger_yaml: str
    remediation_yaml: str
    verify_yaml: str
    failed_attempts_yaml: str
    provenance: dict | None
    signature: str
    source_type: str
    source_url: str
    times_applied: int
    times_succeeded: int
    confidence: float
    token_savings_estimate: int
    created_at: str
    updated_at: str
    match_score: float


# ── Fingerprint ──────────────────────────────────────────────────────────────


def compute_fingerprint(error_text: str, env_class: str) -> str:
    """
    Compute SHA-256 fingerprint of normalized error text + env_class.

    Normalization replaces:
      - Windows/Unix file paths -> <PATH>
      - Line numbers -> line <LINE>
      - PIDs -> pid <PID>
      - Hex addresses -> <ADDR>
      - ISO timestamps -> <TIME>
    """
    normalized = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<PATH>", error_text)
    normalized = re.sub(r"/[^\s\"']+", "<PATH>", normalized)
    normalized = re.sub(r"\bline \d+\b", "line <LINE>", normalized)
    normalized = re.sub(r"\bpid \d+\b", "pid <PID>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b0x[0-9a-f]+\b", "<ADDR>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "<TIME>", normalized
    )
    normalized = normalized.strip().lower()
    content = f"{normalized}|{env_class}"
    return hashlib.sha256(content.encode()).hexdigest()


# ── Environment Detection ────────────────────────────────────────────────────


def detect_env_class() -> str:
    """Auto-detect the current runtime environment class."""
    system = platform.system().lower()

    if system == "windows":
        # Check if running inside WSL (unlikely on native Windows but be safe)
        shell = os.environ.get("SHELL", "")
        comspec = os.environ.get("COMSPEC", "")
        # Git Bash / MSYS2 / Cygwin
        if "/bin/bash" in shell or "MSYSTEM" in os.environ:
            return "windows-bash"
        if "cmd.exe" in comspec.lower() and not shell:
            return "windows-cmd"
        # PowerShell detection
        if os.environ.get("PSModulePath"):  # noqa: SIM112
            return "windows-powershell"
        return "windows-bash"  # default for Claude Code (uses bash)

    if system == "linux":
        # Check WSL
        try:
            with open("/proc/version") as f:
                version_info = f.read().lower()
            if "microsoft" in version_info or "wsl" in version_info:
                return "wsl2"
        except OSError:
            pass
        return "linux-bash"

    if system == "darwin":
        return "macos-bash"

    return f"{system}-unknown"


# ── SSL Context (handle Windows cert issues gracefully) ──────────────────────


def _make_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that works on Windows without cert errors."""
    ctx = ssl.create_default_context()
    return ctx


# ── Lookup ───────────────────────────────────────────────────────────────────


def lookup_frp(
    error_text: str,
    env_class: str | None = None,
    *,
    include_drafts: bool = False,
    min_match_score: float = 0.1,
) -> FRPEntry | None:
    """
    Query aihangout.ai for a verified fix path matching the error.

    Returns an FRPEntry dict if found with acceptable trust tier, else None.
    NEVER raises — all network/parse errors return None silently.

    Args:
        error_text: The error message or traceback text.
        env_class: Environment class (auto-detected if not provided).
        include_drafts: If True, also return draft-tier entries.
        min_match_score: Minimum match_score (0.0-1.0) for text-search fallback results.
            Fingerprint matches are always returned regardless of score.
            Default 0.1 rejects clearly unrelated entries the API returns as fallbacks.
    """
    if not error_text:
        return None

    if env_class is None:
        env_class = detect_env_class()

    fingerprint = compute_fingerprint(error_text, env_class)

    try:
        payload = json.dumps({
            "error_fingerprint": fingerprint,
            "runtime": env_class,
        }).encode()

        req = urllib.request.Request(
            f"{API_BASE}/lookup",
            data=payload,
            headers=_HEADERS_BASE,
            method="POST",
        )
        ctx = _make_ssl_context()
        with urllib.request.urlopen(req, timeout=LOOKUP_TIMEOUT, context=ctx) as resp:
            data = json.loads(resp.read())

        pathbooks = data.get("pathbooks", [])
        if pathbooks:
            entry = pathbooks[0]
            # Self-validate: server may return all entries on a fingerprint miss.
            # Reject if the returned entry's fingerprint doesn't match what we asked for.
            returned_fp = entry.get("error_fingerprint", "")
            if returned_fp and returned_fp != fingerprint:
                pass  # fall through to text-based fallback
            else:
                trust = entry.get("trust_tier", "draft")
                if include_drafts or trust != "draft":
                    return entry
    except Exception:
        pass

    # Fallback: text-based search (with match_score guard to prevent false positives)
    try:
        payload = json.dumps({
            "error": error_text[:500],
            "runtime": env_class,
        }).encode()

        req = urllib.request.Request(
            f"{API_BASE}/lookup",
            data=payload,
            headers=_HEADERS_BASE,
            method="POST",
        )
        ctx = _make_ssl_context()
        with urllib.request.urlopen(req, timeout=LOOKUP_TIMEOUT, context=ctx) as resp:
            data = json.loads(resp.read())

        pathbooks = data.get("pathbooks", [])
        if pathbooks:
            entry = pathbooks[0]
            trust = entry.get("trust_tier", "draft")
            score = entry.get("match_score", 0.0) or 0.0
            # Reject low-confidence fallback matches — the API always returns something,
            # but a score below min_match_score means the match is coincidental.
            if score < min_match_score:
                return None
            if include_drafts or trust != "draft":
                return entry
    except Exception:
        pass

    return None


# ── Contribute ───────────────────────────────────────────────────────────────


def contribute_frp(
    title: str,
    error_text: str,
    env_class: str,
    failed_attempts: list[dict[str, Any]],
    fix_steps: list[dict[str, Any]],
    verify_steps: list[dict[str, Any]] | None = None,
    *,
    jwt: str | None = None,
    pathbook_id: str | None = None,
    source_type: str = "agent_log",
    token_savings_estimate: int = 0,
) -> dict[str, Any]:
    """
    Submit a new FRP entry to the registry.

    Raises on network/auth failure (unlike lookup_frp which never raises).

    Args:
        title: Human-readable name for this fix.
        error_text: The error signature text.
        env_class: Environment class where this fix applies.
        failed_attempts: List of dicts with attempt/command/error keys.
        fix_steps: List of dicts with step/action/command keys.
        verify_steps: List of dicts with check/command keys.
        jwt: Auth token. If None, tries anonymous (will likely 401).
        pathbook_id: Custom ID (auto-generated if absent).
        source_type: Origin of this entry (agent_log, so_import, etc).
        token_savings_estimate: Estimated tokens saved per application.
    """
    fingerprint = compute_fingerprint(error_text, env_class)

    # Build YAML-compatible strings (using json for simplicity — API accepts it)
    trigger_yaml = json.dumps({
        "error_signature": error_text[:500],
        "env_class": env_class,
    })
    remediation_yaml = json.dumps({
        "steps": fix_steps,
        "verify": verify_steps or [],
    })
    verify_yaml = json.dumps(verify_steps or [])
    failed_yaml = json.dumps(failed_attempts)

    payload: dict[str, Any] = {
        "title": title,
        "error_signature": error_text[:500],
        "error_fingerprint": fingerprint,
        "runtime": env_class,
        "trigger_yaml": trigger_yaml,
        "remediation_yaml": remediation_yaml,
        "verify_yaml": verify_yaml,
        "failed_attempts_yaml": failed_yaml,
        "source_type": source_type,
        "trust_tier": "draft",
        "status": "draft",
        "token_savings_estimate": token_savings_estimate,
    }
    if pathbook_id:
        payload["pathbook_id"] = pathbook_id

    data = json.dumps(payload).encode()
    headers: dict[str, str] = {**_HEADERS_BASE}
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"

    req = urllib.request.Request(
        API_BASE,
        data=data,
        headers=headers,
        method="POST",
    )
    ctx = _make_ssl_context()
    with urllib.request.urlopen(req, timeout=CONTRIBUTE_TIMEOUT, context=ctx) as resp:
        return json.loads(resp.read())


# ── FRPSession Context Manager ───────────────────────────────────────────────


@contextmanager
def FRPSession(
    env_class: str | None = None,
    *,
    auto_apply: bool = False,
    include_drafts: bool = False,
):
    """
    Context manager that catches exceptions and looks up FRP fixes.

    Usage:
        with FRPSession() as session:
            # code that might fail
            ...

        # After the block, check session.fix_found
        if session.fix_found:
            print(session.entry)  # The FRP entry with fix steps

    If auto_apply=True and an entry with trust_tier >= 'reproduced' is found,
    executes the fix steps automatically (step.action == 'run' only).
    """
    if env_class is None:
        env_class = detect_env_class()

    session = _FRPSessionState(env_class=env_class)

    try:
        yield session
    except Exception as exc:
        session.exception = exc
        error_text = str(exc)

        entry = lookup_frp(error_text, env_class, include_drafts=include_drafts)
        if entry:
            session.entry = entry
            session.fix_found = True

            if auto_apply and entry.get("trust_tier") not in ("draft", "dangerous"):
                session.applied = _apply_fix(entry)
        else:
            # Re-raise if no fix found — don't swallow unknown errors
            raise


class _FRPSessionState:
    """Internal state holder for FRPSession context manager."""

    __slots__ = ("applied", "entry", "env_class", "exception", "fix_found")

    def __init__(self, env_class: str):
        self.env_class = env_class
        self.exception: Exception | None = None
        self.entry: FRPEntry | None = None
        self.fix_found: bool = False
        self.applied: bool = False


def _apply_fix(entry: FRPEntry) -> bool:
    """
    Attempt to execute fix steps from an FRP entry.
    Only executes 'run' action steps. Returns True if all steps succeeded.
    """
    remediation = entry.get("remediation_yaml", "")
    if not remediation:
        return False

    try:
        fix_data = json.loads(remediation)
    except (json.JSONDecodeError, TypeError):
        return False

    steps = fix_data.get("steps", [])
    for step in steps:
        if step.get("action") != "run":
            continue
        command = step.get("command", "")
        if not command:
            continue
        try:
            result = subprocess.run(  # noqa: S602
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return False
        except (subprocess.TimeoutExpired, OSError):
            return False

    return True
