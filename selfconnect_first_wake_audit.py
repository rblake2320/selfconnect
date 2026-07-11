"""Read-only SelfConnect first-wake audit.

Prints the expected startup order and current health signals. It does not launch
agents, send input, register mesh roles, write files, or mutate state.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNBOOKS = ROOT / "runbooks"
FIRST_WAKE = RUNBOOKS / "first_wake_selfconnect.md"
REGISTRY = RUNBOOKS / "agent_launch_registry.md"
TIMELINE = RUNBOOKS / "PROCESS_TIMELINE.md"
RUNBOOK_META_RE = re.compile(r"<!--\s*selfconnect-runbook:\s*(\{.*?\})\s*-->")


def run(args: list[str], timeout: int = 10) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            args,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # read-only audit should report, not fail hard
        return False, str(exc)
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def powershell(command: str, timeout: int = 10) -> tuple[bool, str]:
    return run(["powershell", "-NoProfile", "-Command", command], timeout=timeout)


def status_line(name: str, ok: bool, detail: str = "") -> str:
    marker = "PASS" if ok else "WARN"
    suffix = f" - {detail}" if detail else ""
    return f"[{marker}] {name}{suffix}"


def command_source(command: str) -> tuple[bool, str]:
    ok, out = powershell(
        f"Get-Command {command} -ErrorAction SilentlyContinue | "
        "Select-Object -First 1 -ExpandProperty Source"
    )
    if ok and out:
        return True, out.splitlines()[0]
    found = shutil.which(command)
    return (found is not None), (found or "not found")


def doctor_summary(expected_package_version: str | None = None) -> tuple[bool, str]:
    ok, out = run(["selfconnect", "doctor", "--json"], timeout=15)
    if not ok:
        return False, out
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return False, "doctor output was not JSON"
    caps = data.get("capabilities", {})
    required = ["win32", "uia_text", "printwindow"]
    missing = [name for name in required if not caps.get(name)]
    version = data.get("version", "?")
    windows = data.get("visible_window_count", "?")
    if missing:
        return False, f"version={version}; visible_windows={windows}; missing={','.join(missing)}"
    if expected_package_version and version != expected_package_version:
        return False, f"runtime_version={version}; package_version={expected_package_version}; visible_windows={windows}"
    return True, f"version={version}; visible_windows={windows}"


def runbook_metadata(path: Path) -> tuple[bool, dict[str, object] | str]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return False, str(exc)
    match = RUNBOOK_META_RE.search(text[:1000])
    if not match:
        return False, "missing selfconnect-runbook metadata"
    try:
        return True, json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return False, f"invalid metadata JSON: {exc}"


def metadata_summary() -> tuple[bool, str]:
    metadata: list[tuple[str, dict[str, object]]] = []
    errors: list[str] = []
    for path in sorted(RUNBOOKS.glob("*.md")):
        ok, data = runbook_metadata(path)
        if not ok:
            errors.append(f"{path.name}: {data}")
            continue
        assert isinstance(data, dict)
        metadata.append((path.name, data))

    entrypoints = [name for name, data in metadata if data.get("entrypoint") is True]
    if entrypoints != ["first_wake_selfconnect.md"]:
        errors.append(f"entrypoint files={entrypoints or 'none'}; expected first_wake_selfconnect.md only")

    for name, data in metadata:
        if data.get("status") in {"superseded", "historical"} and not data.get("replacement"):
            errors.append(f"{name}: {data.get('status')} without replacement")

    current = sum(1 for _, data in metadata if data.get("status") == "current")
    historical = sum(1 for _, data in metadata if data.get("status") in {"historical", "superseded"})
    detail = f"{len(metadata)} tagged; current={current}; historical/superseded={historical}; entrypoint={entrypoints}"
    if errors:
        return False, detail + "; " + "; ".join(errors[:4])
    return True, detail


def main() -> int:
    print("SelfConnect First-Wake Audit")
    print("============================")
    print()
    print("Required order:")
    print("1. Read runbooks/first_wake_selfconnect.md")
    print("2. Query MemoryWeb for fresh/session context")
    print("3. Check repo/package/doctor/mesh health")
    print("4. Read runbooks/agent_launch_registry.md before target launch")
    print("5. Confirm target CLI")
    print("6. Launch via verified recipe")
    print("7. Discover -> guard -> read -> mesh register -> send -> readback -> closeout")
    print()

    checks: list[tuple[str, bool, str]] = []
    checks.append(("repo path", ROOT.exists(), str(ROOT)))
    checks.append(("first-wake runbook", FIRST_WAKE.exists(), str(FIRST_WAKE)))
    checks.append(("launch registry", REGISTRY.exists(), str(REGISTRY)))
    checks.append(("process timeline", TIMELINE.exists(), str(TIMELINE)))

    ok, out = metadata_summary()
    checks.append(("runbook metadata", ok, out))

    ok, out = run(["git", "status", "--short", "--branch"], timeout=10)
    checks.append(("git status", ok, out.splitlines()[0] if out else "no output"))

    ok, out = run(["python", "-m", "pip", "show", "selfconnect"], timeout=15)
    package_version = None
    version_line = next((line for line in out.splitlines() if line.startswith("Version:")), out[:120])
    if version_line.startswith("Version:"):
        package_version = version_line.split(":", 1)[1].strip()
    checks.append(("selfconnect package", ok, version_line))

    ok, out = doctor_summary(package_version)
    checks.append(("selfconnect doctor", ok, out))

    ok, out = run(["selfconnect-mesh", "list"], timeout=15)
    role_count = max(0, len(out.splitlines()) - 2) if out else 0
    checks.append(("mesh list", ok, f"{role_count} visible registry rows" if ok else out[:160]))

    for command in ["claude", "codex", "gemini", "ollama"]:
        ok, source = command_source(command)
        checks.append((f"target CLI: {command}", ok, source))

    print("Health signals:")
    for name, ok, detail in checks:
        print(status_line(name, ok, detail))

    print()
    print("This audit is read-only. It did not launch/contact agents, send input, register roles, or edit files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
