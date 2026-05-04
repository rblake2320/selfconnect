"""Send PAUSE to all mesh agents."""
import sys, os, time, ctypes
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string

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

wins = list_windows()
agents = {"B": 0x1311316, "C": 0x2602034, "D": 0x1870dac}

msg = "MESH PAUSE from Agent-A: Owner is stepping away. All agents STAND DOWN — no new tasks, no commits, no file changes. Stay idle until A resumes. Acknowledge."

for agent_id, hwnd in agents.items():
    w = next((x for x in wins if x.hwnd == hwnd), None)
    if w:
        send_string(w, msg)
        time.sleep(0.3)
        send_enter(hwnd)
        print(f"PAUSE sent to Agent-{agent_id}")
        time.sleep(0.8)
    else:
        print(f"Agent-{agent_id} not found")
