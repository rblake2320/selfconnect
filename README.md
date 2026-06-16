# SelfConnect SDK v0.10.2

**OS-native bridge between AI agents and Windows desktop apps.**  
PostMessage + PrintWindow. No browser. No accessibility layer. No API between agents.

```python
from self_connect import list_windows, send_string, save_capture
```

---

## Why Runbooks Exist

`runbooks/capture_chrome_window.md` shows session 10 and session 14 in its Verified section.
The same procedure was rediscovered twice before anyone wrote it down. That's what this
system exists to prevent.

Six runbooks are included — each one documents a Win32 procedure that cost at least one
full session of trial-and-error before it was proved. `runbook_writer.py` generates new
ones automatically when an operation requires 3+ retries.

---

## What It Does

SelfConnect lets a frontier AI model (Claude, Codex, or any CLI agent) control Windows
desktop applications using raw Win32 APIs — and communicate with *other AI agents* through
the same channel.

```
AI Agent A                         AI Agent B
    │                                   │
    ├─ PrintWindow(hwnd_B) ──────────>  │  reads B's screen
    │                                   │
    ├─ PostMessage(WM_CHAR) ─────────>  │  types into B's terminal
    │                                   │
    │  <──── PostMessage(WM_CHAR) ──────┤  B types into A's terminal
    │                                   │
    └─ PrintWindow(hwnd_A) <──────────  ┘  B reads A's screen
```

Zero API calls between agents. Zero network traffic. Two functions from `user32.dll`.

---

## Installation

```bash
pip install selfconnect                  # core (Pillow + psutil)
pip install selfconnect[uia]             # + UIA text extraction
pip install selfconnect[mcp]             # + MCP server adapter
pip install selfconnect[full]            # + UIA + comtypes
pip install selfconnect[telegram]        # + Telegram approval bridge
pip install selfconnect[claudego]        # + ClaudeGo web dashboard
```

From this GitHub branch for cross-machine testing:

```bash
pip install "selfconnect[full,mcp] @ git+https://github.com/rblake2320/selfconnect.git@test/win32-hardening-v1"
```

---

## Package Probes

The package installs a `selfconnect` command for repeatable testing on other
Windows systems:

```bash
selfconnect doctor --json
selfconnect doctor --windows
selfconnect windows --query "Claude"
selfconnect read --hwnd 0x123456
selfconnect capture --hwnd 0x123456 --path proof.png
```

Input delivery is intentionally gated:

```bash
selfconnect send --hwnd 0x123456 --text "hello" --submit --allow-input
# or set SELFCONNECT_ALLOW_INPUT=1
```

---

## MCP Server

Install the optional MCP dependency and run:

```bash
pip install "selfconnect[mcp] @ git+https://github.com/rblake2320/selfconnect.git@test/win32-hardening-v1"
selfconnect-mcp
```

The MCP server exposes:

- `doctor`
- `list_windows`
- `read_window`
- `capture_window`
- `send_text`

The `send_text` tool is disabled unless explicitly enabled:

```bash
set SELFCONNECT_MCP_ALLOW_INPUT=1
selfconnect-mcp
```

The branch also carries a repo-local Codex skill at
`skills/selfconnect-win32/` and the composed Win32 proof at
`experiments/win32_probe/chained_channel.py`; both are included in the built
wheel for traceability.

---

## Quick Start

```python
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from self_connect import list_windows, send_string, save_capture

# Find all visible windows
for w in list_windows():
    print(f"hwnd={w.hwnd} title={w.title[:60]!r} exe={w.exe_name!r}")

# Type into a window (including Enter) — works on background/minimized windows
send_string(window_target, "your message here\r")  # \r = Enter key

# Capture any window's pixels
save_capture(hwnd, path="proofs/capture.png")
```

---

## Approval Automation

Two daemons for unattended operation. Run them in the background — walk away.

### Local auto-approval (`approval_partner.py`)

Watches all Claude Code terminal windows, detects approval prompts, and injects
`y` or `n` based on allow/deny rules. Unknown tools are escalated.

```bash
python approval_partner.py                # run with default rules
python approval_partner.py --dry-run      # detect prompts but don't inject
python approval_partner.py --approve-all  # approve everything (use with care)
python approval_partner.py --list-windows # show detected Claude terminals
```

Default allow: `Bash(git:*)`, `Bash(npm:*)`, `Bash(python:*)`, `Bash(gh:*)`,
`Read(*)`, `Write(*)`, `Edit(*)`, `Glob(*)`, `Grep(*)`

Default deny: `Bash(rm:*)`, `Bash(rmdir:*)`, `Bash(curl:*)`, `Bash(wget:*)`

### Telegram bridge (`approval_telegram.py`)

Escalates unknown tools to your phone. Tap approve/deny — response is injected
back into the terminal automatically.

```bash
cp .env.approval.example .env.approval
# Edit: set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ALLOWED_USER_ID
python approval_telegram.py
```

Run both together for full coverage: local daemon handles known-safe tools instantly;
unknown tools hit your phone.

---

## ClaudeGo Dashboard

A lightweight web dashboard for monitoring the Claude Code mesh in real time.

