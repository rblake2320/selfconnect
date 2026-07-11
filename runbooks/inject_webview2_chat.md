# Runbook: Inject Text into WebView2 Chat (Gemini / Antigravity / VS Code)
<!-- selfconnect-runbook: {"status":"current","since":"2026-05-05","replacement":null,"entrypoint":false,"kind":"specialized"} -->

## What
Type text into a WebView2-hosted chat interface — Gemini in Antigravity, VS Code extension
chat panels, or any Electron app using the embedded WebView2 control.

## Prerequisites
- `pywinauto>=0.6.8`, `comtypes>=1.4.0` installed
- `self_connect` available on sys.path
- Target window must be **visible** (not minimized)
- You need the HWND of the WebView2 host window

## Steps

### 1. Find the target window
```python
import sys
sys.path.insert(0, '.')
from self_connect import list_windows

windows = list_windows()
# Antigravity / Gemini appears as a VS Code window titled "Antigravity" or similar
target = next((w for w in windows
               if 'Antigravity' in w.title or 'Gemini' in w.title), None)
print(f"Found: hwnd={target.hwnd}  exe={target.exe_name!r}")
```

### 2. Expand the UIA accessibility tree via AccessibleObjectFromWindow
```python
import ctypes, ctypes.wintypes, comtypes, comtypes.client

# Without this call the UIA tree shows only ~2 nodes — the chat input is invisible
OBJID_CLIENT = -4
IID_IAccessible = comtypes.GUID("{618736E0-3C3D-11CF-810C-00AA00389B71}")

accessible = comtypes.POINTER(comtypes.gen.Accessibility.IAccessible)()
hr = ctypes.windll.oleacc.AccessibleObjectFromWindow(
    target.hwnd, OBJID_CLIENT,
    ctypes.byref(IID_IAccessible),
    ctypes.byref(accessible)
)
if hr != 0:
    raise RuntimeError(f"AccessibleObjectFromWindow failed: hr=0x{hr:08x}")
# After this call the UIA tree expands from ~2 nodes to ~268 nodes
print("UIA tree expanded")
```

### 3. Find the chat input element and inject text
```python
from self_connect import get_text_uia, send_string

# Use UIA to locate the chat textarea — look for role=Edit with empty or placeholder text
# The exact approach depends on the app; for Antigravity the input is a contenteditable div.
# send_string uses PostMessage(WM_CHAR) — works without focus for ConPTY targets.
# For WebView2, you may need send_keys() instead (requires foreground).

send_string(target, "Your message here\r")
print("Injected message")
```

### 4. Alternative: use antigravity_controller.py (higher-level)
```python
from antigravity_controller import AntigravityController

ac = AntigravityController()
ac.connect()             # finds the window, expands UIA tree
ac.send_chat("Hello")    # types and submits
response = ac.read_response(timeout=30)
print(f"Response: {response}")
```

## Known Failures

- **UIA tree shows only 2 nodes → can't find chat input**: You skipped Step 2.
  `AccessibleObjectFromWindow` must be called before UIA enumeration. This was
  rediscovered multiple times — always call it first.
- **`send_string()` types characters but doesn't submit**: WebView2 may not respond to
  `PostMessage(WM_CHAR)` for Enter/Submit. Use `send_keys()` after bringing window to
  foreground. See `enter_claude_tui.md` for the SendInput pattern.
- **Window found but `send_string` silently does nothing**: Check that `target.exe_name`
  is a WebView2 host (e.g., `Code.exe`, `msedgewebview2.exe`). Some Electron apps route
  input differently — may need click-first.

## Verified
- Session 12 (2026-04-xx) — Gemini (Antigravity) responded to injected message
- Proved Patent Claim #12: WebView2 OSR chat injection via UIA + WM_CHAR
