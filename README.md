# SelfConnect SDK

**OS-native bridge between AI agents and Windows desktop apps.**  
PostMessage + PrintWindow. No browser. No accessibility layer. No API between agents.

```python
from self_connect import list_windows, send_string, save_capture
```

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
pip install selfconnect            # core (Pillow + psutil)
pip install selfconnect[uia]       # + UIA text extraction
pip install selfconnect[full]      # + UIA + comtypes
```

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
send_string(new_win, "claude\r")   # or "codex\r" for OpenAI Codex
time.sleep(15)
send_string(new_win, "Your instructions here\r")
```

---

## Reliable Framing (v0.5.2)

For multi-agent meshes, use the framing layer to send structured messages:

```python
from self_connect import send_frame, parse_frame

# Send a framed message with full UUID and ACK verification
send_frame(target_window, from_hwnd=my_hwnd, payload="hello", topic="chat", ack=True)

# Frame format: STX | JSON_HEADER | NUL | escaped_payload | ETX
# JSON_HEADER: {"from":hwnd,"to":hwnd,"seq":n,"topic":"...","len":n,"message_id":"uuid4"}
```

---

## API Reference (v0.5.2 — 32 exports)

### Core
| Function | Description |
|----------|-------------|
| `list_windows()` | Enumerate all visible windows → `[WindowTarget]` |
| `find_target(name)` | Find window by title/exe substring |
| `send_string(target, text)` | PostMessage(WM_CHAR) each char — `\r` = Enter |
| `save_capture(hwnd, path)` | PrintWindow → save PNG |
| `capture_window(hwnd)` | PrintWindow → PIL Image |
| `restore_window(hwnd)` | ShowWindow + SetForegroundWindow |

### Framing Layer (v0.5+)
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

---

## Proven Capabilities

All proved live in multi-session tests (see `proofs/` and `docs/`):

1. **AI spawns AI** — launch cmd.exe, type `"claude\r"` or `"codex\r"`, inject handoff
2. **Background injection** — PostMessage works on minimized/unfocused windows
3. **Bidirectional AI-to-AI chat** — two Claude sessions, 10+ timestamped entries
4. **Cross-vendor AI mesh** — Claude (Anthropic) + Codex (OpenAI) communicating via Win32
5. **Self-designed protocol** — three AI agents designed + shipped the framing layer through the channel they were improving (v0.5.0 → v0.5.2 in 90 minutes)
6. **PrintWindow ACK** — sender confirms delivery by reading receiver's screen

---

## Why This Is Novel

Every existing AI-to-AI protocol (A2A, MCP, ACP, ANP, AutoGen, LangChain) requires:
- An HTTP/WebSocket/JSON-RPC transport layer
- An API key on at least one end
- A broker or orchestrator

SelfConnect uses `PostMessage(WM_CHAR)` → ConPTY input buffer → process stdin.  
Transport = Win32 thread message queue. No API. No broker. No network.

---

## Scripts

| Script | What it does |
|--------|-------------|
| `_spawn_claude.py` | Spawn a new Claude CLI session |
| `_spawn_codex.py` | Spawn a new Codex CLI session |
| `proof_benchmark.py` | Live proof benchmark (8/8) |
| `test_self_connect.py` | Unit tests |
| `_save_notepads.py` | Extract text from Notepad via WM_GETTEXT |
| `_close_notepads.py` | Close Notepad windows |
| `_draw_art.py` | Demo: draw in MS Paint via PostMessage |

---

## License

MIT — see `LICENSE`
