"""sc_hooks — install Claude Code hooks that turn spawned agents evented.

Replaces timing-based waits with deterministic signals the receiver already
emits:

    UserPromptSubmit -> "ack"          (injection landed; task -> working)
    Notification     -> "notification" (waiting on permission; -> input-required)
    Stop             -> "stop"         (turn finished; wake the orchestrator)

Each hook runs ``sc_hook_emit.py`` with absolute paths baked in at install
time, so the spawned session needs no environment setup. Hooks are written to
``<project>/.claude/settings.local.json`` (project-local, not committed, does
not touch the user's global settings) and installation is idempotent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

__version__ = "0.12.0"

_EMITTER = Path(__file__).resolve().parent / "sc_hook_emit.py"

# hook event name (Claude Code schema) -> sc event name
HOOK_EVENTS = {
    "UserPromptSubmit": "ack",
    "Notification": "notification",
    "Stop": "stop",
}

_MARKER = "sc_hook_emit.py"  # identifies our commands for idempotency/uninstall


def build_hook_command(task_root: str | Path, agent: str, event: str,
                       python_exe: str = "", emitter: str | Path = "") -> str:
    python_exe = python_exe or sys.executable
    emitter = str(emitter or _EMITTER)
    return (
        f'"{python_exe}" "{emitter}" '
        f'--task-root "{task_root}" --agent "{agent}" --event {event}'
    )


def _settings_path(project_dir: str | Path, settings_name: str) -> Path:
    return Path(project_dir) / ".claude" / settings_name


def _load_settings(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def install_hooks(project_dir: str | Path, task_root: str | Path, agent: str,
                  python_exe: str = "", settings_name: str = "settings.local.json") -> Path:
    """Merge ack/notification/stop hooks into the project's local settings.

    Existing settings and existing hooks are preserved; our commands are added
    once (idempotent on repeated installs).
    """
    path = _settings_path(project_dir, settings_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = _load_settings(path)
    hooks = settings.setdefault("hooks", {})
    for hook_event, sc_event in HOOK_EVENTS.items():
        command = build_hook_command(task_root, agent, sc_event, python_exe)
        matchers = hooks.setdefault(hook_event, [])
        already = any(
            h.get("command") == command
            for m in matchers
            for h in m.get("hooks", [])
        )
        if not already:
            matchers.append({"hooks": [{"type": "command", "command": command}]})
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return path


def uninstall_hooks(project_dir: str | Path,
                    settings_name: str = "settings.local.json") -> bool:
    """Remove every hook entry we installed. Returns True if anything changed."""
    path = _settings_path(project_dir, settings_name)
    settings = _load_settings(path)
    hooks = settings.get("hooks")
    if not hooks:
        return False
    changed = False
    for hook_event in list(hooks.keys()):
        kept = []
        for matcher in hooks[hook_event]:
            inner = [h for h in matcher.get("hooks", []) if _MARKER not in h.get("command", "")]
            if inner != matcher.get("hooks", []):
                changed = True
            if inner:
                matcher["hooks"] = inner
                kept.append(matcher)
            elif matcher.get("hooks") is None:
                kept.append(matcher)  # foreign matcher shape — leave alone
        if kept:
            hooks[hook_event] = kept
        else:
            del hooks[hook_event]
            changed = True
    if changed:
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return changed


__all__ = ["HOOK_EVENTS", "build_hook_command", "install_hooks", "uninstall_hooks"]
