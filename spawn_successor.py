"""Spawn Agent-F (Successor Orchestrator) — a Claude Code Sonnet instance in a NEW window.

Agent-F is a warm standby for Agent-A. It has full institutional knowledge and can
take over as orchestrator if Agent-A hits context limits or goes unresponsive.

Usage:
    python spawn_successor.py

Run this NOW while Agent-A still has context, so the handoff knowledge is baked in.
"""
import sys, os, re, time, subprocess, ctypes
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from self_connect import list_windows, send_string, get_text_uia, submit_claude_input

SC_DIR   = os.path.dirname(os.path.abspath(__file__))
mc_path  = os.path.join(SC_DIR, 'mesh_config.py')
user32   = ctypes.windll.user32

# ── Step 1: Read current mesh state ──────────────────────────────────────────
with open(mc_path, 'r') as fh:
    mc_src = fh.read()

import importlib.util
spec = importlib.util.spec_from_file_location('mesh_config', mc_path)
mcfg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcfg)

print('Step 1: Current mesh state')
for name, hwnd in mcfg.MESH.items():
    print(f'  Agent-{name}: 0x{hwnd:x}')

# ── Step 2: Snapshot existing windows ────────────────────────────────────────
before = {w.hwnd for w in list_windows()}
print(f'\nStep 2: Spawning Agent-F window ({len(before)} existing windows)...')

WT_EXE = (
    r'C:\Users\techai\AppData\Local\Microsoft\WindowsApps\wt.exe'
)
proc = subprocess.Popen(
    [WT_EXE, '-w', 'new', '--title', 'Agent-F-Successor',
     'cmd', '/k', f'title Agent-F-Successor && cd /d "{SC_DIR}" && claude'],
    creationflags=0x00000008  # DETACHED_PROCESS
)
time.sleep(4)  # Windows Terminal needs time to create the window

# ── Step 3: Find the new window (30s timeout) ────────────────────────────────
print('Step 3: Locating Agent-F window...')
new_win = None
for attempt in range(30):
    for w in list_windows():
        if w.hwnd in before:
            continue
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(w.hwnd, cls_buf, 256)
        cls = cls_buf.value.upper()
        title = w.title.lower()
        if 'agent-f' in title or 'successor' in title:
            new_win = w
            print(f'  Found by title: hwnd=0x{w.hwnd:x}  "{w.title[:60]}"')
            break
        if 'CASCADIA' in cls and w.hwnd not in before:
            new_win = w
            print(f'  Found by class: hwnd=0x{w.hwnd:x}  "{w.title[:60]}"')
            break
    if new_win:
        break
    time.sleep(1)
    if attempt % 5 == 4:
        print(f'  Still waiting... ({attempt+1}s)')

if not new_win:
    print('ERROR: Could not find Agent-F window after 30s')
    sys.exit(1)

F_HWND = new_win.hwnd
print(f'Agent-F HWND: 0x{F_HWND:x}')

# ── Step 4: Wait for Claude Code TUI to initialize ───────────────────────────
print('\nStep 4: Waiting for Claude Code TUI to initialize...')
tui_ready = False
for attempt in range(45):
    time.sleep(1)
    text = get_text_uia(F_HWND) or ''
    # Claude Code TUI shows the chevron prompt ❯ or a help tip or Anthropic branding
    if '\u276f' in text or 'anthropic' in text.lower() or 'claude code' in text.lower():
        print(f'  Claude Code TUI ready after {attempt+1}s (text len={len(text)})')
        tui_ready = True
        break
    if attempt % 5 == 4:
        print(f'  Still waiting... ({attempt+1}s, text={len(text)} chars)')

if not tui_ready:
    print('  Warning: TUI may not be ready. Continuing anyway with extra wait...')
    time.sleep(5)

# ── Step 5: Write the successor briefing file ────────────────────────────────
print('\nStep 5: Writing successor_briefing_F.md...')

mesh_lines = '\n'.join(
    f'- Agent-{k}: 0x{v:x}' for k, v in mcfg.MESH.items()
) + f'\n- Agent-F: 0x{F_HWND:x}  ← YOU (successor standby)'

A_HWND = mcfg.AGENT_A_HWND

