"""Send path correction to Gemini."""
import sys, os, time, ctypes
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string

wins = list_windows()
gemini_hwnd = 0x2602034
gemini_w = next((w for w in wins if w.hwnd == gemini_hwnd), None)
if not gemini_w:
    print("Gemini not found")
    sys.exit(1)

correction = (
    "STOP searching other directories. The SelfConnect project is ONLY at:\n"
    "C:/Users/techai/PKA testing/selfconnect/\n\n"
    "Key files to read:\n"
    "- C:/Users/techai/PKA testing/selfconnect/PRODUCTION_REVIEW.md\n"
    "- C:/Users/techai/PKA testing/selfconnect/self_connect.py\n"
    "- C:/Users/techai/PKA testing/selfconnect/vision_agent_dashboard.html\n"
    "- C:/Users/techai/PKA testing/selfconnect/vision_server/main.py\n\n"
    "Agent-B (Claude Code, hwnd=0x1311316) is in a SEPARATE terminal window titled 'SelfConnect mesh peer terminal setup'.\n"
    "Agent-B is currently investigating GitHub CI failures.\n\n"
    "YOUR TASK: Read PRODUCTION_REVIEW.md, then write your intro + 3 improvement ideas to:\n"
    "C:/Users/techai/PKA testing/Owner's Inbox/agent-c-gemini-intro.md\n\n"
    "Use the Read File tool with the exact paths above. Do NOT search — just read those specific files."
)

send_string(gemini_w, correction)
print("Correction sent to Gemini")
time.sleep(0.3)

# Send Enter
ctypes.windll.user32.SetForegroundWindow(gemini_hwnd)
time.sleep(0.4)
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_RETURN = 0x0D

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))
    ]

class INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('ki', KEYBDINPUT), ('padding', ctypes.c_ubyte * 8)]

i_d = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_RETURN))
i_u = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_RETURN, dwFlags=KEYEVENTF_KEYUP))
ctypes.windll.user32.SendInput(1, ctypes.byref(i_d), ctypes.sizeof(INPUT))
time.sleep(0.05)
ctypes.windll.user32.SendInput(1, ctypes.byref(i_u), ctypes.sizeof(INPUT))
print("Enter sent")
