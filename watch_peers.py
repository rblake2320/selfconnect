"""
AXIOM peer watcher — polls Codex, Fix-create-account, and Playwright terminal.
Uses SelfConnect (WM_CHAR + SendInput) to approve safe tool prompts.
Run once to do a single check-and-approve sweep. AXIOM calls this on demand.
"""
import sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import re
from self_connect import list_windows, get_text_uia, send_string, submit_claude_input
from approval_partner import decide, PartnerConfig

PEERS = [
    (0x00B00770, "Codex/techai"),
    (0x00280610, "Fix-broken-create-account"),
    (0x003B0DAE, "Run-Playwright-test"),
]

APPROVAL_MARKER = "press enter to confirm"

wins = list_windows()

for hwnd, role in PEERS:
    win = next((w for w in wins if w.hwnd == hwnd), None)
    if not win:
        print(f"[{role}] NOT FOUND")
        continue

    text = get_text_uia(hwnd) or ""
    lower = text.lower()

    if APPROVAL_MARKER in lower:
        # Extract the $ command line from the approval prompt
        cmd = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("$ "):
                cmd = stripped[2:].strip()
                break
            # Also handle lines that are just the command continuation
            # (wrapped lines without the $ prefix)

        # Map shell command to a Bash(...) tool call for the policy engine
        tool_call = f"Bash({cmd[:100]})" if cmd else "Bash(unknown)"
        decision = decide(tool_call, PartnerConfig())

        if decision is True:
            send_string(win, "y", char_delay=0.05)
            time.sleep(0.3)
            submit_claude_input(hwnd)
            print(f"[{role}] APPROVED: {cmd[:80]!r}")
        elif decision is False:
            send_string(win, "\x1b", char_delay=0.05)
            print(f"[{role}] DENIED: {cmd[:80]!r}")
        else:
            print(f"[{role}] NEEDS REVIEW: {cmd[:80]!r}")
    else:
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        status = lines[-2] if len(lines) >= 2 else (lines[-1] if lines else "(empty)")
        print(f"[{role}] active: {status[:100]}")
