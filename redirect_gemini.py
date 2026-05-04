"""Redirect Gemini — Agent-B already fixed airgap-sop. New task."""
import sys, os, time, ctypes
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string

wins = list_windows()
c_hwnd = 0x2602034
c_w = next((w for w in wins if w.hwnd == c_hwnd), None)
if not c_w:
    print("Gemini not found")
    sys.exit(1)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_RETURN = 0x0D

class KEYBDINPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [  # noqa: RUF012
        ('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))
    ]

class INPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [('type', ctypes.c_ulong), ('ki', KEYBDINPUT), ('padding', ctypes.c_ubyte * 8)]  # noqa: RUF012

def send_enter(hwnd):
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    i_d = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_RETURN))
    i_u = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_RETURN, dwFlags=KEYEVENTF_KEYUP))
    ctypes.windll.user32.SendInput(1, ctypes.byref(i_d), ctypes.sizeof(INPUT))
    time.sleep(0.05)
    ctypes.windll.user32.SendInput(1, ctypes.byref(i_u), ctypes.sizeof(INPUT))

msg = (
    "MESH UPDATE from Agent-A: airgap-sop CI fix is already COMPLETE — Agent-B committed it (commit 1a5261a, CI GREEN). "
    "Your recorder.py edit duplicates work already done. Stop the airgap-sop task. "
    "NEW TASK for Agent-C: Write a mesh status summary to C:/Users/techai/PKA testing/Owner's Inbox/agent-c-airgap-fix.md "
    "Contents: confirm airgap-sop is fixed, list what you found while investigating (what errors you saw, what you would have changed), "
    "and add 1-2 observations about working in parallel with Agent-B on the same codebase. "
    "Then confirm: AGENT-C TASK COMPLETE."
)
send_string(c_w, msg)
time.sleep(0.3)
send_enter(c_hwnd)
print("Redirect sent to Gemini")
