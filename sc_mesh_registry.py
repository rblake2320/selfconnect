"""Local mesh registry for SelfConnect agent windows.

This is a lightweight sidecar registry. It does not send messages and does not
claim every terminal belongs to the active mesh. Agents must explicitly register
their role/window/task.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import sc_cli

REGISTRY_VERSION = 1
DEFAULT_MESH = "default"
DEFAULT_PROFILE = "explore"
VALID_PROFILES = {"explore", "governed"}
STALE_HEARTBEAT_SECONDS = 15 * 60
OLD_SESSION_SECONDS = 2 * 60 * 60
VERY_OLD_SESSION_SECONDS = 4 * 60 * 60
HIGH_TOKEN_ESTIMATE = 120_000
VERY_HIGH_TOKEN_ESTIMATE = 180_000


def default_registry_path() -> Path:
    root = os.environ.get("SELFCONNECT_MESH_DIR")
    if root:
        return Path(root) / "mesh_registry.json"
    local = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(local) / "SelfConnect" / "mesh_registry.json"


def default_handoff_dir() -> Path:
    root = os.environ.get("SELFCONNECT_HANDOFF_DIR")
    if root:
        return Path(root)
    local = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(local) / "SelfConnect" / "handoffs"


def _now() -> float:
    return time.time()


def _birth_id(role: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "-" for ch in role.strip()).strip("-")
    clean = clean or "agent"
    return f"{clean}-{uuid.uuid4().hex[:8]}"


def _slug(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return clean.lower() or "agent"


def _window_fingerprint(*, hwnd: int, pid: int, class_name: str, title: str) -> str:
    import hashlib

    payload = f"{int(hwnd)}|{int(pid)}|{class_name}|{title}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def _empty_registry() -> dict[str, Any]:
    return {"version": REGISTRY_VERSION, "updated_at": _now(), "agents": []}


def _normalize_profile(profile: str | None) -> str:
    value = (profile or DEFAULT_PROFILE).strip().lower()
    if value not in VALID_PROFILES:
        raise ValueError(f"profile must be one of: {', '.join(sorted(VALID_PROFILES))}")
    return value


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    registry_path = Path(path) if path else default_registry_path()
    if not registry_path.exists():
        return _empty_registry()
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_registry()
    if not isinstance(data, dict):
        return _empty_registry()
    data.setdefault("version", REGISTRY_VERSION)
    data.setdefault("updated_at", _now())
    data.setdefault("agents", [])
    for agent in data["agents"]:
        if isinstance(agent, dict):
            agent.setdefault("profile", DEFAULT_PROFILE)
            agent.setdefault("birth_id", _birth_id(str(agent.get("role", "agent"))))
            agent.setdefault("generation", 1)
            agent.setdefault("created_at", agent.get("last_seen", data["updated_at"]))
            agent.setdefault("token_estimate", None)
            agent.setdefault("compact_count", 0)
            agent.setdefault("missed_acks", 0)
            if "window_fingerprint" not in agent:
                agent["window_fingerprint"] = _window_fingerprint(
                    hwnd=int(agent.get("hwnd", 0)),
                    pid=int(agent.get("pid", 0)),
                    class_name=str(agent.get("class_name", "")),
                    title=str(agent.get("title", "")),
                )
    return data


def save_registry(registry: dict[str, Any], path: str | Path | None = None) -> Path:
    registry_path = Path(path) if path else default_registry_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry["updated_at"] = _now()
    tmp = registry_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(registry_path)
    return registry_path


def infer_agent_type(title: str, exe_name: str = "") -> str:
    text = f"{title} {exe_name}".lower()
    if "codex" in text:
        return "codex"
    if "claude" in text or "ckaude" in text:
        return "claude"
    if "gemini" in text:
        return "gemini"
    return "unknown"


def scan_candidates(query: str = "", *, terminal_only: bool = True, limit: int = 100) -> list[dict[str, Any]]:
    records = sc_cli.list_window_records(query=query, limit=limit)
    candidates: list[dict[str, Any]] = []
    for record in records:
        if terminal_only and record["class_name"] not in sc_cli.TERMINAL_CLASSES:
            continue
        item = dict(record)
        item["agent"] = infer_agent_type(item["title"], item["exe_name"])
        item["is_terminal"] = item["class_name"] in sc_cli.TERMINAL_CLASSES
        candidates.append(item)
    return candidates


def _find_agent(registry: dict[str, Any], mesh: str, role: str) -> dict[str, Any] | None:
    for agent in registry.get("agents", []):
        if agent.get("mesh") == mesh and agent.get("role") == role:
            return agent
    return None


def register_agent(
    hwnd: int,
    role: str,
    *,
    mesh: str = DEFAULT_MESH,
    agent_type: str = "",
    task: str = "",
    status: str = "active",
    profile: str = DEFAULT_PROFILE,
    label: str = "",
    notes: str = "",
    replace: bool = False,
    registry_path: str | Path | None = None,
    expected_pid: int | None = None,
    expected_exe: str = "",
    expected_class: str = "",
    expected_title: str = "",
    allow_non_terminal: bool = False,
) -> dict[str, Any]:
    if not role.strip():
        return {"ok": False, "error": "role is required"}
    try:
        normalized_profile = _normalize_profile(profile)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    guard = sc_cli.verify_target(
        hwnd,
        expected_pid=expected_pid,
        expected_exe=expected_exe,
        expected_class=expected_class,
        expected_title=expected_title,
        require_terminal=not allow_non_terminal,
        require_expectation=bool(expected_pid or expected_exe or expected_class or expected_title),
    )
    if not guard["valid"]:
        return {"ok": False, "error": "target invalid", "guard": guard}
    if not allow_non_terminal and not guard["is_terminal"]:
        return {"ok": False, "error": "target is not a terminal", "guard": guard}
    if guard["checks"] and not guard["ok"]:
        return {"ok": False, "error": "target expectation mismatch", "guard": guard}

    registry = load_registry(registry_path)
    existing = _find_agent(registry, mesh, role)
    if existing and int(existing.get("hwnd", 0)) != int(hwnd) and not replace:
        return {
            "ok": False,
            "error": "role already registered with a different hwnd",
            "existing": existing,
            "hint": "use --replace or choose a unique role",
        }

    actual = guard["actual"]
    same_window = bool(existing and int(existing.get("hwnd", 0)) == int(hwnd))
    generation = int(existing.get("generation", 0)) if existing and same_window else int(existing.get("generation", 0)) + 1 if existing else 1
    birth_id = str(existing.get("birth_id")) if existing and same_window else _birth_id(role)
    created_at = float(existing.get("created_at", _now())) if existing and same_window else _now()
    record = {
        "mesh": mesh,
        "role": role,
        "agent": agent_type or infer_agent_type(actual["title"], actual["exe_name"]),
        "label": label or role,
        "birth_id": birth_id,
        "generation": generation,
        "created_at": created_at,
        "hwnd": int(hwnd),
        "pid": actual["pid"],
        "exe_name": actual["exe_name"],
        "class_name": actual["class_name"],
        "title": actual["title"],
        "window_fingerprint": _window_fingerprint(
            hwnd=int(hwnd),
            pid=int(actual["pid"]),
            class_name=str(actual["class_name"]),
            title=str(actual["title"]),
        ),
        "task": task,
        "status": status,
        "profile": normalized_profile,
        "notes": notes,
        "session_id": guard["session_id"],
        "is_terminal": guard["is_terminal"],
        "last_seen": _now(),
    }
    if existing:
        existing.update(record)
    else:
        registry["agents"].append(record)

    saved = save_registry(registry, registry_path)
    return {"ok": True, "path": str(saved), "agent": record}


def register_virtual_agent(
    role: str,
    *,
    mesh: str = DEFAULT_MESH,
    agent_type: str = "local_model",
    task: str = "",
    status: str = "standby",
    profile: str = DEFAULT_PROFILE,
    label: str = "",
    notes: str = "",
    transport: str = "mailbox",
    endpoint: str = "",
    model: str = "",
    replace: bool = False,
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    """Register an addressable mesh participant that has no live HWND.

    This is for durable local model roles and other non-window endpoints. It
    deliberately does not grant input authority; it only gives the role a mesh
    identity, birth_id, generation, and heartbeatable registry row.
    """
    if not role.strip():
        return {"ok": False, "error": "role is required"}
    try:
        normalized_profile = _normalize_profile(profile)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    registry = load_registry(registry_path)
    existing = _find_agent(registry, mesh, role)
    if existing and not replace and str(existing.get("transport", "")) != transport:
        return {
            "ok": False,
            "error": "role already registered with a different transport",
            "existing": existing,
            "hint": "use --replace or choose a unique role",
        }

    same_virtual = bool(existing and int(existing.get("hwnd", 0) or 0) == 0)
    generation = (
        int(existing.get("generation", 0))
        if existing and same_virtual
        else int(existing.get("generation", 0)) + 1
        if existing
        else 1
    )
    birth_id = str(existing.get("birth_id")) if existing and same_virtual else _birth_id(role)
    created_at = float(existing.get("created_at", _now())) if existing and same_virtual else _now()
    title = label or role
    record = {
        "mesh": mesh,
        "role": role,
        "agent": agent_type,
        "label": title,
        "birth_id": birth_id,
        "generation": generation,
        "created_at": created_at,
        "hwnd": 0,
        "pid": 0,
        "exe_name": "",
        "class_name": "virtual",
        "title": title,
        "window_fingerprint": _window_fingerprint(
            hwnd=0,
            pid=0,
            class_name="virtual",
            title=title,
        ),
        "task": task,
        "status": status,
        "profile": normalized_profile,
        "notes": notes,
        "session_id": None,
        "is_terminal": False,
        "transport": transport,
        "endpoint": endpoint,
        "model": model,
        "last_seen": _now(),
    }
    if existing:
        existing.update(record)
    else:
        registry["agents"].append(record)

    saved = save_registry(registry, registry_path)
    return {"ok": True, "path": str(saved), "agent": record}


def update_agent(
    role: str,
    *,
    mesh: str = DEFAULT_MESH,
    task: str | None = None,
    status: str | None = None,
    profile: str | None = None,
    notes: str | None = None,
    token_estimate: int | None = None,
    compact_count: int | None = None,
    missed_acks: int | None = None,
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    existing = _find_agent(registry, mesh, role)
    if not existing:
        return {"ok": False, "error": "role not registered"}
    if task is not None:
        existing["task"] = task
    if status is not None:
        existing["status"] = status
    if profile is not None:
        try:
            existing["profile"] = _normalize_profile(profile)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    if notes is not None:
        existing["notes"] = notes
    if token_estimate is not None:
        existing["token_estimate"] = max(0, int(token_estimate))
    if compact_count is not None:
        existing["compact_count"] = max(0, int(compact_count))
    if missed_acks is not None:
        existing["missed_acks"] = max(0, int(missed_acks))
    existing["last_seen"] = _now()
    saved = save_registry(registry, registry_path)
    return {"ok": True, "path": str(saved), "agent": existing}


def heartbeat(role: str, *, mesh: str = DEFAULT_MESH, registry_path: str | Path | None = None) -> dict[str, Any]:
    registry = load_registry(registry_path)
    existing = _find_agent(registry, mesh, role)
    if not existing:
        return {"ok": False, "error": "role not registered"}
    if int(existing.get("hwnd", 0) or 0) == 0 or existing.get("class_name") == "virtual":
        existing["last_seen"] = _now()
        existing["guard_ok"] = None
        existing["guard_reasons"] = []
        saved = save_registry(registry, registry_path)
        return {"ok": True, "path": str(saved), "agent": existing, "guard": None}
    guard = sc_cli.verify_target(
        int(existing["hwnd"]),
        expected_pid=int(existing["pid"]),
        expected_class=existing["class_name"],
        require_expectation=True,
        require_terminal=bool(existing.get("is_terminal", True)),
    )
    existing["last_seen"] = _now()
    existing["guard_ok"] = guard["ok"]
    existing["guard_reasons"] = guard["reasons"]
    saved = save_registry(registry, registry_path)
    return {"ok": guard["ok"], "path": str(saved), "agent": existing, "guard": guard}


def remove_agent(role: str, *, mesh: str = DEFAULT_MESH, registry_path: str | Path | None = None) -> dict[str, Any]:
    registry = load_registry(registry_path)
    before = len(registry.get("agents", []))
    registry["agents"] = [
        agent for agent in registry.get("agents", [])
        if not (agent.get("mesh") == mesh and agent.get("role") == role)
    ]
    saved = save_registry(registry, registry_path)
    return {"ok": len(registry["agents"]) != before, "path": str(saved), "removed": before - len(registry["agents"])}


def evaluate_sharpness(agent: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Compute a practical drift/sharpness risk from registry-visible signals."""
    current = _now() if now is None else now
    created_at = float(agent.get("created_at", current))
    last_seen = float(agent.get("last_seen", created_at))
    age_seconds = max(0.0, current - created_at)
    heartbeat_age_seconds = max(0.0, current - last_seen)
    status = str(agent.get("status", "")).lower()
    token_estimate = agent.get("token_estimate")
    compact_count = int(agent.get("compact_count") or 0)
    missed_acks = int(agent.get("missed_acks") or 0)

    score = 0
    reasons: list[str] = []
    if age_seconds >= VERY_OLD_SESSION_SECONDS:
        score += 3
        reasons.append("session_age>=4h")
    elif age_seconds >= OLD_SESSION_SECONDS:
        score += 1
        reasons.append("session_age>=2h")

    if heartbeat_age_seconds >= STALE_HEARTBEAT_SECONDS:
        score += 2
        reasons.append("heartbeat_stale>=15m")

    if status in {"off_rails", "blocked", "stuck"}:
        score += 4
        reasons.append(f"status={status}")
    elif status in {"degraded", "compacting"}:
        score += 2
        reasons.append(f"status={status}")

    if compact_count >= 2:
        score += 2
        reasons.append("compact_count>=2")
    elif compact_count == 1:
        score += 1
        reasons.append("compact_count=1")

    if missed_acks >= 2:
        score += 3
        reasons.append("missed_acks>=2")
    elif missed_acks == 1:
        score += 1
        reasons.append("missed_acks=1")

    if token_estimate is not None:
        tokens = int(token_estimate)
        if tokens >= VERY_HIGH_TOKEN_ESTIMATE:
            score += 3
            reasons.append("tokens>=180k")
        elif tokens >= HIGH_TOKEN_ESTIMATE:
            score += 1
            reasons.append("tokens>=120k")

    if score >= 5:
        risk = "red"
        action = "compact_or_replace"
    elif score >= 2:
        risk = "yellow"
        action = "checkpoint_and_probe"
    else:
        risk = "green"
        action = "continue"

    return {
        "role": agent.get("role", ""),
        "birth_id": agent.get("birth_id", ""),
        "status": agent.get("status", ""),
        "age_seconds": round(age_seconds, 3),
        "heartbeat_age_seconds": round(heartbeat_age_seconds, 3),
        "token_estimate": token_estimate,
        "compact_count": compact_count,
        "missed_acks": missed_acks,
        "risk": risk,
        "score": score,
        "action": action,
        "reasons": reasons,
    }


