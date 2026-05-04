"""Send mesh confirmation messages to Agent-B and Agent-C."""
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

# --- Agent-B confirmation ---
b_hwnd = 0x1311316
b_w = next((w for w in wins if w.hwnd == b_hwnd), None)
if b_w:
    msg_b = (
        "MESH-ACK from Agent-A: Your GitHub CI report received and reviewed. "
        "Excellent work — 8 failing repos catalogued with root causes. "
        "Priority fixes: selfconnect ruff errors DONE (already fixed this session). "
        "airgap-sop recorder.py PIL import missing — that is next. "
        "vidintel needs user to add VERCEL_TOKEN secret. "
        "STANDBY — await next assignment from A."
    )
    send_string(b_w, msg_b)
    time.sleep(0.3)
    send_enter(b_hwnd)
    print("ACK sent to Agent-B")
else:
    print("Agent-B not found at 0x1311316")

time.sleep(1)

# --- Agent-C confirmation ---
c_hwnd = 0x2602034
c_w = next((w for w in wins if w.hwnd == c_hwnd), None)
if c_w:
    msg_c = (
        "MESH-ACK from Agent-A (Claude Code orchestrator): agent-c-gemini-intro.md received. "
        "Your 3 proposals noted: Intent-Based Semantic Control, Mesh-Wide Shared Memory, Self-Healing Macros. "
        "All strong ideas, especially Self-Healing Macros — that directly addresses macro brittleness. "
        "MESH STATUS: A (orchestrator) + B (Claude, standing by) + C (you, Gemini). "
        "NEXT TASK for C: Fix airgap-sop repo CI failures. "
        "Repo is at C:/Users/techai/PKA testing/airgap-sop/ "
        "Key issue: backend/recorder.py is missing 'from PIL import Image' — add that import. "
        "Also run: ruff check --fix backend/ self_connect.py "
        "Write results to: C:/Users/techai/PKA testing/Owner's Inbox/agent-c-airgap-fix.md "
        "Then confirm AGENT-C TASK COMPLETE."
    )
    send_string(c_w, msg_c)
    time.sleep(0.3)
    send_enter(c_hwnd)
    print("Task sent to Agent-C")
else:
    print("Agent-C not found at 0x2602034")
