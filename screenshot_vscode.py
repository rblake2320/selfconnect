"""Screenshot VS Code properly — maximize first."""
import ctypes, time
from PIL import ImageGrab

user32 = ctypes.windll.user32
VSCODE_HWND = 0xca088e

SW_MAXIMIZE = 3
SW_RESTORE = 9

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

# Force to foreground
user32.ShowWindow(VSCODE_HWND, SW_RESTORE)
user32.SetForegroundWindow(VSCODE_HWND)
user32.BringWindowToTop(VSCODE_HWND)
time.sleep(1.0)  # give it a second to paint

rect = RECT()
user32.GetWindowRect(VSCODE_HWND, ctypes.byref(rect))
img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
img.save("C:/Users/techai/PKA testing/selfconnect/proofs/vscode_antigravity.png")
print(f"Captured: {rect.right-rect.left}x{rect.bottom-rect.top} at ({rect.left},{rect.top})")
