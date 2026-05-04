"""Inject a Claude message directly into Antigravity's VS Code chat input."""
import ctypes, time
from PIL import ImageGrab

user32 = ctypes.windll.user32
VSCODE_HWND = 0x804b4

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP   = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_SHIFT   = 0x10
VK_P       = 0x50
VK_RETURN  = 0x0D

class KEYBDINPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [('wVk',ctypes.c_ushort),('wScan',ctypes.c_ushort),('dwFlags',ctypes.c_ulong),
                ('time',ctypes.c_ulong),('dwExtraInfo',ctypes.POINTER(ctypes.c_ulong))]  # noqa: RUF012

class MOUSEINPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [('dx',ctypes.c_long),('dy',ctypes.c_long),('mouseData',ctypes.c_ulong),
                ('dwFlags',ctypes.c_ulong),('time',ctypes.c_ulong),('dwExtraInfo',ctypes.POINTER(ctypes.c_ulong))]  # noqa: RUF012

class INPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [('type',ctypes.c_ulong),('mi',MOUSEINPUT),('padding',ctypes.c_ubyte*8)]  # noqa: RUF012

def key_down(vk):
    i = INPUT(type=INPUT_KEYBOARD, mi=MOUSEINPUT())
    i.mi = MOUSEINPUT()
    kb = KEYBDINPUT(wVk=vk)
    ctypes.memmove(ctypes.byref(i, 4), ctypes.byref(kb), ctypes.sizeof(kb))
    i.type = INPUT_KEYBOARD
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))

def key_up(vk):
    kb = KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP)
    i = INPUT(type=INPUT_KEYBOARD)
    ctypes.memmove(ctypes.byref(i, 4), ctypes.byref(kb), ctypes.sizeof(kb))
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))

def send_unicode(text):
    for ch in text:
        kb_d = KEYBDINPUT(wScan=ord(ch), dwFlags=KEYEVENTF_UNICODE)
        kb_u = KEYBDINPUT(wScan=ord(ch), dwFlags=KEYEVENTF_UNICODE|KEYEVENTF_KEYUP)
        i_d = INPUT(type=INPUT_KEYBOARD)
        i_u = INPUT(type=INPUT_KEYBOARD)
        ctypes.memmove(ctypes.byref(i_d, 4), ctypes.byref(kb_d), ctypes.sizeof(kb_d))
        ctypes.memmove(ctypes.byref(i_u, 4), ctypes.byref(kb_u), ctypes.sizeof(kb_u))
        user32.SendInput(1, ctypes.byref(i_d), ctypes.sizeof(INPUT))
        time.sleep(0.02)
        user32.SendInput(1, ctypes.byref(i_u), ctypes.sizeof(INPUT))
        time.sleep(0.01)

# Step 1: Focus VS Code
user32.SetForegroundWindow(VSCODE_HWND)
user32.BringWindowToTop(VSCODE_HWND)
time.sleep(0.6)

# Step 2: Ctrl+Shift+P → focus Antigravity chat
key_down(VK_CONTROL); key_down(VK_SHIFT); key_down(VK_P)
time.sleep(0.05)
key_up(VK_P); key_up(VK_SHIFT); key_up(VK_CONTROL)
time.sleep(0.6)

send_unicode("Antigravity: Focus on Chat View")
time.sleep(0.8)

# Press Enter to execute command
kb_d = KEYBDINPUT(wVk=VK_RETURN)
kb_u = KEYBDINPUT(wVk=VK_RETURN, dwFlags=KEYEVENTF_KEYUP)
i_d = INPUT(type=INPUT_KEYBOARD)
i_u = INPUT(type=INPUT_KEYBOARD)
ctypes.memmove(ctypes.byref(i_d, 4), ctypes.byref(kb_d), ctypes.sizeof(kb_d))
ctypes.memmove(ctypes.byref(i_u, 4), ctypes.byref(kb_u), ctypes.sizeof(kb_u))
user32.SendInput(1, ctypes.byref(i_d), ctypes.sizeof(INPUT))
time.sleep(0.05)
user32.SendInput(1, ctypes.byref(i_u), ctypes.sizeof(INPUT))
time.sleep(1.2)

# Step 3: Type the Claude message into Antigravity
message = (
    "Hello from Claude Code (Agent-A) via SelfConnect Win32 injection. "
    "Please create a new file called hello_mesh.py in the selfconnect folder "
    "with a docstring explaining this was created by Claude orchestrating Gemini "
    "through Antigravity without any API calls — pure Win32 PostMessage."
)
send_unicode(message)
time.sleep(0.4)

# Screenshot before sending
class RECT(ctypes.Structure):
    _fields_ = [('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]
rect = RECT()
user32.GetWindowRect(VSCODE_HWND, ctypes.byref(rect))
img = ImageGrab.grab(bbox=(rect.left,rect.top,rect.right,rect.bottom), all_screens=True)
img.save("proofs/antigravity_injected.png")
print("Message typed — screenshot saved. Ready to send (Enter).")