briefing_content = f"""# Successor Orchestrator Briefing — Agent-F

## 1. Identity and Role

You are **Agent-F**, the successor standby orchestrator for the SelfConnect AI mesh.

- Your HWND: `0x{F_HWND:x}`
- Your model: Claude Sonnet 4.6 (same capability as Agent-A)
- Your working directory: `{SC_DIR}`

You are **NOT yet Agent-A**. You are on warm standby. Take over when:
- Agent-A explicitly asks you to, OR
- Agent-A goes unresponsive (no UIA text change 60s+ after you send a message), OR
- Ron (the user) tells you to step up

---

## 2. Current Mesh State (as of spawn)

{mesh_lines}

Verify live HWNDs immediately:
```python
import sys; sys.path.insert(0, r'{SC_DIR}')
from self_connect import list_windows
for w in list_windows():
    print(hex(w.hwnd), w.title[:60])
```

---

## 3. Project State — v1.0.0 Session 15

Repo: `{SC_DIR}`
Run `git log --oneline -10` to see what was just done.

Key deliverables from sessions 14-15:
- `local_agent.py` — Ollama qwen3.6:27b ReAct agent with 8 tools (Agent-B)
- `spawn_b.py`, `spawn_observer.py`, `spawn_successor.py` — mesh spawn scripts
- `claudego/` — FastAPI approval dashboard, now shows local model agent badges
- `submit_claude_input()` — new SDK function (see below)
- Session 15 commits: runbook, claudego agent-type detection, submit_claude_input

---

## 4. Critical SDK Discovery — Session 15

### `submit_claude_input(hwnd)` — THE way to submit Claude Code TUI input

```python
from self_connect import submit_claude_input
submit_claude_input(hwnd)  # posts WM_CHAR 0x0D to parent CASCADIA window
```

**What fails (and why we discovered this):**
- `PostMessage(WM_KEYDOWN, VK_RETURN)` → silently ignored
- `SendInput(VK_RETURN)` even after `SetForegroundWindow` → ignored
- `pywinauto type_keys(ENTER)` → ignored

**What works:**
- `PostMessage(WM_CHAR, 0x000D, lParam=0x001C0001)` to the parent `CASCADIA_HOSTING_WINDOW_CLASS`

**Pattern for injecting and submitting:**
```python
from self_connect import list_windows, send_string, submit_claude_input
wins = list_windows()
target = next((w for w in wins if w.hwnd == SOME_HWND), None)
send_string(target, "your message here")
import time; time.sleep(0.5)
submit_claude_input(SOME_HWND)
```

---

## 5. Operational Lessons (avoid repeating these mistakes)

1. **Ollama inference timeout**: qwen3.6:27b takes 30-120s. Set `timeout=120` in requests.post.
   If B hangs: psutil to find python.exe with local_agent in cmdline, kill PID,
   re-inject `C:\\Python312\\python.exe local_agent.py` into still-open terminal.

2. **`\\r` normalization for B's output**: B's model writes literal `\\r` in output.
   In `tool_send_message()`, call `.replace('\\\\r', '\\r').replace('\\\\n', '\\n')` before send.

3. **Diff review mode blocks injection**: Claude Code's `⏵⏵ accept edits on` mode
   does NOT respond to any keyboard injection (WM_CHAR, SendInput, pywinauto).
   Only the user clicking the window and pressing Enter/y will unblock it.

4. **`wt.exe -w new` vs `-w 0`**: `-w new` creates a separate window with its own HWND.
   `-w 0` or `-w 0 nt` opens a NEW TAB in an existing window (shares HWND). Always use
   `-w new` for mesh agents that need their own injectable HWND.

5. **UIA truncates long sessions**: Windows Terminal UIA buffer is limited. Scrollback
   from very long sessions (1M+ chars) may not show all content via `get_text_uia()`.
   Read the tail: `text = get_text_uia(hwnd) or ''; text[-500:]`

---

## 6. Patent Claims Status (2026-05-07)

| Claim | Description | Status |
|-------|------------|--------|
| 1 | OS-layer PostMessage(WM_CHAR) agent communication | PROVED |
| 2 | Local model tool execution loop | PROVED |
| 3 | HWND self-discovery (no directory service) | PROVED |
| 4 | Framed message protocol (STX/NUL/ETX) | pending |
| 5 | Autonomous context-preserving migration | pending |
| 6 | Cross-machine relay (Windows ↔ Spark-1 ↔ Spark-2) | pending |
| 7 | Three-hop process control (A→B→spawned-child) | PROVED |

Evidence logs: `observer_logs/events_2026-05-07.md`, `observer_logs/b_patent_summary.md`

---

## 7. Files to Read for Full Context

Read these in order after you come online:
1. `CLAUDE.md` — project instructions, all Win32 gotchas, spawn recipes, standing protocol
2. `mesh_config.py` — live HWNDs (may have changed since this briefing was written)
3. `observer_logs/events_2026-05-07.md` — Agent-E's patent event log
4. `runbooks/` — proven step-by-step procedures for everything
5. `C:\\Users\\techai\\.claude\\projects\\C--Users-techai-PKA-testing\\memory\\project_airgap_sop.md` — session history

---

## 8. Standing Protocol (run this before going idle)

> Before going idle: (1) capture all peers via `get_text_uia()` to confirm their state,
> (2) check for pending approval prompts using `has_approval_prompt()` from approval_partner,
> (3) confirm Enter has been hit on any pending Claude Code response via `submit_claude_input()`.
> Do not park until this checklist is complete.

---

## 9. How to Take Over as Agent-A

When the time comes:
```python
# 1. Update mesh_config.py
import re
mc = open(r'{mc_path}').read()
mc = re.sub(r'AGENT_A_HWND\\s*=\\s*0x[0-9a-fA-F]+',
            f'AGENT_A_HWND = 0x{F_HWND:x}', mc)
open(r'{mc_path}', 'w').write(mc)

# 2. Announce to all peers
import sys; sys.path.insert(0, r'{SC_DIR}')
from self_connect import list_windows, send_string, submit_claude_input
wins = list_windows()
peer_hwnds = [0x{mcfg.AGENT_B_HWND:x}, 0x{mcfg.AGENT_E_HWND:x}]  # update as needed
for hwnd in peer_hwnds:
    peer = next((w for w in wins if w.hwnd == hwnd), None)
    if peer:
        send_string(peer, f'AGENT-A MIGRATED to 0x{F_HWND:x} -- Agent-F is now orchestrator')
        import time; time.sleep(0.3)
        submit_claude_input(hwnd)
```

---

## 10. First Task

1. Run `git log --oneline -5` to orient yourself
2. Run `list_windows()` to see current mesh peers
3. Send "AGENT-F ONLINE" to Agent-A at `0x{A_HWND:x}`:
```python
import sys; sys.path.insert(0, r'{SC_DIR}')
from self_connect import list_windows, send_string, submit_claude_input
wins = list_windows()
a = next((w for w in wins if w.hwnd == 0x{A_HWND:x}), None)
if a:
    send_string(a, 'AGENT-F ONLINE hwnd=0x{F_HWND:x} ready to take over when needed')
    import time; time.sleep(0.3)
    submit_claude_input(0x{A_HWND:x})
```
"""

