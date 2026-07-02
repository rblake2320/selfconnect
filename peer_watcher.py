"""
peer_watcher.py — SelfConnect SDK v0.10.0

Rules-based approval watcher for Agent-B (hwnd=5705128).

Unlike approval_partner.py (which scans all Claude terminals), this script
targets a specific peer agent window and applies the same allow/deny rules
before injecting any response.

Rules (from approval_partner.py):
  ALLOW: Bash(git:*), Bash(npm:*), Bash(node:*), Bash(python:*), Bash(pip:*),
         Bash(ls:*), Bash(find:*), Bash(cat:*), Bash(gh:*),
         Read(*), Write(*), Edit(*), Glob(*), Grep(*)
  DENY:  Bash(rm:*), Bash(rmdir:*), Bash(del:*), Bash(curl:*),
         Bash(wget:*), Bash(format:*), Bash(mkfs:*)
  UNKNOWN: log and DO NOT auto-approve — requires human review

Usage:
    python peer_watcher.py             # poll every 5 minutes (standing protocol)
    python peer_watcher.py --once      # single check and exit
    python peer_watcher.py --interval 30  # poll every 30 seconds
    python peer_watcher.py --dry-run   # detect but don't inject
"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import fnmatch
import re
import time

try:
    from self_connect import WindowTarget, get_text_uia, send_string
except ImportError as exc:
    sys.exit(f"[peer_watcher] ERROR: {exc}")

# ── Target ────────────────────────────────────────────────────────────────────
AGENT_B_HWND = 5705128

# ── Approval UI patterns — ONLY match the interactive selector, not prose ────
# Claude Code approval prompts render a radio-button UI at the bottom:
#   ❯ Yes  No  Always allow "Bash(git status)" for this project
# We must NOT match lines that merely discuss "allow ... for this project".
INTERACTIVE_PATTERNS: list[str] = [
    r"\u276f\s+Yes\s+No",            # ❯ Yes  No  ...
    r"\u203a\s+Yes\s+No",            # › Yes  No  ...
    r"Do you want to proceed",        # explicit proceed prompt
    r"Yes\s+No\s+Always allow",       # the three-option layout
]

# ── Rules (mirrors approval_partner.py DEFAULT_ALLOW / DEFAULT_DENY) ─────────
ALLOW: list[str] = [
    "Bash(git:*)",  "Bash(npm:*)",  "Bash(node:*)", "Bash(python:*)",
    "Bash(pip:*)",  "Bash(ls:*)",   "Bash(find:*)", "Bash(cat:*)",
    "Bash(gh:*)",
    "Read(*)", "Write(*)", "Edit(*)", "Glob(*)", "Grep(*)",
]

DENY: list[str] = [
    "Bash(rm:*)",  "Bash(rmdir:*)", "Bash(del:*)",
    "Bash(curl:*)", "Bash(wget:*)", "Bash(format:*)", "Bash(mkfs:*)",
]


def has_real_approval_prompt(hwnd: int) -> tuple[bool, str]:
    """
    Returns (found, text).  Only returns True when the interactive selector
    UI is visible in the last 20 lines — not when prose merely mentions it.
    """
    text = get_text_uia(hwnd) or ""
    lines = text.splitlines()
    recent = "\n".join(lines[-20:])
    found = any(re.search(p, recent, re.IGNORECASE) for p in INTERACTIVE_PATTERNS)
    return found, text


def extract_tool_call(text: str) -> str | None:
    """Extract ToolName(args) from the approval prompt text."""
    # Primary: "Allow X" / "run X" / "execute X"
    m = re.search(r'\b(Allow|run|execute)\s+([A-Za-z]+\([^)]*\))', text, re.IGNORECASE)
    if m:
        return m.group(2)
    # Fallback: any ToolName(args) token in recent text
    m2 = re.search(r'([A-Za-z]{2,20}\([^)]{0,120}\))', text)
    if m2:
        return m2.group(1)
    return None


def decide(tool_call: str | None) -> bool | None:
    """
    Returns:
        True  → approve
        False → deny
        None  → unknown, do not auto-approve
    """
    if tool_call is None:
        return None  # can't parse → escalate

    for pattern in DENY:
        if fnmatch.fnmatch(tool_call, pattern):
            return False

    for pattern in ALLOW:
        if fnmatch.fnmatch(tool_call, pattern):
            return True

    return None  # unknown


def check_once(dry_run: bool = False) -> None:
    """Single poll of Agent-B."""
    found, text = has_real_approval_prompt(AGENT_B_HWND)

    if not found:
        print("[peer_watcher] Agent-B: no approval prompt.")
        return

    # Extract tool call from the last 30 lines (more context for extraction)
    lines = text.splitlines()
    recent_text = "\n".join(lines[-30:])
    tool_call = extract_tool_call(recent_text)
    label = tool_call or "(unparsed)"

    decision = decide(tool_call)
    win = WindowTarget(hwnd=AGENT_B_HWND, title="Agent-B", exe_name="")

    if decision is True:
        if not dry_run:
            send_string(win, "y\r")
        print(f"[peer_watcher] {'WOULD approve' if dry_run else 'Approved'}: {label}")

    elif decision is False:
        if not dry_run:
            send_string(win, "n\r")
        print(f"[peer_watcher] {'WOULD deny' if dry_run else 'Denied'}: {label}")

    else:
        print(f"[peer_watcher] UNKNOWN tool — NOT auto-approving: {label}")
        print("  → Add to ALLOW list in peer_watcher.py if safe, or approve manually.")


def run_loop(interval: float, dry_run: bool) -> None:
    print(f"[peer_watcher] Started. Polling Agent-B (hwnd={AGENT_B_HWND}) every {interval}s")
    print(f"[peer_watcher] dry_run={dry_run}")
    while True:
        try:
            check_once(dry_run=dry_run)
        except KeyboardInterrupt:
            print("\n[peer_watcher] Stopped.")
            break
        except Exception as exc:
            print(f"[peer_watcher] ERROR: {exc}")
        time.sleep(interval)


def main() -> None:
    p = argparse.ArgumentParser(description="Rules-based peer approval watcher for Agent-B")
    p.add_argument("--once", action="store_true", help="Single check and exit")
    p.add_argument("--interval", type=float, default=300.0,
                   help="Poll interval in seconds (default: 300 = 5 min)")
    p.add_argument("--dry-run", action="store_true",
                   help="Detect and log decisions but don't inject")
    args = p.parse_args()

    if args.once:
        check_once(dry_run=args.dry_run)
    else:
        run_loop(interval=args.interval, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
