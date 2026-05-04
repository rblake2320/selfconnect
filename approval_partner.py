"""
approval_partner.py — SelfConnect SDK v0.10.0

Local auto-approval daemon for Claude Code.

Watches any Claude Code terminal window, detects approval prompts, and
injects "y" or "n" based on configurable allow/deny rules. Unknown tools
can be escalated to a Telegram bridge (approval_telegram.py) if running.

Usage:
    python approval_partner.py                          # watch with default rules
    python approval_partner.py --approve-all            # approve everything (careful)
    python approval_partner.py --dry-run                # detect but don't inject
    python approval_partner.py --telegram               # escalate unknowns to Telegram
    python approval_partner.py --list-windows           # show Claude terminals found

NO changes to settings.local.json, permissions.allow, or hooks required.
This runs as a background sidecar process.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# ── SelfConnect import ────────────────────────────────────────────────────────
try:
    from self_connect import (  # type: ignore[import]
        WindowTarget,
        get_text_uia,
        list_windows,
        send_string,
    )
except ImportError as exc:
    sys.exit(
        f"[partner] ERROR: self_connect not importable — {exc}\n"
        "Run this script from the selfconnect/ directory or add it to PYTHONPATH."
    )

# ── Approval prompt signatures ────────────────────────────────────────────────
# Claude Code pauses and prints one of these patterns before waiting for stdin.
APPROVAL_PATTERNS: list[str] = [
    r"Do you want to proceed",
    r"Allow.*for this project",
    r"Yes.*No.*Always allow",
    r"\u276f Yes",                  # ❯ heavy right-pointing angle quotation mark
    r"\u203a\s*Yes",               # › single right-pointing angle quotation mark
]

# ── Default rule sets (fnmatch-style globs on tool strings like "Bash(git:*)") ─
# Globs are matched against the full "ToolName(arg)" string extracted from the prompt.
DEFAULT_ALLOW: list[str] = [
    "Bash(git:*)",
    "Bash(npm:*)",
    "Bash(node:*)",
    "Bash(python:*)",
    "Bash(pip:*)",
    "Bash(ls:*)",
    "Bash(find:*)",
    "Bash(cat:*)",
    "Bash(gh:*)",
    "Read(*)",
    "Write(*)",
    "Edit(*)",
    "Glob(*)",
    "Grep(*)",
]

DEFAULT_DENY: list[str] = [
    "Bash(rm:*)",
    "Bash(rmdir:*)",
    "Bash(del:*)",
    "Bash(curl:*)",        # network calls default-deny; add specific curl patterns to allow if needed
    "Bash(wget:*)",
    "Bash(format:*)",
    "Bash(mkfs:*)",
]

# ── Telegram escalation (optional integration) ────────────────────────────────
# If approval_telegram.py is running, it watches a shared file for escalation requests.
TELEGRAM_ESCALATION_FILE = "approval_partner_escalations.txt"


@dataclass
class PartnerConfig:
    allow_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOW))
    deny_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DENY))
    default_action: str = "escalate"   # "approve", "deny", or "escalate"
    dry_run: bool = False
    poll_interval: float = 2.0
    cooldown: float = 3.0
    telegram_escalate: bool = False
    verbose: bool = True

    def __post_init__(self) -> None:
        valid = {"approve", "deny", "escalate"}
        if self.default_action not in valid:
            raise ValueError(f"default_action must be one of {valid}, got {self.default_action!r}")


# ── Window discovery ──────────────────────────────────────────────────────────

def find_claude_terminals() -> list[WindowTarget]:
    """Return all windows that look like a Claude Code terminal."""
    results: list[WindowTarget] = []
    for w in list_windows():
        title_lower = w.title.lower()
        exe_lower = (w.exe_name or "").lower()
        # Windows Terminal, cmd, PowerShell — all Claude Code hosts
        if any(x in title_lower for x in ("claude", "claude code")):
            results.append(w)
        elif any(x in exe_lower for x in ("windowsterminal", "cmd.exe", "powershell")):
            # Only include if the title hints at Claude Code activity
            if any(x in title_lower for x in ("claude", "pka", "selfconnect", "anthropic")):
                results.append(w)
    return results


# ── Prompt detection ──────────────────────────────────────────────────────────

def has_approval_prompt(hwnd: int) -> bool:
    """Return True if the window currently shows a Claude Code approval prompt."""
    text = get_text_uia(hwnd) or ""
    return any(re.search(p, text, re.IGNORECASE) for p in APPROVAL_PATTERNS)


def extract_tool_call(text: str) -> Optional[str]:
    """
    Extract the tool call string from the approval prompt text.

    Claude Code shows something like:
        Do you want to run Bash(npm install)?
        Allow Bash(git push) for this project?

    Returns the full "ToolName(args)" string, or None if unparseable.
    """
    # Primary: explicit "Allow X" or "run X" patterns
    m = re.search(r'\b(Allow|run|execute)\s+([A-Za-z]+\([^)]*\))', text, re.IGNORECASE)
    if m:
        return m.group(2)

    # Fallback: any ToolName(args) token near a prompt keyword
    m = re.search(r'([A-Za-z]{2,20}\([^)]{0,120}\))', text)
    if m:
        return m.group(1)

    return None


# ── Rules engine ──────────────────────────────────────────────────────────────

def evaluate_rules(tool_call: str, cfg: PartnerConfig) -> Optional[bool]:
    """
    Match tool_call against deny then allow patterns.

    Returns:
        False  → deny
        True   → approve
        None   → unknown (use cfg.default_action)
    """
    for pattern in cfg.deny_patterns:
        if fnmatch.fnmatch(tool_call, pattern):
            return False
    for pattern in cfg.allow_patterns:
        if fnmatch.fnmatch(tool_call, pattern):
            return True
    return None


def decide(tool_call: Optional[str], cfg: PartnerConfig) -> Optional[bool]:
    """
    Full decision logic.

    Returns True (approve), False (deny), or None (escalate/unknown).
    """
    if tool_call is None:
        # Can't parse → apply default
        if cfg.default_action == "approve":
            return True
        if cfg.default_action == "deny":
            return False
        return None  # escalate

    result = evaluate_rules(tool_call, cfg)
    if result is not None:
        return result

    # Unknown tool — apply default
    if cfg.default_action == "approve":
        return True
    if cfg.default_action == "deny":
        return False
    return None  # escalate


# ── Response injection ────────────────────────────────────────────────────────

def inject_response(win: WindowTarget, approve: bool, cfg: PartnerConfig) -> None:
    """Send 'y' or 'n' to the terminal window via PostMessage(WM_CHAR)."""
    char = "y\r" if approve else "n\r"
    if not cfg.dry_run:
        send_string(win, char)


def write_escalation(tool_call: Optional[str], prompt_text: str) -> None:
    """Write an escalation request to the shared file for approval_telegram.py."""
    try:
        with open(TELEGRAM_ESCALATION_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"ESCALATE|{tool_call or 'UNKNOWN'}|{prompt_text[:200]}\n")
    except OSError:
        pass  # Telegram bridge not configured — silent


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(cfg: PartnerConfig) -> None:
    tag = "[DRY-RUN]" if cfg.dry_run else "[partner]"
    print(f"{tag} Starting. Poll interval={cfg.poll_interval}s, default={cfg.default_action}")

    seen_prompt: dict[int, float] = {}   # hwnd → last action timestamp (avoid double-firing)

    while True:
        try:
            terminals = find_claude_terminals()

            if not terminals and cfg.verbose:
                print(f"{tag} No Claude terminals found — waiting...")

            for win in terminals:
                now = time.monotonic()
                # Cooldown: don't re-fire on same window too quickly
                if now - seen_prompt.get(win.hwnd, 0) < cfg.cooldown:
                    continue

                if not has_approval_prompt(win.hwnd):
                    continue

                text = get_text_uia(win.hwnd) or ""
                tool_call = extract_tool_call(text)
                decision = decide(tool_call, cfg)

                label = tool_call or "(unparsed tool)"

                if decision is True:
                    inject_response(win, approve=True, cfg=cfg)
                    verb = "WOULD approve" if cfg.dry_run else "Auto-approved"
                    print(f"{tag} {verb}: {label}  [{win.title[:50]}]")
                    seen_prompt[win.hwnd] = now

                elif decision is False:
                    inject_response(win, approve=False, cfg=cfg)
                    verb = "WOULD deny" if cfg.dry_run else "Auto-denied"
                    print(f"{tag} {verb}: {label}  [{win.title[:50]}]")
                    seen_prompt[win.hwnd] = now

                else:
                    # Unknown — escalate or just log
                    if cfg.telegram_escalate:
                        write_escalation(tool_call, text)
                        print(f"{tag} Escalated to Telegram: {label}")
                    else:
                        print(f"{tag} UNKNOWN (no rule matched, no Telegram): {label}")
                        print("  → To auto-approve, add to ALLOW_RULES in approval_partner.py")
                        print("  → Or run with --telegram to escalate to phone")
                    seen_prompt[win.hwnd] = now

        except KeyboardInterrupt:
            print(f"\n{tag} Stopped.")
            break
        except Exception as exc:
            print(f"{tag} ERROR in poll loop: {exc}")

        time.sleep(cfg.poll_interval)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SelfConnect approval partner — auto-approve Claude Code tool calls",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python approval_partner.py                   # run with default rules
  python approval_partner.py --approve-all     # approve everything (use with care)
  python approval_partner.py --dry-run         # detect prompts but don't inject
  python approval_partner.py --telegram        # escalate unknowns via Telegram
  python approval_partner.py --list-windows    # show detected Claude terminals
        """,
    )
    p.add_argument("--approve-all", action="store_true",
                   help="Approve all prompts regardless of rules (overrides everything)")
    p.add_argument("--deny-all", action="store_true",
                   help="Deny all prompts regardless of rules")
    p.add_argument("--dry-run", action="store_true",
                   help="Detect prompts and log decisions but don't inject anything")
    p.add_argument("--telegram", action="store_true",
                   help="Write unknown tools to escalation file for approval_telegram.py")
    p.add_argument("--poll", type=float, default=2.0, metavar="SECONDS",
                   help="How often to check the terminal (default: 2.0)")
    p.add_argument("--list-windows", action="store_true",
                   help="List detected Claude Code terminals and exit")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress 'no terminals found' messages")
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.list_windows:
        terminals = find_claude_terminals()
        if not terminals:
            print("No Claude Code terminals found.")
        for w in terminals:
            print(f"  hwnd={w.hwnd:10d}  {w.title}")
        return

    if args.approve_all and args.deny_all:
        parser.error("--approve-all and --deny-all are mutually exclusive")

    default_action = "escalate"
    if args.approve_all:
        default_action = "approve"
    elif args.deny_all:
        default_action = "deny"

    cfg = PartnerConfig(
        default_action=default_action,
        dry_run=args.dry_run,
        poll_interval=args.poll,
        telegram_escalate=args.telegram,
        verbose=not args.quiet,
    )

    run(cfg)


if __name__ == "__main__":
    main()
