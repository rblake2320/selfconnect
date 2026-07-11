# Runbook: Submit Text / Press Enter in Claude Code TUI
<!-- selfconnect-runbook: {"status":"current","since":"2026-05-04","replacement":null,"entrypoint":false,"kind":"specialized"} -->

## What
Send text AND submit it (press Enter) in the Claude Code terminal UI. This requires two
different mechanisms: PostMessage for typing, SendInput for Enter.

## Prerequisites
- `self_connect` available on sys.path
- Target window must be **brought to foreground** before sending Enter
- You have the `WindowTarget` object for the Claude Code terminal

## Steps

### 1. Type the message body (no foreground needed)
```python
import sys, time
sys.path.insert(0, '.')
from self_connect import list_windows, send_string

# Find the Claude Code terminal
target = next((w for w in list_windows()
               if 'claude' in w.title.lower()), None)
if not target:
    raise RuntimeError("Claude Code terminal not found")

# send_string uses PostMessage(WM_CHAR) — background-safe, no focus needed
send_string(target, "Your message text here")
time.sleep(0.1)   # brief pause before sending Enter
```

### 2. Submit with SetForegroundWindow + SendInput (Enter)
```python
from self_connect import send_keys  # send_keys uses SendInput

# send_keys requires the window to be in the foreground
import ctypes
ctypes.windll.user32.SetForegroundWindow(target.hwnd)
time.sleep(0.05)  # allow foreground switch to complete

send_keys(target, "\r")   # \r = Enter key via SendInput
print("Message submitted")
```

### One-liner shortcut (if send_string already appends \\r)
```python
# send_string with "\r" at the end works for ConPTY (Windows Terminal / cmd / PowerShell).
# It does NOT reliably submit in Claude Code's own TUI input box.
# Use the two-step pattern above for Claude Code specifically.
send_string(target, "message\r")   # OK for regular terminals
# vs.
send_string(target, "message")     # type only
send_keys(target, "\r")            # submit — use this for Claude Code TUI
```

## Known Failures

- **`PostMessage(WM_KEYDOWN, VK_RETURN, ...)` → message typed but not submitted**:
  Claude Code's TUI input intercepts VK_RETURN via keyboard hook, not the message queue.
  PostMessage does NOT trigger it. This was the most common failure mode — discovered in
  multiple sessions. Do NOT use PostMessage for Enter in Claude Code.
- **`send_string(target, "text\r")` → Enter doesn't register**: Same issue as above when
  the target is Claude Code's own input (not a ConPTY shell). The `\r` in send_string maps
  to `PostMessage(WM_CHAR, 0x0D)` which is ignored by Claude Code's input handler.
- **Window flicker / focus steal**: `SetForegroundWindow` briefly steals focus. If the user
  is typing when this runs, their keystrokes may go to the target window. Add a 0.5s delay
  if running interactively alongside a human.

## Verified
- Session 7 (2026-04-xx) — SetForegroundWindow + SendInput confirmed working
- Session 14 (2026-05-04) — briefings delivered to Agent B and Agent C using this pattern
