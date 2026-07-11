# Runbook: Spawn a New Claude Code Terminal
<!-- selfconnect-runbook: {"status":"superseded","since":"2026-07-05","replacement":"first_wake_selfconnect.md","entrypoint":false} -->

> Superseded for first-wake launch/contact work as of 2026-07-05.
> Start with `first_wake_selfconnect.md` and `agent_launch_registry.md`.
> This file is retained as historical evidence of the older raw Windows Terminal
> spawn and WM_CHAR briefing flow.

## What
Open a new Windows Terminal tab running Claude Code in the selfconnect working directory,
wait for it to initialize, then inject a briefing message.

## Prerequisites
- Windows Terminal (`wt.exe`) installed and on PATH
- `claude` CLI installed and on PATH
- `self_connect` available on sys.path
- The parent process must NOT be running in a Windows Service / Session 0

## Steps

### 1. Spawn the terminal with DETACHED_PROCESS
```python
import subprocess, sys, time
sys.path.insert(0, '.')

WORKING_DIR = r'C:\Users\techai\PKA testing\selfconnect'
AGENT_TITLE = 'Agent B'   # must be unique so list_windows() can find it

subprocess.Popen(
    ['wt.exe', '-w', '0', 'nt',
     '--title', AGENT_TITLE,
     'cmd', '/k',
     f'cd /d {WORKING_DIR} && claude'],
    creationflags=0x00000008,   # DETACHED_PROCESS — don't inherit our console
)
print(f"[spawn] Launched '{AGENT_TITLE}' tab")
```

### 2. Poll list_windows() until the new tab appears
```python
from self_connect import list_windows

new_win = None
for attempt in range(30):
    time.sleep(1)
    candidates = [w for w in list_windows()
                  if AGENT_TITLE in w.title
                  or ('claude' in w.title.lower() and 'selfconnect' in w.title.lower())]
    if candidates:
        new_win = candidates[0]
        print(f"[spawn] Window appeared after {attempt+1}s: hwnd={new_win.hwnd}")
        break

if not new_win:
    raise RuntimeError(f"'{AGENT_TITLE}' did not appear within 30s")
```

### 3. Wait for Claude Code TUI to initialize
```python
# Claude Code takes ~3-5 seconds after the window appears to display its TUI.
# Injecting too early → characters typed into cmd before claude starts.
time.sleep(5)
```

### 4. Inject the briefing via PostMessage(WM_CHAR)
```python
from self_connect import send_string

briefing = (
    f"{AGENT_TITLE}: You are in the selfconnect repo. "
    "Read CLAUDE.md for full context. Your task: [TASK HERE]"
)
send_string(new_win, briefing + "\r")
print(f"[spawn] Briefed {AGENT_TITLE} at hwnd={new_win.hwnd}")
```

### Alternative: use the bundled script
```bash
python _spawn_claude.py
```
Edit `_spawn_claude.py` to set AGENT_TITLE and BRIEFING before running.

## Known Failures

- **Window never appears in `list_windows()`**: Check that `wt.exe` is accessible. Run
  `where wt` in a terminal. If not found, use the full path:
  `C:\Users\<user>\AppData\Local\Microsoft\WindowsApps\wt.exe`
- **Briefing typed into cmd prompt, not Claude**: The 5-second sleep (Step 3) wasn't enough.
  Increase to 8-10s on slow machines, or poll `get_text_uia(new_win.hwnd)` and wait until
  the Claude Code welcome banner appears before injecting.
- **DETACHED_PROCESS omitted → child terminal inherits parent's console**: The spawned tab
  will share stdio with the parent process and may misbehave. Always include
  `creationflags=0x00000008`.
- **Running from a Windows Service (Session 0)**: `wt.exe` requires an interactive desktop
  session. It will silently fail in Session 0. Run the spawner from a logged-in user session.

## Verified
- Session 7 (2026-04-xx) — first successful A→B spawn and briefing
- Session 14 (2026-05-04) — used to brief Agent B and Agent C
