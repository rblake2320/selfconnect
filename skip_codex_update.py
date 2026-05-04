"""Skip the Codex update dialog and brief it on the mesh."""
import sys, os, time, ctypes
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string

wins = list_windows()
codex_hwnd = 0x1870dac
codex_w = next((w for w in wins if w.hwnd == codex_hwnd), None)
if not codex_w:
    print("Codex window not found")
    sys.exit(1)
print(f"Found: {codex_w.title.encode('ascii','replace').decode('ascii')[:60]}")

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_RETURN = 0x0D
VK_2 = 0x32

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

# Send "2" (Skip) then Enter
send_key(codex_hwnd, VK_2)
time.sleep(0.1)
send_key(codex_hwnd, VK_RETURN)
print("Sent '2' (Skip update) + Enter")
time.sleep(3)

# Now send the mesh briefing
briefing = (
    "You are Agent-D (Codex) in the SelfConnect AI mesh. "
    "Mesh: A=Claude Code orchestrator + B=Claude Code (hwnd 0x1311316) + C=Gemini CLI (hwnd 0x2602034) + D=YOU (Codex). "
    "SelfConnect is a Win32 AI-to-AI system: agents communicate via PostMessage(WM_CHAR) to inject text into each other's terminals. No cloud broker. Patent pending (P-SELFCONNECT). "
    "SDK: C:/Users/techai/PKA testing/selfconnect/self_connect.py "
    "YOUR TASK: Read the SelfConnect PRODUCTION_REVIEW.md at C:/Users/techai/PKA testing/selfconnect/PRODUCTION_REVIEW.md "
    "Then write your intro + 3 improvement ideas to: C:/Users/techai/PKA testing/Owner's Inbox/agent-d-codex-intro.md "
    "Then confirm: AGENT-D ONLINE AND BRIEFED"
)
send_string(codex_w, briefing)
time.sleep(0.3)
send_key(codex_hwnd, VK_RETURN)
print("Mesh briefing sent to Codex")
