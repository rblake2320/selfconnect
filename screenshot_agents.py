"""Capture screenshots of Agent-B and Agent-C terminals using PIL."""
import sys, os, ctypes, time
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import ImageGrab
import ctypes

user32 = ctypes.windll.user32

def grab_window(hwnd):
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))

os.makedirs("C:/Users/techai/PKA testing/selfconnect/proofs", exist_ok=True)

for label, hwnd_val in [("agent_b", 0x1311316), ("agent_c_gemini", 0x2602034)]:
    try:
        # bring window to front briefly
        user32.SetForegroundWindow(hwnd_val)
        time.sleep(0.5)
        img = grab_window(hwnd_val)
        out = f"C:/Users/techai/PKA testing/selfconnect/proofs/{label}_status.png"
        img.save(out)
        print(f"Saved: {out}")
    except Exception as e:
        print(f"{label} 0x{hwnd_val:x} error: {e}")
