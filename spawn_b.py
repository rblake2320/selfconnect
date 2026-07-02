"""Spawn Agent-B (ollama run qwen3.6:27b), get HWND, disable thinking, inject full briefing."""
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string

SC_DIR = os.path.dirname(os.path.abspath(__file__))
A_HWND = 0x0ea80dfe

# --- Step 1: Snapshot existing windows ---
before = {w.hwnd for w in list_windows()}

# --- Step 2: Spawn new Windows Terminal tab ---
print('Spawning Agent-B terminal...')
wt = r'C:\Users\techai\AppData\Local\Microsoft\WindowsApps\wt.exe'
# -w new = separate window; launch local_agent.py instead of bare ollama run
agent_script = os.path.join(SC_DIR, 'local_agent.py')
subprocess.Popen(
    [wt, '-w', 'new', 'cmd', '/k',
     f'title Agent-B-local && cd /d "{SC_DIR}" && C:\\Python312\\python.exe "{agent_script}"'],
    creationflags=0x00000008
)

# --- Step 3: Wait for new window ---
b = None
for i in range(30):
    time.sleep(1)
    new_wins = [w for w in list_windows() if w.hwnd not in before]
    matches = [w for w in new_wins if 'Agent-B' in w.title or 'local_agent' in w.title.lower()]
    if matches:
        b = matches[0]
        print(f'Found B: 0x{b.hwnd:x} — {b.title.encode("ascii","replace").decode()[:60]}')
        break
    if i % 5 == 4:
        print(f'  waiting... ({i+1}s)')

if not b:
    # Try title match only
    for w in list_windows():
        t = w.title.encode('ascii','replace').decode()
        if 'Agent-B' in t or 'local_agent' in t.lower():
            b = w
            print(f'Found by title: 0x{b.hwnd:x} — {t[:60]}')
            break

if not b:
    print('ERROR: Could not find Agent-B window after 30s')
    sys.exit(1)

B_HWND = b.hwnd
print(f'B HWND: 0x{B_HWND:x}')

# --- Step 4: Wait for agent REPL to be ready ---
print('Waiting for local_agent.py to start...')
time.sleep(10)

# --- Step 5: Update mesh_config.py ---
mc_path = os.path.join(SC_DIR, 'mesh_config.py')
with open(mc_path) as f:
    mc = f.read()
import re

mc = re.sub(r'AGENT_B_HWND\s*=\s*0x[0-9a-fA-F]+', f'AGENT_B_HWND = 0x{B_HWND:x}', mc)
with open(mc_path, 'w') as f:
    f.write(mc)
print(f'mesh_config.py updated: AGENT_B_HWND = 0x{B_HWND:x}')

# --- Step 6: Inject first task (B finds A and reports in) ---
task = 'Find the window titled airgap-sop-production and send it the message: AGENT-B ONLINE via local_agent'
send_string(b, task + '\r', char_delay=0.02)
print('Task injected. B will execute autonomously.')
print(f'\nDone. B HWND: 0x{B_HWND:x}')
