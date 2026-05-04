"""Open Antigravity panel in VS Code and inject a task from Claude."""
import ctypes, time
from PIL import ImageGrab

user32 = ctypes.windll.user32
VSCODE_HWND = 0x804b4
# VS Code window: 2405,232 -> 3630,1040

# Bring VS Code to front
user32.SetForegroundWindow(VSCODE_HWND)
user32.BringWindowToTop(VSCODE_HWND)
time.sleep(0.6)

# Use Ctrl+Shift+P (command palette) to open Antigravity
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_P = 0x50

class KEYBDINPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [  # noqa: RUF012
        ('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))
    ]

class INPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [('type', ctypes.c_ulong), ('ki', KEYBDINPUT), ('padding', ctypes.c_ubyte * 8)]  # noqa: RUF012

def key_down(vk):
    i = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk))
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))

def key_up(vk):
    i = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP))
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))

# Press Ctrl+Shift+P
key_down(VK_CONTROL)
key_down(VK_SHIFT)
key_down(VK_P)
time.sleep(0.05)
key_up(VK_P)
key_up(VK_SHIFT)
key_up(VK_CONTROL)
time.sleep(0.8)

# Type "Antigravity" to find the command
WM_CHAR = 0x0102
# Get the Chrome render surface child
children = []
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
def cb(hwnd, _):
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    if 'chrome_renderwidget' in cls.value.lower():
        children.append(hwnd)
    return True
user32.EnumChildWindows(VSCODE_HWND, WNDENUMPROC(cb), 0)

# Type "Antigravity: Focus" in command palette via SendInput
search = "Antigravity: Focus on Chat View"
for ch in search:
    wm_char_i = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0, wScan=ord(ch), dwFlags=0x0004))  # KEYEVENTF_UNICODE
    wm_char_u = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0, wScan=ord(ch), dwFlags=0x0004|KEYEVENTF_KEYUP))
    user32.SendInput(1, ctypes.byref(wm_char_i), ctypes.sizeof(INPUT))
    time.sleep(0.03)
    user32.SendInput(1, ctypes.byref(wm_char_u), ctypes.sizeof(INPUT))

print("Typed search in command palette")
time.sleep(1.0)

# Screenshot to see what appeared
class RECT(ctypes.Structure):
    _fields_ = [('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]
rect = RECT()
user32.GetWindowRect(VSCODE_HWND, ctypes.byref(rect))
img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
img.save("proofs/vscode_palette.png")
print("Screenshot saved")
