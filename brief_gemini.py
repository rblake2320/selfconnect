"""Brief Gemini (Agent-C) on the SelfConnect mesh."""
import sys, os, time, ctypes
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string, focus_window

wins = list_windows()
gemini_hwnd = 0x2602034
gemini_w = next((w for w in wins if w.hwnd == gemini_hwnd), None)
if not gemini_w:
    print("ERROR: Gemini window not found")
    sys.exit(1)

briefing = (
    "You are Agent-C (Gemini) in the SelfConnect AI mesh. Your HWND is 0x2602034.\n\n"
    "== MESH STATE ==\n"
    "- Agent-A: Claude Code (orchestrator, this session)\n"
    "- Agent-B: Claude Code hwnd=0x1311316, currently investigating GitHub CI failures across 50 repos\n"
    "- Agent-C: YOU (Gemini CLI v0.40.1, Google One AI Ultra) - just joined\n\n"
    "== WHAT SELFCONNECT IS ==\n"
    "Win32-based AI-to-AI communication. Agents communicate via PostMessage(WM_CHAR) to inject\n"
    "text into each other's terminals, and PrintWindow to read what others wrote. No cloud broker,\n"
    "no API between agents - pure OS channels. Patented system (P-SELFCONNECT).\n"
    "SDK: C:/Users/techai/PKA testing/selfconnect/self_connect.py\n\n"
    "== YOUR FIRST TASK ==\n"
    "1. Read PRODUCTION_REVIEW.md at C:/Users/techai/PKA testing/selfconnect/PRODUCTION_REVIEW.md\n"
    "2. Review vision_agent_dashboard.html - understand what it does\n"
    "3. Write a file: C:/Users/techai/PKA testing/Owner's Inbox/agent-c-gemini-intro.md\n"
    "   Contents: your introduction, what you see in the project, and 3 ideas you have for improving it\n"
    "4. Then confirm: AGENT-C ONLINE AND BRIEFED\n\n"
    "The project: SelfConnect Vision Agent - AI that sees and controls any Windows app via Win32.\n"
    "Live server at http://127.0.0.1:7421/dashboard\n\n"
    "Go."
)

send_string(gemini_w, briefing)
print("Gemini briefed successfully")
time.sleep(0.5)

# Send Enter via SendInput (required for Gemini CLI)
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
print("Enter sent — Gemini is processing")