```bash
python -m claudego           # starts on http://localhost:9090
```

Features:
- Live window list with approval-prompt status indicators (green / yellow / red)
- System tray icon (`claudego/tray.py`) — color reflects mesh health
- Desktop toast notifications (`claudego/notifier.py`) via winotify

```bash
pip install selfconnect[claudego]   # installs pystray + winotify
```

---

## Spawn Another AI Session

```python
import subprocess, ctypes, time
from self_connect import list_windows, send_string, restore_window

before = {w.hwnd for w in list_windows()}
proc = subprocess.Popen(
    ["cmd.exe", "/k", "cd /d C:\\your\\project"],
    creationflags=subprocess.CREATE_NEW_CONSOLE
)
time.sleep(2.5)

# Find new window via hwnd-set-diff
new_win = None
user32 = ctypes.windll.user32
for w in list_windows():
    if w.hwnd not in before:
        cb = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(w.hwnd, cb, 256)
        if "CASCADIA" in cb.value.upper() or "Console" in cb.value:
            new_win = w; break

restore_window(new_win.hwnd)
time.sleep(0.3)
send_string(new_win, "claude\r")   # or "codex\r" / "gemini\r"
time.sleep(15)
send_string(new_win, "Your instructions here\r")
```

---

## Reliable Framing

For multi-agent meshes, use the framing layer to send structured messages:

```python
from self_connect import send_frame, parse_frame

# Send a framed message with full UUID and ACK verification
send_frame(target_window, from_hwnd=my_hwnd, payload="hello", topic="chat", ack=True)

# Frame format: STX | JSON_HEADER | NUL | escaped_payload | ETX
# JSON_HEADER: {"from":hwnd,"to":hwnd,"seq":n,"topic":"...","len":n,"message_id":"uuid4"}
```

---

## API Reference (v0.10.0 — 60 exports)

Full export list and signatures: see `CLAUDE.md` → Key Files section.

### Core
| Function | Description |
|----------|-------------|
| `list_windows()` | Enumerate all visible windows → `[WindowTarget]` |
| `find_target(name)` | Find window by title/exe substring |
| `send_string(target, text)` | PostMessage(WM_CHAR) each char — `\r` = Enter |
| `save_capture(hwnd, path)` | PrintWindow → save PNG |
| `capture_window(hwnd)` | PrintWindow → PIL Image |
| `restore_window(hwnd)` | ShowWindow + SetForegroundWindow |

### Framing Layer
| Function | Description |
|----------|-------------|
| `build_frame(from_hwnd, to_hwnd, payload, topic, seq)` | Build STX\|JSON\|NUL\|payload\|ETX string |
| `parse_frame(raw)` | Extract + validate + unescape frame from buffer |
| `send_frame(target, from_hwnd, payload, ...)` | Build + send, optional ACK |
| `verify_delivery(hwnd, fingerprint, timeout, ...)` | PrintWindow ACK — confirms receiver saw message |

### Window Info
| Function | Description |
|----------|-------------|
| `get_own_terminal_hwnd()` | Get this process's terminal HWND |
| `get_child_texts(hwnd)` | WM_GETTEXT on child windows |
| `get_text_uia(hwnd)` | UIA text extraction (requires pywinauto) |
| `get_clipboard_text()` | Read clipboard |
| `set_clipboard_text(text)` | Write clipboard |

### Extended (v0.8+)
| Class / Function | Description |
|----------|-------------|
| `WatchdogLoop` | Async watchdog — fires callback when pattern appears in target window |
| `ApprovalRelay` | Policy-gated guardian — A approves B's tool prompts |
| `MessageListener` | Async interrupt-pattern listener for incoming framed messages |
| `MigrationCoordinator` | Context-preserving role migration between agents |
| `send_keys(target, keys)` | SendInput (foreground) — for Enter/submit in Claude Code TUI |

---

## Session History

| Session | Version | Key Win |
|---------|---------|---------|
| 4 | v0.4.0 | First cross-AI PostMessage proof (Claude → Claude) |
| 5 | v0.5.x | Framing layer (STX/NUL/ETX protocol) — self-designed by agents |
| 6 | v0.6.0 | Universal Win32 app control; full design-to-code pipeline |
| 7 | v0.8.0 | WatchdogLoop, ApprovalRelay, MessageListener |
| 8 | v0.9.0 | MigrationCoordinator — context-preserving role migration |
| 9 | v0.9.0 | Spark-2 Linux peer via hub_relay — cross-machine mesh live |
| 10 | v0.9.0 | Browser automation (PIL.ImageGrab); CAPTCHA 100% correct |
| 11 | v0.9.0 | PyPI publish readiness; CI green on master |
| 12 | v0.9.0 | WebView2/Antigravity chat injection proved (Gemini responded) |
| 13 | v0.9.1 | approval_partner + approval_telegram shipped |
| 14 | v0.10.0 | ClaudeGo dashboard (tray + notifier); peer_watcher rules engine |

---

## What Works / What Doesn't

