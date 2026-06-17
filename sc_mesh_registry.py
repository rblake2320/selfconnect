"""Local mesh registry for SelfConnect agent windows.

This is a lightweight sidecar registry. It does not send messages and does not
claim every terminal belongs to the active mesh. Agents must explicitly register
their role/window/task.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import sc_cli

REGISTRY_VERSION = 1
DEFAULT_MESH = "default"
DEFAULT_PROFILE = "explore"
VALID_PROFILES = {"explore", "governed"}


def default_registry_path() -> Path:
    root = os.environ.get("SELFCONNECT_MESH_DIR")
    if root:
        return Path(root) / "mesh_registry.json"
    local = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(local) / "SelfConnect" / "mesh_registry.json"


def _now() -> float:
    return time.time()


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
    record = {
        "mesh": mesh,
        "role": role,
        "agent": agent_type or infer_agent_type(actual["title"], actual["exe_name"]),
        "label": label or role,
        "hwnd": int(hwnd),
        "pid": actual["pid"],
        "exe_name": actual["exe_name"],
        "class_name": actual["class_name"],
        "title": actual["title"],
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


def update_agent(
    role: str,
    *,
    mesh: str = DEFAULT_MESH,
    task: str | None = None,
    status: str | None = None,
    profile: str | None = None,
    notes: str | None = None,
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
    existing["last_seen"] = _now()
    saved = save_registry(registry, registry_path)
    return {"ok": True, "path": str(saved), "agent": existing}


def heartbeat(role: str, *, mesh: str = DEFAULT_MESH, registry_path: str | Path | None = None) -> dict[str, Any]:
    registry = load_registry(registry_path)
    existing = _find_agent(registry, mesh, role)
    if not existing:
        return {"ok": False, "error": "role not registered"}
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


def _print_json(data: Any) -> int:
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


def _print_agents(registry: dict[str, Any]) -> int:
    print(f"{'mesh':<12} {'role':<18} {'agent':<8} {'profile':<8} {'status':<10} {'hwnd':>12}  task")
    print("-" * 100)
    for agent in registry.get("agents", []):
        print(
            f"{agent.get('mesh', ''):<12} {agent.get('role', ''):<18} "
            f"{agent.get('agent', ''):<8} {agent.get('profile', DEFAULT_PROFILE):<8} "
            f"{agent.get('status', ''):<10} "
            f"{int(agent.get('hwnd', 0)):>12}  {agent.get('task', '')[:40]}"
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

    p = sub.add_parser("heartbeat")
    p.add_argument("--role", required=True)
    p.add_argument("--mesh", default=DEFAULT_MESH)

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
        return _print_json(update_agent(args.role, mesh=args.mesh, task=args.task, status=args.status, profile=args.profile, notes=args.notes, registry_path=registry_path))
    if args.command == "heartbeat":
        return _print_json(heartbeat(args.role, mesh=args.mesh, registry_path=registry_path))
    if args.command == "remove":
        return _print_json(remove_agent(args.role, mesh=args.mesh, registry_path=registry_path))
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
