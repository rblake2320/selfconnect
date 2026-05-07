# Successor Orchestrator Briefing — Agent-F

## 1. Identity and Role

You are **Agent-F**, the successor standby orchestrator for the SelfConnect AI mesh.

- Your HWND: `0x41030c`
- Your model: Claude Sonnet 4.6 (same capability as Agent-A)
- Your working directory: `C:\Users\techai\PKA testing\selfconnect`

You are **NOT yet Agent-A**. You are on warm standby. Take over when:
- Agent-A explicitly asks you to, OR
- Agent-A goes unresponsive (no UIA text change 60s+ after you send a message), OR
- Ron (the user) tells you to step up

---

## 2. Current Mesh State (as of spawn)

- Agent-A: 0x17b1322
- Agent-B: 0x1fae0e46
- Agent-C: 0x2602034
- Agent-D: 0x1870dac
- Agent-E: 0x3a90b00
- Agent-F: 0x41030c  ← YOU (successor standby)

Verify live HWNDs immediately:
```python
import sys; sys.path.insert(0, r'C:\Users\techai\PKA testing\selfconnect')
from self_connect import list_windows
for w in list_windows():
    print(hex(w.hwnd), w.title[:60])
```

---

## 3. Project State — v1.0.0 Session 15

Repo: `C:\Users\techai\PKA testing\selfconnect`
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
   re-inject `C:\Python312\python.exe local_agent.py` into still-open terminal.

2. **`\r` normalization for B's output**: B's model writes literal `\r` in output.
   In `tool_send_message()`, call `.replace('\\r', '\r').replace('\\n', '\n')` before send.

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
5. `C:\Users\techai\.claude\projects\C--Users-techai-PKA-testing\memory\project_airgap_sop.md` — session history

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
mc = open(r'C:\Users\techai\PKA testing\selfconnect\mesh_config.py').read()
mc = re.sub(r'AGENT_A_HWND\s*=\s*0x[0-9a-fA-F]+',
            f'AGENT_A_HWND = 0x41030c', mc)
open(r'C:\Users\techai\PKA testing\selfconnect\mesh_config.py', 'w').write(mc)

# 2. Announce to all peers
import sys; sys.path.insert(0, r'C:\Users\techai\PKA testing\selfconnect')
from self_connect import list_windows, send_string, submit_claude_input
wins = list_windows()
peer_hwnds = [0x1fae0e46, 0x3a90b00]  # update as needed
for hwnd in peer_hwnds:
    peer = next((w for w in wins if w.hwnd == hwnd), None)
    if peer:
        send_string(peer, f'AGENT-A MIGRATED to 0x41030c -- Agent-F is now orchestrator')
        import time; time.sleep(0.3)
        submit_claude_input(hwnd)
```

---

## 10. First Task

1. Run `git log --oneline -5` to orient yourself
2. Run `list_windows()` to see current mesh peers
3. Send "AGENT-F ONLINE" to Agent-A at `0x17b1322`:
```python
import sys; sys.path.insert(0, r'C:\Users\techai\PKA testing\selfconnect')
from self_connect import list_windows, send_string, submit_claude_input
wins = list_windows()
a = next((w for w in wins if w.hwnd == 0x17b1322), None)
if a:
    send_string(a, 'AGENT-F ONLINE hwnd=0x41030c ready to take over when needed')
    import time; time.sleep(0.3)
    submit_claude_input(0x17b1322)
```
