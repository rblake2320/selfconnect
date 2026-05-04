"""
SelfConnect → Antigravity clipboard injection.
Finds Antigravity window, focuses chat input, pastes message via Ctrl+V.
More reliable than character-by-character SendInput for WebView inputs.
"""
import sys, ctypes, time, subprocess
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

INPUT_KEYBOARD  = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL      = 0x11
VK_V            = 0x56
VK_RETURN       = 0x0D
VK_ESCAPE       = 0x1B
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk',ctypes.c_ushort),('wScan',ctypes.c_ushort),
                ('dwFlags',ctypes.c_ulong),('time',ctypes.c_ulong),
                ('dwExtraInfo',ctypes.POINTER(ctypes.c_ulong))]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx',ctypes.c_long),('dy',ctypes.c_long),('mouseData',ctypes.c_ulong),
                ('dwFlags',ctypes.c_ulong),('time',ctypes.c_ulong),
                ('dwExtraInfo',ctypes.POINTER(ctypes.c_ulong))]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('_u', _INPUT_UNION), ('_pad', ctypes.c_ubyte*4)]

def key_press(vk):
    i_d = INPUT(type=INPUT_KEYBOARD)
    i_d._u.ki = KEYBDINPUT(wVk=vk)
    i_u = INPUT(type=INPUT_KEYBOARD)
    i_u._u.ki = KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP)
    user32.SendInput(1, ctypes.byref(i_d), ctypes.sizeof(INPUT))
    time.sleep(0.05)
    user32.SendInput(1, ctypes.byref(i_u), ctypes.sizeof(INPUT))

def ctrl_v():
    i_d = INPUT(type=INPUT_KEYBOARD)
    i_d._u.ki = KEYBDINPUT(wVk=VK_CONTROL)
    iv_d = INPUT(type=INPUT_KEYBOARD)
    iv_d._u.ki = KEYBDINPUT(wVk=VK_V)
    iv_u = INPUT(type=INPUT_KEYBOARD)
    iv_u._u.ki = KEYBDINPUT(wVk=VK_V, dwFlags=KEYEVENTF_KEYUP)
    i_u = INPUT(type=INPUT_KEYBOARD)
    i_u._u.ki = KEYBDINPUT(wVk=VK_CONTROL, dwFlags=KEYEVENTF_KEYUP)
    user32.SendInput(1, ctypes.byref(i_d), ctypes.sizeof(INPUT))
    time.sleep(0.03)
    user32.SendInput(1, ctypes.byref(iv_d), ctypes.sizeof(INPUT))
    time.sleep(0.03)
    user32.SendInput(1, ctypes.byref(iv_u), ctypes.sizeof(INPUT))
    time.sleep(0.03)
    user32.SendInput(1, ctypes.byref(i_u), ctypes.sizeof(INPUT))

def click(x, y):
    user32.SetCursorPos(x, y)
    time.sleep(0.05)
    m_d = INPUT(type=0)  # INPUT_MOUSE
    m_d._u.mi = MOUSEINPUT(dwFlags=MOUSEEVENTF_LEFTDOWN)
    m_u = INPUT(type=0)
    m_u._u.mi = MOUSEINPUT(dwFlags=MOUSEEVENTF_LEFTUP)
    user32.SendInput(1, ctypes.byref(m_d), ctypes.sizeof(INPUT))
    time.sleep(0.05)
    user32.SendInput(1, ctypes.byref(m_u), ctypes.sizeof(INPUT))

def set_clipboard(text):
    """Set Windows clipboard to text using powershell (no ctypes CF_UNICODETEXT needed)."""
    escaped = text.replace('"', '`"').replace("'", "`'")
    subprocess.run(
        ['powershell', '-Command', f'Set-Clipboard -Value "{escaped}"'],
        capture_output=True
    )

def find_antigravity_hwnd():
    """Find Antigravity.exe window handle."""
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    def cb(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(512)
        kernel32.GetModuleFileNameExW = None  # not available this way
        # Check window title
        title_buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title_buf, 256)
        title = title_buf.value
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        cls = cls_buf.value
        if cls == 'Chrome_WidgetWin_1' and title:
            found.append((hwnd, title, pid.value))
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found

