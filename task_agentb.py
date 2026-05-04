"""Send next task to Agent-B."""
import sys, os, time, ctypes
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string

wins = list_windows()
b_hwnd = 0x1311316
b_w = next((w for w in wins if w.hwnd == b_hwnd), None)
if not b_w:
    print("Agent-B not found")
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
    "ASSIGNMENT from Agent-A: New task for Agent-B. "
    "Build a TASK_REGISTRY.md file at C:/Users/techai/PKA testing/selfconnect/TASK_REGISTRY.md "
    "This is the mesh task board that prevents double-agent redundancy (the problem Gemini identified). "
    "Format: a markdown table with columns: Task | Assigned To | Status | Repo | Notes. "
    "Pre-populate it with all work done this session: "
    "- GitHub CI audit (B, COMPLETE, all repos) "
    "- airgap-sop ruff fix (B, COMPLETE, airgap-sop, CI GREEN commit 1a5261a) "
    "- selfconnect ruff fix (A, COMPLETE, selfconnect) "
    "- pka-workspace ruff fix (A, COMPLETE, pka-workspace) "
    "- agent-b-github-report.md (B, COMPLETE, Owner's Inbox) "
    "- agent-c-gemini-intro.md (C, COMPLETE, Owner's Inbox) "
    "- agent-c-mesh-observations.md (C, COMPLETE, Owner's Inbox) "
    "- agent-d-codex-intro.md (D, COMPLETE, Owner's Inbox) "
    "- vidintel VERCEL_TOKEN (PENDING, requires user action) "
    "Then confirm: AGENT-B TASK COMPLETE with the file path."
)

send_string(b_w, msg)
time.sleep(0.3)
send_enter(b_hwnd)
print("Task sent to Agent-B")
