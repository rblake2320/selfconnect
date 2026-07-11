# Runbook: Capture Chrome/Edge/Electron Window
<!-- selfconnect-runbook: {"status":"current","since":"2026-05-05","replacement":null,"entrypoint":false,"kind":"specialized"} -->

## What
Capture the visible content of a Chrome, Edge, or Electron window as a PIL Image for OCR,
screenshot diffing, or visual readback.

## Prerequisites
- `Pillow>=10.0.0` installed (`pip install Pillow`)
- Target window must be **visible and unobstructed** — not minimized, not behind another window
- `self_connect` available on sys.path

## Steps

### 1. Find the target window
```python
import sys
sys.path.insert(0, '.')
from self_connect import list_windows, find_target

windows = list_windows()
target = next((w for w in windows if 'Chrome' in w.title or 'Antigravity' in w.title), None)
if not target:
    raise RuntimeError("Target window not found — check list_windows() output")
print(f"Found: hwnd={target.hwnd}  title={target.title!r}")
```

### 2. Get the window rectangle (physical pixels)
```python
import ctypes

rect = ctypes.wintypes.RECT()
ctypes.windll.user32.GetWindowRect(target.hwnd, ctypes.byref(rect))
l, t, r, b = rect.left, rect.top, rect.right, rect.bottom
print(f"Window rect: ({l}, {t}, {r}, {b})  size={r-l}x{b-t}")
```

### 3. Capture with PIL.ImageGrab (the only method that works)
```python
from PIL import ImageGrab

img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
img.save("capture_output.png")
print(f"Captured: {img.size[0]}x{img.size[1]} px saved to capture_output.png")
```

### 4. DPI correction (if coordinates seem off on 120 DPI displays)
```python
# Chrome runs at 120 DPI (1.25x scale). UIA returns logical coords.
# GetWindowRect returns physical pixels. PIL captures at logical resolution.
DPI_SCALE = 1.25  # for 120 DPI — adjust if display is 96 DPI (1.0x)

# To convert a UIA logical coordinate to a PIL pixel offset:
# pixel_x = (uia_x - l) / DPI_SCALE
# pixel_y = (uia_y - t) / DPI_SCALE
```

## Known Failures

- **`PrintWindow(hwnd, hdc, 0)` → black image**: Chrome uses GPU compositing (Direct3D).
  PrintWindow only captures the GDI layer — Chrome renders nothing there. Do NOT use.
- **`BitBlt` from DC → black image**: Same root cause as PrintWindow. Do NOT use.
- **Minimized window → black or wrong-size capture**: ImageGrab cannot capture a minimized
  window. Restore it first: `ctypes.windll.user32.ShowWindow(hwnd, 9)` (SW_RESTORE).
- **Wrong rect on multi-monitor with different DPI per monitor**: GetWindowRect returns
  virtual screen coords. `all_screens=True` is required — without it PIL only captures
  the primary monitor region.

## Verified
- Session 10 (2026-04-xx) — CAPTCHA recognition 100% correct using this method
- Session 14 (2026-05-04) — confirmed still working on RTX 5090 system, 120 DPI display
