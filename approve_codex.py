"""Approve Codex's command prompt (send 'y' + Enter)."""
import sys, os, time, ctypes
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows

wins = list_windows()
codex_hwnd = 0x1870dac
codex_w = next((w for w in wins if w.hwnd == codex_hwnd), None)
if not codex_w:
    print("Not found")
    sys.exit(1)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

class KEYBDINPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [  # noqa: RUF012
        ('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))
    ]

class INPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [('type', ctypes.c_ulong), ('ki', KEYBDINPUT), ('padding', ctypes.c_ubyte * 8)]  # noqa: RUF012

def send_key(hwnd, vk):
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    i_d = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk))
    i_u = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP))
    ctypes.windll.user32.SendInput(1, ctypes.byref(i_d), ctypes.sizeof(INPUT))
    time.sleep(0.05)
    ctypes.windll.user32.SendInput(1, ctypes.byref(i_u), ctypes.sizeof(INPUT))

VK_Y = 0x59
VK_RETURN = 0x0D

# Send 'y' to approve
send_key(codex_hwnd, VK_Y)
time.sleep(0.1)
send_key(codex_hwnd, VK_RETURN)
print("Approved Codex command (y + Enter)")
