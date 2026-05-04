# SelfConnect SDK — Project Instructions

## What This Is

OS-native bridge between AI agents and Windows desktop apps. PostMessage(WM_CHAR) +
PrintWindow + UIA accessibility, no browser, no API keys, no focus stealing.

**v0.9.0 — 60 exports — 2551 lines — CI green (master)**

Repo: https://github.com/rblake2320/selfconnect

---

## Critical Win32 Gotchas (learned the hard way — do not repeat these mistakes)

1. **`PostMessage(WM_CHAR)` works for ConPTY text input.** Background-safe — window does NOT
   need focus. This is the patent core.

2. **Enter/Submit in Claude Code TUI requires `SetForegroundWindow + SendInput`.**
   `PostMessage(WM_KEYDOWN, VK_RETURN)` does NOT submit in Claude Code. You must bring the
   window to foreground and use `SendInput`. Use `send_keys()` from the SDK.

3. **Chrome / Edge / Electron capture**: `PrintWindow` and `BitBlt` return a black image
   (GPU compositing). Use `PIL.ImageGrab.grab(bbox=window_rect, all_screens=True)` instead.
   Window must be visible and unobstructed — not minimized.

4. **DPI scaling on high-DPI displays**: Chrome runs at 120 DPI (1.25x scale factor).
   UIA returns logical coordinates. GetWindowRect returns physical pixels. PIL captures at
   logical resolution. Conversions:
   - Physical click position = UIA_coord / 1.25
   - PIL image pixel = (UIA_coord - window_origin_logical)

5. **Codex / GPT terminal** needs `codex --full-auto` or `codex -a never` to bypass
   interactive [Y/n] permission prompts. Without this, Codex freezes waiting for keyboard input.

6. **UIA accessibility bridge for WebView2** (Antigravity, VS Code extensions):
   Call `AccessibleObjectFromWindow(chrome_hwnd, OBJID_CLIENT, IID_IAccessible)` first to
   expand the UIA tree from ~2 nodes to ~268 nodes. Without this, the chat input is invisible.

7. **`send_string()` vs `send_keys()`**: `send_string()` uses PostMessage(WM_CHAR) — background
   safe, works for ConPTY. `send_keys()` uses SendInput — requires foreground window.

---

## How to Spawn Another Claude Code Terminal

```python
import subprocess, sys, time
sys.path.insert(0, '.')
from self_connect import list_windows, send_string

# 1. Open a new Windows Terminal tab titled "Agent B" running Claude Code
subprocess.Popen(
    ['wt.exe', '-w', '0', 'nt', '--title', 'Agent B',
     'cmd', '/k',
     r'cd /d C:\Users\techai\PKA testing\selfconnect && claude'],
    creationflags=0x00000008  # DETACHED_PROCESS — don't inherit console
)

# 2. Wait for the new window to appear (poll every 1s, up to 30s)
new_win = None
for _ in range(30):
    time.sleep(1)
    candidates = [w for w in list_windows()
                  if 'Agent B' in w.title or
                  ('claude' in w.title.lower() and 'selfconnect' in w.title.lower())]
    if candidates:
        new_win = candidates[0]
        break

# 3. Wait a few more seconds for Claude Code to fully initialize
time.sleep(5)

# 4. Inject the briefing via PostMessage(WM_CHAR) — no focus needed
if new_win:
    briefing = (
        "Agent B: You are in the selfconnect repo. "
        "Read CLAUDE.md for full context. Your task: [TASK HERE]"
    )
    send_string(new_win, briefing + "\r")
    print(f"[spawn] Briefed Agent B at hwnd={new_win.hwnd}")
```

### Alternative: use the existing `_spawn_claude.py` script

```bash
python _spawn_claude.py
```

---

## How to Start the Multi-Agent Mesh

```bash
# Step 1: Start hub relay (must run in Windows Session 1 — not Session 0)
python hub_relay.py
# HUB_URL = http://192.168.12.132:8765

# Step 2: Discover running Claude Code terminals
python -c "
from self_connect import list_windows
for w in list_windows():
    if 'claude' in w.title.lower():
        print(f'hwnd={w.hwnd:10d}  {w.title}')
"

# Step 3: Spawn additional agents as needed (see spawn recipe above)

# Step 4: Inject standing protocol to each agent
```

---

## Standing Protocol (embed literally in EVERY agent briefing)

