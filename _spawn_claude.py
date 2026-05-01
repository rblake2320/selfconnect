"""Spawn a new terminal, type into it via SelfConnect, then launch Claude."""
import sys, os, time, subprocess, ctypes
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from self_connect import (
    list_windows, send_string, capture_window, save_capture, restore_window
)

user32 = ctypes.windll.user32

os.makedirs('proofs', exist_ok=True)

# Step 1: Snapshot + launch
before = {w.hwnd for w in list_windows()}
print("Step 1: Launching new cmd.exe...")

proc = subprocess.Popen(
    ["cmd.exe", "/k", "cd /d \"C:\\Users\\techai\\PKA testing\\airgap-sop\""],
    creationflags=subprocess.CREATE_NEW_CONSOLE
)
time.sleep(2.5)

# Step 2: Find new window
print("Step 2: Finding terminal...")
new_win = None
for w in list_windows():
    if w.hwnd not in before:
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(w.hwnd, cls_buf, 256)
        cls = cls_buf.value
        if "Console" in cls or "cmd" in w.title.lower() or "Terminal" in cls or "CASCADIA" in cls.upper():
            new_win = w
            print(f"  Found: hwnd={w.hwnd} class={cls!r} title={w.title[:50]!r}")
            break

if not new_win:
    # Broader search
    for w in list_windows():
        if w.hwnd not in before and w.exe_name and ("cmd" in w.exe_name.lower() or "terminal" in w.exe_name.lower()):
            new_win = w
            print(f"  Found (fallback): hwnd={w.hwnd} title={w.title[:50]!r}")
            break

if not new_win:
    print("ERROR: no terminal found")
    for w in list_windows():
        if w.hwnd not in before:
            print(f"  hwnd={w.hwnd} title={w.title[:60]!r} exe={w.exe_name!r}")
    sys.exit(1)

restore_window(new_win.hwnd)
time.sleep(0.3)

# Step 3: Test echo with \r (carriage return = Enter in console)
print("Step 3: Typing test command with Enter...")
send_string(new_win, "echo SELFCONNECT TERMINAL TEST\r")
time.sleep(1.5)

img1 = save_capture(new_win.hwnd, path="proofs/spawn_step1_echo.png")
print(f"  Captured: {img1}")

# Step 4: Launch Claude
print("Step 4: Typing 'claude' + Enter...")
send_string(new_win, "claude\r")
print("  Waiting 15s for Claude to start...")
time.sleep(15)

img2 = save_capture(new_win.hwnd, path="proofs/spawn_step2_claude.png")
print(f"  Captured: {img2}")

# Step 5: Type handoff message
print("Step 5: Sending handoff message...")
handoff = 'Read the handoff file at "C:\\Users\\techai\\PKA testing\\Owner\'s Inbox\\selfconnect-handoff-2026-04-30.md" and self_connect.py. This is the SelfConnect SDK. Continue where we left off.'

send_string(new_win, handoff)
time.sleep(0.5)

# Claude Code uses Ink TUI — Enter might need to be sent differently
# Try \r first, then \n
send_string(new_win, "\r")
time.sleep(0.5)
send_string(new_win, "\n")
time.sleep(1)

img3 = save_capture(new_win.hwnd, path="proofs/spawn_step3_handoff.png")
print(f"  Captured: {img3}")

print("\nWaiting 20s for Claude to process...")
time.sleep(20)

img4 = save_capture(new_win.hwnd, path="proofs/spawn_step4_response.png")
print(f"  Final capture: {img4}")

print("\nDone! Check proofs/spawn_step*.png for evidence.")
