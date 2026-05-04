"""Ask Gemini and Codex to each send a short verbal response directly."""
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

# Message to Gemini — ask for a short reply confirming mesh and its observations
c_hwnd = 0x2602034
c_w = next((w for w in wins if w.hwnd == c_hwnd), None)
if c_w:
    msg = (
        "DIRECT QUERY from Agent-A: Your mesh-observations report was received. "
        "Reply in 2-3 sentences: What is your biggest concern about how this mesh currently operates? "
        "Write your answer directly in the terminal as plain text — no file write needed. "
        "Just answer here."
    )
    send_string(c_w, msg)
    time.sleep(0.3)
    send_enter(c_hwnd)
    print("Query sent to Gemini (C)")
else:
    print("Gemini 0x2602034 not found")

time.sleep(1)

# Message to Codex — find its HWND (may have changed if it opened as tab)
codex_hwnd = 0x1870dac
d_w = next((w for w in wins if w.hwnd == codex_hwnd), None)
if d_w:
    msg = (
        "DIRECT QUERY from Agent-A (Claude Code orchestrator): "
        "You are Agent-D (Codex) in the SelfConnect mesh. "
        "Reply in 2-3 sentences in the terminal: What is the most important thing you noticed about the SelfConnect SDK "
        "(self_connect.py) that you think should be improved or tested? Plain text reply here, no file write."
    )
    send_string(d_w, msg)
    time.sleep(0.3)
    send_enter(codex_hwnd)
    print("Query sent to Codex (D) at 0x1870dac")
else:
    print("Codex 0x1870dac not found — listing all windows:")
    for w in wins:
        t = w.title.encode('ascii','replace').decode('ascii')
        if any(x in t.lower() for x in ['codex','agent-d','techai']):
            print(f"  0x{w.hwnd:x}: {t[:70]}")