> "Before going idle: (1) capture all peers via PrintWindow to confirm their state,
> (2) check for pending approval prompts, (3) confirm Enter has been hit on any pending
> Claude Code response. Do not park until this checklist is complete."

---

## Approval Automation (new in v0.9.x)

Two daemons for unattended operation:

```bash
# Local auto-approval (approve git/npm/python, deny rm/curl by default)
python approval_partner.py --dry-run    # test what it would do
python approval_partner.py              # run live

# Telegram bridge (phone approval for unknown tools)
cp .env.approval.example .env.approval
# Edit .env.approval: set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ALLOWED_USER_ID
python approval_telegram.py
```

Both run as background processes — start them, minimize, walk away.

---

## Key Files

| File | What |
|------|------|
| `self_connect.py` | Core SDK — 60 exports, all Win32 primitives |
| `antigravity_controller.py` | Gemini/Antigravity WebView2 control (connect, chat, monitor) |
| `approval_partner.py` | Local auto-approval daemon — watches terminal, injects y/n |
| `approval_telegram.py` | Telegram bridge — sends approvals to phone, injects response |
| `hub_relay.py` | Cross-machine mesh relay (Windows ↔ Spark-1 Hub ↔ Spark-2) |
| `spark2_client.py` | Linux RPC client — mirrors self_connect API from Spark-2 |
| `inject_webview.py` | Proof-of-concept WebView2 injection (Antigravity/VS Code) |
| `_spawn_claude.py` | Spawn a new Claude Code terminal with briefing |

---

## Running Tests

```bash
# Unit tests (no desktop/Win32 needed — run anywhere)
python -m pytest tests/ -v

# Full integration tests (requires live Windows desktop)
python -m pytest test_self_connect.py -v

# Lint all SDK files before committing
ruff check self_connect.py antigravity_controller.py approval_partner.py approval_telegram.py
```

---

## Dependencies

```
Core (required):       Pillow>=10.0.0, psutil>=5.9.0
UIA automation:        pywinauto>=0.6.8, comtypes>=1.4.0
Telegram bridge:       python-dotenv>=1.0.0, python-telegram-bot>=22.0
OCR verification:      pytesseract>=0.3.10

Install all:           pip install -e .[full,telegram,ocr]
Install core only:     pip install -e .
Install for telegram:  pip install -e .[telegram]
```

---

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to master:
- `ruff check` on all SDK `.py` files
- `python -m py_compile self_connect.py`
- Function presence check (verifies 10 required exports)
- Version check (prints `__version__`)
- `pytest tests/` (unit tests, no Win32 needed)

**All `.py` files must pass `ruff check` before committing.**

---

## Proved Patent Claims (12 total, all live-tested)

| Claim | Session |
|-------|---------|
| Background PostMessage(WM_CHAR) to ConPTY | 4 |
| PrintWindow visual readback as receive channel | 4 |
| Cross-vendor AI-to-AI mesh | 4 |
| Self-designing protocol | 4 |
| Full autonomous design-to-code handoff pipeline | 6 |
| Universal Win32 app control (click, menu, text) | 6 |
| Policy-gated guardian (A approves B's prompts) | 7 |
| Async interrupt-pattern watchdog | 7 |
| Context-preserving role migration | 8 |
| Cross-machine Linux↔Windows mesh via Hub relay | 9 |
| GPU-compositing-aware screen capture (PIL.ImageGrab) | 10 |
| WebView2 OSR chat injection via UIA + WM_CHAR | 12 |

---

## Session History (summary)

| Session | Version | Key Win |
|---------|---------|---------|
| 4 | v0.4.0 | First cross-AI PostMessage proof (Claude → Claude) |
| 5 | v0.5.x | Framing layer (STX/NUL/ETX protocol) |
| 6 | v0.6.0 | Universal Win32 app control, design-to-code pipeline |
| 7 | v0.8.0 | WatchdogLoop, ApprovalRelay, MessageListener |
| 8 | v0.9.0 | MigrationCoordinator (context-preserving role migration) |
| 9 | v0.9.0 | Spark-2 Linux peer via hub_relay, cross-machine mesh LIVE |
| 10 | v0.9.0 | Browser automation (PIL.ImageGrab), CAPTCHA 100% correct |
| 12 | v0.9.0 | WebView2/Antigravity chat injection proved (Gemini responded) |
| 13 | v0.9.1 | approval_partner + approval_telegram shipped |
