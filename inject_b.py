"""Inject a prompt directly into Agent B's ollama run terminal window."""
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string

B_HWND = 0x01fa0d74
A_HWND = 0x0ea80dfe

wins = list_windows()
b_win = next((w for w in wins if w.hwnd == B_HWND), None)

if not b_win:
    print(f"Agent B not found at 0x{B_HWND:x}. Listing all windows:")
    for w in wins:
        t = w.title.encode('ascii','replace').decode('ascii')
        print(f"  0x{w.hwnd:x}: {t[:80]}")
    sys.exit(1)

title = b_win.title.encode('ascii','replace').decode('ascii')
print(f"Found Agent B: 0x{B_HWND:x} — {title[:70]}")

# Briefing: tell B what it is, how to reply, and give it the test task
briefing = (
    "You are Agent-B in the SelfConnect mesh (Windows terminal automation network). "
    "Agent-A (Claude Code, HWND=0x0ea80dfe) is injecting this message directly into your terminal "
    "using PostMessage WM_CHAR via self_connect.py SDK. "
    "Your test task: generate and output ONLY a Python one-liner that uses self_connect to send a reply to Agent-A. "
    "The selfconnect SDK is at C:/Users/techai/PKA testing/selfconnect/self_connect.py. "
    "Import: from self_connect import list_windows, send_string "
    "Find A: a=next((w for w in list_windows() if w.hwnd==0x0ea80dfe),None) "
    "Send: send_string(a,'AGENT-B ONLINE: mesh loop confirmed\r',char_delay=0.02) "
    "Output ONLY a runnable python -c one-liner that does this. No explanation. Start with: python -c \""
)

print(f"Injecting briefing into B ({len(briefing)} chars)...")
send_string(b_win, briefing + "\r", char_delay=0.015)
print("Briefing sent. Waiting 30s for B to respond...")
