"""Full status check on all peer terminals via SelfConnect."""
import sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from self_connect import get_text_uia, list_windows, send_string, submit_claude_input

SKIP_WORDS = ["Vertical", "System", "Minimize", "Maximize", "Close",
              "Drizzlewick", "Small Decrease", "Small Increase",
              "Large Decrease", "Large Increase", "__", "``"]

wins = list_windows()

peers = [
    (0x00B00770, "Codex/techai"),
    (0x00280610, "Fix-broken-create-account"),
    (0x003B0DAE, "Run-Playwright-test"),
]

for hwnd, role in peers:
    text = get_text_uia(hwnd) or ""
    # Only check the LAST 25 lines for an active prompt — not scrollback history
    all_lines = text.splitlines()
    recent = all_lines[-25:] if len(all_lines) > 25 else all_lines
    recent_text = "\n".join(recent).lower()
    has_prompt = "press enter to confirm" in recent_text

    # Extract pending command from recent lines only
    cmd = ""
    for line in reversed(recent):
        if line.strip().startswith("$ "):
            cmd = line.strip()[2:].strip()
            break

    # Get last 15 meaningful content lines
    content = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(w in s for w in SKIP_WORDS):
            continue
        content.append(s)

    print(f"=== {role} (0x{hwnd:08X}) ===")
    print(f"  Approval prompt: {'YES — ' + cmd[:70] if has_prompt else 'none'}")
    print(f"  Last activity:")
    for l in content[-12:]:
        print(f"    {l[:105]}")

    if has_prompt:
        win = next((w for w in wins if w.hwnd == hwnd), None)
        if win:
            send_string(win, "y", char_delay=0.05)
            time.sleep(0.3)
            submit_claude_input(hwnd)
            print(f"  >> Approved and submitted")
    print()
