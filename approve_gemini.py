"""Approve Gemini's file write dialog ('Apply this change? 1. Allow once')."""
import sys, os, time, ctypes
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows

wins = list_windows()
gemini_hwnd = 0x2602034
gemini_w = next((w for w in wins if w.hwnd == gemini_hwnd), None)
if not gemini_w:
    print("Gemini not found")
    sys.exit(1)
print("Found Gemini, sending approval...")

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_RETURN = 0x0D
VK_1 = 0x31

class KEYBDINPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [  # noqa: RUF012
        ('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))
    ]

class INPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [('type', ctypes.c_ulong), ('ki', KEYBDINPUT), ('padding', ctypes.c_ubyte * 8)]  # noqa: RUF012

ctypes.windll.user32.SetForegroundWindow(gemini_hwnd)
time.sleep(0.5)

# Send "1" to select "Allow once"
i_d = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_1))
i_u = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_1, dwFlags=KEYEVENTF_KEYUP))
ctypes.windll.user32.SendInput(1, ctypes.byref(i_d), ctypes.sizeof(INPUT))
time.sleep(0.05)
ctypes.windll.user32.SendInput(1, ctypes.byref(i_u), ctypes.sizeof(INPUT))
time.sleep(0.15)

# Send Enter
i_d2 = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_RETURN))
i_u2 = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_RETURN, dwFlags=KEYEVENTF_KEYUP))
ctypes.windll.user32.SendInput(1, ctypes.byref(i_d2), ctypes.sizeof(INPUT))
time.sleep(0.05)
ctypes.windll.user32.SendInput(1, ctypes.byref(i_u2), ctypes.sizeof(INPUT))
print("Approval sent to Gemini (1 + Enter)")
