"""Screenshot all Codex/new windows."""
import ctypes, os, time
from PIL import ImageGrab

user32 = ctypes.windll.user32
os.makedirs("C:/Users/techai/PKA testing/selfconnect/proofs", exist_ok=True)

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

def grab(hwnd, label):
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
    out = f"C:/Users/techai/PKA testing/selfconnect/proofs/{label}.png"
    img.save(out)
    print(f"Saved {out}")

targets = [
    (0x1870dac, "codex_terminal"),
    (0x17212de, "codex_app"),
    (0x4a11ce, "agent_d_window"),
]

for hwnd, label in targets:
    try:
        grab(hwnd, label)
    except Exception as e:
        print(f"0x{hwnd:x} {label}: {e}")
