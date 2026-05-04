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
4. **Cross-vendor AI mesh** — Claude (Anthropic) + Codex (OpenAI) + Gemini CLI (Google) communicating via Win32 terminal injection. 4-agent live mesh: Agent-A (Claude Sonnet 4.6) + Agent-B (Claude Code) + Agent-C (Gemini CLI v0.40.1) + Agent-D (Codex v0.125.0 / GPT-5)
5. **Self-designed protocol** — three AI agents designed + shipped the framing layer through the channel they were improving (v0.5.0 → v0.5.2 in 90 minutes)
6. **PrintWindow ACK** — sender confirms delivery by reading receiver's screen
7. **Claude ↔ Gemini via Win32** — Claude Code injected a message into Antigravity
   (Google's standalone Electron IDE) and Gemini 3.1 Pro replied. Confirmed live via UIA
   accessibility tree extraction. Zero API calls. Zero clipboard. Zero foreground window.

   ```
   Claude:  "Hello from Claude Agent-A. What model are you?"
   Gemini:  "Hello Claude Agent-A! 👋 I am Antigravity, an agentic AI coding assistant
            designed by the Google Deepmind team, and I'm currently running on the
            Gemini 3.1 Pro model. It's great to meet a fellow AI!"
   ```

   Full chain: `Claude Code → Python Win32 UIA+WM_CHAR → Antigravity Electron → Gemini 3.1 Pro`
   Evidence: `proofs/after_send_invoke_full.png`, `proofs/wm_char_result_panel.png`

8. **Browser automation — zero external dependencies** (Session 10, 2026-05-02) —
   Full live test against Perplexity AI using only Win32 primitives. No Playwright,
   no Selenium, no browser extension, no MCP, no WebDriver, no API calls.

   | Step | Method |
   |------|--------|
   | Screen capture | `PIL.ImageGrab.grab(bbox=window_rect, all_screens=True)` — only method that works on GPU-composited Chrome windows; `BitBlt` and `PrintWindow(PW_RENDERFULLCONTENT)` return blank pixels |
   | Click | `ctypes SetCursorPos() + mouse_event(MOUSEEVENTF_LEFTDOWN/UP)` — raw Win32 |
   | Text input | Write → temp file → PowerShell `Set-Clipboard` → `keybd_event(Ctrl+V)` |
   | Submit | `keybd_event(VK_RETURN)` |

   CAPTCHA result: **100% correct** on trial 3 — Perplexity's verdict: *"Perfect — 100% correct. J4NQ8 is exactly right."*

   New patent claim: **GPU-compositing-aware screen capture** — `PIL.ImageGrab` as the
   only viable capture path for hardware-accelerated browser windows, extending the
   SelfConnect claim from terminal-window automation to any visible composited window.
   Evidence: `proofs/checkpoint_A_s10_browser_automation.json`

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

# Connect (auto-discovers running Antigravity window)
session = connect()
print(session)  # AntigravitySession(hwnd=0x..., model='Gemini 3.1 Pro', ...)

# Send a message and get the response
response = chat(session, "What model are you?")
print(response)

# Background monitor — emit events when Gemini responds
monitor = (
    AntigravityMonitor(session)
    .on("response", lambda r: print(f"Gemini: {r}"))
    .on("model_changed", lambda m: print(f"Model switched to: {m}"))
    .start()
)
```

**Glossary** (for Windows Win32 newcomers):

| Term | Meaning |
|------|---------|
| HWND | **H**andle to a **W**i**N**dow — the unique integer Windows assigns to every window, used in all Win32 API calls |
| UIA | UI Automation — Microsoft's accessibility API for finding and controlling UI elements (buttons, inputs) without mouse/keyboard |
| OSR | Offscreen Rendering — Chromium/WebView2 mode that blocks external SendInput; WM_CHAR PostMessage bypasses it |
| WM_CHAR | Windows Message: Character — delivers a keystroke directly to a window's message queue |

**CLI:**
```bash
python antigravity_controller.py --list                        # find Antigravity windows
python antigravity_controller.py --chat "Hello, who are you?"  # send message, print response
python antigravity_controller.py --buttons                     # list all UIA button names
python antigravity_controller.py --model                       # show current model
```

---

## Scripts

| Script | What it does |
|--------|-------------|
| `antigravity_controller.py` | High-level Antigravity/Electron SDK — `connect`, `chat`, `AntigravityMonitor` |
| `inject_webview.py` | Low-level proof script — UIA+WM_CHAR injection into any Electron app |
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