briefing_path = os.path.join(SC_DIR, 'successor_briefing_F.md')
with open(briefing_path, 'w', encoding='utf-8') as fh:
    fh.write(briefing_content)
print(f'  Written: {briefing_path}')

# ── Step 6: Update mesh_config.py ────────────────────────────────────────────
print('\nStep 6: Updating mesh_config.py...')
if 'AGENT_F_HWND' in mc_src:
    mc_src = re.sub(r'AGENT_F_HWND\s*=\s*0x[0-9a-fA-F]+',
                    f'AGENT_F_HWND = 0x{F_HWND:x}', mc_src)
else:
    mc_src = mc_src.replace(
        'AGENT_E_HWND = ',
        f'AGENT_F_HWND = 0x{F_HWND:x}   # Successor orchestrator (standby)\nAGENT_E_HWND = '
    )
    if '"E": AGENT_E_HWND,' in mc_src:
        mc_src = mc_src.replace('"E": AGENT_E_HWND,',
                                '"E": AGENT_E_HWND,\n    "F": AGENT_F_HWND,')
    elif '"E": AGENT_E_HWND' in mc_src:
        mc_src = mc_src.replace('"E": AGENT_E_HWND',
                                '"E": AGENT_E_HWND,\n    "F": AGENT_F_HWND')

with open(mc_path, 'w') as fh:
    fh.write(mc_src)
print(f'  mesh_config.py: AGENT_F_HWND = 0x{F_HWND:x}')

# ── Step 7: Inject briefing pointer ──────────────────────────────────────────
print('\nStep 7: Injecting briefing pointer...')

pointer = (
    f'You are Agent-F (Successor Orchestrator, Claude Sonnet) in the SelfConnect AI mesh. '
    f'Your HWND is 0x{F_HWND:x}. Your working directory is {SC_DIR}. '
    f'Read the file {briefing_path} for your full mission, mesh state, and SDK discoveries. '
    f'Then announce yourself to Agent-A.'
)

send_string(new_win, pointer)
time.sleep(1.0)

# ── Step 8: Submit using the session 15 WM_CHAR 0x0D fix ─────────────────────
print('Step 8: Submitting via submit_claude_input...')
result = submit_claude_input(F_HWND)
print(f'  submit_claude_input returned: {result}')
time.sleep(2)

# ── Step 9: Verify F is processing ───────────────────────────────────────────
print('\nStep 9: Verifying Agent-F received briefing...')
baseline_len = len(get_text_uia(F_HWND) or '')
for attempt in range(10):
    time.sleep(2)
    text = get_text_uia(F_HWND) or ''
    if len(text) != baseline_len:
        print(f'  Activity detected after {(attempt+1)*2}s (text grew {len(text)-baseline_len} chars)')
        break
    if attempt == 9:
        print(f'  Warning: no text change detected. F may need manual check.')

# ── Step 10: Summary ──────────────────────────────────────────────────────────
hwnd_str = f'0x{F_HWND:x}'
print(f"""
+-----------------------------------------------------------+
|  Agent-F (Successor) SPAWNED                              |
+-----------------------------------------------------------+
|  HWND:    {hwnd_str:<50}|
|  Model:   Claude Sonnet (same as Agent-A)                 |
|  Briefing: successor_briefing_F.md                        |
|  Status:  Warm standby -- ready to take over              |
+-----------------------------------------------------------+
|  To hand off to F:                                        |
|  Tell F: "You are now Agent-A. Update mesh_config.py      |
|  and announce to all peers."                              |
+-----------------------------------------------------------+
""")
