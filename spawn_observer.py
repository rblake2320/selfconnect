"""Spawn Agent-E (Observer) — a Claude Code instance in a NEW separate window.
Watches all mesh agents, logs patent-worthy events, and can communicate with the mesh."""
import ctypes
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from self_connect import get_text_uia, list_windows, send_string

SC_DIR = os.path.dirname(os.path.abspath(__file__))
user32 = ctypes.windll.user32

# --- Step 1: Snapshot existing windows ---
before = {w.hwnd for w in list_windows()}
print('Step 1: Launching new console for Agent-E (Observer)...')

proc = subprocess.Popen(
    ['cmd.exe', '/k', f'title Agent-E-Observer && cd /d "{SC_DIR}"'],
    creationflags=subprocess.CREATE_NEW_CONSOLE
)
time.sleep(3)

# --- Step 2: Find the new window ---
print('Step 2: Finding Agent-E window...')
new_win = None
for w in list_windows():
    if w.hwnd not in before:
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(w.hwnd, cls_buf, 256)
        cls = cls_buf.value
        t = w.title.encode('ascii', 'replace').decode()
        if 'Agent-E' in t or 'Observer' in t:
            new_win = w
            print(f'  Found by title: hwnd=0x{w.hwnd:x} title={t[:60]}')
            break
        if 'Console' in cls or 'Terminal' in cls or 'CASCADIA' in cls.upper():
            new_win = w
            print(f'  Found by class: hwnd=0x{w.hwnd:x} class={cls!r} title={t[:60]}')
            break

if not new_win:
    for w in list_windows():
        if w.hwnd not in before and w.exe_name and ('cmd' in w.exe_name.lower() or 'terminal' in w.exe_name.lower()):
            new_win = w
            t = w.title.encode('ascii', 'replace').decode()
            print(f'  Found (fallback): hwnd=0x{w.hwnd:x} title={t[:60]}')
            break

if not new_win:
    print('ERROR: Could not find Agent-E window')
    sys.exit(1)

E_HWND = new_win.hwnd
print(f'Agent-E HWND: 0x{E_HWND:x}')

# --- Step 3: Launch Claude Code (haiku model for cost efficiency) ---
print('Step 3: Launching Claude Code with haiku model...')
send_string(new_win, 'claude --model haiku\r')
print('  Waiting 15s for Claude Code to initialize...')
time.sleep(15)

# Verify Claude started
text = get_text_uia(E_HWND) or ''
if 'claude' in text.lower() or '>' in text:
    print('  Claude Code appears to be running.')
else:
    print('  Warning: Claude Code may not have started. Continuing anyway.')

# --- Step 4: Update mesh_config.py ---
mc_path = os.path.join(SC_DIR, 'mesh_config.py')
with open(mc_path) as f:
    mc = f.read()

if 'AGENT_E_HWND' in mc:
    import re
    mc = re.sub(r'AGENT_E_HWND\s*=\s*0x[0-9a-fA-F]+', f'AGENT_E_HWND = 0x{E_HWND:x}', mc)
else:
    # Add E after D line
    mc = mc.replace(
        'AGENT_D_HWND = ',
        f'AGENT_E_HWND = 0x{E_HWND:x}   # Observer Claude (patent/github logger)\nAGENT_D_HWND = '
    )
    # Add E to MESH dict
    mc = mc.replace('"D": AGENT_D_HWND,', '"D": AGENT_D_HWND,\n    "E": AGENT_E_HWND,')

with open(mc_path, 'w') as f:
    f.write(mc)
print(f'mesh_config.py updated: AGENT_E_HWND = 0x{E_HWND:x}')

# --- Step 5: Create logs directory ---
logs_dir = os.path.join(SC_DIR, 'observer_logs')
os.makedirs(logs_dir, exist_ok=True)
print(f'Log directory: {logs_dir}')

# --- Step 6: Inject the briefing ---
print('Step 5: Injecting Observer briefing...')

# Read current mesh HWNDs from config
import importlib.util

spec = importlib.util.spec_from_file_location('mesh_config', mc_path)
mcfg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcfg)

briefing = (
    f'You are Agent-E (Observer) in the SelfConnect AI mesh. '
    f'Your HWND is 0x{E_HWND:x}. '
    f'You are in: {SC_DIR}. '
    f'Read the file {SC_DIR}/observer_briefing.md for your full mission and instructions.'
)

send_string(new_win, briefing)
time.sleep(0.5)
send_string(new_win, '\r')
time.sleep(0.5)
send_string(new_win, '\n')
time.sleep(1)

print(f'\nDone. Agent-E (Observer) spawned at HWND 0x{E_HWND:x}')
print(f'Logs will be at: {logs_dir}/')
print(f'Briefing file: {SC_DIR}/observer_briefing.md')
