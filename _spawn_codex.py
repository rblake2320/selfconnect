"""Session B runs this to spawn Session C (Codex/ChatGPT)."""
import sys, os, time, subprocess, ctypes
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from self_connect import list_windows, send_string, save_capture, restore_window

user32 = ctypes.windll.user32
os.makedirs('proofs', exist_ok=True)

print("Step 1: Snapshot existing windows...")
before = {w.hwnd for w in list_windows()}

print("Step 2: Launch new cmd.exe...")
proc = subprocess.Popen(
    ["cmd.exe", "/k", "cd /d \"C:\\Users\\techai\\PKA testing\\airgap-sop\""],
    creationflags=subprocess.CREATE_NEW_CONSOLE
)
time.sleep(2.5)

print("Step 3: Find new window via hwnd-set-diff...")
new_win = None
for w in list_windows():
    if w.hwnd not in before:
        cb = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(w.hwnd, cb, 256)
        cls = cb.value
        if "CASCADIA" in cls.upper() or "Console" in cls:
            new_win = w
            print(f"  Found: hwnd={w.hwnd} class={cls!r} title={w.title[:50]!r}")
            break

if not new_win:
    print("ERROR: no terminal found")
    sys.exit(1)

restore_window(new_win.hwnd)
time.sleep(0.3)

print("Step 4: Type 'codex' + Enter...")
send_string(new_win, "codex\r")
print("  Waiting 15s for Codex to start...")
time.sleep(15)

img = save_capture(new_win.hwnd, path="proofs/session_c_codex_start.png")
print(f"  Captured startup: {img}")
print(f"  Session C hwnd = {new_win.hwnd}")

print("Step 5: Waiting 10 more seconds for Codex UI to settle...")
time.sleep(10)
img2 = save_capture(new_win.hwnd, path="proofs/session_c_codex_settled.png")
print(f"  Captured settled: {img2}")
print(f"\nSession C hwnd={new_win.hwnd} — check proofs/session_c_codex_start.png")
