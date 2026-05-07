"""Brief Agent B (qwen3.6 in ollama run) with full SelfConnect SDK knowledge."""
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string, get_text_uia

B_HWND = 0x01fa0d74
SC_DIR = 'C:/Users/techai/PKA testing/selfconnect'

wins = list_windows()
b = next((w for w in wins if w.hwnd == B_HWND), None)
if not b:
    print(f'ERROR: Agent B not found at 0x{B_HWND:x}')
    sys.exit(1)

print(f'Found B: {b.title.encode("ascii","replace").decode()[:60]}')

briefing = (
    "You are Agent-B (qwen3.6:27b) in the SelfConnect AI mesh. HWND=0x01fa0d74.\n"
    "\n"
    "== MESH ==\n"
    "Agent-A: Claude Code orchestrator. HWND=0x0ea80dfe. Sends you tasks.\n"
    "Agent-B: YOU. Receives tasks, replies via relay.\n"
    "Agent-C: Gemini CLI. HWND=0x2602034.\n"
    "Agent-D: Codex. HWND=0x1870dac.\n"
    "\n"
    "== SELFCONNECT ==\n"
    "Win32 AI-to-AI communication via PostMessage(WM_CHAR). No cloud, no API between agents.\n"
    "SDK: C:/Users/techai/PKA testing/selfconnect/self_connect.py\n"
    "\n"
    "== KEY SDK FUNCTIONS ==\n"
    "from self_connect import list_windows, send_string, get_text_uia, capture_window\n"
    "list_windows() -> list of WindowTarget(hwnd, title, pid, exe)\n"
    "send_string(target, text, char_delay=0.05) -> injects text via PostMessage. Add \\r for Enter.\n"
    "get_text_uia(hwnd) -> reads all text from a window via UI Automation\n"
    "capture_window(hwnd) -> PIL Image screenshot of any window\n"
    "\n"
    "== HOW YOU REPLY TO A ==\n"
    "You cannot execute code. Output a command and Agent-A's relay executes it.\n"
    "SIMPLEST: python b_send.py <your message here>\n"
    "Example: python b_send.py Task complete, found 4 windows\n"
    "CUSTOM: python b_reply.py (sends fixed confirmation)\n"
    "PATH: C:/Users/techai/PKA testing/selfconnect/\n"
    "\n"
    "== RULES ==\n"
    "1. Output ONE command per task. No markdown. No explanation before or after.\n"
    "2. Use b_send.py for any custom message to A. No chr() encoding. No -c one-liners.\n"
    "3. When asked to list windows: call list_windows(), filter by known titles.\n"
    "\n"
    "== YOUR FIRST TASK ==\n"
    "Use what you now know. Reply to A: confirm you are briefed and state the HWND of Agent-A.\n"
    "Output exactly: python b_send.py AGENT-B BRIEFED: A is at HWND 0x0ea80dfe"
)

print(f'Injecting briefing ({len(briefing)} chars)...')
snapshot = get_text_uia(B_HWND) or ''
send_string(b, briefing + '\r', char_delay=0.015)

print('Waiting 30s for B to respond...')
time.sleep(30)

after = get_text_uia(B_HWND) or ''
new_text = after[len(snapshot):]
print('\n--- B response ---')
print(new_text[-1500:])
print('--- end ---\n')

# Execute B's reply if it output the expected command
lines = after.splitlines()
cmd = next(
    (l.strip() for l in reversed(lines)
     if l.strip().startswith('python b_send.py') or l.strip().startswith('python b_reply.py')),
    None
)
if cmd:
    import subprocess
    print(f'Executing: {cmd}')
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=SC_DIR)
    print('stdout:', r.stdout.strip())
    print('stderr:', r.stderr.strip()[:200])
else:
    print('No reply command found in B output yet.')