def find_render_widget(parent_hwnd):
    """Find Chrome_RenderWidgetHostHWND inside parent."""
    renders = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    def cb(hwnd, _):
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        if 'renderwidget' in cls_buf.value.lower():
            renders.append(hwnd)
        return True
    user32.EnumChildWindows(parent_hwnd, WNDENUMPROC(cb), 0)
    return renders

# ─── Discover Antigravity window ───────────────────────────────────────────────
print("Scanning for Chrome_WidgetWin_1 windows...")
windows = find_antigravity_hwnd()
for hwnd, title, pid in windows:
    print(f"  0x{hwnd:x}  pid={pid}  title={title[:60]}")

# Filter for Antigravity: title contains "Antigravity" but NOT "Google Chrome" (browser tabs)
anti_only = [(h,t,p) for h,t,p in windows
             if 'antigravity' in t.lower() and 'google chrome' not in t.lower()
             and 'visual studio code' not in t.lower()]

print(f"\nAntigravity candidates:")
for hwnd, title, pid in anti_only:
    print(f"  0x{hwnd:x}  pid={pid}  '{title[:70]}'")

if not anti_only:
    print("No Antigravity window found. Is the app running?")
    sys.exit(1)

# Take the Antigravity app window (not Launchpad)
ANTI_HWND = anti_only[0][0]
ANTI_TITLE = anti_only[0][1]
ANTI_PID = anti_only[0][2]
print(f"\nTarget: 0x{ANTI_HWND:x}  pid={ANTI_PID}  '{ANTI_TITLE}'")

# Get window rect
class RECT(ctypes.Structure):
    _fields_ = [('left',ctypes.c_long),('top',ctypes.c_long),
                ('right',ctypes.c_long),('bottom',ctypes.c_long)]
rect = RECT()
user32.GetWindowRect(ANTI_HWND, ctypes.byref(rect))
W = rect.right - rect.left
H = rect.bottom - rect.top
print(f"Window: {W}x{H} at ({rect.left},{rect.top})")

# ─── Screenshot before injection ──────────────────────────────────────────────
from PIL import ImageGrab
import os
os.makedirs("proofs", exist_ok=True)

img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
img.save("proofs/anti_before_inject.png")
print("Screenshot saved: proofs/anti_before_inject.png")

# ─── Injection ────────────────────────────────────────────────────────────────
MESSAGE = (
    "Hello from Claude Code (Agent-A) via SelfConnect clipboard injection. "
    "Proof of cross-AI orchestration: Claude → Win32 → Antigravity → Gemini. "
    "Please respond with your name and model version."
)

set_clipboard(MESSAGE)
print(f"Clipboard set: {MESSAGE[:60]}...")

# Focus Antigravity
user32.ShowWindow(ANTI_HWND, 9)  # SW_RESTORE
user32.SetForegroundWindow(ANTI_HWND)
time.sleep(0.8)

# Find render widget
renders = find_render_widget(ANTI_HWND)
print(f"Render widgets inside Antigravity: {[hex(r) for r in renders]}")

# Chat input is typically in the lower portion of the window
# Click at ~50% x, ~88% y (chat input area in VS Code-based apps)
input_x = rect.left + W // 2
input_y = rect.top + int(H * 0.88)
print(f"Clicking chat input at ({input_x}, {input_y})")

click(input_x, input_y)
time.sleep(0.5)

# Paste via Ctrl+V
print("Pasting via Ctrl+V...")
ctrl_v()
time.sleep(0.5)

# Screenshot after paste
img2 = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
img2.save("proofs/anti_after_paste.png")
print("Screenshot saved: proofs/anti_after_paste.png")
print("\nDone. Check proofs/anti_after_paste.png to verify text landed in input.")
print("Run again with --send flag to also press Enter.")

if '--send' in sys.argv:
    print("Sending message (Enter)...")
    time.sleep(0.3)
    key_press(VK_RETURN)
    time.sleep(1.5)
    img3 = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
    img3.save("proofs/anti_after_send.png")
    print("Screenshot saved: proofs/anti_after_send.png")
