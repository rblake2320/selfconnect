"""Test SelfConnect → Antigravity (VS Code) injection via Win32."""
import sys, os, time, ctypes
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string

user32 = ctypes.windll.user32

VSCODE_HWND = 0xca088e

# Find all child windows of VS Code to locate the Antigravity input
children = []
def enum_child_cb(hwnd, _):
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    cls_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls_buf, 256)
    title = buf.value.encode('ascii','replace').decode('ascii')
    cls = cls_buf.value.encode('ascii','replace').decode('ascii')
    children.append((hwnd, cls, title))
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
user32.EnumChildWindows(VSCODE_HWND, WNDENUMPROC(enum_child_cb), 0)

print(f"Found {len(children)} child windows in VS Code")
# Show any that look like input areas
for hwnd, cls, title in children:
    if any(x in cls.lower() for x in ['edit', 'input', 'chrome', 'web']):
        print(f"  INPUT candidate: 0x{hwnd:x} cls={cls[:40]} title={title[:40]}")

# VS Code renders via Chromium — the whole editor is one big Chrome_WidgetWin
# Try clicking into the Antigravity input area, then sending keys
# First bring VS Code to foreground
user32.SetForegroundWindow(VSCODE_HWND)
time.sleep(0.5)

# Use WM_CHAR injection directly to VS Code main window
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

class KEYBDINPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [  # noqa: RUF012
        ('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))
    ]

class INPUT(ctypes.Structure):  # noqa: RUF012
    _fields_ = [('type', ctypes.c_ulong), ('ki', KEYBDINPUT), ('padding', ctypes.c_ubyte * 8)]  # noqa: RUF012

def send_key(vk):
    i_d = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk))
    i_u = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP))
    user32.SendInput(1, ctypes.byref(i_d), ctypes.sizeof(INPUT))
    time.sleep(0.03)
    user32.SendInput(1, ctypes.byref(i_u), ctypes.sizeof(INPUT))

# Try clicking the Antigravity panel input via WM_CHAR on the VS Code chrome window
# Find Chrome_WidgetWin_1 (the main render surface)
chrome_win = next((h for h, c, t in children if 'chrome_widgetwin' in c.lower()), None)
if chrome_win:
    print(f"\nChrome render surface: 0x{chrome_win:x}")
    # Post WM_CHAR characters as test
    WM_CHAR = 0x0102
    test_msg = "Hello from SelfConnect Agent-A via Win32 PostMessage"
    print(f"Injecting via WM_CHAR to Chrome surface...")
    for ch in test_msg:
        ctypes.windll.user32.PostMessageW(chrome_win, WM_CHAR, ord(ch), 0)
        time.sleep(0.02)
    print("Injection attempted")
else:
    print("No Chrome render surface found — trying direct SendInput to foreground")
    msg = "Hello from SelfConnect Agent-A via Win32 PostMessage"
    wins = list_windows()
    vscode_w = next((w for w in wins if w.hwnd == VSCODE_HWND), None)
    if vscode_w:
        send_string(vscode_w, msg)
        print("send_string dispatched")