| Method | Works? | Notes |
|--------|--------|-------|
| `PostMessage(WM_CHAR)` to Windows Terminal | YES | The core mechanism |
| `PostMessage(WM_CHAR)` to cmd.exe | YES | Legacy console |
| `send_string` with `\r` inside string | YES | Enter key — MUST be in string |
| `PostMessage(WM_CHAR)` to UWP Notepad | NO | TSF input, silently drops |
| `SendInput` from background | NO | Always goes to foreground |
| Separate `PostMessage(WM_CHAR, 13)` for Enter | NO | Ignored by Windows Terminal |
| `WM_KEYDOWN/WM_KEYUP` for VK_RETURN | NO | Ignored by Windows Terminal |
| `PostMessage(WM_CHAR)` to Chrome_RenderWidgetHostHWND | YES | WebView2/Electron chat injection — requires UIA `set_focus()` first |
| `SendInput` to WebView2 (OSR mode) | NO | Offscreen rendering blocks all external input |
| Clipboard paste into WebView2 | NO | Claude Code subprocess is sandboxed from clipboard |
| UIA `set_focus()` without Win32 foreground | YES | Transfers Blink focus internally — works behind lock screen |
| UIA `invoke()` on WebView2 buttons | YES | Submits chat, dismisses popups — no click events needed |

---

## Proven Capabilities

All proved live in multi-session tests (see `proofs/` and `docs/`):

1. **AI spawns AI** — launch cmd.exe, type `"claude\r"`, `"gemini\r"`, or `"codex\r"`, inject handoff
2. **Background injection** — PostMessage works on minimized/unfocused windows
3. **Bidirectional AI-to-AI chat** — two Claude sessions, 10+ timestamped entries
4. **Cross-vendor AI mesh** — Claude + Codex + Gemini CLI communicating via Win32 terminal injection. 4-agent live mesh: Agent-A (Claude Sonnet 4.6) + Agent-B (Claude Code) + Agent-C (Gemini CLI) + Agent-D (Codex / GPT-5)
5. **Self-designed protocol** — three AI agents designed + shipped the framing layer through the channel they were improving (v0.5.0 → v0.5.2 in 90 minutes)
6. **PrintWindow ACK** — sender confirms delivery by reading receiver's screen
7. **Claude ↔ Gemini via Win32** — Claude Code injected a message into Antigravity
   (Google's standalone Electron IDE) and Gemini 3.1 Pro replied. Zero API calls. Zero clipboard. Zero foreground window.

   ```
   Claude:  "Hello from Claude Agent-A. What model are you?"
   Gemini:  "Hello Claude Agent-A! I am Antigravity, running on Gemini 3.1 Pro."
   ```

   Full chain: `Claude Code → Python Win32 UIA+WM_CHAR → Antigravity Electron → Gemini 3.1 Pro`

8. **Browser automation — zero external dependencies** (Session 10) —
   Full live test against Perplexity AI using only Win32 primitives. No Playwright,
   no Selenium, no browser extension, no MCP, no WebDriver, no API calls.
   CAPTCHA result: **100% correct** — `PIL.ImageGrab` as the only viable capture path
   for GPU-composited browser windows.

---

## Why This Is Novel

Every existing AI-to-AI protocol (A2A, MCP, ACP, ANP, AutoGen, LangChain) requires:
- An HTTP/WebSocket/JSON-RPC transport layer
- An API key on at least one end
- A broker or orchestrator

SelfConnect uses `PostMessage(WM_CHAR)` → ConPTY input buffer → process stdin.  
Transport = Win32 thread message queue. No API. No broker. No network.

---

## Antigravity / Electron Chat Automation

`antigravity_controller.py` — high-level SDK for controlling Antigravity (Google's
Electron IDE) and any Electron/WebView2 chat interface programmatically.

```python
from antigravity_controller import connect, chat, AntigravityMonitor

session = connect()
response = chat(session, "What model are you?")

monitor = (
    AntigravityMonitor(session)
    .on("response", lambda r: print(f"Gemini: {r}"))
    .start()
)
```

**CLI:**
```bash
python antigravity_controller.py --list
python antigravity_controller.py --chat "Hello, who are you?"
python antigravity_controller.py --model
```

---

## Scripts

| Script | What it does |
|--------|-------------|
| `antigravity_controller.py` | High-level Antigravity/Electron SDK — `connect`, `chat`, `AntigravityMonitor` |
| `approval_partner.py` | Local auto-approval daemon — watches terminals, injects y/n via rules engine |
| `approval_telegram.py` | Telegram bridge — phone approval for unknown tools |
| `peer_watcher.py` | Rules-based peer approval watcher for a specific agent window |
| `hub_relay.py` | Cross-machine mesh relay (Windows ↔ Spark-1 ↔ Spark-2) |
| `spark2_client.py` | Linux RPC client — mirrors self_connect API from Spark-2 |
| `inject_webview.py` | Low-level proof script — UIA+WM_CHAR injection into any Electron app |
| `_spawn_claude.py` | Spawn a new Claude CLI session |
| `proof_benchmark.py` | Live proof benchmark |
| `test_self_connect.py` | Unit tests |

---

## License

MIT — see `LICENSE`