def health_report(registry: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    current = _now() if now is None else now
    items = [evaluate_sharpness(agent, now=current) for agent in registry.get("agents", [])]
    counts = {risk: sum(1 for item in items if item["risk"] == risk) for risk in ("green", "yellow", "red")}
    return {"generated_at": current, "counts": counts, "agents": items}


def _git_value(repo_path: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def repo_snapshot(repo_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(repo_path or Path.cwd()).resolve()
    branch = _git_value(path, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _git_value(path, "rev-parse", "--short", "HEAD")
    status = _git_value(path, "status", "--short", "--branch")
    return {
        "path": str(path),
        "is_git": bool(branch and commit),
        "branch": branch,
        "commit": commit,
        "status": status,
        "dirty": bool("\n" in status or (status and not status.startswith("## "))),
    }


def _format_agent_line(agent: dict[str, Any], health: dict[str, Any]) -> str:
    return (
        f"- {agent.get('role', '')}: birth={agent.get('birth_id', '')} "
        f"status={agent.get('status', '')} risk={health.get('risk', '')} "
        f"hwnd={agent.get('hwnd', '')} task={agent.get('task', '')}"
    )


def write_compact_handoff(
    role: str,
    *,
    mesh: str = DEFAULT_MESH,
    summary: str = "",
    next_action: str = "",
    tests: str = "",
    repo_path: str | Path | None = None,
    handoff_dir: str | Path | None = None,
    status: str = "handoff",
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    existing = _find_agent(registry, mesh, role)
    if not existing:
        return {"ok": False, "error": "role not registered"}

    current = _now()
    health = evaluate_sharpness(existing, now=current)
    snapshot = repo_snapshot(repo_path)
    handoffs = Path(handoff_dir) if handoff_dir else default_handoff_dir()
    handoffs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(current).strftime("%Y%m%d-%H%M%S")
    filename = f"{stamp}-{_slug(role)}-{_slug(str(existing.get('birth_id', '')))}.md"
    path = handoffs / filename

    all_health = {item["role"]: item for item in health_report(registry, now=current)["agents"]}
    peer_lines = [
        _format_agent_line(agent, all_health.get(str(agent.get("role", "")), {}))
        for agent in registry.get("agents", [])
    ]
    content = "\n".join([
        f"# SelfConnect Compact Handoff - {role}",
        "",
        f"- generated_at: {datetime.fromtimestamp(current).isoformat(timespec='seconds')}",
        f"- mesh: {mesh}",
        f"- role: {existing.get('role', '')}",
        f"- birth_id: {existing.get('birth_id', '')}",
        f"- generation: {existing.get('generation', '')}",
        f"- agent: {existing.get('agent', '')}",
        f"- profile: {existing.get('profile', '')}",
        f"- status: {existing.get('status', '')}",
        f"- health_risk: {health.get('risk', '')}",
        f"- health_action: {health.get('action', '')}",
        f"- hwnd: {existing.get('hwnd', '')}",
        f"- pid: {existing.get('pid', '')}",
        f"- class: {existing.get('class_name', '')}",
        f"- title: {existing.get('title', '')}",
        "",
        "## Current Task",
        str(existing.get("task", "")),
        "",
        "## Summary",
        summary or "No summary provided.",
        "",
        "## Next Action",
        next_action or "No next action provided.",
        "",
        "## Tests / Validation",
        tests or "Not reported.",
        "",
        "## Repo Snapshot",
        f"- path: {snapshot['path']}",
        f"- is_git: {snapshot['is_git']}",
        f"- branch: {snapshot['branch']}",
        f"- commit: {snapshot['commit']}",
        f"- dirty: {snapshot['dirty']}",
        "",
        "```text",
        snapshot["status"],
        "```",
        "",
        "## Mesh Snapshot",
        *peer_lines,
        "",
    ])
    path.write_text(content, encoding="utf-8")

    existing["last_handoff_path"] = str(path)
    existing["last_handoff_at"] = current
    existing["compact_count"] = int(existing.get("compact_count") or 0) + 1
    existing["last_seen"] = current
    if status:
        existing["status"] = status
    saved = save_registry(registry, registry_path)
    return {"ok": True, "path": str(path), "registry_path": str(saved), "agent": existing, "health": health, "repo": snapshot}


def watch_report(registry: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    current = _now() if now is None else now
    health_items = {item["role"]: item for item in health_report(registry, now=current)["agents"]}
    rows: list[dict[str, Any]] = []
    for agent in registry.get("agents", []):
        health = health_items.get(str(agent.get("role", "")), {})
        rows.append({
            "role": agent.get("role", ""),
            "birth_id": agent.get("birth_id", ""),
            "agent": agent.get("agent", ""),
            "profile": agent.get("profile", DEFAULT_PROFILE),
            "status": agent.get("status", ""),
            "risk": health.get("risk", ""),
            "age_seconds": health.get("age_seconds", 0),
            "heartbeat_age_seconds": health.get("heartbeat_age_seconds", 0),
            "hwnd": agent.get("hwnd", 0),
            "task": agent.get("task", ""),
            "action": health.get("action", ""),
        })
    return {"generated_at": current, "agents": rows}


def _print_json(data: Any) -> int:
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


def _print_agents(registry: dict[str, Any]) -> int:
    print(f"{'mesh':<12} {'role':<18} {'birth':<14} {'agent':<8} {'profile':<8} {'status':<10} {'hwnd':>12}  task")
    print("-" * 116)
    for agent in registry.get("agents", []):
        birth = str(agent.get("birth_id", ""))[:14]
        print(
            f"{agent.get('mesh', ''):<12} {agent.get('role', ''):<18} "
            f"{birth:<14} "
            f"{agent.get('agent', ''):<8} {agent.get('profile', DEFAULT_PROFILE):<8} "
            f"{agent.get('status', ''):<10} "
            f"{int(agent.get('hwnd', 0)):>12}  {agent.get('task', '')[:40]}"
        )
    return 0


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.0f}m"


def _print_health(report: dict[str, Any]) -> int:
    print(
        f"{'role':<18} {'risk':<6} {'age':>6} {'idle':>6} "
        f"{'tokens':>9} {'comp':>5} {'miss':>5} action"
    )
    print("-" * 86)
    for item in report.get("agents", []):
        tokens = item.get("token_estimate")
        token_text = "unknown" if tokens is None else str(tokens)
        print(
            f"{item.get('role', ''):<18} {item.get('risk', ''):<6} "
            f"{_minutes(float(item.get('age_seconds', 0))):>6} "
            f"{_minutes(float(item.get('heartbeat_age_seconds', 0))):>6} "
            f"{token_text:>9} {int(item.get('compact_count', 0)):>5} "
            f"{int(item.get('missed_acks', 0)):>5} {item.get('action', '')}"
        )
    return 0


def _print_watch(report: dict[str, Any]) -> int:
    print(
        f"{'role':<18} {'birth':<14} {'agent':<8} {'risk':<6} {'status':<10} "
        f"{'age':>6} {'idle':>6} {'hwnd':>12}  task"
    )
    print("-" * 122)
    for item in report.get("agents", []):
        print(
            f"{item.get('role', ''):<18} {str(item.get('birth_id', ''))[:14]:<14} "
            f"{item.get('agent', ''):<8} {item.get('risk', ''):<6} "
            f"{item.get('status', ''):<10} "
            f"{_minutes(float(item.get('age_seconds', 0))):>6} "
            f"{_minutes(float(item.get('heartbeat_age_seconds', 0))):>6} "
            f"{int(item.get('hwnd', 0)):>12}  {str(item.get('task', ''))[:44]}"
        )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="selfconnect-mesh", description="Track SelfConnect mesh windows and tasks")
    parser.add_argument("--registry", default="", help="override registry JSON path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan")
    p.add_argument("--query", default="")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--all", action="store_true", help="include non-terminal windows")

    p = sub.add_parser("list")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("register")
    p.add_argument("--role", required=True)
    p.add_argument("--hwnd", required=True, type=sc_cli.parse_hwnd)
    p.add_argument("--mesh", default=DEFAULT_MESH)
    p.add_argument("--agent", default="")
    p.add_argument("--task", default="")
    p.add_argument("--status", default="active")
    p.add_argument("--profile", choices=sorted(VALID_PROFILES), default=DEFAULT_PROFILE)
    p.add_argument("--label", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--expect-pid", type=int, default=None)
    p.add_argument("--expect-exe", default="")
    p.add_argument("--expect-class", default="")
    p.add_argument("--expect-title", default="")
    p.add_argument("--allow-non-terminal", action="store_true")

    p = sub.add_parser("update")
    p.add_argument("--role", required=True)
    p.add_argument("--mesh", default=DEFAULT_MESH)
    p.add_argument("--task", default=None)
    p.add_argument("--status", default=None)
    p.add_argument("--profile", choices=sorted(VALID_PROFILES), default=None)
    p.add_argument("--notes", default=None)
    p.add_argument("--tokens", type=int, default=None, help="manual token estimate for drift tracking")
    p.add_argument("--compact-count", type=int, default=None)
    p.add_argument("--missed-acks", type=int, default=None)

    p = sub.add_parser("heartbeat")
    p.add_argument("--role", required=True)
    p.add_argument("--mesh", default=DEFAULT_MESH)

    p = sub.add_parser("health")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("watch")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("handoff")
    p.add_argument("--role", required=True)
    p.add_argument("--mesh", default=DEFAULT_MESH)
    p.add_argument("--summary", default="")
    p.add_argument("--next", dest="next_action", default="")
    p.add_argument("--tests", default="")
    p.add_argument("--repo", default="")
    p.add_argument("--handoff-dir", default="")
    p.add_argument("--status", default="handoff")

    p = sub.add_parser("remove")
    p.add_argument("--role", required=True)
    p.add_argument("--mesh", default=DEFAULT_MESH)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    registry_path = args.registry or None

    if args.command == "scan":
        return _print_json(scan_candidates(args.query, terminal_only=not args.all, limit=args.limit))
    if args.command == "list":
        registry = load_registry(registry_path)
        return _print_json(registry) if args.json else _print_agents(registry)
    if args.command == "register":
        return _print_json(register_agent(
            args.hwnd,
            args.role,
            mesh=args.mesh,
            agent_type=args.agent,
            task=args.task,
            status=args.status,
            profile=args.profile,
            label=args.label,
            notes=args.notes,
            replace=args.replace,
            registry_path=registry_path,
            expected_pid=args.expect_pid,
            expected_exe=args.expect_exe,
            expected_class=args.expect_class,
            expected_title=args.expect_title,
            allow_non_terminal=args.allow_non_terminal,
        ))
    if args.command == "update":
        return _print_json(update_agent(
            args.role,
            mesh=args.mesh,
            task=args.task,
            status=args.status,
            profile=args.profile,
            notes=args.notes,
            token_estimate=args.tokens,
            compact_count=args.compact_count,
            missed_acks=args.missed_acks,
            registry_path=registry_path,
        ))
    if args.command == "heartbeat":
        return _print_json(heartbeat(args.role, mesh=args.mesh, registry_path=registry_path))
    if args.command == "health":
        report = health_report(load_registry(registry_path))
        return _print_json(report) if args.json else _print_health(report)
    if args.command == "watch":
        report = watch_report(load_registry(registry_path))
        return _print_json(report) if args.json else _print_watch(report)
    if args.command == "handoff":
        return _print_json(write_compact_handoff(
            args.role,
            mesh=args.mesh,
            summary=args.summary,
            next_action=args.next_action,
            tests=args.tests,
            repo_path=args.repo or None,
            handoff_dir=args.handoff_dir or None,
            status=args.status,
            registry_path=registry_path,
        ))
    if args.command == "remove":
        return _print_json(remove_agent(args.role, mesh=args.mesh, registry_path=registry_path))
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
