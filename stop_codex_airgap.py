"""Stop Codex from working on airgap-sop — redirect to selfconnect."""
import sys, os, time, ctypes
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B

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

wins = list_windows()

# The airgap-sop Codex window
codex_airgap_hwnd = 0x6515ca
w = next((x for x in wins if x.hwnd == codex_airgap_hwnd), None)
if w:
    t = w.title.encode('ascii','replace').decode('ascii')
    print(f"Found: 0x{w.hwnd:x} {t[:60]}")
    # Send Escape to interrupt any in-progress task
    send_key(codex_airgap_hwnd, VK_ESCAPE)
    time.sleep(0.5)
    msg = (
        "STOP. You are Agent-D (Codex) in the SelfConnect mesh. "
        "airgap-sop is NOT your task — Agent-B already fixed it and it is CI GREEN. "
        "Do NOT make any more commits to airgap-sop. "
        "Your only task is done: agent-d-codex-intro.md is in Owner's Inbox. "
        "STANDBY — await next assignment from Agent-A (Claude Code orchestrator). "
        "Do not touch any repos until given a new task."
    )
    send_string(w, msg)
    time.sleep(0.3)
    send_key(codex_airgap_hwnd, VK_RETURN)
    print("Stop order sent to Codex airgap window")
else:
    print("0x6515ca not found — listing codex-related windows:")
    for x in wins:
        t = x.title.encode('ascii','replace').decode('ascii')
        if any(k in t.lower() for k in ['codex','airgap','techai']):
            print(f"  0x{x.hwnd:x}: {t[:70]}")

# Also send to the main Codex terminal in case it's different
main_codex_hwnd = 0x1870dac
w2 = next((x for x in wins if x.hwnd == main_codex_hwnd), None)
if w2:
    t2 = w2.title.encode('ascii','replace').decode('ascii')
    print(f"Also messaging main Codex: 0x{w2.hwnd:x} {t2[:50]}")
    send_string(w2, "STOP any airgap-sop work. STANDBY for next assignment from A.")
    time.sleep(0.3)
    send_key(main_codex_hwnd, VK_RETURN)
