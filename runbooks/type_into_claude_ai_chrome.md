# Runbook: Type into Claude.ai Chat Input via Chrome

## What
Send text to the Claude.ai chat input in Google Chrome using SelfConnect SDK
(PostMessage WM_CHAR to the render widget). No MCP browser tools, no Playwright.

## Prerequisites
- `Pillow>=10.0.0`, `self_connect` on sys.path
- Chrome window must be visible (not minimized)
- DPI awareness **must** be set before any Win32 coordinate calls

## Steps

### 1. Set DPI awareness FIRST (critical on high-DPI displays)
```python
import sys
sys.path.insert(0, '.')
from self_connect import set_dpi_aware
set_dpi_aware()  # MUST be before any GetWindowRect or click_at call
```

### 2. Find the Chrome window
```python
from self_connect import list_windows

wins = list_windows()
chrome = next(w for w in wins if 'claude' in w.title.lower() and 'chrome' in w.title.lower())
print(f"hwnd={chrome.hwnd}  title={chrome.title!r}")
```

### 3. Find the Chrome_RenderWidgetHostHWND child
```python
import ctypes, ctypes.wintypes

user32 = ctypes.windll.user32
RENDER_HWND = None

def cb(hwnd, lp):
    global RENDER_HWND
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    if buf.value == 'Chrome_RenderWidgetHostHWND' and RENDER_HWND is None:
        RENDER_HWND = hwnd
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ssize_t, ctypes.c_ssize_t)
user32.EnumChildWindows(chrome.hwnd, WNDENUMPROC(cb), 0)
print(f"Render widget: {RENDER_HWND}")
```

### 4. Get Chrome window rect (physical pixels, requires DPI awareness from Step 1)
```python
rect = ctypes.wintypes.RECT()
user32.GetWindowRect(chrome.hwnd, ctypes.byref(rect))
l, t, r, b = rect.left, rect.top, rect.right, rect.bottom
print(f"Chrome physical rect: ({l},{t},{r},{b})  size={r-l}x{b-t}")
```

### 5. Capture and locate the input box
```python
from PIL import ImageGrab

img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)

# Scan bottom 200px for white rows (input box background = white, page = off-white)
h, w = img.size[1], img.size[0]
white_rows = []
for py in range(h - 200, h):
    whites = sum(1 for px in range(w // 4, 3 * w // 4)
                 if img.getpixel((px, py))[:3] == (255, 255, 255))
    if whites > 50:
        white_rows.append(py)

input_cy = t + (white_rows[0] + white_rows[-1]) // 2
input_cx = (l + r) // 2
print(f"Input box center: screen ({input_cx}, {input_cy})")
```

### 6. Click the input box to focus it
```python
import time
from self_connect import click_at

# Compute render widget rect for client-coord click
rw_rect = ctypes.wintypes.RECT()
user32.GetWindowRect(RENDER_HWND, ctypes.byref(rw_rect))

cx_client = input_cx - rw_rect.left
cy_client = input_cy - rw_rect.top
lParam = ctypes.c_long((cy_client << 16) | (cx_client & 0xFFFF)).value

user32.SetForegroundWindow(chrome.hwnd)
time.sleep(0.3)
user32.PostMessageW(RENDER_HWND, 0x0201, 0x0001, lParam)  # WM_LBUTTONDOWN
time.sleep(0.05)
user32.PostMessageW(RENDER_HWND, 0x0202, 0, lParam)       # WM_LBUTTONUP
time.sleep(0.5)
```

### 7. Type the message via WM_CHAR
```python
WM_CHAR = 0x0102

def post_char(ch):
    user32.PostMessageW(RENDER_HWND, WM_CHAR, ord(ch), 1)
    time.sleep(0.02)

for ch in "Your message here":
    post_char(ch)
```

### 8. Submit with Enter
```python
WM_KEYDOWN = 0x0100
WM_KEYUP   = 0x0101
VK_RETURN  = 0x0D

user32.PostMessageW(RENDER_HWND, WM_KEYDOWN, VK_RETURN, 1)
time.sleep(0.05)
user32.PostMessageW(RENDER_HWND, WM_KEYUP, VK_RETURN, 1)
print("Submitted")
```

### 9. Wait for response and read via UIA
```python
from self_connect import get_text_uia
import time

for _ in range(60):
    time.sleep(2)
    text = get_text_uia(chrome.hwnd) or ''
    # Simple heuristic: response appeared if last 200 chars changed
    print(f"Last chars: {text[-100:]!r}")
    if 'Write a message' in text and len(text) > prev_len:
        break
```

## Known Failures

- **Typing does nothing (text doesn't appear)**: DPI awareness not set — characters go to wrong
  window or wrong position. Always call `set_dpi_aware()` FIRST.
- **Click opens wrong element / new tab**: pre-DPI coordinates were logical (×0.8 scale),
  causing cursor to land outside the input. Fix: call `set_dpi_aware()` before any rect lookup.
- **WM_CHAR types but Enter doesn't submit**: Some Claude.ai versions require `VK_RETURN` via
  `WM_KEYDOWN` (not `WM_CHAR`). Use `PostMessageW(RENDER_HWND, WM_KEYDOWN, VK_RETURN, 1)`.
- **Input not visible in capture**: Page is still generating. Spinner present = input scrolled
  below viewport. Wait for generation to complete before attempting to interact.
- **Chrome render widget not found (RENDER_HWND=None)**: Chrome may have restarted and created
  a new HWND. Re-run EnumChildWindows to get fresh HWND values.

## DPI Notes
- Verified on 4096×1728 display with 1.25× DPI scale (physical = 5120×2160)
- Pre-DPI: GetWindowRect returns logical coords; mixing with physical PIL pixels causes ~25% offset
- Post-DPI: all Win32 calls use physical pixels; PIL captures physical pixels; no translation needed
- Multi-monitor: if Chrome spans monitors with different DPI, use GetDpiForMonitor per monitor

## Verified
- Session 15 (2026-05-16) — typed and submitted message to Claude.ai Opus 4.7
- Working pattern: set_dpi_aware() → EnumChildWindows → PostMessage WM_LBUTTONDOWN → WM_CHAR loop → WM_KEYDOWN VK_RETURN
