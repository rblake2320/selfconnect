"""Launch Codex CLI as Agent-D in a new standalone terminal window."""
import subprocess, time, os, sys, ctypes

codex_bin = os.path.expandvars(
    r'%APPDATA%\npm\node_modules\@openai\codex\bin\codex.js'
)
wt = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe')

# Launch a new standalone window running codex
cmd = [
    wt, '-w', 'new',
    '--title', 'SelfConnect mesh peer - Codex Agent-D',
    'cmd', '/k',
    f'title SelfConnect mesh peer - Codex Agent-D && node "{codex_bin}"'
]

proc = subprocess.Popen(cmd)
print(f"Launched Codex terminal (pid={proc.pid})")
print("Wait ~5s for window to open, then we grab its HWND")
time.sleep(5)

# Find the new Codex window
import ctypes
user32 = ctypes.windll.user32

found = []
def enum_cb(hwnd, _):
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value
    if 'Codex' in title or 'codex' in title.lower() or 'Agent-D' in title:
        found.append((hwnd, title))
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
cb = WNDENUMPROC(enum_cb)
user32.EnumWindows(cb, 0)

if found:
    for hwnd, title in found:
        print(f"  Found Codex window: hwnd=0x{hwnd:x} title={title[:60]}")
else:
    print("Codex window not found yet — may need more time to open")
